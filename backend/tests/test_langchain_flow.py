import json
from types import SimpleNamespace

import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI as RealChatOpenAI

from app import langchain_flow


def test_langchain_prompt_model_and_read_only_tool_schema_are_used(monkeypatch):
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured["model_kwargs"] = kwargs

        def bind_tools(self, tools, **kwargs):
            captured["tools"] = tools
            captured["bind_kwargs"] = kwargs
            return self

        def invoke(self, messages):
            captured["messages"] = messages
            return AIMessage(content="", tool_calls=[{"id": "call-1", "name": "usage_guide", "args": {}}])

    monkeypatch.setattr(langchain_flow, "ChatOpenAI", FakeChatOpenAI)
    settings = SimpleNamespace(provider="ollama", base_url="http://127.0.0.1:11434/v1",
                               model="qwen3:8b", api_key="")
    messages = [
        {"role": "system", "content": "只依据已核验引用回答。"},
        {"role": "user", "content": "怎么查看申请记录？"},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "evidence-1", "type": "function",
            "function": {"name": "search_policies", "arguments": '{"query":"政策"}'},
        }]},
        {"role": "tool", "tool_call_id": "evidence-1", "content": '{"citations":["[1]"]}'},
    ]
    tools = [{"type": "function", "function": {
        "name": "usage_guide", "description": "查询当前角色的操作入口",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }}]

    response = langchain_flow.complete(settings, messages, tools=tools, timeout=12)

    assert response == {"role": "assistant", "content": "", "tool_calls": [{
        "id": "call-1", "type": "function", "function": {"name": "usage_guide", "arguments": "{}"},
    }]}
    assert captured["model_kwargs"]["api_key"] == "ollama-local-no-key"
    assert captured["model_kwargs"]["timeout"] == 12
    assert captured["model_kwargs"]["http_client"].follow_redirects is False
    assert captured["bind_kwargs"]["tool_choice"] == "auto"
    assert captured["tools"] == tools
    assert [type(message) for message in captured["messages"]] == [
        SystemMessage, HumanMessage, AIMessage, ToolMessage,
    ]


def test_real_chatopenai_adapter_sends_controlled_schema_to_compatible_endpoint(monkeypatch):
    captured = []

    def handler(request):
        captured.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={
            "id": "completion-1", "object": "chat.completion", "created": 1, "model": "fixture",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{
                    "id": "call-1", "type": "function",
                    "function": {"name": "usage_guide", "arguments": "{}"},
                }],
            }}],
        })

    transport = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setenv("LANGCHAIN_OPENAI_TCP_KEEPALIVE", "0")

    monkeypatch.setattr(langchain_flow, "_request_client", lambda _timeout: transport)
    monkeypatch.setattr(langchain_flow, "ChatOpenAI", RealChatOpenAI)
    settings = SimpleNamespace(provider="custom", base_url="https://fixture.example/v1",
                               model="fixture", api_key="test-key")
    response = langchain_flow.complete(settings, [
        {"role": "system", "content": "只读。"},
        {"role": "user", "content": "怎么操作？"},
    ], tools=[{"type": "function", "function": {
        "name": "usage_guide", "description": "查询当前角色的操作入口",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }}])

    assert response["tool_calls"][0]["function"] == {"name": "usage_guide", "arguments": "{}"}
    assert captured[0][0] == "/v1/chat/completions"
    assert captured[0][1]["tools"][0]["function"]["name"] == "usage_guide"
    assert captured[0][1]["messages"][0]["role"] == "system"


def test_real_deepseek_adapter_preserves_disabled_thinking_parameter(monkeypatch):
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "completion-2", "object": "chat.completion", "created": 1, "model": "fixture",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": "已收到。",
            }}],
        })

    transport = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(langchain_flow, "_request_client", lambda _timeout: transport)
    settings = SimpleNamespace(provider="deepseek", base_url="https://fixture.example/v1",
                               model="fixture", api_key="test-key")
    response = langchain_flow.complete(settings, [{"role": "user", "content": "你好"}])

    assert response == {"role": "assistant", "content": "已收到。"}
    assert captured[0]["thinking"] == {"type": "disabled"}
