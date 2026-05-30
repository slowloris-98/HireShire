from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from hireshire.matcher.scorer import MatchResult

logger = logging.getLogger(__name__)


class MatchStore:
    def __init__(self, base_dir: Path, run_id: str):
        self.run_dir = base_dir / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self._progress_path = self.run_dir / "progress.jsonl"

    def append_result(self, result: MatchResult) -> None:
        """Write one result immediately — survives a mid-run crash."""
        with self._progress_path.open("a", encoding="utf-8") as f:
            f.write(result.model_dump_json() + "\n")

    def finalise(
        self,
        shortlisted: list[MatchResult],
        rejected: list[MatchResult],
        started_at: datetime,
        threshold: int,
        model: str,
        total_loaded: int,
    ) -> None:
        self._write("shortlisted.json", shortlisted)
        self._write("rejected.json", rejected)
        self._write_manifest(shortlisted, rejected, started_at, threshold, model, total_loaded)
        # progress.jsonl is now superseded by the final files
        self._progress_path.unlink(missing_ok=True)
        logger.info(
            "Saved %d shortlisted, %d rejected to %s",
            len(shortlisted), len(rejected), self.run_dir,
        )

    def _write(self, filename: str, results: list[MatchResult]) -> None:
        path = self.run_dir / filename
        path.write_text(
            json.dumps([r.model_dump(mode="json") for r in results], indent=2, default=str),
            encoding="utf-8",
        )

    def _write_manifest(
        self,
        shortlisted: list[MatchResult],
        rejected: list[MatchResult],
        started_at: datetime,
        threshold: int,
        model: str,
        total_loaded: int,
    ) -> None:
        skipped = [r for r in rejected if r.skipped]
        scored = total_loaded - len(skipped)

        manifest = {
            "run_id": self.run_id,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "threshold": threshold,
            "model": model,
            "total_jobs_loaded": total_loaded,
            "total_jobs_scored": scored,
            "total_jobs_skipped": len(skipped),
            "shortlisted_count": len(shortlisted),
            "rejected_count": len(rejected) - len(skipped),
        }
        path = self.run_dir / "manifest.json"
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")