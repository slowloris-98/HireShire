from __future__ import annotations

import json
import logging
from pathlib import Path

from hireshire.matcher.scorer import MatchResult
from hireshire.models.job import Job

logger = logging.getLogger(__name__)


def latest_matches_run(matches_dir: Path) -> Path | None:
    runs = sorted(matches_dir.iterdir(), reverse=True) if matches_dir.exists() else []
    return runs[0] if runs else None


def load_shortlisted(
    matches_dir: Path,
    runs_dir: Path,
    run_id: str | None = None,
) -> list[tuple[MatchResult, Job]]:
    match_dir = (matches_dir / run_id) if run_id else latest_matches_run(matches_dir)
    if not match_dir or not match_dir.exists():
        logger.error("No matches run found in %s", matches_dir)
        return []

    shortlisted_path = match_dir / "shortlisted.json"
    if not shortlisted_path.exists():
        logger.warning("shortlisted.json not found in %s", match_dir)
        return []

    try:
        raw = json.loads(shortlisted_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("Could not read %s: %s", shortlisted_path, exc)
        return []

    job_cache: dict[str, dict[str, dict[str, Job]]] = {}
    results: list[tuple[MatchResult, Job]] = []

    for record in raw:
        try:
            match_result = MatchResult(**record)
        except Exception as exc:
            logger.warning("Skipping malformed match result: %s", exc)
            continue

        source_run_id = match_result.source_run_id
        board_token = match_result.board_token
        job_id = match_result.job_id

        if source_run_id not in job_cache:
            job_cache[source_run_id] = {}

        if board_token not in job_cache[source_run_id]:
            job_file = runs_dir / source_run_id / f"{board_token}.json"
            if job_file.exists():
                try:
                    raw_jobs = json.loads(job_file.read_text(encoding="utf-8"))
                    job_cache[source_run_id][board_token] = {j["job_id"]: Job(**j) for j in raw_jobs}
                except Exception as exc:
                    logger.warning("Could not load %s: %s", job_file, exc)
                    job_cache[source_run_id][board_token] = {}
            else:
                logger.warning(
                    "Original job file not found: %s — re-run the scraper to restore it",
                    job_file,
                )
                job_cache[source_run_id][board_token] = {}

        job = job_cache.get(source_run_id, {}).get(board_token, {}).get(job_id)
        if not job:
            logger.warning("Skipping %s/%s: original job data not found", board_token, job_id)
            continue

        results.append((match_result, job))

    logger.info("Loaded %d shortlisted jobs for tuning", len(results))
    return results
