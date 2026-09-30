from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select

from app import main
from app.models import Application, AuditLog, PolicyDoc
from app.services import now


def new_review_job(client, headers, owner="library", quota=1):
    data = {
        "title": "独立审查模拟岗位",
        "description": "仅存于隔离测试库，验证自动重新开放的发布约束",
        "location": "隔离图书馆",
        "area": "A",
        "category": "temporary",
        "wage": "18.00",
        "quota": quota,
        "slots": [{"day": 1, "start": "14:00", "end": "16:00"}],
        "skills": [],
    }
    response = client.post("/api/jobs", json=data, headers=headers(owner))
    assert response.status_code == 201, response.text
    job_id = response.json()["id"]
    assert client.post(
        f"/api/jobs/{job_id}/actions", json={"action": "publish"}, headers=headers(owner)
    ).status_code == 200
    return job_id, data


def filled_job(client, headers):
    """An isolated recruitment batch that closes after one approval."""
    job_id, data = new_review_job(client, headers)
    response = client.post(
        "/api/applications",
        json={"job_id": job_id, "reason": "申请这条独立审查模拟岗位"},
        headers=headers("student6"),
    )
    assert response.status_code == 201, response.text
    app_id = response.json()["id"]
    assert client.post(
        f"/api/applications/{app_id}/actions", json={"action": "approve"}, headers=headers("library")
    ).status_code == 200
    closed = client.get(f"/api/jobs/{job_id}", headers=headers("library")).json()
    assert (closed["status"], closed["close_reason"]) == ("closed", "full")
    return job_id, app_id, data


@pytest.mark.parametrize("quota", [1, 2], ids=["later-withdrawal", "immediate-quota-increase"])
def test_full_job_cannot_clear_slots_before_automatic_reopening(client, headers, factory, quota):
    job_id, app_id, data = filled_job(client, headers)
    with factory() as db:
        before_audit = db.scalar(select(func.count()).select_from(AuditLog))

    # Both a quota increase and a later approved withdrawal can reopen closed/full.
    # Neither path may bypass the requirement for a valid published working slot.
    response = client.put(
        f"/api/jobs/{job_id}", json={**data, "slots": [], "quota": quota}, headers=headers("library")
    )
    assert response.status_code == 400, response.text
    unchanged = client.get(f"/api/jobs/{job_id}", headers=headers("library")).json()
    assert unchanged["slots"] == data["slots"] and unchanged["quota"] == 1
    assert unchanged["status"] == "closed" and unchanged["close_reason"] == "full"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(AuditLog)) == before_audit

    assert client.post(
        f"/api/applications/{app_id}/actions", json={"action": "withdraw"}, headers=headers("student6")
    ).status_code == 200
    reopened = client.get(f"/api/jobs/{job_id}", headers=headers("library")).json()
    assert reopened["status"] == "published" and reopened["slots"]


def test_natural_months_share_iso_week_at_calendar_year_boundary(client, headers, factory):
    # December 31 and January 1 are different payroll months but the same ISO week.
    # Moving a later record to a new week must recalculate its historical month too.
    with factory.begin() as db:
        db.get(Application, 1).onboard_at = datetime(2025, 12, 1, 9)
    december = client.post(
        "/api/workhours",
        json={"application_id": 1, "work_date": "2025-12-31", "hours": "8"},
        headers=headers("library"),
    )
    january = client.post(
        "/api/workhours",
        json={"application_id": 1, "work_date": "2026-01-01", "hours": "1"},
        headers=headers("library"),
    )
    assert december.status_code == january.status_code == 201
    assert december.json()["state"] == "normal" and january.json()["state"] == "pending"
    for month, normal, pending in [("2025-12", "144.00", "0.00"), ("2026-01", "0.00", "18.00")]:
        summary = client.get(f"/api/payroll/summary?month={month}", headers=headers("student")).json()
        assert (summary["normal_amount"], summary["pending_amount"]) == (normal, pending)
    response = client.post(
        f"/api/workhours/{january.json()['id']}/corrections",
        json={"work_date": "2026-01-05", "hours": "1", "reason": "更正为下一自然周"},
        headers=headers("library"),
    )
    assert response.status_code == 201 and response.json()["state"] == "normal"
    summary = client.get("/api/payroll/summary?month=2026-01", headers=headers("student")).json()
    assert (summary["normal_amount"], summary["pending_amount"]) == ("18.00", "0.00")


def synchronize_initial_auth_reads(engine, participants):
    """Establish concurrent transaction snapshots before any business row lock."""
    barrier = Barrier(participants)
    marker = f"review-auth-sync-{uuid4()}"

    def after_cursor_execute(connection, _cursor, statement, _parameters, _context, _executemany):
        sql = " ".join(statement.lower().split())
        if "from users" not in sql or "where users.id" not in sql or "for update" in sql:
            return
        if connection.info.get(marker):
            return
        connection.info[marker] = True
        # Each caller holds a separate connection here. A failed caller cannot hang the suite.
        barrier.wait(timeout=10)

    event.listen(engine, "after_cursor_execute", after_cursor_execute)
    return after_cursor_execute


def test_mysql_quota_two_three_parallel_approvals_use_current_counts(client, headers, factory):
    engine = factory.kw["bind"]
    if engine.dialect.name != "mysql":
        pytest.skip("Requires an independently provisioned MySQL verification database")
    job_id, _ = new_review_job(client, headers, quota=2)
    app_ids = []
    for student in ["student4", "student5", "student6"]:
        response = client.post(
            "/api/applications", json={"job_id": job_id, "reason": "独立并发录用审查"}, headers=headers(student)
        )
        assert response.status_code == 201, response.text
        app_ids.append(response.json()["id"])
    unit_headers = headers("library")
    listener = synchronize_initial_auth_reads(engine, 3)
    try:
        with ThreadPoolExecutor(3) as pool:
            responses = list(pool.map(
                lambda app_id: client.post(
                    f"/api/applications/{app_id}/actions", json={"action": "approve"}, headers=unit_headers
                ), app_ids
            ))
    finally:
        event.remove(engine, "after_cursor_execute", listener)
    assert sorted(response.status_code for response in responses) == [200, 200, 409]
    job = client.get(f"/api/jobs/{job_id}", headers=unit_headers).json()
    assert (job["occupied"], job["status"], job["close_reason"]) == (2, "closed", "full")
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Application).where(
            Application.job_id == job_id, Application.status == "approved"
        )) == 2


def test_mysql_cross_unit_parallel_hours_recalculate_from_current_tails(client, headers, factory, monkeypatch):
    engine = factory.kw["bind"]
    if engine.dialect.name != "mysql":
        pytest.skip("Requires an independently provisioned MySQL verification database")
    # DATETIME(0) rounds .750000 upward in MySQL. All recalculation inputs must
    # use the persisted timestamps, including the newly inserted identity-map row.
    frozen_now = now().replace(hour=12, minute=0, second=0, microsecond=750000)
    monkeypatch.setattr(main, "now", lambda: frozen_now)
    work_inputs = []
    for owner in ["library", "lab"]:
        job_id, _ = new_review_job(client, headers, owner=owner)
        response = client.post(
            "/api/applications", json={"job_id": job_id, "reason": "独立跨单位工时审查"}, headers=headers("student6")
        )
        assert response.status_code == 201, response.text
        app_id = response.json()["id"]
        for action in ["approve", "onboard"]:
            assert client.post(
                f"/api/applications/{app_id}/actions", json={"action": action}, headers=headers(owner)
            ).status_code == 200
        work_inputs.append((app_id, headers(owner)))
    work_date = now().date().isoformat()
    listener = synchronize_initial_auth_reads(engine, 2)
    try:
        with ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(
                lambda work_input: client.post(
                    "/api/workhours",
                    json={"application_id": work_input[0], "work_date": work_date, "hours": "5"},
                    headers=work_input[1],
                ), work_inputs
            ))
    finally:
        event.remove(engine, "after_cursor_execute", listener)
    assert [response.status_code for response in responses] == [201, 201]
    results = sorted((response.json() for response in responses), key=lambda row: row["root_id"])
    assert [row["state"] for row in results] == ["normal", "pending"]
    current = client.get(f"/api/workhours?month={work_date[:7]}", headers=headers("student6")).json()["items"]
    assert [row["state"] for row in sorted(current, key=lambda row: row["root_id"])] == ["normal", "pending"]
    summary = client.get(f"/api/payroll/summary?month={work_date[:7]}", headers=headers("student6")).json()
    assert (summary["normal_hours"], summary["pending_hours"]) == ("5.00", "5.00")


def review_policy_import(verified_at):
    return {
        "title": "独立时区审查测试来源",
        "publisher": "仅隔离测试夹具",
        "source_url": "https://example.org/timezone-fixture",
        "version": "test-only",
        "verified_at": verified_at,
        "verification_note": "测试时间转换，不是正式核验政策来源",
        "sections": [{"location": "测试定位", "text": "这段内容仅用于独立隔离回归测试"}],
        "is_school_policy": False,
    }


def test_policy_import_future_timestamp_uses_its_actual_timezone(client, headers, factory, monkeypatch):
    monkeypatch.setattr(main, "now", lambda: datetime(2026, 9, 30, 23, 30))
    with factory() as db:
        before_docs = db.scalar(select(func.count()).select_from(PolicyDoc))
    # UTC 15:31 is Chinese local 23:31, one minute in the future.
    response = client.post(
        "/api/policies", json=review_policy_import("2026-09-30T15:31:00Z"), headers=headers("aid")
    )
    assert response.status_code == 400, response.text
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(PolicyDoc)) == before_docs


def test_policy_import_past_timestamp_is_saved_as_chinese_local_time(client, headers, monkeypatch):
    monkeypatch.setattr(main, "now", lambda: datetime(2026, 9, 30, 23, 30))
    # Japanese local 00:15 next day is Chinese local 23:15 today, already in the past.
    response = client.post(
        "/api/policies", json=review_policy_import("2026-10-01T00:15:00+09:00"), headers=headers("aid")
    )
    assert response.status_code == 201, response.text
    assert response.json()["verified_at"] == "2026-09-30T23:15:00"


def test_policy_unknown_new_topic_does_not_inherit_unrelated_followup_context(client, headers):
    first = client.post("/api/qa", json={"question": "每周", "conversation_id": "independent-context"},
                        headers=headers("student"))
    assert first.status_code == 200
    assert first.json()["citations"][0]["location"] == "第二十一条"
    changed = client.post("/api/qa", json={
        "question": "那量子计算设备补贴的具体金额是多少？", "conversation_id": "independent-context"
    }, headers=headers("student"))
    assert changed.status_code == 200
    assert not changed.json()["citations"], changed.text
    assert "没有可靠依据" in changed.json()["answer"]


@pytest.mark.parametrize("unit_name", ["数字校园实验室", "大学生活动中心"])
def test_assistant_negating_full_unit_name_does_not_reinclude_its_short_alias(client, headers, unit_name):
    response = client.post("/api/assistant/messages", json={
        "question": f"不要{unit_name}的岗位", "conversation_id": "independent-negation"
    }, headers=headers("student"))
    assert response.status_code == 200
    result = response.json()
    assert result["status"] in {"matched", "partial"}, result
    assert result["items"]
    assert all(item["job"]["unit_name"] != unit_name for item in result["items"])
    assert any(condition["key"] == "excluded_units" for condition in result["filters"])
    assert all(condition["key"] != "units" for condition in result["filters"])
