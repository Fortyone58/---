from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base

PRECISE_DATETIME = DateTime().with_variant(DATETIME(fsp=6), "mysql")


class Unit(Base):
    __tablename__ = "unit"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    area: Mapped[str] = mapped_column(String(1), default="A")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str] = mapped_column(String(50))
    role: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), default="active")
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("unit.id"))
    hardship: Mapped[str | None] = mapped_column(String(2))
    hardship_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    skills: Mapped[list | None] = mapped_column(JSON, nullable=True)
    slots: Mapped[list | None] = mapped_column(JSON, nullable=True)
    area: Mapped[str | None] = mapped_column(String(1))
    major: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME)


class Job(Base):
    __tablename__ = "job"
    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("unit.id"), index=True)
    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text)
    location: Mapped[str] = mapped_column(String(100))
    area: Mapped[str] = mapped_column(String(1))
    category: Mapped[str] = mapped_column(String(16))
    wage: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    quota: Mapped[int] = mapped_column(Integer)
    slots: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    close_reason: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME)
    published_at: Mapped[datetime | None] = mapped_column(PRECISE_DATETIME)


class JobSkill(Base):
    __tablename__ = "job_skill"
    __table_args__ = (UniqueConstraint("job_id", "skill"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job.id", ondelete="CASCADE"), index=True)
    skill: Mapped[str] = mapped_column(String(32))


class Application(Base):
    __tablename__ = "application"
    __table_args__ = (UniqueConstraint("student_id", "job_id", "application_no"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job.id"), index=True)
    application_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="pending_review", index=True)
    reason: Mapped[str] = mapped_column(Text)
    review_note: Mapped[str] = mapped_column(Text, default="")
    job_snapshot: Mapped[dict] = mapped_column(JSON)
    salary_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME)
    approved_at: Mapped[datetime | None] = mapped_column(PRECISE_DATETIME)
    onboard_at: Mapped[datetime | None] = mapped_column(PRECISE_DATETIME)
    finished_at: Mapped[datetime | None] = mapped_column(PRECISE_DATETIME)
    withdrawn_at: Mapped[datetime | None] = mapped_column(PRECISE_DATETIME)


class WorkHour(Base):
    __tablename__ = "work_hour"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("application.id"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    work_date: Mapped[date] = mapped_column(Date)
    hours: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    previous_id: Mapped[int | None] = mapped_column(ForeignKey("work_hour.id"), unique=True)
    root_id: Mapped[int | None] = mapped_column(ForeignKey("work_hour.id"))
    root_created_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME)
    reason: Mapped[str] = mapped_column(Text, default="")
    operator_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME)
    is_abnormal: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    verified_at: Mapped[datetime | None] = mapped_column(PRECISE_DATETIME)
    verification_note: Mapped[str | None] = mapped_column(Text)


class SystemSetting(Base):
    __tablename__ = "system_setting"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True)
    value: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    modified_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    modified_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64))
    resource_type: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[int | None] = mapped_column(Integer)
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME, index=True)


class PolicyDoc(Base):
    __tablename__ = "policy_doc"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    publisher: Mapped[str] = mapped_column(String(100))
    source_url: Mapped[str] = mapped_column(String(500))
    version: Mapped[str] = mapped_column(String(100))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_at: Mapped[datetime | None] = mapped_column(PRECISE_DATETIME)
    verification_note: Mapped[str] = mapped_column(Text)
    imported_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME)
    sections: Mapped[list] = mapped_column(JSON)
    is_school_policy: Mapped[bool] = mapped_column(Boolean, default=False)
    # Authenticity and current applicability are separate facts. Old verified
    # originals remain queryable, while archived school notices are opt-in.
    source_key: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    usage_scope: Mapped[str] = mapped_column(String(32), default="general_policy",
                                            server_default="general_policy")
    current_answer_allowed: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    publication_date: Mapped[date | None] = mapped_column(Date)
    effective_from: Mapped[date | None] = mapped_column(Date)
    expires_at: Mapped[datetime | None] = mapped_column(PRECISE_DATETIME)
    applicability: Mapped[str | None] = mapped_column(Text, default="", nullable=True)
    source_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ChatLog(Base):
    __tablename__ = "chat_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(String(64))
    question: Mapped[str] = mapped_column(Text)
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME)
