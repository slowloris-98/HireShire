"""Lint the pre-authored resume bullet corpus (projects_bullets.yaml).

These checks are properties of the *source data*, not of any single tune run — bullets are emitted
verbatim, so validating once here is cheaper than re-checking every job. Errors fail; the
hard-number check is advisory (the corpus intentionally keeps a few number-free credibility bullets,
e.g. the Accenture ACE Award bullet).

Run standalone:   python -m hireshire.tuner.lint [path/to/projects_bullets.yaml]
Or via pytest:    tests/test_projects_bullets.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

DEFAULT_PATH = Path("data/resume_projects/projects_bullets.yaml")

# Dash-as-punctuation: spaced double hyphen or a unicode em/en dash. NOT `---` (a deliberate
# typographic em-dash the authored stat bullets use) and NOT `--` inside `\hfill` date ranges
# (those live in headers, not bullets).
_DASH_PUNCT = re.compile(r" -- |—|–")
_HAS_NUMBER = re.compile(r"\d")


def _iter_bullets(data: dict):
    """Yield (label, bullet_text) for every bullet and catch bullet in the corpus."""
    for p in data.get("projects", []):
        pid = p.get("id", "?")
        for i, b in enumerate(p.get("bullets", [])):
            yield f"{pid}[{i}]", b
        for key, b in (p.get("catch_bullets") or {}).items():
            yield f"{pid}.catch.{key}", b


def lint(path: str | Path = DEFAULT_PATH) -> tuple[list[str], list[str]]:
    """Return (errors, warnings)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    errors: list[str] = []
    warnings: list[str] = []

    # 1. No dash-as-punctuation in any bullet or catch bullet.
    for label, b in _iter_bullets(data):
        if _DASH_PUNCT.search(b):
            errors.append(f"{label}: dash used as punctuation -> {b[:70]!r}")

    # 2. Accenture opening verbs must be unique.
    projects = {p.get("id"): p for p in data.get("projects", [])}
    acc = projects.get("accenture")
    if acc:
        seen: dict[str, list[int]] = {}
        for i, b in enumerate(acc.get("bullets", [])):
            words = b.split()
            verb = words[0].rstrip(",").lower() if words else ""
            seen.setdefault(verb, []).append(i)
        for verb, idxs in seen.items():
            if len(idxs) > 1:
                errors.append(f"accenture: opening verb '{verb}' repeated at bullets {idxs}")

    # 3. Summaries must avoid dash punctuation too.
    for key, s in (data.get("summaries") or {}).items():
        if _DASH_PUNCT.search(s):
            errors.append(f"summary[{key}]: dash used as punctuation")

    # 4. Advisory: every bullet should carry a hard number.
    for label, b in _iter_bullets(data):
        if not _HAS_NUMBER.search(b):
            warnings.append(f"{label}: no hard number -> {b[:70]!r}")

    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    path = Path(argv[0]) if argv else DEFAULT_PATH
    errors, warnings = lint(path)
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    if errors:
        print(f"\n{len(errors)} error(s), {len(warnings)} warning(s) — FAIL")
        return 1
    print(f"OK — 0 errors, {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
