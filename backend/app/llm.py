"""OpenAI-compatible chat transport. Credentials stay in the local private .env."""

import hashlib
import json
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values, set_key
from fastapi import HTTPException

from . import config
from .agent_schemas import AISettingsInput

PRESETS = [
    {"id": "deepseek", "label": "DeepSeek Flash", "base_url": "https://api.deepseek.com",
     "model": "deepseek-flash", "requires_key": True, "docs_url": "https://api-docs.deepseek.com/"},
    {"id": "mimo", "label": "小米 MiMo", "base_url": "https://api.xiaomimimo.com/v1",
     "model": "mimo-v2.6-flash", "requires_key": True, "docs_url": "https://platform.xiaomimimo.com/"},
    {"id": "bailian", "label": "阿里云百炼（北京）", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "model": "qwen3.8-flash", "requires_key": True, "docs_url": "https://help.aliyun.com/zh/model-studio/"},
    {"id": "openrouter", "label": "OpenRouter 免费路由", "base_url": "https://openrouter.ai/api/v1",
     "model": "openrouter/free", "requires_key": True, "docs_url": "https://openrouter.ai/docs/guides/routing/model-variants/free"},
    {"id": "ollama", "label": "本机 Ollama", "base_url": "http://127.0.0.1:11434/v1",
     "model": "qwen3:8b", "requires_key": False, "docs_url": "https://docs.ollama.com/api/openai-compatibility"},
    {"id": "custom", "label": "其他兼容接口", "base_url": "https://api.example.com/v1",
     "model": "your-model-id", "requires_key": True, "docs_url": ""},
]
SETTINGS_LOCK = threading.RLock()
CONFIG_KEYS = ("LLM_ENABLED", "LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL",
               "LLM_VERIFIED_FINGERPRINT")


def _settings_path():
    return Path(os.getenv("QINGHE_DOTENV_PATH", config.ROOT / ".env"))


@dataclass(frozen=True, repr=False)
class ModelSettings:
    enabled: bool
    provider: str
    base_url: str
    model: str
    api_key: str
    verified_fingerprint: str = ""

    @property
    def configured(self):
        return bool(self.base_url and self.model and (self.api_key or
                    (self.provider == "ollama" and urlsplit(self.base_url).hostname in
                     {"127.0.0.1", "localhost", "::1"})))

    @property
    def fingerprint(self):
        raw = json.dumps([self.provider, self.base_url, self.model, self.api_key]).encode()
        return hashlib.sha256(raw).hexdigest()

    @property
    def verified(self):
        return self.configured and self.verified_fingerprint == self.fingerprint


def get_settings():
    with SETTINGS_LOCK:
        values = dotenv_values(_settings_path())
        def read(key, fallback=""):
            return str(values.get(key, os.getenv(key, fallback)) or fallback)
        return ModelSettings(read("LLM_ENABLED", "false").lower() == "true",
                             read("LLM_PROVIDER", "deepseek"),
                             read("LLM_BASE_URL", PRESETS[0]["base_url"]).rstrip("/"),
                             read("LLM_MODEL", PRESETS[0]["model"]), read("LLM_API_KEY"),
                             read("LLM_VERIFIED_FINGERPRINT"))


def public_settings(settings=None):
    settings = settings or get_settings()
    return {"enabled": settings.enabled, "provider": settings.provider, "base_url": settings.base_url,
            "model": settings.model, "has_api_key": bool(settings.api_key),
            "configured": settings.configured, "connection_verified": settings.verified,
            "presets": PRESETS, "notice": "模型和接口可随时切换；是否免费以服务商账号的额度与期限为准。"}


def agent_status(settings=None):
    settings = settings or get_settings()
    ready = settings.enabled and settings.configured
    notice = ("已配置模型；LangChain 会编排已核验原文和授权范围内的只读工具，实际结果以每次回答模式为准。" if ready else
              "原文与业务查询模式：尚未启用可用模型，当前结果由系统检索生成。管理员可在 AI 配置中接入。")
    return {"enabled": settings.enabled, "configured": settings.configured,
            "connection_verified": settings.verified, "provider": settings.provider,
            "model": settings.model, "mode": "model" if ready else "original_query",
            "orchestration": "langchain_controlled_rag" if ready else "source_and_rules_fallback",
            "max_message_length": 1800, "notice": notice}


def _write_values(changes):
    """Atomic replacement preserves unrelated database/JWT/bootstrap settings."""
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(prefix=".ai-config-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(path.read_text(encoding="utf-8") if path.exists() else "")
        for key, value in changes.items():
            set_key(filename, key, value, quote_mode="always")
        os.replace(filename, path)
        for key, value in changes.items():
            os.environ[key] = value
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


def save_settings(data: AISettingsInput):
    with SETTINGS_LOCK:
        old = get_settings()
        new_key = data.api_key.get_secret_value().strip() if data.api_key else ""
        target_changed = urlsplit(old.base_url).netloc.lower() != urlsplit(data.base_url).netloc.lower()
        if target_changed and old.api_key and not new_key and not data.clear_api_key:
            raise HTTPException(400, "切换接口服务时请填写新服务密钥，或勾选清除旧密钥")
        key = "" if data.clear_api_key else new_key or old.api_key
        settings = ModelSettings(data.enabled, data.provider, data.base_url, data.model, key)
        if settings.enabled and not settings.configured:
            raise HTTPException(400, "启用前请填写该服务的 API 密钥；本机 Ollama 不需要密钥")
        changes = {"LLM_ENABLED": str(settings.enabled).lower(), "LLM_PROVIDER": settings.provider,
                   "LLM_BASE_URL": settings.base_url, "LLM_MODEL": settings.model,
                   "LLM_API_KEY": settings.api_key,
                   "LLM_VERIFIED_FINGERPRINT": old.verified_fingerprint if settings.fingerprint == old.fingerprint else ""}
        try:
            _write_values(changes)
        except OSError:
            raise HTTPException(503, "本机配置保存失败，请检查项目目录是否可写") from None
        return public_settings(get_settings())


class ModelError(Exception):
    """Only safe categories cross the application boundary; no provider body."""
    MESSAGES = {"auth": "模型服务鉴权失败，请检查密钥", "quota": "模型服务限流或额度不足",
                "protocol": "模型或接口参数不兼容，请检查模型名称和工具调用支持",
                "service": "模型服务暂时不可用", "timeout": "模型服务响应超时",
                "network": "无法连接模型服务", "invalid": "模型响应格式无效",
                "budget": "本次模型工具调用达到次数限制"}

    def __init__(self, kind):
        self.kind = kind
        super().__init__(self.MESSAGES.get(kind, "模型请求未完成"))


def complete(settings, messages, tools=None, timeout=25):
    # Validate private configuration before the request enters LangChain.
    try:
        AISettingsInput(enabled=settings.enabled, provider=settings.provider,
                        base_url=settings.base_url, model=settings.model)
    except ValueError:
        raise ModelError("protocol") from None
    from . import langchain_flow

    try:
        return langchain_flow.complete(settings, messages, tools=tools, timeout=timeout)
    except langchain_flow.LangChainFlowError as error:
        raise ModelError(error.kind) from None


def test_connection():
    settings = get_settings()
    if not settings.configured:
        return {"ok": False, "connection_verified": False, "message": "请先保存接口、模型和所需密钥"}
    try:
        complete(settings, [{"role": "user", "content": "仅回复：连接成功。"}], timeout=15)
    except ModelError as error:
        return {"ok": False, "connection_verified": False, "message": str(error)}
    with SETTINGS_LOCK:
        if get_settings().fingerprint != settings.fingerprint:
            return {"ok": False, "connection_verified": False, "message": "配置已变更，请重新测试"}
        try:
            _write_values({"LLM_VERIFIED_FINGERPRINT": settings.fingerprint})
        except OSError:
            return {"ok": False, "connection_verified": False, "message": "连接成功，本机验证状态保存失败"}
    return {"ok": True, "connection_verified": True, "message": "模型已响应。工具调用能力以实际对话验证为准。"}
