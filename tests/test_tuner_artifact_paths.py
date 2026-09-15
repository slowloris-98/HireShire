"""Artifact paths must work for namespaced job IDs on Windows."""
from __future__ import annotations

from hireshire.storage.db import Database
from hireshire.tuner.store import TuneStore


def test_namespaced_job_id_uses_a_windows_safe_artifact_directory(tmp_path):
    store = TuneStore(tmp_path / "tuned", "run-1", db=Database(tmp_path / "test.db"))

    job_dir = store.job_dir("direct:intuit:24119")
    job_dir.mkdir()
    (job_dir / "Udayan_Atreya_Resume.tex").write_text("resume", encoding="utf-8")

    assert ":" not in job_dir.name
    assert store.is_done("direct:intuit:24119")


def test_safe_job_id_keeps_its_existing_artifact_directory_name(tmp_path):
    store = TuneStore(tmp_path / "tuned", "run-1", db=Database(tmp_path / "test.db"))

    assert store.job_dir("JR-123").name == "JR-123"
