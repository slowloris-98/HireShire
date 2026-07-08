from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from hireshire.storage.db import PHASE_TUNE, Database, get_db
from hireshire.tuner.evaluator import EvaluatorResult

logger = logging.getLogger(__name__)

_TEX_NAME = "Udayan_Atreya_Resume"


@dataclass
class PdfMetrics:
    """Geometry of a compiled resume PDF. `bottom_margin` is points from the page bottom to the
    lowest text on page 1 (smaller = fuller page); None when it could not be measured."""
    pages: int
    bottom_margin: float | None = None

    def __int__(self) -> int:  # backward-compat: callers that treated the result as a page count
        return self.pages


def _measure_pdf(pdf_path: Path) -> tuple[int | None, float | None]:
    """Return (page_count, bottom_margin_pts) via pdfminer.

    bottom_margin = distance in points from the bottom edge of page 1 to its lowest text element
    (smaller = fuller page). Returns (None, None) if pdfminer is unavailable or the read fails.
    """
    try:
        from pdfminer.high_level import extract_pages  # type: ignore[import-untyped]
    except ImportError:
        logger.debug("pdfminer not installed — bottom margin unavailable")
        return None, None
    try:
        pages = list(extract_pages(str(pdf_path)))
    except Exception as exc:  # corrupt/locked PDF — let caller fall back to stdout page count
        logger.warning("pdfminer failed to read %s: %s", pdf_path, exc)
        return None, None
    if not pages:
        return 0, None
    ys = [el.y0 for el in pages[0] if hasattr(el, "y0")]
    bottom_margin = min(ys) if ys else None
    return len(pages), bottom_margin


class TuneStore:
    """Tuner persistence: resume tex/PDF stay on disk under
    ``data/tuned/<run_id>/<job_id>/`` (genuine binary artifacts); per-job status,
    paths, and the critique are mirrored into the ``tuned_jobs`` DB table and the
    run summary into the ``runs`` table."""

    def __init__(self, base_dir: Path, run_id: str, db: Optional[Database] = None) -> None:
        self.run_dir = base_dir / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self._db = db or get_db()
        self._tuned_count = 0
        self._skipped_count = 0
        self._error_count = 0

    def is_done(self, job_id: str) -> bool:
        """True if the optimized tex already exists for this job (completion marker)."""
        return (self.run_dir / job_id / f"{_TEX_NAME}.tex").exists()

    def save_job(
        self,
        job_id: str,
        job_description: str,
        critique: EvaluatorResult,
        optimized_tex: str,
    ) -> Path:
        """
        Write all three artifacts for one job. Returns the job subdirectory.
        resume_optimized.tex is written last so is_done() only returns True
        when all files are present.
        """
        job_dir = self.run_dir / job_id
        job_dir.mkdir(exist_ok=True)

        (job_dir / "job_description.txt").write_text(job_description, encoding="utf-8")
        (job_dir / "critique.json").write_text(
            json.dumps(critique.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
        # Written last — is_done() checks for this file
        tex_path = job_dir / f"{_TEX_NAME}.tex"
        tex_path.write_text(optimized_tex, encoding="utf-8")

        self._db.record_tuned(
            self.run_id, job_id, "tuned",
            str(tex_path), str(job_dir / f"{_TEX_NAME}.pdf"),
            json.dumps(critique.model_dump(mode="json"), default=str),
        )
        self._tuned_count += 1
        logger.info("Saved tuned resume for job %s → %s", job_id, job_dir)
        return job_dir

    def save_rejection(
        self,
        job_id: str,
        job_description: str,
        critique: EvaluatorResult,
    ) -> Path:
        """Persist the audit trail for a job the evaluator rejected as incompatible.

        Writes job_description.txt + critique.json (which carries reject/reject_reason) but no
        tex — no resume is produced, so the job is not counted as tuned and is_done() stays False.
        """
        job_dir = self.run_dir / job_id
        job_dir.mkdir(exist_ok=True)
        (job_dir / "job_description.txt").write_text(job_description, encoding="utf-8")
        (job_dir / "critique.json").write_text(
            json.dumps(critique.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
        self._db.record_tuned(
            self.run_id, job_id, "rejected", None, None,
            json.dumps(critique.model_dump(mode="json"), default=str),
        )
        logger.info("Saved rejection critique for job %s → %s", job_id, job_dir)
        return job_dir

    def compile_pdf(self, job_dir: Path) -> PdfMetrics:
        """Compile Udayan_Atreya_Resume.tex in job_dir.

        Returns PdfMetrics(pages, bottom_margin). pages is 0 on failure. bottom_margin is measured
        via pdfminer when available (None otherwise). PdfMetrics is int()-comparable and exposes
        `.pages`, so `metrics.pages > 1` reads naturally in the fit loop.
        """
        return self._compile_pdf(job_dir)

    def update_tex(self, job_dir: Path, optimized_tex: str) -> None:
        """Overwrite the tex file in job_dir for a trim retry."""
        (job_dir / f"{_TEX_NAME}.tex").write_text(optimized_tex, encoding="utf-8")

    def _compile_pdf(self, job_dir: Path) -> PdfMetrics:
        pdflatex = shutil.which("pdflatex")
        if not pdflatex:
            logger.warning("pdflatex not found on PATH — skipping PDF compilation")
            return PdfMetrics(0)
        tex_name = f"{_TEX_NAME}.tex"
        try:
            result = subprocess.run(
                [pdflatex, "-interaction=nonstopmode", tex_name],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=job_dir,
            )
        except subprocess.TimeoutExpired:
            logger.warning("pdflatex timed out in %s", job_dir)
            return PdfMetrics(0)
        finally:
            for ext in (".aux", ".log", ".out"):
                (job_dir / f"{_TEX_NAME}{ext}").unlink(missing_ok=True)
        if result.returncode != 0:
            logger.warning(
                "pdflatex failed in %s (exit %d):\n%s",
                job_dir, result.returncode, result.stdout[-800:],
            )
            return PdfMetrics(0)

        pdf_path = job_dir / f"{_TEX_NAME}.pdf"
        pages, bottom_margin = _measure_pdf(pdf_path)
        if pages is None:  # pdfminer unavailable/failed — fall back to pdflatex stdout parse
            m = re.search(r"Output written on .+? \((\d+) page", result.stdout)
            pages = int(m.group(1)) if m else 1
        logger.info(
            "PDF compiled → %s (%d page(s), bottom margin %s)",
            pdf_path, pages, f"{bottom_margin:.1f}pt" if bottom_margin is not None else "n/a",
        )
        return PdfMetrics(pages, bottom_margin)

    def record_skip(self) -> None:
        self._skipped_count += 1

    def record_error(self) -> None:
        self._error_count += 1

    def finalise_run(
        self,
        started_at: datetime,
        source_run_id: str,
        model: str,
        provider: str,
        total_loaded: int,
    ) -> None:
        stats = {
            "source_matches_run_id": source_run_id,
            "provider": provider,
            "model": model,
            "total_jobs_loaded": total_loaded,
            "tuned_count": self._tuned_count,
            "skipped_count": self._skipped_count,
            "error_count": self._error_count,
        }
        self._db.finalise_run(self.run_id, PHASE_TUNE, started_at.isoformat(), None, stats)
        logger.info(
            "Tune run finalised: %d tuned, %d skipped, %d errors (run %s)",
            self._tuned_count, self._skipped_count, self._error_count, self.run_id,
        )
