from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class SeenStore:
    """Persistent set of job IDs already scored by the matcher, stored as a JSON list."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._ids: set[str] = set()
        if path.exists():
            try:
                self._ids = set(json.loads(path.read_text(encoding="utf-8")))
                logger.info("SeenStore: %d previously scored job IDs loaded", len(self._ids))
            except Exception:
                logger.warning("SeenStore: failed to load %s — starting fresh", path)

    def __contains__(self, job_id: str) -> bool:
        return job_id in self._ids

    def add(self, job_id: str) -> None:
        self._ids.add(job_id)

    def save(self) -> None:
        """Atomically write the seen set to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(self._ids), indent=2), encoding="utf-8")
        tmp.replace(self._path)
        logger.info("SeenStore: %d job IDs saved → %s", len(self._ids), self._path)
