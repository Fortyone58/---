import re
from collections import Counter
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import config, schemas as s
from .assistant import assistant_history, assistant_reply
from .agent import agent_conversations, agent_history, agent_reply
from .agent_schemas import AISettingsInput, AgentQuestion
from .llm import agent_status, public_settings, save_settings, test_connection
from .database import Base, engine, get_db
from .models import Application, AuditLog, Job, JobSkill, PolicyDoc, SystemSetting, Unit, User, WorkHour
from .policy import policy_conversations, policy_document_data, policy_history, query_policy as answer_policy_query
from .security import current_user, hash_password, make_token, require, verify_password
from .services import (
    application_data, audit, check_work_date, freeze_salary, get_or_404, hours_data, job_data,
    matching, now, occupied, payroll, recompute_hours, scoped_applications, stamp, sync_quota,
    tails_statement, user_data, visible_application, visible_job,
)


@asynccontextmanager
async def lifespan(_app):
    if len(config.JWT_SECRET) < 32:
        raise RuntimeError("请先从 backend 运行 uv run python -m app.seed 初始化本地环境")
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="青禾 · 校园勤工助学", version=config.VERSION, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
                   allow_credentials=False, allow_methods=["*"], allow_headers=["Authorization", "Content-Type"])


@app.exception_handler(HTTPException)
async def http_error(_request, error):
    names = {400: "invalid_request", 401: "unauthenticated", 403: "forbidden", 404: "not_found",
             409: "conflict", 503: "unavailable"}
    return JSONResponse(status_code=error.status_code,
                        content={"error": names.get(error.status_code, "error"), "message": error.detail})


@app.exception_handler(RequestValidationError)
async def validation_error(_request, error):
    details = [{"field": ".".join(map(str, e["loc"][1:])), "message": e["msg"]} for e in error.errors()]
    return JSONResponse(status_code=400, content={"error": "invalid_request", "message": "请检查填写内容",
                                                 "details": details})


@app.exception_handler(IntegrityError)
async def integrity_error(_request, _error):
    return JSONResponse(status_code=409, content={"error": "conflict", "message": "记录已存在或已被更新，请刷新后重试"})


@app.get("/api/ping")
def ping():
    return {"status": "ok", "project": "qinghe-sol", "version": config.VERSION,
            "database": engine.dialect.name}


@app.get("/api/auth/demo-accounts")
def demo_accounts(db: Session = Depends(get_db)):
    if config.APP_ENV != "demo":
        raise HTTPException(404, "当前环境没有演示账号")
    names = ["student", "library", "aid", "admin_demo", "lab", "student2", "student3",
             "student4", "student5", "student6", "pending"]
    return [{"username": u.username, "display_name": u.display_name, "role": u.role, "status": u.status}
            for u in db.scalars(select(User).where(User.username.in_(names)))]


@app.post("/api/auth/register", status_code=201)
def register(data: s.Register, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.username == data.username)):
        raise HTTPException(409, "此账号已注册")
    user = User(username=data.username, password_hash=hash_password(data.password), display_name=data.display_name,
                role="student", status="pending_activation", created_at=now())
    db.add(user)
    db.flush()
    audit(db, user, "student.register", "users", user.id, after={"status": user.status})
    return {"message": "注册成功，请等待系统管理员启用后登录", "status": user.status}


@app.post("/api/auth/login")
def login(data: s.Login, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == data.username))
    if user is None or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "账号或密码不正确")
    if user.status != "active":
        raise HTTPException(403, "此学生账号待系统管理员启用")
    return {"access_token": make_token(user), "token_type": "bearer", "user": user_data(db, user)}


@app.get("/api/auth/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return user_data(db, user)


@app.get("/api/meta")
def meta(_user: User = Depends(current_user)):
    ai = agent_status()
    return {"skills": config.SKILLS, "roles": config.ROLE_NAMES, "rule_version": config.RULE_VERSION,
            "areas": {"A": "A 区 · 教学与图书馆", "B": "B 区 · 行政与生活", "C": "C 区 · 实验与活动"},
            "demo_month": "2026-09", "weekly_limit": 8, "monthly_limit": 40,
            "version": config.VERSION, "capabilities": {"policy": "original_query",
            "job_assistant": "rule_assistant", "ai_agent": ai["mode"],
            "external_model_connected": ai["enabled"] and ai["connection_verified"]}}


@app.get("/api/profiles/me")
def profile(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return user_data(db, user)


@app.put("/api/profiles/me")
def update_profile(data: s.Profile, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "student")
    before = user_data(db, user)
    for key, value in data.model_dump(mode="json").items():
        setattr(user, key, value)
    audit(db, user, "profile.update", "users", user.id, before, user_data(db, user))
    return user_data(db, user)


@app.get("/api/jobs")
def jobs(keyword: str = "", area: str = "", category: str = "", status: str = "",
         user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = select(Job)
    if user.role == "unit":
        stmt = stmt.where(Job.unit_id == user.unit_id)
    if user.role == "student":
        stmt = stmt.where(Job.status.in_(("published", "closed")))
    if keyword:
        stmt = stmt.where(Job.title.contains(keyword, autoescape=True))
    if area:
        stmt = stmt.where(Job.area == area)
    if category:
        stmt = stmt.where(Job.category == category)
    if status:
        stmt = stmt.where(Job.status == status)
    items = [job_data(db, j, user.id if user.role == "student" else None)
             for j in db.scalars(stmt.order_by(Job.published_at.desc(), Job.id))]
    return {"items": items, "total": len(items)}


@app.get("/api/jobs/{identity}")
def job_detail(identity: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = get_or_404(db, Job, identity)
    visible_job(db, user, job)
    return job_data(db, job, user.id if user.role == "student" else None)


def set_skills(db, job, skills):
    for row in db.scalars(select(JobSkill).where(JobSkill.job_id == job.id)):
        db.delete(row)
    db.flush()
    db.add_all([JobSkill(job_id=job.id, skill=skill) for skill in skills])


@app.post("/api/jobs", status_code=201)
def create_job(data: s.JobInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "unit")
    values = data.model_dump(mode="python", exclude={"skills"})
    values["slots"] = [slot.model_dump() for slot in data.slots]
    job = Job(**values, unit_id=user.unit_id, status="draft", created_at=now())
    db.add(job)
    db.flush()
    set_skills(db, job, data.skills)
    result = job_data(db, job)
    audit(db, user, "job.create", "job", job.id, after=result)
    return result


@app.put("/api/jobs/{identity}")
def edit_job(identity: int, data: s.JobInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "unit")
    job = get_or_404(db, Job, identity, lock=True)
    visible_job(db, user, job)
    before = job_data(db, job)
    if data.quota < occupied(db, job.id):
        raise HTTPException(409, "名额不得少于当前已录用人数（含已结束）")
    if not data.slots and (job.status == "published" or
                           (job.status == "closed" and job.close_reason == "full")):
        raise HTTPException(400, "招聘中或满额自动关闭的岗位须保留有效工作时段")
    for key, value in data.model_dump(exclude={"skills"}).items():
        setattr(job, key, value)
    set_skills(db, job, data.skills)
    sync_quota(db, job)
    result = job_data(db, job)
    audit(db, user, "job.update", "job", job.id, before, result)
    return result


@app.post("/api/jobs/{identity}/actions")
def job_action(identity: int, data: s.Action, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "unit")
    job = get_or_404(db, Job, identity, lock=True)
    visible_job(db, user, job)
    before = job_data(db, job)
    if data.action == "publish":
        if job.status == "published":
            raise HTTPException(409, "此岗位已发布")
        if not job.slots:
            raise HTTPException(400, "发布前请添加至少一个有效时段")
        if occupied(db, job.id) >= job.quota:
            raise HTTPException(409, "岗位已满额，不能发布")
        job.status, job.close_reason = "published", None
        job.published_at = now()
    elif data.action == "close":
        if job.status == "draft" or (job.status == "closed" and job.close_reason == "manual"):
            raise HTTPException(409, "当前状态不能再次关闭")
        job.status, job.close_reason = "closed", "manual"
    else:
        raise HTTPException(400, "未知岗位动作")
    result = job_data(db, job)
    audit(db, user, f"job.{data.action}", "job", job.id, before, result)
    return result


@app.delete("/api/jobs/{identity}")
def delete_job(identity: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "unit")
    job = get_or_404(db, Job, identity, lock=True)
    visible_job(db, user, job)
    if job.status != "draft" or db.scalar(select(Application.id).where(Application.job_id == identity).limit(1)):
        raise HTTPException(409, "只可删除无申请关联的草稿，其他岗位请关闭")
    before = job_data(db, job)
    for row in db.scalars(select(JobSkill).where(JobSkill.job_id == identity)):
        db.delete(row)
    db.delete(job)
    audit(db, user, "job.delete", "job", identity, before)
    return {"message": "草稿已删除"}


@app.get("/api/applications")
def applications(status: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = scoped_applications(user)
    if status:
        stmt = stmt.where(Application.status == status)
    items = [application_data(db, a) for a in db.scalars(stmt.order_by(Application.created_at.desc(), Application.id.desc()))]
    return {"items": items, "total": len(items)}


@app.post("/api/applications", status_code=201)
def create_application(data: s.ApplyInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "student")
    job = get_or_404(db, Job, data.job_id, lock=True)
    if job.status != "published" or occupied(db, job.id) >= job.quota:
        raise HTTPException(409, "此岗位当前没有可申请名额")
    old = list(db.scalars(select(Application).where(Application.student_id == user.id,
                                                   Application.job_id == job.id).order_by(Application.application_no)))
    if old and (len(old) != 1 or old[0].status != "withdrawn"):
        raise HTTPException(409, "仅首次撤销后可再次申请一次；驳回、上岗或终态不可重申")
    record = Application(student_id=user.id, job_id=job.id, application_no=len(old)+1,
                         reason=data.reason, status="pending_review", job_snapshot=job_data(db, job), created_at=now())
    db.add(record)
    db.flush()
    result = application_data(db, record)
    audit(db, user, "application.create", "application", record.id, after=result)
    return result


@app.post("/api/applications/{identity}/actions")
def application_action(identity: int, data: s.Action, user: User = Depends(current_user), db: Session = Depends(get_db)):
    probe = get_or_404(db, Application, identity)
    job = get_or_404(db, Job, probe.job_id, lock=True)
    record = get_or_404(db, Application, identity, lock=True)
    visible_application(db, user, record)
    transitions = {"approve": ("pending_review", "approved"), "reject": ("pending_review", "rejected"),
                   "onboard": ("approved", "onboard"), "finish": ("onboard", "finished")}
    if data.action == "withdraw":
        require(user, "student")
        if record.student_id != user.id or record.status not in {"pending_review", "approved"}:
            raise HTTPException(409, "只有本人待审核或已批准申请可撤销")
        target = "withdrawn"
    else:
        require(user, "unit")
        if data.action not in transitions:
            raise HTTPException(400, "未知申请动作")
        origin, target = transitions[data.action]
        if record.status != origin:
            raise HTTPException(409, "当前申请状态不能执行此动作，请刷新")
        if data.action == "approve" and (job.status != "published" or occupied(db, job.id) >= job.quota):
            raise HTTPException(409, "岗位暂停招聘或已满额，不能批准")
        if data.action == "reject" and len(data.note) < 3:
            raise HTTPException(400, "驳回时请填写至少三字说明")
    before, job_before = application_data(db, record), job_data(db, job)
    record.status = target
    if target == "approved":
        record.approved_at, record.salary_snapshot = now(), freeze_salary(job)
    elif target == "onboard":
        record.onboard_at = now()
    elif target == "finished":
        record.finished_at = now()
    elif target == "withdrawn":
        record.withdrawn_at = now()
    if data.action != "withdraw":
        record.review_note = data.note or record.review_note
    sync_quota(db, job)
    result = application_data(db, record)
    audit(db, user, f"application.{data.action}", "application", identity, before, result)
    if (job_before["status"], job_before["close_reason"]) != (job.status, job.close_reason):
        audit(db, user, "job.quota_sync", "job", job.id, job_before, job_data(db, job))
    return result


def month_value(month):
    month = month or now().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(400, "月份格式应为 YYYY-MM")
    return month


@app.get("/api/workhours")
def workhours(month: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    month = month_value(month)
    app_ids = list(db.scalars(scoped_applications(user).with_only_columns(Application.id)))
    rows = list(db.scalars(tails_statement().where(WorkHour.application_id.in_(app_ids))
                          .order_by(WorkHour.work_date.desc(), WorkHour.id.desc()))) if app_ids else []
    return {"items": [hours_data(db, row) for row in rows if row.work_date.strftime("%Y-%m") == month],
            "month": month, "summary": payroll(db, user, month)}


@app.post("/api/workhours", status_code=201)
def add_hours(data: s.HoursInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "unit")
    record = get_or_404(db, Application, data.application_id)
    visible_application(db, user, record)
    get_or_404(db, User, record.student_id, lock=True)
    record = get_or_404(db, Application, record.id, lock=True)
    if record.status != "onboard":
        raise HTTPException(409, "只有已上岗申请可登记新工时")
    check_work_date(record, data.work_date)
    row = WorkHour(application_id=record.id, student_id=record.student_id, work_date=data.work_date,
                   hours=data.hours, reason=data.reason, operator_id=user.id, created_at=now(), root_created_at=now())
    db.add(row)
    db.flush()
    row.root_id = row.id
    changes = recompute_hours(db, row.student_id)
    result = hours_data(db, row)
    audit(db, user, "workhour.create", "work_hour", row.id, after={**result, "recomputed": changes})
    return result


@app.post("/api/workhours/{identity}/corrections", status_code=201)
def correct_hours(identity: int, data: s.Correction, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "unit")
    previous = get_or_404(db, WorkHour, identity)
    record = get_or_404(db, Application, previous.application_id)
    visible_application(db, user, record)
    get_or_404(db, User, previous.student_id, lock=True)
    previous = get_or_404(db, WorkHour, identity, lock=True)
    if db.scalar(select(WorkHour.id).where(WorkHour.previous_id == previous.id)):
        raise HTTPException(409, "这条记录已被更正，请选择当前链尾")
    if record.status not in {"onboard", "finished"}:
        raise HTTPException(409, "当前申请状态不能更正工时")
    check_work_date(record, data.work_date)
    before = hours_data(db, previous)
    row = WorkHour(application_id=record.id, student_id=previous.student_id, work_date=data.work_date,
                   hours=data.hours, reason=data.reason, operator_id=user.id, created_at=now(),
                   previous_id=identity, root_id=previous.root_id, root_created_at=previous.root_created_at)
    db.add(row)
    db.flush()
    changes = recompute_hours(db, row.student_id)
    result = hours_data(db, row)
    audit(db, user, "workhour.correct", "work_hour", row.id, before, {**result, "recomputed": changes})
    return result


@app.post("/api/workhours/{identity}/verify")
def verify_hours(identity: int, data: s.Verification, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "aid")
    row = get_or_404(db, WorkHour, identity)
    get_or_404(db, User, row.student_id, lock=True)
    row = get_or_404(db, WorkHour, identity, lock=True)
    if db.scalar(select(WorkHour.id).where(WorkHour.previous_id == identity)):
        raise HTTPException(409, "仅当前链尾可核实")
    if not row.is_abnormal or row.verified_by:
        raise HTTPException(409, "这条工时无需再次核实")
    before = hours_data(db, row)
    row.verified_by, row.verified_at, row.verification_note = user.id, now(), data.note
    result = hours_data(db, row)
    audit(db, user, "workhour.verify", "work_hour", identity, before, result)
    return result


@app.get("/api/workhours/{identity}/history")
def hours_history(identity: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = get_or_404(db, WorkHour, identity)
    visible_application(db, user, get_or_404(db, Application, row.application_id))
    rows = list(db.scalars(select(WorkHour).where(WorkHour.root_id == row.root_id).order_by(WorkHour.id)))
    return {"items": [hours_data(db, record) for record in rows]}


@app.get("/api/payroll/summary")
def payroll_summary(month: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    return payroll(db, user, month_value(month))


@app.get("/api/matching")
def matching_list(user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "student")
    return matching(db, user)


@app.get("/api/stats")
def stats(month: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    month = month_value(month)
    job_stmt = select(Job)
    if user.role == "unit":
        job_stmt = job_stmt.where(Job.unit_id == user.unit_id)
    if user.role == "student":
        job_stmt = job_stmt.where(Job.status != "draft")
    job_rows = list(db.scalars(job_stmt))
    applications = list(db.scalars(scoped_applications(user)))
    students = list(db.scalars(select(User).where(User.role == "student"))) if user.role in {"aid", "admin"} else []
    return {"month": month, "jobs": dict(Counter(j.status for j in job_rows)),
            "job_total": len(job_rows), "available_jobs": sum(j.status == "published" for j in job_rows),
            "applications": dict(Counter(a.status for a in applications)), "application_total": len(applications),
            "payroll": payroll(db, user, month),
            "pending_activation": sum(u.status == "pending_activation" for u in students),
            "pending_hardship": sum(not u.hardship_confirmed for u in students if u.status == "active"),
            "student_total": len(students), "scope": "本人" if user.role == "student" else (
                "本单位" if user.role == "unit" else "全局"),
            "notice": "岗位/申请为当前状态；工时/薪酬为所选自然月"}


@app.get("/api/admin/users")
def users(user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "admin", "aid")
    return {"items": [user_data(db, u) for u in db.scalars(select(User).order_by(User.id))]}


@app.post("/api/admin/users", status_code=201)
def create_user(data: s.CreateUser, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "admin")
    if data.unit_id:
        get_or_404(db, Unit, data.unit_id)
    if db.scalar(select(User).where(User.username == data.username)):
        raise HTTPException(409, "账号已存在")
    row = User(username=data.username, password_hash=hash_password(data.password), display_name=data.display_name,
               role=data.role, unit_id=data.unit_id, status="active", created_at=now())
    db.add(row)
    db.flush()
    result = user_data(db, row)
    audit(db, user, "user.create", "users", row.id, after=result)
    return result


@app.post("/api/admin/users/{identity}/activate")
def activate(identity: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "admin")
    row = get_or_404(db, User, identity, lock=True)
    if row.role != "student" or row.status != "pending_activation":
        raise HTTPException(409, "仅待启用学生可执行启用")
    before = user_data(db, row)
    row.status = "active"
    result = user_data(db, row)
    audit(db, user, "student.activate", "users", identity, before, result)
    return result


@app.post("/api/admin/users/{identity}/hardship")
def hardship(identity: int, data: s.Hardship, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "aid")
    row = get_or_404(db, User, identity, lock=True)
    if row.role != "student":
        raise HTTPException(400, "仅可确认学生困难演示等级")
    before = user_data(db, row)
    row.hardship, row.hardship_confirmed = data.hardship, True
    result = user_data(db, row)
    audit(db, user, "student.hardship_confirm", "users", identity, before, {**result, "note": data.note})
    return result


@app.get("/api/admin/units")
def units(user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "admin", "aid")
    return {"items": [{"id": u.id, "name": u.name, "area": u.area, "description": u.description}
                      for u in db.scalars(select(Unit).order_by(Unit.id))]}


@app.post("/api/admin/units", status_code=201)
def add_unit(data: s.UnitInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "admin")
    row = Unit(**data.model_dump())
    db.add(row)
    db.flush()
    result = {"id": row.id, **data.model_dump()}
    audit(db, user, "unit.create", "unit", row.id, after=result)
    return result


@app.get("/api/admin/settings")
def settings(user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "admin")
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == "matching_weights"))
    return {"weights": row.value, "version": row.version, "modified_at": stamp(row.modified_at),
            "rule_version": config.RULE_VERSION}


@app.put("/api/admin/settings/matching")
def update_weights(data: s.Weights, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "admin")
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == "matching_weights").with_for_update())
    before = {"weights": row.value, "version": row.version}
    row.value, row.version, row.modified_by, row.modified_at = data.model_dump(), row.version+1, user.id, now()
    result = {"weights": row.value, "version": row.version, "modified_at": stamp(row.modified_at)}
    audit(db, user, "settings.matching_update", "system_setting", row.id, before, result)
    return result


@app.get("/api/admin/audit")
def audit_logs(limit: int = Query(default=100, ge=1, le=500), user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "admin")
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))
    return {"items": [{"id": a.id, "actor": db.get(User, a.actor_id).display_name if a.actor_id else "初始化",
                       "action": a.action, "resource_type": a.resource_type, "resource_id": a.resource_id,
                       "before": a.before, "after": a.after, "created_at": stamp(a.created_at)} for a in rows]}


def policy_data(doc, include_sections=False):
    return policy_document_data(doc, include_sections)


@app.get("/api/policies")
def policy_docs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = select(PolicyDoc)
    if user.role != "aid":
        stmt = stmt.where(PolicyDoc.verified.is_(True))
    return {"items": [policy_data(d) for d in db.scalars(stmt)]}


@app.get("/api/policies/{identity}")
def policy_detail(identity: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    doc = get_or_404(db, PolicyDoc, identity)
    if not doc.verified and user.role != "aid":
        raise HTTPException(403, "该原文尚未核验")
    return policy_data(doc, True)


@app.post("/api/policies", status_code=201)
def import_policy(data: s.PolicyInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "aid")
    verified_at = data.verified_at
    if verified_at.tzinfo is not None:
        verified_at = verified_at.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    if verified_at > now():
        raise HTTPException(400, "核验时间不能在未来")
    values = data.model_dump(mode="python")
    values["source_url"] = str(values["source_url"])
    values["verified_at"] = verified_at
    if values.get("expires_at") and values["expires_at"].tzinfo is not None:
        values["expires_at"] = values["expires_at"].astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    doc = PolicyDoc(**values, verified=True, imported_at=now())
    db.add(doc)
    db.flush()
    result = policy_data(doc)
    audit(db, user, "policy.import_verified", "policy_doc", doc.id, after=result)
    return result


@app.post("/api/qa")
def query_policy(data: s.Question, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return answer_policy_query(db, user, data)


@app.post("/api/assistant/messages")
def assistant_messages(data: s.Question, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "student")
    return assistant_reply(db, user, data)


@app.get("/api/assistant/history")
def assistant_query_history(conversation_id: str | None = Query(default=None, min_length=1, max_length=64),
                            user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "student")
    return assistant_history(db, user, conversation_id)


@app.get("/api/qa/history")
def query_history(conversation_id: str | None = Query(default=None, min_length=1, max_length=64),
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    return policy_history(db, user, conversation_id)


@app.get("/api/qa/conversations")
def query_conversations(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return policy_conversations(db, user)


@app.get("/api/agent/status")
def ai_status(_user: User = Depends(current_user)):
    return agent_status()


@app.post("/api/agent/messages")
def ai_message(data: AgentQuestion, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return agent_reply(db, user, data)


@app.get("/api/agent/history")
def ai_history(conversation_id: str | None = Query(default=None, min_length=1, max_length=64),
               user: User = Depends(current_user), db: Session = Depends(get_db)):
    return agent_history(db, user, conversation_id)


@app.get("/api/agent/conversations")
def ai_conversations(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return agent_conversations(db, user)


@app.get("/api/admin/ai/settings")
def ai_settings(user: User = Depends(current_user)):
    require(user, "admin")
    return public_settings()


@app.put("/api/admin/ai/settings")
def ai_settings_save(data: AISettingsInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "admin")
    result = save_settings(data)
    audit(db, user, "ai.settings_update", "ai_settings", None,
          after={key: result[key] for key in ("enabled", "provider", "base_url", "model", "has_api_key")})
    return result


@app.post("/api/admin/ai/test")
def ai_connection_test(user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "admin")
    db.commit()  # Never hold the auth/SQLite write transaction across a network request.
    result = test_connection()
    audit(db, user, "ai.connection_test", "ai_settings", None, after=result)
    return result
