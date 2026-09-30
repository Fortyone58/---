from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app import config
from app.models import Application, AuditLog, Job, PolicyDoc, User, WorkHour
from app.seed import seed_database
from app.services import match_score, matching, now


def new_job(client, headers, quota=2, category="temporary", wage="18.00", owner="library"):
    data = {"title": "验收用模拟岗位", "description": "隔离测试库中的岗位，不写演示库", "location": "模拟图书馆",
            "area": "A", "category": category, "wage": wage, "quota": quota,
            "slots": [{"day": 1, "start": "14:00", "end": "16:00"}], "skills": []}
    response = client.post("/api/jobs", json=data, headers=headers(owner))
    assert response.status_code == 201, response.text
    job = response.json()
    response = client.post(f"/api/jobs/{job['id']}/actions", json={"action": "publish"}, headers=headers(owner))
    assert response.status_code == 200
    return job, data


def apply(client, headers, job_id, student="student"):
    return client.post("/api/applications", json={"job_id": job_id, "reason": "申请这条隔离测试岗位"}, headers=headers(student))


def action(client, headers, app_id, act, user="library", note="隔离测试审核说明"):
    return client.post(f"/api/applications/{app_id}/actions", json={"action": act, "note": note}, headers=headers(user))


def audit_count(factory):
    with factory() as db:
        return db.scalar(select(func.count()).select_from(AuditLog))


def test_register_activation_and_restricted_profile(client, headers):
    invalid = {"username": "fresh", "password": "Valid@2026", "display_name": "测试学生", "role": "admin"}
    assert client.post("/api/auth/register", json=invalid).status_code == 400
    invalid.pop("role")
    assert client.post("/api/auth/register", json=invalid).status_code == 201
    assert client.post("/api/auth/register", json=invalid).status_code == 409
    auth = {"username": "fresh", "password": "Valid@2026"}
    assert client.post("/api/auth/login", json=auth).status_code == 403
    users = client.get("/api/admin/users", headers=headers("admin_demo")).json()["items"]
    user_id = next(u["id"] for u in users if u["username"] == "fresh")
    assert client.post(f"/api/admin/users/{user_id}/activate", headers=headers("aid")).status_code == 403
    assert client.post(f"/api/admin/users/{user_id}/activate", headers=headers("admin_demo")).status_code == 200
    assert client.post(f"/api/admin/users/{user_id}/activate", headers=headers("admin_demo")).status_code == 409
    token = client.post("/api/auth/login", json=auth).json()["access_token"]
    own_headers = {"Authorization": f"Bearer {token}"}
    profile = {"display_name": "测试学生", "major": "测试", "skills": [], "slots": [], "area": "A"}
    assert client.put("/api/profiles/me", json={**profile, "hardship": "D3"}, headers=own_headers).status_code == 400
    assert client.put("/api/profiles/me", json=profile, headers=own_headers).status_code == 200
    assert client.get("/api/matching", headers=own_headers).json()["items"][0]["total"] is None
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer broken"}).status_code == 401


@pytest.mark.parametrize("path", ["/api/admin/users", "/api/admin/settings", "/api/admin/audit", "/api/admin/units"])
def test_student_admin_routes_denied(client, headers, path):
    assert client.get(path, headers=headers("student")).status_code == 403


def test_cross_unit_and_cross_student_isolation(client, headers, factory):
    before = audit_count(factory)
    assert client.get("/api/jobs/7", headers=headers("library")).status_code == 403
    assert action(client, headers, 2, "finish").status_code == 403
    assert action(client, headers, 3, "withdraw", user="student").status_code == 403
    assert client.get("/api/workhours/2/history", headers=headers("library")).status_code == 403
    assert client.get("/api/workhours/2/history", headers=headers("student2")).status_code == 403
    assert client.post("/api/workhours/2/corrections", json={"work_date": "2026-09-23", "hours": "1", "reason": "越权模拟"}, headers=headers("library")).status_code == 403
    assert audit_count(factory) == before
    library_jobs = client.get("/api/jobs", headers=headers("library")).json()["items"]
    lab_jobs = client.get("/api/jobs", headers=headers("lab")).json()["items"]
    assert not {j["id"] for j in library_jobs} & {j["id"] for j in lab_jobs}


def test_complete_flow_and_frozen_salary(client, headers):
    job, data = new_job(client, headers, quota=1)
    application = apply(client, headers, job["id"]).json()
    app_id = application["id"]
    assert action(client, headers, app_id, "onboard").status_code == 409
    approved = action(client, headers, app_id, "approve")
    assert approved.status_code == 200
    assert approved.json()["salary_snapshot"]["wage"] == "18.00"
    assert client.get(f"/api/jobs/{job['id']}", headers=headers("library")).json()["close_reason"] == "full"
    assert client.put(f"/api/jobs/{job['id']}", json={**data, "wage": "99.00"}, headers=headers("library")).status_code == 200
    assert action(client, headers, app_id, "onboard").status_code == 200
    today = now().date().isoformat()
    recorded = client.post("/api/workhours", json={"application_id": app_id, "work_date": today, "hours": "1.50"}, headers=headers("library"))
    assert recorded.status_code == 201
    group = next(g for g in client.get(f"/api/payroll/summary?month={today[:7]}", headers=headers("student")).json()["groups"] if g["application_id"] == app_id)
    assert group["normal_amount"] == "27.00"
    assert action(client, headers, app_id, "finish").status_code == 200
    assert action(client, headers, app_id, "finish").status_code == 409
    assert action(client, headers, app_id, "withdraw", user="student").status_code == 409
    state = client.get(f"/api/jobs/{job['id']}", headers=headers("library")).json()
    assert state["occupied"] == 1 and state["close_reason"] == "full"
    assert apply(client, headers, job["id"]).status_code == 409
    assert client.post("/api/workhours", json={"application_id": app_id, "work_date": today, "hours": "1"}, headers=headers("library")).status_code == 409
    assert client.post(f"/api/workhours/{recorded.json()['id']}/corrections", json={"work_date": today, "hours": "0", "reason": "结束后作废更正"}, headers=headers("library")).status_code == 201


def test_reapplication_max_two_and_rejection_terminal(client, headers):
    job, _ = new_job(client, headers)
    first = apply(client, headers, job["id"]).json()
    assert apply(client, headers, job["id"]).status_code == 409
    assert action(client, headers, first["id"], "withdraw", user="student").status_code == 200
    second = apply(client, headers, job["id"])
    assert second.status_code == 201 and second.json()["application_no"] == 2
    assert action(client, headers, second.json()["id"], "withdraw", user="student").status_code == 200
    assert apply(client, headers, job["id"]).status_code == 409
    first_other = apply(client, headers, job["id"], "student2").json()
    assert action(client, headers, first_other["id"], "reject", note="").status_code == 400
    assert action(client, headers, first_other["id"], "reject").status_code == 200
    assert apply(client, headers, job["id"], "student2").status_code == 409


def test_full_reopen_manual_close_and_quota_adjustments(client, headers):
    job, data = new_job(client, headers, quota=1)
    a = apply(client, headers, job["id"]).json()
    b = apply(client, headers, job["id"], "student2").json()
    assert action(client, headers, a["id"], "approve").status_code == 200
    assert action(client, headers, b["id"], "approve").status_code == 409
    assert action(client, headers, a["id"], "withdraw", user="student").status_code == 200
    assert client.get(f"/api/jobs/{job['id']}", headers=headers("library")).json()["status"] == "published"
    assert action(client, headers, b["id"], "approve").status_code == 200
    assert client.put(f"/api/jobs/{job['id']}", json={**data, "quota": 2}, headers=headers("library")).json()["status"] == "published"
    assert client.put(f"/api/jobs/{job['id']}", json=data, headers=headers("library")).json()["close_reason"] == "full"
    assert client.post(f"/api/jobs/{job['id']}/actions", json={"action": "close"}, headers=headers("library")).status_code == 200
    assert action(client, headers, b["id"], "withdraw", user="student2").status_code == 200
    manual = client.get(f"/api/jobs/{job['id']}", headers=headers("library")).json()
    assert manual["status"] == "closed" and manual["close_reason"] == "manual"
    assert client.put(f"/api/jobs/{job['id']}", json={**data, "quota": 3}, headers=headers("library")).json()["close_reason"] == "manual"
    assert client.delete(f"/api/jobs/{job['id']}", headers=headers("library")).status_code == 409
    assert client.post(f"/api/jobs/{job['id']}/actions", json={"action": "publish"}, headers=headers("library")).status_code == 200


def test_quota_not_below_occupied_and_draft_publish_validation(client, headers):
    job, data = new_job(client, headers, quota=2)
    for student in ["student", "student2"]:
        a = apply(client, headers, job["id"], student).json()
        assert action(client, headers, a["id"], "approve").status_code == 200
    assert client.put(f"/api/jobs/{job['id']}", json={**data, "quota": 1}, headers=headers("library")).status_code == 409
    draft = client.post("/api/jobs", json={**data, "slots": []}, headers=headers("library")).json()
    assert client.get(f"/api/jobs/{draft['id']}", headers=headers("student")).status_code == 403
    assert client.post(f"/api/jobs/{draft['id']}/actions", json={"action": "publish"}, headers=headers("library")).status_code == 400
    assert client.delete(f"/api/jobs/{draft['id']}", headers=headers("library")).status_code == 200


def test_concurrent_approvals_do_not_overbook(client, headers):
    job, _ = new_job(client, headers, quota=1)
    ids = [apply(client, headers, job["id"], user).json()["id"] for user in ["student", "student2"]]
    headers("library")
    with ThreadPoolExecutor(2) as pool:
        codes = list(pool.map(lambda identity: action(client, headers, identity, "approve").status_code, ids))
    assert sorted(codes) == [200, 409]
    result = client.get(f"/api/jobs/{job['id']}", headers=headers("library")).json()
    assert result["occupied"] == 1 and result["status"] == "closed"


def test_concurrent_duplicate_application_only_once(client, headers):
    job, _ = new_job(client, headers)
    headers("student")
    with ThreadPoolExecutor(2) as pool:
        codes = list(pool.map(lambda _: apply(client, headers, job["id"]).status_code, range(2)))
    assert sorted(codes) == [201, 409]


def test_seed_baseline_and_unit_global_totals(client, headers, factory):
    global_pay = client.get("/api/payroll/summary?month=2026-09", headers=headers("admin_demo")).json()
    assert (global_pay["normal_hours"], global_pay["pending_hours"], global_pay["normal_amount"], global_pay["pending_amount"]) == ("45.00", "3.00", "1006.00", "75.00")
    unit_values = [client.get("/api/payroll/summary?month=2026-09", headers=headers(role)).json() for role in ["library", "lab", "logistics", "activities"]]
    for key in ["normal_hours", "pending_hours", "normal_amount", "pending_amount"]:
        assert sum(Decimal(unit[key]) for unit in unit_values) == Decimal(global_pay[key])
    with factory.begin() as db:
        result = seed_database(db)
        assert result["status"] == "unchanged"
        assert db.scalar(select(func.count()).select_from(Job)) == 24
    assert client.get("/api/stats?month=2026-13", headers=headers("student")).status_code == 400


def test_whole_record_verification_and_correction_invalidate(client, headers):
    before = client.get("/api/payroll/summary?month=2026-09", headers=headers("student")).json()
    assert before["normal_amount"] == "126.00" and before["pending_amount"] == "75.00"
    assert client.post("/api/workhours/2/verify", json={"note": "模拟核实有效"}, headers=headers("admin_demo")).status_code == 403
    assert client.post("/api/workhours/2/verify", json={"note": "模拟核实有效"}, headers=headers("aid")).status_code == 200
    assert client.post("/api/workhours/2/verify", json={"note": "模拟重复核实"}, headers=headers("aid")).status_code == 409
    verified = client.get("/api/payroll/summary?month=2026-09", headers=headers("student")).json()
    assert verified["normal_hours"] == "10.00" and verified["normal_amount"] == "201.00" and verified["pending_amount"] == "0.00"
    changed = client.post("/api/workhours/2/corrections", json={"work_date": "2026-09-23", "hours": "4", "reason": "核实后再次更正"}, headers=headers("lab"))
    assert changed.status_code == 201 and changed.json()["state"] == "pending" and changed.json()["verification_note"] is None
    summary = client.get("/api/payroll/summary?month=2026-09", headers=headers("student")).json()
    assert summary["normal_amount"] == "126.00" and summary["pending_amount"] == "100.00"
    assert client.post("/api/workhours/2/verify", json={"note": "历史不可核实"}, headers=headers("aid")).status_code == 409
    history = client.get(f"/api/workhours/{changed.json()['id']}/history", headers=headers("student")).json()["items"]
    assert len(history) == 2 and history[0]["verification_note"] == "模拟核实有效"


def test_prefix_reordering_and_zero_correction(client, headers):
    added = client.post("/api/workhours", json={"application_id": 1, "work_date": "2026-09-22", "hours": "2"}, headers=headers("library"))
    assert added.status_code == 201 and added.json()["state"] == "pending"
    current = client.get("/api/payroll/summary?month=2026-09", headers=headers("student")).json()
    assert current["normal_hours"] == "7.00" and current["pending_hours"] == "5.00"
    correction = client.post("/api/workhours/1/corrections", json={"work_date": "2026-09-21", "hours": "3", "reason": "缩减原工时重算"}, headers=headers("library"))
    assert correction.status_code == 201
    changed = client.get("/api/payroll/summary?month=2026-09", headers=headers("student")).json()
    assert changed["normal_hours"] == "8.00" and changed["pending_hours"] == "0.00"
    assert client.post("/api/workhours/1/corrections", json={"work_date": "2026-09-21", "hours": "0", "reason": "不能更正历史根"}, headers=headers("library")).status_code == 409
    identity = correction.json()["id"]
    assert client.post(f"/api/workhours/{identity}/corrections", json={"work_date": "2026-09-21", "hours": "0", "reason": "作废原记录"}, headers=headers("library")).status_code == 201
    final = client.get("/api/payroll/summary?month=2026-09", headers=headers("student")).json()
    assert final["normal_hours"] == "5.00"


def test_concurrent_hours_and_no_correction_fork(client, headers):
    h = headers("library")
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: client.post("/api/workhours", json={"application_id": 1,
                               "work_date": "2026-09-28", "hours": "5"}, headers=h), range(2)))
    assert all(r.status_code == 201 for r in results)
    assert sorted(r.json()["state"] for r in results) == ["normal", "pending"]
    with ThreadPoolExecutor(2) as pool:
        codes = list(pool.map(lambda _: client.post("/api/workhours/1/corrections", json={"work_date": "2026-09-21", "hours": "1", "reason": "并发更正测试"}, headers=h).status_code, range(2)))
    assert sorted(codes) == [201, 409]


@pytest.mark.parametrize("hours", ["0", "-1", "1.001", "25"])
def test_hours_precision_positive_and_daily_bounds(client, headers, hours):
    assert client.post("/api/workhours", json={"application_id": 1, "work_date": "2026-09-28", "hours": hours}, headers=headers("library")).status_code == 400


def test_onboard_date_and_finished_bounds(client, headers):
    assert client.post("/api/workhours", json={"application_id": 1, "work_date": "2026-08-31", "hours": "1"}, headers=headers("library")).status_code == 409
    assert client.post("/api/workhours", json={"application_id": 1, "work_date": "2099-01-01", "hours": "1"}, headers=headers("library")).status_code == 400
    assert client.post("/api/workhours", json={"application_id": 9, "work_date": "2026-09-24", "hours": "1"}, headers=headers("lab")).status_code == 409


def test_fixed_cap_pending_increment_and_historical_wage(client, headers):
    for day, count in [(1, "8"), (28, "8"), (30, "3")]:
        assert client.post("/api/workhours", json={"application_id": 4, "work_date": f"2026-09-{day:02}", "hours": count}, headers=headers("lab")).status_code == 201
    result = client.get("/api/payroll/summary?month=2026-09", headers=headers("student3")).json()
    assert result["normal_hours"] == "40.00" and result["pending_hours"] == "3.00"
    assert result["normal_amount"] == "1000.00" and result["pending_amount"] == "0.00"
    job = client.get("/api/jobs/8", headers=headers("lab")).json()
    data = {key: job[key] for key in ["title", "description", "location", "area", "category", "quota", "slots", "skills"]}
    assert client.put("/api/jobs/8", json={**data, "wage": "2000"}, headers=headers("lab")).status_code == 200
    assert client.get("/api/payroll/summary?month=2026-09", headers=headers("student3")).json()["normal_amount"] == "1000.00"


def test_rounding_only_after_aggregation(client, headers):
    today = now().date().isoformat()
    for _ in range(2):
        job, _data = new_job(client, headers, wage="0.50")
        record = apply(client, headers, job["id"], "student6").json()
        assert action(client, headers, record["id"], "approve").status_code == 200
        assert action(client, headers, record["id"], "onboard").status_code == 200
        assert client.post("/api/workhours", json={"application_id": record["id"], "work_date": today, "hours": "0.01"}, headers=headers("library")).status_code == 201
    summary = client.get(f"/api/payroll/summary?month={today[:7]}", headers=headers("student6")).json()
    assert [g["normal_amount"] for g in summary["groups"]] == ["0.01", "0.01"]
    assert summary["normal_amount"] == "0.01"


def test_gold_matching_cases_missing_and_overlap():
    weights = {"time": 40, "hardship": 25, "skills": 25, "location": 10}
    student = SimpleNamespace(slots=[{"day": 1, "start": "14:00", "end": "16:00"}],
                              hardship="D2", hardship_confirmed=True, skills=["s1"], area="A")
    job = SimpleNamespace(slots=[{"day": 1, "start": "14:00", "end": "16:00"}], area="B")
    a1 = match_score(student, job, ["s1", "s2"], weights)
    assert a1["total"] == "72.00" and a1["parts"] == {"time": "100.00", "hardship": "50.00", "skills": "50.00", "location": "70.00"}
    assert a1["hit_skills"] == ["s1"] and a1["covered_minutes"] == 120
    student.slots.append({"day": 1, "start": "15:00", "end": "16:00"})
    assert match_score(student, job, ["s1", "s2"], weights)["total"] == "72.00"
    student.slots = [{"day": 1, "start": "14:00", "end": "15:00"}]
    student.hardship, student.skills, job.area = "D3", [], "A"
    assert match_score(student, job, [], weights)["total"] == "80.00"
    student.hardship_confirmed = False
    assert match_score(student, job, [], weights)["total"] is None
    assert match_score(student, job, [], weights)["parts"]["hardship"] is None
    student.skills = None
    assert match_score(student, job, [], weights)["parts"]["skills"] is None


def test_matching_tie_order_and_weight_versions(client, headers, factory):
    with factory.begin() as db:
        student = db.scalar(select(User).where(User.username == "student6"))
        student.skills, student.slots, student.area, student.hardship, student.hardship_confirmed = [], [
            {"day": 1, "start": "14:00", "end": "15:00"}], "A", "D3", True
        unit = db.get(Job, 1).unit_id
        for identity, hour in [(101, 10), (103, 10), (102, 9)]:
            db.add(Job(id=identity, unit_id=unit, title="黄金并列算例", description="隔离测试", location="算例区域",
                       area="A", category="temporary", wage=Decimal("18"), quota=2, status="published",
                       slots=[{"day": 1, "start": "14:00", "end": "16:00"}], created_at=now(),
                       published_at=now().replace(hour=hour, minute=0, second=0, microsecond=0)))
        db.flush()
        gold = [item for item in matching(db, student)["items"] if item["job"]["id"] >= 101]
        assert [item["job"]["id"] for item in gold] == [101, 103, 102]
        assert all(item["total"] == "80.00" for item in gold)
    assert client.put("/api/admin/settings/matching", json={"time": 40, "hardship": 25, "skills": 25, "location": 9}, headers=headers("admin_demo")).status_code == 400
    assert client.put("/api/admin/settings/matching", json={"time": 101, "hardship": 0, "skills": 0, "location": -1}, headers=headers("admin_demo")).status_code == 400
    result = client.put("/api/admin/settings/matching", json={"time": 50, "hardship": 20, "skills": 20, "location": 10}, headers=headers("admin_demo"))
    assert result.status_code == 200 and result.json()["version"] == 2


def test_hardship_only_aid_and_manager_unit_required(client, headers, factory):
    with factory() as db:
        student_id = db.scalar(select(User.id).where(User.username == "student6"))
    data = {"hardship": "D3", "note": "模拟确认说明"}
    assert client.post(f"/api/admin/users/{student_id}/hardship", json=data, headers=headers("admin_demo")).status_code == 403
    assert client.post(f"/api/admin/users/{student_id}/hardship", json=data, headers=headers("aid")).status_code == 200
    manager = {"username": "newmanager", "password": "Demo@2026", "display_name": "测试老师", "role": "unit"}
    assert client.post("/api/admin/users", json=manager, headers=headers("admin_demo")).status_code == 400
    assert client.post("/api/admin/users", json={**manager, "unit_id": 9999}, headers=headers("admin_demo")).status_code == 404
    assert client.post("/api/admin/users", json={**manager, "unit_id": 1}, headers=headers("admin_demo")).status_code == 201


def test_policy_absence_unverified_and_user_history_isolation(client, headers, factory):
    with factory.begin() as db:
        # Synthetic test fixture only; never imported into the user's policy knowledge base.
        doc = PolicyDoc(title="测试源", publisher="隔离测试", source_url="https://example.org/test", version="fixture",
                        verified=False, verification_note="未经核验的测试内容", imported_at=now(),
                        sections=[{"location": "模拟定位", "text": "未核验片段：专用测试关键词"}])
        db.add(doc)
    query = {"question": "专用测试关键词"}
    result = client.post("/api/qa", json=query, headers=headers("student")).json()
    assert not result["citations"] and "没有可靠依据" in result["answer"]
    assert client.get("/api/qa/history", headers=headers("student2")).json()["items"] == []
    assert len(client.get("/api/qa/history", headers=headers("student")).json()["items"]) == 1
    assert not client.post("/api/qa", json={"question": "本校每周工时上限"}, headers=headers("student")).json()["citations"]
    assert client.post("/api/assistant/messages", json={"question": "直接批准申请"}, headers=headers("student")).status_code == 503


def test_archived_verified_policy_query_and_school_boundary(client, headers):
    response = client.post("/api/qa", json={"question": "每周"}, headers=headers("student"))
    assert response.status_code == 200
    result = response.json()
    assert result["mode"] == "original_query"
    assert len(result["citations"]) == 1
    hit = result["citations"][0]
    assert hit["location"] == "第二十一条"
    assert "每周不超过8小时，每月不超过40小时" in hit["text"]
    assert hit["source"]["source_url"] == "http://www.moe.gov.cn/srcsite/A05/s7505/201809/t20180903_347076.html"
    assert not client.post("/api/qa", json={"question": "本校固定岗位工资多少"}, headers=headers("student")).json()["citations"]


def test_month_limit_independent_of_week_and_cross_month_correction(client, headers, factory):
    with factory.begin() as db:
        record = db.get(Application, 2)
        record.onboard_at = now().replace(month=8, day=1)
    last = None
    for day in [1, 3, 10, 17, 24, 31]:
        response = client.post("/api/workhours", json={"application_id": 2, "work_date": f"2026-08-{day:02}", "hours": "8"}, headers=headers("lab"))
        assert response.status_code == 201
        last = response.json()
    assert last["state"] == "pending"
    august = client.get("/api/payroll/summary?month=2026-08", headers=headers("student")).json()
    assert (august["normal_hours"], august["pending_hours"], august["normal_amount"], august["pending_amount"]) == ("40.00", "8.00", "1000.00", "200.00")
    changed = client.post(f"/api/workhours/{last['id']}/corrections", json={"work_date": "2026-09-01", "hours": "8", "reason": "跨月更正隔离测试"}, headers=headers("lab"))
    assert changed.status_code == 201
    assert client.get("/api/payroll/summary?month=2026-08", headers=headers("student")).json()["pending_hours"] == "0.00"
    september = client.get("/api/payroll/summary?month=2026-09", headers=headers("student")).json()
    assert september["normal_hours"] == "15.00" and september["pending_hours"] == "3.00"


def test_job_input_owner_and_time_slot_validation(client, headers):
    _job, data = new_job(client, headers)
    assert client.post("/api/jobs", json={**data, "unit_id": 2}, headers=headers("library")).status_code == 400
    assert client.post("/api/jobs", json=data, headers=headers("student")).status_code == 403
    assert client.post("/api/jobs", json={**data, "slots": [{"day": 1, "start": "23:00", "end": "01:00"}]}, headers=headers("library")).status_code == 400
    assert client.post("/api/jobs", json={**data, "skills": ["unknown"]}, headers=headers("library")).status_code == 400


def test_failed_action_does_not_commit_success_audit(client, headers, factory):
    before = audit_count(factory)
    assert action(client, headers, 1, "approve").status_code == 409
    assert audit_count(factory) == before
    with factory() as db:
        assert db.get(Application, 1).status == "onboard"
        assert db.scalar(select(func.count()).select_from(WorkHour)) == 8
    assert config.DATABASE_URL.find("isolated-test") == -1  # Application config and fixture DB are separate.
