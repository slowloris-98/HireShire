"""Pytest wrapper around the projects_bullets.yaml lint (run from the repo root)."""
from hireshire.tuner.lint import lint


def test_no_lint_errors():
    errors, _warnings = lint()
    assert not errors, "projects_bullets.yaml lint errors:\n" + "\n".join(errors)
