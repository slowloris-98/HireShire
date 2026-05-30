from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class MatcherSettings(BaseModel):
    threshold: int = 70
    concurrency: int = 1
    model: str = "gemini-2.0-flash"
    max_content_chars: int = 8000
    resume_path: str = "resume.pdf"
    runs_dir: str = "data/runs"
    matches_dir: str = "data/matches"
    request_interval_s: float = 13.0  # min seconds between requests; 13s = ~4.6 RPM (safe for 5 RPM free tier)


class MatcherConfig(BaseModel):
    settings: MatcherSettings


def load_matcher_config(path: str | Path = "config/matcher.yaml") -> MatcherConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return MatcherConfig(settings=MatcherSettings(**raw.get("settings", {})))
