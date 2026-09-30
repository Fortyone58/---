from collections import defaultdict
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from .config import RULE_VERSION
from .models import Application, AuditLog, Job, JobSkill, SystemSetting, Unit, User, WorkHour

OCCUPYING = ("approved", "onboard", "finished")
ZERO = Decimal("0")
HUNDRED = Decimal("100")


def now():
    return datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)


def number(value):
    return str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def stamp(value):
    return value.isoformat() if value else None


def audit(db, user, action, kind, resource_id, before=None, after=None):
    db.add(AuditLog(actor_id=user.id if user else None, action=action, resource_type=kind,
                    resource_id=resource_id, before=before, after=after, created_at=now()))


def get_or_404(db, model, identity, lock=False):
    stmt = select(model).where(model.id == identity)
    row = db.scalar(stmt.with_for_update().execution_options(populate_existing=True) if lock else stmt)
    if row is None:
        raise HTTPException(404, "记录不存在")
    return row


def visible_job(db, user, job):
    if user.role == "unit" and job.unit_id != user.unit_id:
        raise HTTPException(403, "只能访问本单位岗位")
    if user.role == "student" and job.status == "draft":
        raise HTTPException(403, "该岗位尚未发布")


def visible_application(db, user, app):
    if user.role == "student" and app.student_id != user.id:
        raise HTTPException(403, "只能访问本人的申请")
    job = get_or_404(db, Job, app.job_id)
    if user.role == "unit" and job.unit_id != user.unit_id:
        raise HTTPException(403, "只能访问本单位申请")
    return job


def user_data(db, user):
    unit = db.get(Unit, user.unit_id) if user.unit_id else None
    return {"id": user.id, "username": user.username, "display_name": user.display_name,
            "role": user.role, "status": user.status, "unit_id": user.unit_id,
            "unit_name": unit.name if unit else None, "hardship": user.hardship,
            "hardship_confirmed": user.hardship_confirmed, "skills": user.skills,
            "slots": user.slots, "area": user.area, "major": user.major}


def occupied(db, job_id):
    db.flush()
    return db.scalar(select(func.count()).select_from(Application).where(
        Application.job_id == job_id, Application.status.in_(OCCUPYING)))


def job_data(db, job, student_id=None):
    unit = db.get(Unit, job.unit_id)
    skills = list(db.scalars(select(JobSkill.skill).where(JobSkill.job_id == job.id).order_by(JobSkill.skill)))
    count = occupied(db, job.id)
    result = {"id": job.id, "unit_id": job.unit_id, "unit_name": unit.name,
              "title": job.title, "description": job.description, "location": job.location,
              "area": job.area, "category": job.category, "wage": number(job.wage),
              "quota": job.quota, "occupied": count, "remaining": max(0, job.quota-count),
              "slots": job.slots, "skills": skills, "status": job.status,
              "close_reason": job.close_reason, "published_at": stamp(job.published_at)}
    if student_id:
        apps = list(db.scalars(select(Application).where(Application.job_id == job.id,
                                                         Application.student_id == student_id)
                               .order_by(Application.application_no)))
        result["my_status"] = apps[-1].status if apps else None
        allowed = not apps or (len(apps) == 1 and apps[0].status == "withdrawn")
        result["can_apply"] = allowed and job.status == "published" and count < job.quota
        result["apply_blocked"] = "" if result["can_apply"] else (
            "此岗位已申请；仅首次撤销可再申请一次" if not allowed else "此岗位当前暂停招聘")
    return result


def freeze_salary(job):
    return {"category": job.category, "wage": number(job.wage), "fixed_basis_hours": "40",
            "rule_version": RULE_VERSION, "frozen_at": stamp(now())}


def sync_quota(db, job):
    count = occupied(db, job.id)
    if job.status == "published" and count >= job.quota:
        job.status, job.close_reason = "closed", "full"
    elif job.status == "closed" and job.close_reason == "full" and count < job.quota:
        job.status, job.close_reason = "published", None


def application_data(db, app):
    job, student = db.get(Job, app.job_id), db.get(User, app.student_id)
    unit = db.get(Unit, job.unit_id)
    return {"id": app.id, "job_id": app.job_id, "student_id": app.student_id,
            "student_name": student.display_name, "student_major": student.major,
            "job_title": app.job_snapshot.get("title", job.title), "unit_name": unit.name,
            "status": app.status, "application_no": app.application_no,
            "reason": app.reason, "review_note": app.review_note,
            "job_snapshot": app.job_snapshot, "salary_snapshot": app.salary_snapshot,
            "created_at": stamp(app.created_at), "approved_at": stamp(app.approved_at),
            "onboard_at": stamp(app.onboard_at), "finished_at": stamp(app.finished_at),
            "withdrawn_at": stamp(app.withdrawn_at),
            "blocked_reason": "岗位已满额，暂不能批准" if job.close_reason == "full" else (
                "单位已主动关闭岗位，暂不能批准" if job.status != "published" else "")}


def scoped_applications(user):
    stmt = select(Application).join(Job, Application.job_id == Job.id)
    if user.role == "student":
        stmt = stmt.where(Application.student_id == user.id)
    elif user.role == "unit":
        stmt = stmt.where(Job.unit_id == user.unit_id)
    return stmt


def tails_statement():
    successor = aliased(WorkHour)
    return select(WorkHour).outerjoin(successor, successor.previous_id == WorkHour.id).where(
        successor.id.is_(None))


def recompute_hours(db, student_id):
    db.flush()
    # MySQL DATETIME(0) rounds fractional seconds. Reload the new row too so
    # cached microseconds cannot reorder it ahead of already persisted tails.
    rows = list(db.scalars(tails_statement().where(WorkHour.student_id == student_id)
                           .execution_options(populate_existing=True)))
    rows.sort(key=lambda row: (row.work_date, row.root_created_at, row.root_id))
    weeks, months = defaultdict(Decimal), defaultdict(Decimal)
    changes = []
    for row in rows:
        week = row.work_date.isocalendar()[:2]
        month = (row.work_date.year, row.work_date.month)
        weeks[week] += row.hours
        months[month] += row.hours
        abnormal = row.hours > 0 and (weeks[week] > Decimal("8") or months[month] > Decimal("40"))
        old = row.is_abnormal
        if abnormal and not old:
            row.verified_by, row.verified_at, row.verification_note = None, None, None
        row.is_abnormal = abnormal
        if old != abnormal:
            changes.append({"record_id": row.id, "before": old, "after": abnormal})
    return changes


def hours_data(db, row):
    app = db.get(Application, row.application_id)
    job, student = db.get(Job, app.job_id), db.get(User, row.student_id)
    state = "verified" if row.is_abnormal and row.verified_by else (
        "pending" if row.is_abnormal else "normal")
    return {"id": row.id, "application_id": row.application_id, "student_id": row.student_id,
            "student_name": student.display_name, "job_title": app.job_snapshot.get("title", job.title),
            "unit_name": db.get(Unit, job.unit_id).name, "work_date": str(row.work_date),
            "hours": number(row.hours), "state": state, "is_abnormal": row.is_abnormal,
            "previous_id": row.previous_id, "root_id": row.root_id, "reason": row.reason,
            "created_at": stamp(row.created_at), "operator_name": db.get(User, row.operator_id).display_name,
            "verification_note": row.verification_note, "verified_at": stamp(row.verified_at)}


def check_work_date(app, work_date):
    if not app.onboard_at or work_date < app.onboard_at.date():
        raise HTTPException(409, "工时日期不得早于上岗日期")
    if work_date > now().date():
        raise HTTPException(400, "不能登记未来日期的工时")
    if app.finished_at and work_date > app.finished_at.date():
        raise HTTPException(409, "工时日期不得晚于结束日期")


def payroll(db, user, month):
    apps = list(db.scalars(scoped_applications(user)))
    app_map = {app.id: app for app in apps}
    rows = list(db.scalars(tails_statement().where(WorkHour.application_id.in_(app_map)))) if apps else []
    groups = {}
    for row in rows:
        if row.work_date.strftime("%Y-%m") != month:
            continue
        group = groups.setdefault(row.application_id, {"normal": ZERO, "pending": ZERO})
        key = "pending" if row.is_abnormal and not row.verified_by else "normal"
        group[key] += row.hours
    total_normal = total_pending = total_hours = pending_hours = ZERO
    result = []
    for app_id, hours in groups.items():
        app = app_map[app_id]
        frozen = app.salary_snapshot
        if not frozen:
            raise RuntimeError("工时缺少批准时计薪快照")
        rate = Decimal(frozen["wage"])
        basis = Decimal(frozen["fixed_basis_hours"])

        def amount(h):
            return h * rate if frozen["category"] == "temporary" else min(h, basis) / basis * rate

        normal = amount(hours["normal"])
        pending = amount(hours["normal"] + hours["pending"]) - normal
        total_normal += normal
        total_pending += pending
        total_hours += hours["normal"]
        pending_hours += hours["pending"]
        result.append({"application_id": app_id, "job_title": app.job_snapshot["title"],
                       "student_name": db.get(User, app.student_id).display_name,
                       "normal_hours": number(hours["normal"]), "pending_hours": number(hours["pending"]),
                       "normal_amount": number(normal), "pending_amount": number(pending),
                       "salary_snapshot": frozen})
    return {"month": month, "normal_hours": number(total_hours), "pending_hours": number(pending_hours),
            "normal_amount": number(total_normal), "pending_amount": number(total_pending),
            "groups": result, "notice": "应结算估算值，不代表实际发薪"}


def merged_slots(slots):
    days = defaultdict(list)
    for slot in slots or []:
        def minutes(t):
            hour, minute = map(int, t.split(":"))
            return hour * 60 + minute
        days[slot["day"]].append((minutes(slot["start"]), minutes(slot["end"])))
    merged = {}
    for day, intervals in days.items():
        items = []
        for start, end in sorted(intervals):
            if items and start <= items[-1][1]:
                items[-1] = (items[-1][0], max(items[-1][1], end))
            else:
                items.append((start, end))
        merged[day] = items
    return merged


def match_score(student, job, skills, weights):
    available, required = merged_slots(student.slots), merged_slots(job.slots)
    minutes = sum(end-start for intervals in required.values() for start, end in intervals)
    covered = sum(max(0, min(e1, e2)-max(s1, s2)) for day, intervals in required.items()
                  for s1, e1 in intervals for s2, e2 in available.get(day, []))
    missing = []
    time = HUNDRED * Decimal(covered) / Decimal(minutes) if student.slots and minutes else None
    if time is None:
        missing.append("可用时段待填写")
    hardship = {"D1": ZERO, "D2": Decimal("50"), "D3": HUNDRED}.get(student.hardship) \
        if student.hardship_confirmed else None
    if hardship is None:
        missing.append("困难等级待资助中心确认")
    hit = sorted(set(skills) & set(student.skills or []))
    skill = (HUNDRED * Decimal(len(hit)) / Decimal(len(skills)) if skills else HUNDRED) \
        if student.skills is not None else None
    if skill is None:
        missing.append("技能档案待填写")
    location = None
    location_note = "常用区域待填写"
    if student.area in {"A", "B", "C"}:
        distance = abs(ord(student.area)-ord(job.area))
        location = [HUNDRED, Decimal("70"), Decimal("40")][distance]
        location_note = ["同一校区区域", "相邻校区区域", "跨两个校区区域"][distance]
    else:
        missing.append(location_note)
    parts = {"time": time, "hardship": hardship, "skills": skill, "location": location}
    total = sum(Decimal(weights[key]) * value for key, value in parts.items()) / HUNDRED if not missing else None
    return {"total": number(total) if total is not None else None,
            "parts": {key: number(value) if value is not None else None for key, value in parts.items()},
            "hit_skills": hit, "covered_minutes": covered, "required_minutes": minutes,
            "location_note": location_note, "missing": missing, "_raw_total": total, "_raw_time": time}


def matching(db: Session, student: User):
    setting = db.scalar(select(SystemSetting).where(SystemSetting.key == "matching_weights"))
    items = []
    for job in db.scalars(select(Job).where(Job.status == "published")):
        data = job_data(db, job, student.id)
        if data["remaining"] <= 0 or not data["can_apply"]:
            continue
        score = match_score(student, job, data["skills"], setting.value)
        items.append({"job": data, **score, "parameter_version": setting.version})
    items.sort(key=lambda x: (x["_raw_total"] is None, -(x["_raw_total"] or ZERO),
                             -(x["_raw_time"] or ZERO),
                             -datetime.fromisoformat(x["job"]["published_at"])
                             .replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp(), x["job"]["id"]))
    for item in items:
        item.pop("_raw_total")
        item.pop("_raw_time")
    return {"items": items, "weights": setting.value, "version": setting.version,
            "notice": "规则匹配；困难代码与分值仅为演示约定"}
