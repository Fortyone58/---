import argparse
import json
import os
import secrets
import sys
from contextlib import closing
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from . import config
from .database import SessionLocal, engine
from .migrate import REVISION, upgrade_schema
from .models import Application, AuditLog, Job, JobSkill, PolicyDoc, SystemSetting, Unit, User, WorkHour
from .security import hash_password
from .services import freeze_salary, job_data, now, recompute_hours, sync_quota

SEED_VERSION = "qinghe-demo-2026-09-v1"
BASE_TIME = datetime(2026, 9, 1, 9)


def ensure_env():
    path = config.ROOT / ".env"
    if not path.exists():
        path.write_text(
            f"APP_ENV=demo\nDATABASE_URL=sqlite:///{(config.ROOT / 'data/campus_demo.db').as_posix()}\n"
            f"JWT_SECRET={secrets.token_hex(32)}\nTOKEN_HOURS=12\nBOOTSTRAP_USERNAME=owner\n"
            f"BOOTSTRAP_PASSWORD={secrets.token_urlsafe(20)}\n", encoding="utf-8")
        from dotenv import load_dotenv
        load_dotenv(path, override=True)
        config.JWT_SECRET = os.environ["JWT_SECRET"]
        print("Local configuration created. Bootstrap credentials are in .env; keep this file private.")


def seed_database(db, include_bootstrap=False):
    if db.scalar(select(User.id).where(User.username == "student")):
        return {"status": "unchanged", "version": SEED_VERSION}
    if include_bootstrap:
        owner = User(username=os.getenv("BOOTSTRAP_USERNAME", "owner"), display_name="本机初始化管理员",
                     password_hash=hash_password(os.environ["BOOTSTRAP_PASSWORD"]), role="admin",
                     status="active", created_at=BASE_TIME)
        db.add(owner)
    units = [Unit(name=name, area=area, description=description) for name, area, description in [
        ("图书馆", "A", "阅读服务、文献整理与读者支持"),
        ("数字校园实验室", "C", "实验室值班、数据整理与校园技术支持"),
        ("后勤服务中心", "B", "校园生活服务与公共空间维护"),
        ("大学生活动中心", "C", "活动组织、宣传设计与场馆服务"),
    ]]
    db.add_all(units)
    db.flush()
    shared_hash = hash_password(config.DEMO_PASSWORD)
    records = [
        ("student", "林同学", "student", None, "D2", ["office", "excel", "communication"], "A"),
        ("student2", "陈同学", "student", None, "D3", ["office", "excel"], "A"),
        ("student3", "周同学", "student", None, "D2", ["python", "excel"], "C"),
        ("student4", "许同学", "student", None, "D1", ["writing", "design"], "B"),
        ("student5", "吴同学", "student", None, "D3", ["communication"], "A"),
        ("student6", "郑同学", "student", None, None, [], "B"),
        ("pending", "新注册同学", "student", None, None, None, None),
        ("library", "图书馆 · 张老师", "unit", units[0].id, None, None, None),
        ("lab", "实验室 · 李老师", "unit", units[1].id, None, None, None),
        ("logistics", "后勤中心 · 王老师", "unit", units[2].id, None, None, None),
        ("activities", "活动中心 · 赵老师", "unit", units[3].id, None, None, None),
        ("aid", "资助中心 · 刘老师", "aid", None, None, None, None),
        ("admin_demo", "演示管理员", "admin", None, None, None, None),
    ]
    people = {}
    for username, name, role, unit_id, hardship, skills, area in records:
        row = User(username=username, display_name=name, role=role, unit_id=unit_id, hardship=hardship,
                   hardship_confirmed=hardship is not None, skills=skills, area=area,
                   slots=[{"day": 1, "start": "14:00", "end": "18:00"},
                          {"day": 3, "start": "14:00", "end": "18:00"},
                          {"day": 5, "start": "14:00", "end": "17:00"}] if role == "student" and area else None,
                   major="计算机科学与技术 · 2024级" if role == "student" else "",
                   password_hash=shared_hash, status="pending_activation" if username == "pending" else "active",
                   created_at=datetime(2026, 8, 27, 9))
        db.add(row)
        people[username] = row
    db.flush()
    specs = [
        (0, "阅览室服务助理", "temporary", "18", 3, ["office", "communication"], "图书馆一楼阅览室"),
        (0, "图书整理与编目", "fixed", "800", 2, ["office", "excel"], "图书馆二楼书库"),
        (0, "文献数字化助手", "temporary", "22", 2, ["office", "excel"], "图书馆数字资源室"),
        (0, "阅读活动宣传助理", "temporary", "20", 2, ["writing", "design"], "图书馆阅读推广室"),
        (0, "读者咨询与导览", "temporary", "18", 1, ["communication"], "图书馆服务台"),
        (0, "期刊上架助理", "fixed", "720", 2, [], "图书馆期刊阅览室"),
        (1, "实验室值班助手", "temporary", "25", 3, ["python", "office"], "计算机实验楼 302"),
        (1, "校园数据整理助理", "fixed", "1000", 2, ["excel", "python"], "数字校园工作室"),
        (1, "信息服务台助理", "temporary", "20", 2, ["communication", "office"], "信息服务大厅"),
        (1, "设备巡检记录员", "temporary", "22", 1, ["office"], "实验楼设备管理室"),
        (1, "技术文档整理助理", "temporary", "24", 2, ["writing", "python"], "实验楼 305"),
        (1, "开放实验室引导员", "fixed", "880", 2, ["communication"], "开放实验室"),
        (2, "校园公共空间巡查", "temporary", "18", 3, [], "行政楼服务点"),
        (2, "宿舍服务站助理", "fixed", "760", 2, ["communication"], "学生宿舍服务站"),
        (2, "后勤报修信息整理", "temporary", "20", 2, ["excel", "office"], "后勤服务大厅"),
        (2, "失物招领服务助理", "temporary", "18", 2, ["communication"], "生活区服务中心"),
        (2, "校园绿色行动助理", "temporary", "20", 3, ["organization"], "校园环保工作站"),
        (2, "餐厅意见收集助理", "fixed", "720", 2, [], "学生餐厅服务点"),
        (3, "校园活动执行助理", "temporary", "22", 4, ["organization", "communication"], "大学生活动中心"),
        (3, "校园摄影记录助理", "temporary", "28", 2, ["photography"], "活动中心摄影工作室"),
        (3, "新媒体内容编辑", "temporary", "25", 2, ["writing", "design"], "校园媒体中心"),
        (3, "场馆预约服务助理", "fixed", "800", 2, ["office"], "文体场馆服务台"),
        (3, "校园展览讲解助理", "temporary", "22", 3, ["communication"], "校园文化展厅"),
        (3, "志愿活动资料整理", "temporary", "20", 2, ["excel", "organization"], "志愿服务中心"),
    ]
    jobs = []
    for index, (unit_index, title, category, wage, quota, skills, location) in enumerate(specs):
        slots = [{"day": 1 if index % 3 != 2 else 2, "start": "14:00", "end": "16:00"},
                 {"day": 3 if index % 2 == 0 else 5, "start": "14:00", "end": "16:00"}]
        job = Job(unit_id=units[unit_index].id, title=title, category=category, wage=Decimal(wage), quota=quota,
                  location=location, area=units[unit_index].area, slots=slots,
                  description=f"参与{title}相关日常工作，在老师指导下完成任务。\n工作内容：服务支持、信息记录及指定事务协助。\n"
                              "岗位要求：认真负责，遵守工作安排，能在约定课余时段到岗。\n本岗位及工资均为原型演示数据。",
                  status="draft" if index == 5 else "published", created_at=datetime(2026, 8, 28, 9),
                  published_at=datetime(2026, 8, 28, 10, index) if index != 5 else None)
        db.add(job)
        db.flush()
        db.add_all([JobSkill(job_id=job.id, skill=skill) for skill in skills])
        jobs.append(job)
    db.flush()
    app_specs = [
        ("student", 0, "onboard"), ("student", 6, "onboard"), ("student2", 1, "onboard"),
        ("student3", 7, "onboard"), ("student4", 3, "pending_review"), ("student5", 4, "approved"),
        ("student6", 2, "rejected"), ("student4", 8, "withdrawn"), ("student2", 9, "finished"),
        ("student3", 10, "pending_review"), ("student6", 11, "pending_review"),
    ]
    apps = []
    for name, job_index, status in app_specs:
        job = jobs[job_index]
        row = Application(student_id=people[name].id, job_id=job.id, application_no=1, status=status,
                          reason="我希望利用课余时间参与校园服务，并积累实践经验。以上为模拟申请。",
                          review_note="模拟数据：已核对演示安排" if status in {"approved", "onboard", "finished"} else (
                              "模拟数据：时段不符合本轮岗位安排" if status == "rejected" else ""),
                          created_at=datetime(2026, 9, 1, 8), job_snapshot=job_data(db, job),
                          salary_snapshot=freeze_salary(job) if status in {"approved", "onboard", "finished"} else None,
                          approved_at=BASE_TIME if status in {"approved", "onboard", "finished"} else None,
                          onboard_at=BASE_TIME if status in {"onboard", "finished"} else None,
                          finished_at=datetime(2026, 9, 24, 17) if status == "finished" else None,
                          withdrawn_at=datetime(2026, 9, 2, 12) if status == "withdrawn" else None)
        if row.salary_snapshot:
            row.salary_snapshot = {**row.salary_snapshot, "frozen_at": BASE_TIME.isoformat()}
        db.add(row)
        apps.append(row)
    db.flush()
    for job in jobs:
        sync_quota(db, job)
    jobs[10].status, jobs[10].close_reason = "closed", "manual"
    hours_specs = [(0, 21, "7", "library"), (1, 23, "3", "lab"), (2, 14, "8", "library"),
                   (2, 21, "8", "library"), (3, 7, "8", "lab"), (3, 14, "8", "lab"), (3, 21, "8", "lab")]
    hours = []
    for index, (app_index, day, count, operator) in enumerate(hours_specs):
        record = apps[app_index]
        timestamp = datetime(2026, 9, day, 17, index)
        row = WorkHour(application_id=record.id, student_id=record.student_id, work_date=date(2026, 9, day),
                       hours=Decimal(count), operator_id=people[operator].id, reason="模拟工时登记",
                       root_created_at=timestamp, created_at=timestamp)
        db.add(row)
        db.flush()
        row.root_id = row.id
        hours.append(row)
    original = hours[2]
    db.add(WorkHour(application_id=original.application_id, student_id=original.student_id,
                    work_date=original.work_date, hours=Decimal("6"), previous_id=original.id,
                    root_id=original.root_id, root_created_at=original.root_created_at,
                    operator_id=people["library"].id, reason="模拟更正：核对签到后由8小时更正为6小时",
                    created_at=datetime(2026, 9, 15, 10)))
    db.flush()
    for person in people.values():
        if person.role == "student":
            recompute_hours(db, person.id)
    db.add(SystemSetting(key="matching_weights", value={"time": 40, "hardship": 25, "skills": 25, "location": 10},
                         version=1, modified_at=BASE_TIME))
    revision = db.scalar(select(SystemSetting).where(SystemSetting.key == "schema_version"))
    if revision is None:
        db.add(SystemSetting(key="schema_version", value={"revision": REVISION, "seed": SEED_VERSION},
                             version=1, modified_at=BASE_TIME))
    else:
        revision.value = {**revision.value, "seed": SEED_VERSION}
    policy_path = config.ROOT / "data/policies/verified-policy.json"
    if policy_path.exists():
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        if policy.get("verified") is True:
            policy["verified_at"] = datetime.fromisoformat(policy["verified_at"])
            policy["imported_at"] = now()
            db.add(PolicyDoc(**policy))
    result = {"status": "created", "version": SEED_VERSION, "jobs": len(jobs),
              "accounts": len(people) + int(include_bootstrap), "applications": len(apps)}
    db.add(AuditLog(actor_id=None, action="demo.seed", resource_type="system", resource_id=None,
                    after=result, created_at=now()))
    return result


def reset_demo(confirm):
    expected = (config.ROOT / "data/campus_demo.db").resolve()
    if config.APP_ENV == "demo" and make_url(config.DATABASE_URL).get_backend_name() == "mysql":
        sys.path.insert(0, str(config.ROOT / "scripts"))
        from dotenv import dotenv_values
        from mysql_data import reset
        configured_url = dotenv_values(config.ROOT / ".env").get("DATABASE_URL")
        if not configured_url or make_url(configured_url) != make_url(config.DATABASE_URL):
            raise SystemExit("Active MySQL URL differs from managed configuration; reset refused.")
        engine.dispose()
        print(json.dumps(reset(confirm=confirm), ensure_ascii=True))
        return
    if config.APP_ENV != "demo" or not config.DATABASE_URL.startswith("sqlite:///"):
        raise SystemExit("Reset is limited to the project's managed demo; other environments are refused.")
    configured = Path(config.DATABASE_URL.removeprefix("sqlite:///")).resolve()
    if configured != expected or confirm != "campus-demo":
        raise SystemExit("Demo target mismatch. Refused reset.")
    engine.dispose()
    if expected.exists():
        backup = config.ROOT / "work/backups" / f"demo-{now().strftime('%Y%m%d-%H%M%S-%f')}.db"
        backup.parent.mkdir(parents=True, exist_ok=True)
        import sqlite3
        with closing(sqlite3.connect(expected)) as source, closing(sqlite3.connect(backup)) as destination:
            source.backup(destination)
        for path in [expected, expected.with_name(expected.name+"-wal"), expected.with_name(expected.name+"-shm")]:
            if path.parent != expected.parent:
                raise SystemExit("Unexpected path")
            path.unlink(missing_ok=True)
        print(f"Demo backup: {backup}")
    log = config.ROOT / "work/reset-log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": now().isoformat(), "target": str(expected), "version": SEED_VERSION})+"\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    ensure_env()
    if args.reset:
        reset_demo(args.confirm)
    (config.ROOT / "data").mkdir(exist_ok=True)
    upgrade_schema()
    with SessionLocal.begin() as db:
        print(json.dumps(seed_database(db, include_bootstrap=True), ensure_ascii=True))
        print(f"Schema {REVISION}; jobs={db.scalar(select(func.count()).select_from(Job))}; "
              f"users={db.scalar(select(func.count()).select_from(User))}")


if __name__ == "__main__":
    main()
