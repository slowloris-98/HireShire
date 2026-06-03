from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from hireshire.tuner.evaluator import EvaluatorResult

logger = logging.getLogger(__name__)

_TEX_NAME = "Udayan_Atreya_Resume"


class TuneStore:
    def __init__(self, base_dir: Path, run_id: str) -> None:
        self.run_dir = base_dir / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
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

        self._tuned_count += 1
        logger.info("Saved tuned resume for job %s → %s", job_id, job_dir)
        return job_dir

    def compile_pdf(self, job_dir: Path) -> int:
        """Compile Udayan_Atreya_Resume.tex in job_dir. Returns page count, 0 on failure."""
        return self._compile_pdf(job_dir)

    def update_tex(self, job_dir: Path, optimized_tex: str) -> None:
        """Overwrite the tex file in job_dir for a trim retry."""
        (job_dir / f"{_TEX_NAME}.tex").write_text(optimized_tex, encoding="utf-8")

    def _compile_pdf(self, job_dir: Path) -> int:
        pdflatex = shutil.which("pdflatex")
        if not pdflatex:
            logger.warning("pdflatex not found on PATH — skipping PDF compilation")
            return 0
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
            return 0
        finally:
            for ext in (".aux", ".log", ".out"):
                (job_dir / f"{_TEX_NAME}{ext}").unlink(missing_ok=True)
        if result.returncode != 0:
            logger.warning(
                "pdflatex failed in %s (exit %d):\n%s",
                job_dir, result.returncode, result.stdout[-800:],
            )
            return 0
        m = re.search(r"Output written on .+? \((\d+) page", result.stdout)
        pages = int(m.group(1)) if m else 1
        logger.info("PDF compiled → %s (%d page(s))", job_dir / f"{_TEX_NAME}.pdf", pages)
        return pages

    def record_skip(self) -> None:
        self._skipped_count += 1

    def record_error(self) -> None:
        self._error_count += 1

    def save_manifest(
        self,
        started_at: datetime,
        source_run_id: str,
        model: str,
        provider: str,
        total_loaded: int,
    ) -> None:
        manifest = {
            "run_id": self.run_id,
            "source_matches_run_id": source_run_id,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
            "model": model,
            "total_jobs_loaded": total_loaded,
            "tuned_count": self._tuned_count,
            "skipped_count": self._skipped_count,
            "error_count": self._error_count,
        }
        path = self.run_dir / "manifest.json"
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        logger.info(
            "Manifest saved: %d tuned, %d skipped, %d errors → %s",
            self._tuned_count, self._skipped_count, self._error_count, self.run_dir,
        )
