"""Tools the chat agent can call.

Read tools query the datastore directly (read-only). Run tools are
confirmation-gated: they never launch a subprocess — they return a proposal the
UI renders with a Confirm button, and the actual start goes through
POST /api/runs/{phase} only after the user clicks Confirm.

`search_jobs` / `get_top_matches` results are also mirrored into the bottom-right
job-list panel: the chat streamer reads the `job_ids` out of their JSON output.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from langchain_core.tools import tool

from hireshire.webapp.config_spec import PHASE_SPECS
from hireshire.webapp.deps import get_readdb
from hireshire.webapp.jobs_query import query_jobs, resolve_run_id

# Tool names whose output carries job_ids to push into the job-list panel.
JOB_RESULT_TOOLS = {"search_jobs", "get_top_matches"}
RUN_PROPOSAL_TOOLS = {"run_phase", "stop_phase"}


def _jobs_payload(rows) -> str:
    return json.dumps({
        "job_ids": [r.job_id for r in rows],
        "count": len(rows),
        "jobs": [
            {
                "job_id": r.job_id, "title": r.title, "company": r.company,
                "score": r.relevance_score, "applied": r.applied,
                "resume": r.resume_available, "url": r.job_url,
            }
            for r in rows
        ],
    })


@tool
def search_jobs(query: str = "", run_id: str = "", min_score: int = 0,
                applied: str = "any", limit: int = 20) -> str:
    """Search the current run's shortlisted/tuned jobs and show them in the job panel.

    Args:
        query: substring to match against job title or company (empty = all).
        run_id: specific run id, or empty for the latest run.
        min_score: minimum relevance score (0-100).
        applied: 'yes', 'no', or 'any' to filter by application status.
        limit: max number of jobs to return.
    """
    db = get_readdb()
    applied_flag = {"yes": True, "no": False}.get(applied.lower())
    rows = query_jobs(
        db, run_id=run_id or None, min_score=min_score or None,
        applied=applied_flag, q=query or None, limit=limit,
    )
    return _jobs_payload(rows)


@tool
def get_top_matches(when: str = "latest", n: int = 10) -> str:
    """Return the top N highest-scoring jobs and show them in the job panel.

    Args:
        when: 'latest' for the most recent run, 'today', 'yesterday', or a run id.
        n: how many top jobs to return.
    """
    db = get_readdb()
    run_id = None
    when_l = when.lower()
    if when_l in ("today", "yesterday"):
        target = datetime.now(timezone.utc).date()
        if when_l == "yesterday":
            target -= timedelta(days=1)
        prefix = target.isoformat()  # run_ids start with YYYY-MM-DD
        candidates = [r for r in db.all_run_ids() if r.startswith(prefix)]
        if not candidates:
            return json.dumps({"job_ids": [], "count": 0, "jobs": [],
                               "note": f"No pipeline/match run found for {when_l} ({prefix})."})
        run_id = candidates[0]
    elif when_l != "latest":
        run_id = when
    rows = query_jobs(db, run_id=run_id, limit=n)
    return _jobs_payload(rows)


@tool
def run_stats(run_id: str = "") -> str:
    """Get counts (jobs, matches, shortlisted, tuned) for a run. Empty = latest run."""
    db = get_readdb()
    resolved = resolve_run_id(db, run_id or None)
    if not resolved:
        return json.dumps({"note": "No runs found yet."})
    return json.dumps(db.run_counts(resolved))


@tool
def list_runs(limit: int = 10) -> str:
    """List the most recent run ids and the latest run per phase."""
    db = get_readdb()
    return json.dumps({
        "recent_runs": db.all_run_ids()[:limit],
        "latest_by_phase": db.latest_runs_by_phase(),
    })


@tool
def explain_config(phase: str = "", key: str = "") -> str:
    """Explain what config settings mean.

    Args:
        phase: one of scraper, matcher, funnel, tuner, applier (empty = all phases).
        key: a specific setting name (empty = every exposed setting for the phase).
    """
    phases = [phase] if phase else list(PHASE_SPECS)
    out: dict = {}
    for ph in phases:
        spec = PHASE_SPECS.get(ph)
        if not spec:
            out[ph] = "Unknown phase."
            continue
        fields = {key: spec.fields[key]} if key and key in spec.fields else spec.fields
        out[ph] = {name: fs.doc for name, fs in fields.items()}
    return json.dumps(out)


@tool
def run_phase(phase: str, once: bool = True, no_llm: bool = False,
              apply: bool = False, dry_run: bool = False) -> str:
    """Propose starting a pipeline phase. Requires the user to Confirm in the UI.

    Does NOT start anything itself. Use for 'run the scraper', 'kick off the
    pipeline', etc. Tell the user you've prepared the run and they should confirm.

    Args:
        phase: scraper, matcher, tuner, applier, or orchestrator.
        once: (orchestrator) run one cycle and stop instead of scheduling.
        no_llm: (orchestrator) skip LLM scoring in the matcher.
        apply: (orchestrator) run the applier after tuning.
        dry_run: (applier) fill forms but do not submit.
    """
    flags = {"once": once, "no_llm": no_llm, "apply": apply, "dry_run": dry_run}
    return json.dumps({"action": "run", "phase": phase, "flags": flags})


@tool
def stop_phase(phase: str) -> str:
    """Propose stopping a running phase. Requires the user to Confirm in the UI.

    Args:
        phase: scraper, matcher, tuner, applier, or orchestrator.
    """
    return json.dumps({"action": "stop", "phase": phase, "flags": {}})


ALL_TOOLS = [
    search_jobs, get_top_matches, run_stats, list_runs,
    explain_config, run_phase, stop_phase,
]
