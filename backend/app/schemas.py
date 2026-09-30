import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from .config import SKILLS


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Slot(StrictModel):
    day: int = Field(ge=1, le=7)
    start: str
    end: str

    @model_validator(mode="after")
    def check_times(self):
        for value in [self.start, self.end]:
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
                raise ValueError("时间格式应为 HH:MM")
        if self.start >= self.end:
            raise ValueError("结束时间须晚于开始时间，跨日请分开登记")
        return self


def normalize_skills(value):
    if value is None:
        return None
    normalized = sorted({item.strip().lower() for item in value})
    if any(item not in SKILLS for item in normalized):
        raise ValueError("技能必须选择受控词表中的项目")
    return normalized


class Register(StrictModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$")
    password: str = Field(min_length=8, max_length=100)
    display_name: str = Field(min_length=1, max_length=50)


class Login(StrictModel):
    username: str = Field(max_length=32)
    password: str = Field(max_length=100)


class Profile(StrictModel):
    display_name: str = Field(min_length=1, max_length=50)
    major: str = Field(default="", max_length=100)
    skills: list[str] = Field(max_length=10)
    slots: list[Slot] = Field(max_length=30)
    area: Literal["A", "B", "C"]
    _skills = field_validator("skills")(normalize_skills)


class JobInput(StrictModel):
    title: str = Field(min_length=2, max_length=100)
    description: str = Field(min_length=4, max_length=4000)
    location: str = Field(min_length=2, max_length=100)
    area: Literal["A", "B", "C"]
    category: Literal["temporary", "fixed"]
    wage: Decimal = Field(gt=0, le=100000, decimal_places=2)
    quota: int = Field(ge=1, le=1000, strict=True)
    slots: list[Slot] = Field(max_length=30)
    skills: list[str] = Field(max_length=10)
    _skills = field_validator("skills")(normalize_skills)


class ApplyInput(StrictModel):
    job_id: int = Field(gt=0)
    reason: str = Field(min_length=5, max_length=1000)


class Action(StrictModel):
    action: str = Field(max_length=32)
    note: str = Field(default="", max_length=1000)


class HoursInput(StrictModel):
    application_id: int = Field(gt=0)
    work_date: date
    hours: Decimal = Field(gt=0, le=24, decimal_places=2)
    reason: str = Field(default="", max_length=1000)


class Correction(StrictModel):
    work_date: date
    hours: Decimal = Field(ge=0, le=24, decimal_places=2)
    reason: str = Field(min_length=3, max_length=1000)


class Verification(StrictModel):
    note: str = Field(min_length=3, max_length=1000)


class Hardship(StrictModel):
    hardship: Literal["D1", "D2", "D3"]
    note: str = Field(min_length=3, max_length=1000)


class CreateUser(Register):
    role: Literal["unit", "aid", "admin"]
    unit_id: int | None = None

    @model_validator(mode="after")
    def check_unit(self):
        if self.role == "unit" and self.unit_id is None:
            raise ValueError("用工单位管理员必须绑定单位")
        if self.role != "unit" and self.unit_id is not None:
            raise ValueError("该角色不应绑定用工单位")
        return self


class UnitInput(StrictModel):
    name: str = Field(min_length=2, max_length=100)
    description: str = Field(default="", max_length=1000)
    area: Literal["A", "B", "C"]


class Weights(StrictModel):
    time: int = Field(ge=0, le=100, strict=True)
    hardship: int = Field(ge=0, le=100, strict=True)
    skills: int = Field(ge=0, le=100, strict=True)
    location: int = Field(ge=0, le=100, strict=True)

    @model_validator(mode="after")
    def total(self):
        if sum(self.model_dump().values()) != 100:
            raise ValueError("四项权重之和必须为 100")
        return self


class Question(StrictModel):
    question: str = Field(min_length=2, max_length=500)
    conversation_id: str = Field(default="policy-query", max_length=64)


class Section(StrictModel):
    location: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=5, max_length=10000)


class PolicyInput(StrictModel):
    title: str = Field(min_length=2, max_length=200)
    publisher: str = Field(min_length=2, max_length=100)
    source_url: HttpUrl
    version: str = Field(min_length=1, max_length=100)
    verified_at: datetime
    verification_note: str = Field(min_length=5, max_length=2000)
    sections: list[Section] = Field(min_length=1, max_length=200)
    is_school_policy: bool = False
