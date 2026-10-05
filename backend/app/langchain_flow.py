"""Controlled LangChain execution for the authenticated assistant path.

This module only renders the already-redacted conversation, binds the fixed
read-only tool descriptions, and calls an OpenAI-compatible chat model. Tool
calls are returned as data; ``agent.py`` remains the only place that validates
arguments, checks identity/resource scope, and executes a read query.
"""

import json
import re
from collections.abc import Sequence
from typing import Any

import httpx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI


MAX_TOOL_CALLS_PER_RESPONSE = 6
MAX_TOOL_ARGUMENT_BYTES = 6_000
MAX_MODEL_CONTENT = 16_000
_TOOL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

# The system policy is a first-class template variable. The caller supplies
# redacted history, the current question, and cited evidence as discrete
# messages after it; this prevents ad-hoc role serialization before the model.
RAG_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "{system_prompt}"),
    MessagesPlaceholder("conversation_and_evidence"),
])


class LangChainFlowError(Exception):
    """A provider failure category that is safe for the application to expose."""

    def __init__(self, kind: str):
        self.kind = kind
        super().__init__(kind)


def _content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, list):
        pieces = []
        for item in value:
            if isinstance(item, str):
                pieces.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                pieces.append(item["text"])
            else:
                raise LangChainFlowError("invalid")
        return "".join(pieces)
    raise LangChainFlowError("invalid")


def _tool_calls_from_wire(raw_calls: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_calls, list) or len(raw_calls) > MAX_TOOL_CALLS_PER_RESPONSE:
        raise LangChainFlowError("invalid")
    calls = []
    for raw in raw_calls:
        if not isinstance(raw, dict):
            raise LangChainFlowError("invalid")
        identity = raw.get("id")
        function = raw.get("function")
        if not isinstance(identity, str) or not identity or len(identity) > 100 or not isinstance(function, dict):
            raise LangChainFlowError("invalid")
        name, arguments = function.get("name"), function.get("arguments")
        if not isinstance(name, str) or not _TOOL_NAME.fullmatch(name) or not isinstance(arguments, str):
            raise LangChainFlowError("invalid")
        if len(arguments.encode("utf-8")) > MAX_TOOL_ARGUMENT_BYTES:
            raise LangChainFlowError("invalid")
        try:
            parsed = json.loads(arguments)
        except (TypeError, ValueError):
            raise LangChainFlowError("invalid") from None
        if not isinstance(parsed, dict):
            raise LangChainFlowError("invalid")
        calls.append({"id": identity, "name": name, "args": parsed, "type": "tool_call"})
    return calls


def _to_langchain_messages(messages: Sequence[BaseMessage | dict[str, Any]]) -> list[BaseMessage]:
    rendered = []
    for item in messages:
        if isinstance(item, BaseMessage):
            rendered.append(item)
            continue
        if not isinstance(item, dict):
            raise LangChainFlowError("invalid")
        role = item.get("role")
        content = _content(item.get("content"))
        if role == "system":
            rendered.append(SystemMessage(content=content))
        elif role == "user":
            rendered.append(HumanMessage(content=content))
        elif role == "assistant":
            raw_calls = item.get("tool_calls") or []
            rendered.append(AIMessage(content=content, tool_calls=_tool_calls_from_wire(raw_calls)))
        elif role == "tool":
            identity = item.get("tool_call_id")
            if not isinstance(identity, str) or not identity or len(identity) > 100:
                raise LangChainFlowError("invalid")
            rendered.append(ToolMessage(content=content, tool_call_id=identity))
        else:
            raise LangChainFlowError("invalid")
    return rendered


def _render_prompt(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    system_prompt = ""
    conversation = list(messages)
    if conversation and isinstance(conversation[0], SystemMessage):
        system_prompt = _content(conversation[0].content)
        conversation = conversation[1:]
    return RAG_PROMPT.invoke({"system_prompt": system_prompt,
                              "conversation_and_evidence": conversation}).to_messages()


def _controlled_tools(tools: Sequence[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Accept only the server-built function schemas, never model-supplied tools."""
    normalized = []
    names = set()
    for definition in tools or []:
        function = definition.get("function") if isinstance(definition, dict) else None
        if not isinstance(function, dict):
            raise LangChainFlowError("invalid")
        name, description, parameters = (function.get("name"), function.get("description"),
                                         function.get("parameters"))
        if (not isinstance(name, str) or not _TOOL_NAME.fullmatch(name) or name in names or
                not isinstance(description, str) or not isinstance(parameters, dict)):
            raise LangChainFlowError("invalid")
        try:
            safe_parameters = json.loads(json.dumps(parameters, ensure_ascii=False))
        except (TypeError, ValueError):
            raise LangChainFlowError("invalid") from None
        names.add(name)
        normalized.append({"type": "function", "function": {"name": name, "description": description,
                                                                  "parameters": safe_parameters}})
    return normalized


def _request_client(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=max(0.1, min(timeout, 25)), follow_redirects=False)


def _model(settings: Any, timeout: float, http_client: httpx.Client) -> ChatOpenAI:
    kwargs: dict[str, Any] = {
        "model": settings.model,
        "base_url": settings.base_url,
        # Ollama's OpenAI-compatible endpoint permits any non-empty placeholder.
        "api_key": settings.api_key or "ollama-local-no-key",
        "timeout": max(0.1, min(timeout, 25)),
        "max_tokens": 1800,
        "max_retries": 0,
        "http_client": http_client,
    }
    if settings.provider == "deepseek":
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    try:
        return ChatOpenAI(**kwargs)
    except (TypeError, ValueError):
        raise LangChainFlowError("protocol") from None


def _error_kind(error: Exception) -> str:
    status = getattr(error, "status_code", None)
    if status in {401, 403}:
        return "auth"
    if status == 429:
        return "quota"
    if status in {400, 404, 405, 422}:
        return "protocol"
    name = type(error).__name__.lower()
    if isinstance(error, httpx.TimeoutException) or "timeout" in name:
        return "timeout"
    if isinstance(error, httpx.RequestError) or "connection" in name or "connect" in name:
        return "network"
    if status is not None:
        return "service"
    return "invalid"


def _wire_response(message: AIMessage) -> dict[str, Any]:
    if not isinstance(message, AIMessage) or getattr(message, "invalid_tool_calls", None):
        raise LangChainFlowError("invalid")
    content = _content(message.content)
    if len(content) > MAX_MODEL_CONTENT:
        raise LangChainFlowError("invalid")
    calls = list(message.tool_calls or [])
    if len(calls) > MAX_TOOL_CALLS_PER_RESPONSE:
        raise LangChainFlowError("invalid")
    output = {"role": "assistant", "content": content}
    if calls:
        wire_calls = []
        for call in calls:
            identity, name, arguments = call.get("id"), call.get("name"), call.get("args")
            if (not isinstance(identity, str) or not identity or len(identity) > 100 or
                    not isinstance(name, str) or not _TOOL_NAME.fullmatch(name) or
                    not isinstance(arguments, dict)):
                raise LangChainFlowError("invalid")
            encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
            if len(encoded.encode("utf-8")) > MAX_TOOL_ARGUMENT_BYTES:
                raise LangChainFlowError("invalid")
            wire_calls.append({"id": identity, "type": "function",
                               "function": {"name": name, "arguments": encoded}})
        output["tool_calls"] = wire_calls
    elif not content:
        raise LangChainFlowError("invalid")
    return output


def complete(settings: Any, messages: Sequence[BaseMessage | dict[str, Any]],
             tools: Sequence[dict[str, Any]] | None = None, timeout: float = 25) -> dict[str, Any]:
    """Run one LangChain-rendered, server-bounded model turn.

    The model receives citations and sanitized tool results as data. Returned
    tool calls are deliberately not invoked here, so a model cannot bypass the
    FastAPI authorization and Pydantic validation in ``agent._run_tool``.
    """
    http_client = None
    try:
        prompt_messages = _render_prompt(_to_langchain_messages(messages))
        http_client = _request_client(timeout)
        model = _model(settings, timeout, http_client)
        controlled_tools = _controlled_tools(tools)
        runnable = model.bind_tools(controlled_tools, tool_choice="auto") if controlled_tools else model
        return _wire_response(runnable.invoke(prompt_messages))
    except LangChainFlowError:
        raise
    except Exception as error:
        raise LangChainFlowError(_error_kind(error)) from None
    finally:
        if http_client is not None:
            http_client.close()
