from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

from hireshire.applier.store import AppliedStore
from hireshire.matcher.scorer import MatchResult
from hireshire.models.job import Job
from hireshire.storage.db import PHASE_MATCH, Database, get_db

logger = logging.getLogger(__name__)


def load_shortlisted(
    store: AppliedStore,
    run_id: str | None = None,
    db: Optional[Database] = None,
) -> list[tuple[MatchResult, Job]]:
    """Load shortlisted (MatchResult, Job) pairs to apply to, skipping jobs that
    were already applied. Reads from the shared database."""
    db = db or get_db()
    if run_id is None:
        run_id = db.latest_run(PHASE_MATCH)
    if not run_id:
        logger.error("No matches run found in the database")
        return []

    raw = db.load_shortlisted(run_id)
    if not raw:
        logger.warning("No shortlisted matches for run %s", run_id)
        return []

    matches: list[MatchResult] = []
    ids_by_run: dict[str, list[str]] = defaultdict(list)
    for record in raw:
        try:
            mr = MatchResult(**record)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping malformed match result: %s", exc)
            continue
        if store.is_applied(mr.job_id):
            logger.info("Skipping already-applied job %s (%s)", mr.job_id, mr.title)
            continue
        matches.append(mr)
        ids_by_run[mr.source_run_id].append(mr.job_id)

    job_cache: dict[str, dict[str, Job]] = {
        src: db.get_jobs(src, ids) for src, ids in ids_by_run.items()
    }

    results: list[tuple[MatchResult, Job]] = []
    for mr in matches:
        job = job_cache.get(mr.source_run_id, {}).get(mr.job_id)
        if not job:
            logger.warning("Skipping %s/%s: original job data not found", mr.board_token, mr.job_id)
            continue
        results.append((mr, job))

    logger.info("Loaded %d shortlisted jobs to apply (skipped already-applied)", len(results))
    return results
