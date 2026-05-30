from __future__ import annotations

import json
import logging
from pathlib import Path

from hireshire.models.job import Job

logger = logging.getLogger(__name__)


def load_jobs(run_dir: Path) -> list[Job]:
    seen: set[str] = set()
    jobs: list[Job] = []

    for path in sorted(run_dir.glob("*.json")):
        if path.name == "manifest.json":
            continue
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping %s: %s", path.name, exc)
            continue

        for record in records:
            try:
                job = Job(**record)
            except Exception as exc:
                logger.warning("Skipping malformed job record in %s: %s", path.name, exc)
                continue

            if job.job_id not in seen:
                seen.add(job.job_id)
                jobs.append(job)

    logger.info("Loaded %d unique jobs from %s", len(jobs), run_dir)
    return jobs
