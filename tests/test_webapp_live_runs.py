from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from hireshire.storage.db import PHASE_PIPELINE, Database
from hireshire.webapp.deps import ReadDB
from hireshire.webapp.jobs_query import query_jobs


def _match_json(job_id: str, score: int | None, *, skipped: bool = False) -> str:
    return json.dumps({
        "job_id": job_id,
        "board_token": "acme",
        "title": f"Engineer {job_id}",
        "location": "Remote",
        "absolute_url": f"https://example.com/jobs/{job_id}",
        "relevance_score": score,
        "skipped": skipped,
    })


def _upsert_match(
    db: Database,
    run_id: str,
    job_id: str,
    score: int | None,
    *,
    skipped: bool = False,
) -> None:
    db.upsert_match(
        run_id,
        job_id,
        "acme",
        f"Engineer {job_id}",
        score,
        score is not None and score >= 80,
        skipped,
        "filtered" if skipped else None,
        run_id,
        "2026-09-14T03:00:00+00:00",
        _match_json(job_id, score, skipped=skipped),
    )


def test_live_run_does_not_replace_latest_completed_run(tmp_path):
    path = tmp_path / "test.db"
    db = Database(path)
    completed = "2026-09-14T02-00-00Z"
    live = "2026-09-14T03-00-00Z"

    db.finalise_run(completed, PHASE_PIPELINE, "2026-09-14T02:00:00+00:00")
    db.start_run(live, PHASE_PIPELINE, "2026-09-14T03:00:00+00:00")

    assert db.latest_run(PHASE_PIPELINE) == completed
    read_db = ReadDB(path)
    assert read_db.latest_run(PHASE_PIPELINE) == completed
    assert read_db.live_run_ids() == [live]
    assert read_db.is_live_run(live)


def _set_stats(db: Database, run_id: str, stats: dict) -> None:
    with db._conn:
        db._conn.execute(
            "UPDATE runs SET stats_json=? WHERE run_id=? AND phase=?",
            (json.dumps(stats), run_id, PHASE_PIPELINE),
        )


def _stats(db: Database, run_id: str) -> dict:
    row = db._conn.execute(
        "SELECT stats_json FROM runs WHERE run_id=? AND phase=?", (run_id, PHASE_PIPELINE)
    ).fetchone()
    return json.loads(row["stats_json"])


def test_run_with_stale_heartbeat_is_not_live(tmp_path):
    path = tmp_path / "test.db"
    db = Database(path)
    run_id = "2026-09-14T03-00-00Z"
    db.start_run(run_id, PHASE_PIPELINE, "2026-09-14T03:00:00+00:00")
    stale = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    _set_stats(db, run_id, {**_stats(db, run_id), "heartbeat_at": stale})

    read_db = ReadDB(path)
    assert read_db.live_run_ids() == []
    assert not read_db.is_live_run(run_id)

    db.heartbeat_run(run_id, PHASE_PIPELINE)
    assert read_db.live_run_ids() == [run_id]


def test_unfinished_run_without_heartbeat_is_not_live(tmp_path):
    # Rows written before heartbeats existed, whose process has long since died.
    path = tmp_path / "test.db"
    db = Database(path)
    run_id = "2026-09-14T03-00-00Z"
    db.start_run(run_id, PHASE_PIPELINE, "2026-09-14T03:00:00+00:00")
    _set_stats(db, run_id, {"status": "running"})

    assert ReadDB(path).live_run_ids() == []


def test_stop_unfinished_runs_only_finalises_the_given_pid(tmp_path):
    path = tmp_path / "test.db"
    db = Database(path)
    killed = "2026-09-14T03-00-00Z"
    other = "2026-09-14T04-00-00Z"
    db.start_run(killed, PHASE_PIPELINE, "2026-09-14T03:00:00+00:00")
    db.start_run(other, PHASE_PIPELINE, "2026-09-14T04:00:00+00:00")
    _set_stats(db, killed, {**_stats(db, killed), "pid": 1111})
    _set_stats(db, other, {**_stats(db, other), "pid": 2222})

    assert db.stop_unfinished_runs(PHASE_PIPELINE, 1111) == [killed]

    read_db = ReadDB(path)
    assert read_db.live_run_ids() == [other]
    assert _stats(db, killed)["status"] == "stopped"
    assert _stats(db, other)["status"] == "running"
    # A finalised run no longer takes heartbeats.
    db.heartbeat_run(killed, PHASE_PIPELINE)
    assert not read_db.is_live_run(killed)


def test_live_jobs_show_only_llm_scores_and_overlay_tuner_result(tmp_path):
    path = tmp_path / "test.db"
    db = Database(path)
    run_id = "2026-09-14T03-00-00Z"
    db.start_run(run_id, PHASE_PIPELINE, "2026-09-14T03:00:00+00:00")

    _upsert_match(db, run_id, "scored", 87)
    _upsert_match(db, run_id, "low-score", 42)
    _upsert_match(db, run_id, "funnel-filtered", 0, skipped=True)
    _upsert_match(db, run_id, "llm-disabled", None)
    db.record_pipeline_result(run_id, {
        "job_id": "scored",
        "company": "acme",
        "title": "Engineer scored",
        "job_url": "https://example.com/jobs/scored",
        "relevance_score": 87,
        "resume_pdf": "data/tuned/scored/resume.pdf",
        "tuner_status": "tuned",
        "processed_at": "2026-09-14T03:01:00+00:00",
    })

    rows = query_jobs(ReadDB(path), run_id=run_id)

    assert [row.job_id for row in rows] == ["scored", "low-score"]
    assert rows[0].relevance_score == 87
    assert rows[0].resume_available is True
    assert rows[0].tuner_status == "tuned"
    assert rows[1].relevance_score == 42
    assert rows[1].resume_available is False
