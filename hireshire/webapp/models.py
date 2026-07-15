"""Pydantic request/response schemas shared across the dashboard routers."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel

Phase = Literal["scraper", "matcher", "funnel", "tuner", "applier"]
RunPhase = Literal["scraper", "matcher", "tuner", "applier", "orchestrator"]


class JobRow(BaseModel):
    job_id: str
    title: str
    company: Optional[str] = None
    location: Optional[str] = None
    job_url: Optional[str] = None
    relevance_score: Optional[int] = None
    resume_pdf: Optional[str] = None          # tuned PDF path, if present
    resume_available: bool = False
    run_id: Optional[str] = None
    tuner_status: Optional[str] = None
    applied: bool = False
    applied_status: Optional[str] = None       # submitted / dry_run / error / skipped


class RunsResponse(BaseModel):
    run_ids: list[str]
    latest: dict[str, Optional[str]]           # phase -> run_id


class ConfigResponse(BaseModel):
    phase: str
    values: dict[str, Any]
    docs: dict[str, str]                        # field key -> human description
    types: dict[str, str] = {}                  # field key -> UI type hint
    options: dict[str, list[str]] = {}          # field key -> enum options


class ConfigPatch(BaseModel):
    values: dict[str, Any]


class RunRequest(BaseModel):
    flags: dict[str, Any] = {}                 # phase-appropriate flags


class RunState(BaseModel):
    phase: str
    running: bool
    pid: Optional[int] = None
    started_at: Optional[str] = None
    last_exit: Optional[int] = None
    argv: Optional[list[str]] = None


class ChatRequest(BaseModel):
    message: str
    history: list[dict[str, str]] = []         # [{role, content}]
