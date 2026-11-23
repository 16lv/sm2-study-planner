from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from study_planner.app import PlannerDatabase, compute_sm2_schedule, create_app


def test_sm2_spaced_repetition_interval_progression() -> None:
    reps1, ef1, int1 = compute_sm2_schedule(quality=5, repetitions=0, easiness=2.5, interval_days=1)
    assert (reps1, int1) == (1, 1)
    assert ef1 == 2.6

    reps2, ef2, int2 = compute_sm2_schedule(
        quality=5, repetitions=reps1, easiness=ef1, interval_days=int1
    )
    assert (reps2, int2) == (2, 6)

    reps3, _, int3 = compute_sm2_schedule(
        quality=5, repetitions=reps2, easiness=ef2, interval_days=int2
    )
    assert reps3 == 3
    assert int3 >= 15

    # Low recall quality (<3) resets interval back to 1 day
    reset_reps, _, reset_int = compute_sm2_schedule(
        quality=2, repetitions=reps3, easiness=ef2, interval_days=int3
    )
    assert reset_reps == 0
    assert reset_int == 1


def test_sm2_easiness_floor_never_drops_below_1_3() -> None:
    _, ef, _ = compute_sm2_schedule(quality=0, repetitions=3, easiness=1.35, interval_days=10)
    assert ef >= 1.3


def test_full_module_assessment_due_queue_and_lazy_db(tmp_path: Path) -> None:
    lazy_db = tmp_path / "lazy_planner.db"
    lazy_app = create_app(db_path=str(lazy_db))
    assert not lazy_db.exists()
    with TestClient(lazy_app) as lazy_client:
        assert lazy_client.get("/api/modules").json() == []
    assert lazy_db.exists()

    db = PlannerDatabase(":memory:")
    client = TestClient(create_app(db))

    mod_resp = client.post(
        "/api/modules",
        json={
            "code": "cm2102",
            "title": "Database Systems",
            "credits": 20,
            "semester": "Autumn",
        },
    )
    assert mod_resp.status_code == 201
    assert mod_resp.json()["code"] == "CM2102"

    ass_resp = client.post(
        "/api/assessments",
        json={
            "module_code": "CM2102",
            "title": "PostgreSQL Normalisation & Indexing Coursework",
            "weight_pct": 40.0,
            "due_date": "2026-11-28",
            "completed": False,
        },
    )
    assert ass_resp.status_code == 201
    ass_id = ass_resp.json()["id"]

    done_resp = client.patch(f"/api/assessments/{ass_id}/complete", json={"completed": True})
    assert done_resp.status_code == 200
    assert done_resp.json()["completed"] is True

    topic_resp = client.post(
        "/api/topics",
        json={"module_code": "CM2102", "topic": "BCNF and 3NF Dependency Preservation"},
    )
    assert topic_resp.status_code == 201
    topic_id = topic_resp.json()["id"]

    # Newly added topic is due today before its first review
    assert len(client.get("/api/topics/due").json()) == 1

    rev_resp = client.post(f"/api/topics/{topic_id}/review", json={"quality": 5})
    assert rev_resp.status_code == 200
    assert rev_resp.json()["repetitions"] == 1
    assert client.get("/api/topics/due").json() == []

    # Updating module metadata must not wipe child assessments or revision topics
    update_resp = client.post(
        "/api/modules",
        json={
            "code": "CM2102",
            "title": "Database Systems & Normalisation",
            "credits": 20,
            "semester": "Autumn",
        },
    )
    assert update_resp.status_code == 201
    assert len(client.get("/api/assessments", params={"module_code": "CM2102"}).json()) == 1
    assert len(client.get("/api/topics", params={"module_code": "CM2102"}).json()) == 1

    # Individual delete routes return 204 when present and 404 once removed
    extra_topic = client.post(
        "/api/topics",
        json={"module_code": "CM2102", "topic": "Temporary topic"},
    )
    assert extra_topic.status_code == 201
    extra_id = extra_topic.json()["id"]
    assert client.delete(f"/api/topics/{extra_id}").status_code == 204
    assert client.delete(f"/api/topics/{extra_id}").status_code == 404

    # Deleting module cascades cleanly to assessments and revision topics
    assert client.delete("/api/modules/CM2102").status_code == 204
    assert client.get("/api/assessments").json() == []
    assert client.get("/api/topics").json() == []

    missing_topic = client.post(
        "/api/topics",
        json={"module_code": "CM9999", "topic": "Orphan"},
    )
    assert missing_topic.status_code == 404

    with pytest.raises(ValueError, match="between 0 and 5"):
        compute_sm2_schedule(quality=6, repetitions=0, easiness=2.5, interval_days=1)


def test_overview_summary_tracks_assessments_and_due_topics() -> None:
    db = PlannerDatabase(":memory:")
    client = TestClient(create_app(db))

    assert client.post(
        "/api/modules",
        json={"code": "CM2208", "title": "Scientific Computing", "credits": 20, "semester": "Spring"},
    ).status_code == 201

    assert client.post(
        "/api/assessments",
        json={
            "module_code": "CM2208",
            "title": "Overdue Exercise",
            "weight_pct": 25.0,
            "due_date": "2020-01-10",
            "completed": False,
        },
    ).status_code == 201

    assert client.post(
        "/api/topics",
        json={"module_code": "CM2208", "topic": "LU Factorisation"},
    ).status_code == 201

    overview = client.get("/api/overview").json()
    assert overview["module_count"] == 1
    assert overview["total_credits"] == 20
    assert overview["assessments"]["overdue"] == 1
    assert overview["topics"]["due_today"] == 1
