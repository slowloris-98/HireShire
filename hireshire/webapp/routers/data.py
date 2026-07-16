"""Read-only data endpoints: runs, the unified job list, applied history, resume PDFs."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from hireshire.webapp.deps import get_readdb
from hireshire.webapp.jobs_query import query_jobs, resolve_run_id
from hireshire.webapp.models import JobRow, RunsResponse

router = APIRouter(prefix="/api", tags=["data"])


@router.get("/runs", response_model=RunsResponse)
def get_runs() -> RunsResponse:
    db = get_readdb()
    return RunsResponse(run_ids=db.all_run_ids(), latest=db.latest_runs_by_phase())


@router.get("/jobs", response_model=list[JobRow])
def get_jobs(
    run_id: Optional[str] = None,
    min_score: Optional[int] = None,
    applied: Optional[bool] = None,
    q: Optional[str] = None,
    location: Optional[str] = None,
    job_ids: Optional[str] = Query(None, description="comma-separated job ids"),
    limit: Optional[int] = None,
) -> list[JobRow]:
    db = get_readdb()
    ids = [s for s in job_ids.split(",") if s] if job_ids else None
    return query_jobs(
        db, run_id=run_id, job_ids=ids, min_score=min_score,
        applied=applied, q=q, location=location, limit=limit,
    )


@router.get("/applied")
def get_applied() -> list[dict]:
    return get_readdb().load_applied()


@router.get("/runs/{run_id}/summary")
def get_run_summary(run_id: str) -> dict:
    db = get_readdb()
    return {"run_id": run_id, "phases": db.run_summary(run_id), "counts": db.run_counts(run_id)}


@router.get("/resume/{run_id}/{job_id}")
def get_resume(run_id: str, job_id: str) -> FileResponse:
    """Stream the tuned resume PDF for a job (from tuned_jobs / data/tuned/...)."""
    db = get_readdb()
    paths = db.tuned_paths(run_id, job_id)
    pdf = (paths or {}).get("resume_pdf_path")
    if not pdf or not Path(pdf).exists():
        raise HTTPException(status_code=404, detail="No tuned resume PDF for this job.")
    return FileResponse(pdf, media_type="application/pdf", filename=Path(pdf).name)
