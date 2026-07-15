"""Unified job-list query — the single source both the /api/jobs endpoint and the
chat agent's search tools use to build the bottom-right job-list panel rows.

A run's rows come from `pipeline_results` when present (they carry the tuned
resume path + tuner status), otherwise from shortlisted `matches`. Applied
status is layered on from the cross-run `applied` table.
"""
from __future__ import annotations

from typing import Optional

from hireshire.webapp.deps import ReadDB
from hireshire.webapp.models import JobRow
from hireshire.storage.db import PHASE_MATCH, PHASE_PIPELINE


def resolve_run_id(db: ReadDB, run_id: Optional[str]) -> Optional[str]:
    """Pick the run to show: explicit id, else latest pipeline, else latest match."""
    if run_id:
        return run_id
    return db.latest_run(PHASE_PIPELINE) or db.latest_run(PHASE_MATCH)


def _rows_for_run(db: ReadDB, run_id: str) -> list[JobRow]:
    applied = db.applied_by_id()
    locations = db.job_locations(run_id)
    pipeline = db.load_pipeline_results(run_id)

    rows: list[JobRow] = []
    if pipeline:
        for r in pipeline:
            jid = r["job_id"]
            app = applied.get(jid)
            rows.append(JobRow(
                job_id=jid,
                title=r.get("title") or "",
                company=r.get("company"),
                location=locations.get(jid),
                job_url=r.get("job_url"),
                relevance_score=r.get("relevance_score"),
                resume_pdf=r.get("resume_pdf"),
                resume_available=bool(r.get("resume_pdf")),
                run_id=run_id,
                tuner_status=r.get("tuner_status"),
                applied=app is not None,
                applied_status=(app or {}).get("status"),
            ))
    else:
        for m in db.load_shortlisted(run_id):
            jid = str(m.get("job_id"))
            app = applied.get(jid)
            rows.append(JobRow(
                job_id=jid,
                title=m.get("title") or "",
                company=m.get("board_token"),
                location=locations.get(jid) or m.get("location"),
                job_url=m.get("absolute_url"),
                relevance_score=m.get("relevance_score"),
                resume_available=False,
                run_id=run_id,
                applied=app is not None,
                applied_status=(app or {}).get("status"),
            ))
    return rows


def query_jobs(
    db: ReadDB,
    *,
    run_id: Optional[str] = None,
    job_ids: Optional[list[str]] = None,
    min_score: Optional[int] = None,
    applied: Optional[bool] = None,
    q: Optional[str] = None,
    location: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[JobRow]:
    resolved = resolve_run_id(db, run_id)
    if not resolved:
        return []
    rows = _rows_for_run(db, resolved)

    if job_ids is not None:
        wanted = {str(j) for j in job_ids}
        rows = [r for r in rows if r.job_id in wanted]
    if min_score is not None:
        rows = [r for r in rows if (r.relevance_score or 0) >= min_score]
    if applied is not None:
        rows = [r for r in rows if r.applied == applied]
    if q:
        needle = q.lower()
        rows = [r for r in rows
                if needle in (r.title or "").lower()
                or needle in (r.company or "").lower()]
    if location:
        loc = location.lower()
        rows = [r for r in rows if loc in (r.location or "").lower()]

    rows.sort(key=lambda r: (r.relevance_score or 0), reverse=True)
    if limit is not None:
        rows = rows[:limit]
    return rows
