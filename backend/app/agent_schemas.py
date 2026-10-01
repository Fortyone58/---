import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class AgentQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=1800)
    conversation_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


class AISettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    enabled: bool
    provider: str = Field(pattern=r"^(deepseek|mimo|bailian|openrouter|custom|ollama)$")
    base_url: str = Field(min_length=1, max_length=300)
    model: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._:/-]+$")
    api_key: SecretStr | None = None
    clear_api_key: bool = False

    @field_validator("base_url")
    @classmethod
    def check_url(cls, value):
        parts = urlsplit(value)
        if (not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or
                re.search(r"[\s\\]", value)):
            raise ValueError("请填写不含账号、查询参数或片段的 API 基础地址")
        local = parts.hostname in {"127.0.0.1", "localhost", "::1"}
        if parts.scheme != "https" and not (parts.scheme == "http" and local):
            raise ValueError("远程接口使用 HTTPS；HTTP 仅允许本机模型服务")
        try:
            parts.port
        except ValueError:
            raise ValueError("接口端口无效") from None
        return value.rstrip("/")

    @field_validator("api_key")
    @classmethod
    def check_key(cls, value):
        if value is not None:
            raw = value.get_secret_value()
            if len(raw) > 4096 or any(ord(char) < 32 for char in raw):
                raise ValueError("密钥格式无效")
        return value


class PolicySearch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=1800)


class JobSearch(PolicySearch):
    pass


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MonthlySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    month: str = Field(pattern=r"^(19|20)\d{2}-(0[1-9]|1[0-2])$")


class JobDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: int = Field(ge=1, strict=True)
