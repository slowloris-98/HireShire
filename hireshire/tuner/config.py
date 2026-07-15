from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class TunerSettings(BaseModel):
    enable_tuner: bool = True  # dashboard/orchestrator default: run the tuner phase
    resume_tex_path: str = "data/resume_projects/Udayan_Resume.tex"
    resume_template_path: str = "data/resume_projects/resume_template.tex"
    projects_bullets_path: str = "data/resume_projects/projects_bullets.yaml"
    projects_path: str = "data/resume_projects/projects.md"
    matches_dir: str = "data/matches"
    runs_dir: str = "data/scraped"
    tuned_dir: str = "data/tuned"
    db_path: str = "data/hireshire.db"
    # Shared defaults — used when per-pass overrides are not set
    model: str = "claude-sonnet-4-6"
    # Per-pass overrides (None = fall back to LLM_PROVIDER env var + model above)
    evaluator_provider: str | None = None
    evaluator_model: str | None = None
    optimizer_provider: str | None = None
    optimizer_model: str | None = None
    max_jd_chars: int = 12000
    max_tex_chars: int = 15000
    request_interval_s: float = 5.0
    claude_cli_timeout_s: float = 600.0


class TunerConfig(BaseModel):
    settings: TunerSettings


def load_tuner_config(path: str | Path = "config/tuner.yaml") -> TunerConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return TunerConfig(settings=TunerSettings(**raw.get("settings", {})))
