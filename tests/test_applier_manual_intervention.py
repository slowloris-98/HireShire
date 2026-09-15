"""Regression coverage for Workday's manual-application path."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from applier import apply_job
from hireshire.applier.config import ApplierSettings
from hireshire.applier.loader import load_shortlisted
from hireshire.applier.manual_intervention import (
    DIRECT_MANUAL_INTERVENTION_MESSAGE,
    MANUAL_INTERVENTION_STATUS,
    WORKDAY_MANUAL_INTERVENTION_MESSAGE,
)
from hireshire.applier.store import AppliedStore
from hireshire.matcher.scorer import MatchResult
from hireshire.models.job import Job, Location
from hireshire.storage.db import Database
from hireshire.webapp.deps import ReadDB
from hireshire.webapp.jobs_query import query_jobs


class TrackingAnswerer:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_answers(self, **_: object) -> dict[str, str]:
        self.calls += 1
        return {"first_name": "Ada"}


class TrackingFiller:
    def __init__(self) -> None:
        self.calls = 0

    async def fill(self, **_: object) -> dict[str, str | None]:
        self.calls += 1
        return {"status": "dry_run", "error": None, "screenshot": None}


def _job(job_id: str, source: str, board_token: str = "acme") -> Job:
    now = datetime.now(timezone.utc)
    hostname = "acme.wd5.myworkdayjobs.com" if source == "workday" else "example.com"
    return Job(
        source=source,
        board_token=board_token,
        job_id=job_id,
        title="Backend Engineer",
        location=Location(name="Remote"),
        absolute_url=f"https://{hostname}/jobs/{job_id}",  # type: ignore[arg-type]
        updated_at=now,
        scraped_at=now,
    )


def _match(job: Job, source_run_id: str = "scrape-run") -> MatchResult:
    now = datetime.now(timezone.utc)
    return MatchResult(
        job_id=job.job_id,
        board_token=job.board_token,
        title=job.title,
        location=job.location.name,
        absolute_url=str(job.absolute_url),
        match_reasons=[],
        disqualifiers=[],
        recommend=True,
        scored_at=now,
        source_run_id=source_run_id,
    )


def _settings() -> ApplierSettings:
    return ApplierSettings(first_name="Ada", last_name="Lovelace", email="ada@example.com")


def test_workday_job_requires_manual_intervention_without_automation(tmp_path):
    job = _job("wd-1", "workday")
    answerer = TrackingAnswerer()
    filler = TrackingFiller()

    record = asyncio.run(apply_job(
        _match(job), job, settings=_settings(), resume_text="resume", resume_path=Path(tmp_path / "resume.pdf"),
        dry_run=False, answerer=answerer, filler=filler,
    ))

    assert record.status == MANUAL_INTERVENTION_STATUS
    assert record.error == WORKDAY_MANUAL_INTERVENTION_MESSAGE
    assert record.dry_run is False
    assert answerer.calls == 0
    assert filler.calls == 0


def test_direct_job_requires_manual_intervention_without_automation(tmp_path):
    job = _job("direct:google:123", "direct", board_token="google")
    answerer = TrackingAnswerer()
    filler = TrackingFiller()

    record = asyncio.run(apply_job(
        _match(job), job, settings=_settings(), resume_text="resume", resume_path=Path(tmp_path / "resume.pdf"),
        dry_run=False, answerer=answerer, filler=filler,
    ))

    assert record.status == MANUAL_INTERVENTION_STATUS
    assert record.error == DIRECT_MANUAL_INTERVENTION_MESSAGE
    assert answerer.calls == 0
    assert filler.calls == 0


def test_non_workday_job_keeps_automated_flow(tmp_path):
    job = _job("gh-1", "greenhouse")
    answerer = TrackingAnswerer()
    filler = TrackingFiller()

    record = asyncio.run(apply_job(
        _match(job), job, settings=_settings(), resume_text="resume", resume_path=Path(tmp_path / "resume.pdf"),
        dry_run=True, answerer=answerer, filler=filler,
    ))

    assert record.status == "dry_run"
    assert answerer.calls == 1
    assert filler.calls == 1


def test_manual_intervention_record_is_persisted_and_prevents_retry(tmp_path):
    db = Database(tmp_path / "test.db")
    job = _job("wd-1", "workday")
    match = _match(job)
    db.insert_jobs("scrape-run", [job])
    db.upsert_match(
        "match-run", job.job_id, job.board_token, job.title, 90, True, False, None,
        "scrape-run", match.scored_at.isoformat(), match.model_dump_json(),
    )
    store = AppliedStore(db=db)
    record = asyncio.run(apply_job(
        match, job, settings=_settings(), resume_text="resume", resume_path=Path(tmp_path / "resume.pdf"),
        dry_run=False, answerer=TrackingAnswerer(), filler=TrackingFiller(),
    ))
    store.append(record)

    persisted = db.load_applied()
    assert persisted[0]["status"] == MANUAL_INTERVENTION_STATUS
    assert persisted[0]["error"] == WORKDAY_MANUAL_INTERVENTION_MESSAGE
    assert load_shortlisted(store, run_id="match-run", db=db) == []


def test_direct_job_in_exclude_companies_reaches_manual_action_preflight(tmp_path):
    db = Database(tmp_path / "test.db")
    job = _job("direct:google:123", "direct", board_token="google")
    match = _match(job)
    db.insert_jobs("scrape-run", [job])
    db.upsert_match(
        "match-run", job.job_id, job.board_token, job.title, 90, True, False, None,
        "scrape-run", match.scored_at.isoformat(), match.model_dump_json(),
    )

    jobs = load_shortlisted(
        AppliedStore(db=db), run_id="match-run", db=db, exclude_companies=["google"],
    )

    assert [(mr.job_id, loaded_job.source) for mr, loaded_job in jobs] == [(job.job_id, "direct")]


def test_non_direct_excluded_company_remains_omitted(tmp_path):
    db = Database(tmp_path / "test.db")
    job = _job("gh-1", "greenhouse", board_token="google")
    match = _match(job)
    db.insert_jobs("scrape-run", [job])
    db.upsert_match(
        "match-run", job.job_id, job.board_token, job.title, 90, True, False, None,
        "scrape-run", match.scored_at.isoformat(), match.model_dump_json(),
    )

    assert load_shortlisted(
        AppliedStore(db=db), run_id="match-run", db=db, exclude_companies=["google"],
    ) == []


def test_dashboard_job_row_exposes_manual_intervention_status(tmp_path):
    db = Database(tmp_path / "test.db")
    job = _job("wd-1", "workday")
    db.record_pipeline_result("pipeline-run", {
        "job_id": job.job_id,
        "company": job.board_token,
        "title": job.title,
        "job_url": str(job.absolute_url),
        "relevance_score": 90,
        "tuner_status": "tuned",
    })
    db.record_applied(
        job.job_id, job.board_token, job.title, str(job.absolute_url),
        datetime.now(timezone.utc).isoformat(), MANUAL_INTERVENTION_STATUS, False, None,
        WORKDAY_MANUAL_INTERVENTION_MESSAGE,
    )

    rows = query_jobs(ReadDB(db.path), run_id="pipeline-run")

    assert rows[0].applied is True
    assert rows[0].applied_status == MANUAL_INTERVENTION_STATUS
