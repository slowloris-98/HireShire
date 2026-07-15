"""Per-phase subprocess manager for the dashboard's run controls.

Launches the existing phase entrypoint scripts (scraper.py, matcher.py,
tuner.py, applier.py, orchestrate.py) as tracked subprocesses, one at a time
per phase, redirecting output to logs/<phase>.log for the live tail.
"""
from __future__ import annotations

import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = PROJECT_ROOT / "logs"

# UI phase -> entrypoint script
_SCRIPTS = {
    "scraper": "scraper.py",
    "matcher": "matcher.py",
    "tuner": "tuner.py",
    "applier": "applier.py",
    "orchestrator": "orchestrate.py",
}


def _build_argv(phase: str, flags: dict[str, Any]) -> list[str]:
    """Translate a flags dict into CLI args for the phase script."""
    argv = [sys.executable, _SCRIPTS[phase]]
    if phase == "orchestrator":
        if flags.get("once", True):
            argv.append("--once")
        elif flags.get("now"):
            argv.append("--now")
        if flags.get("interval"):
            argv += ["--interval", str(flags["interval"])]
        for name in ("no_tuner", "no_matcher", "no_llm", "apply"):
            if flags.get(name):
                argv.append("--" + name.replace("_", "-"))
    elif phase == "tuner":
        if flags.get("run_id"):
            argv += ["--run-id", str(flags["run_id"])]
        if flags.get("job_id"):
            argv += ["--job-id", str(flags["job_id"])]
        if flags.get("force"):
            argv.append("--force")
    elif phase == "applier":
        if flags.get("dry_run"):
            argv.append("--dry-run")
        if flags.get("run_id"):
            argv += ["--run-id", str(flags["run_id"])]
    return argv


class _Proc:
    def __init__(self, popen: subprocess.Popen, argv: list[str], log_path: Path) -> None:
        self.popen = popen
        self.argv = argv
        self.log_path = log_path
        self.started_at = datetime.now(timezone.utc).isoformat()


class RunManager:
    def __init__(self) -> None:
        self._procs: dict[str, _Proc] = {}
        self._last_exit: dict[str, int] = {}
        self._lock = threading.Lock()

    def log_path(self, phase: str) -> Path:
        return LOG_DIR / f"{phase}.log"

    def is_running(self, phase: str) -> bool:
        p = self._procs.get(phase)
        return p is not None and p.popen.poll() is None

    def start(self, phase: str, flags: dict[str, Any]) -> dict:
        if phase not in _SCRIPTS:
            raise ValueError(f"Unknown phase '{phase}'.")
        with self._lock:
            if self.is_running(phase):
                raise RuntimeError(f"{phase} is already running.")
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            argv = _build_argv(phase, flags)
            log_path = self.log_path(phase)
            header = f"\n{'=' * 70}\n$ {' '.join(argv)}  @ {datetime.now().isoformat()}\n{'=' * 70}\n"
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(header)
            logf = open(log_path, "a", encoding="utf-8", buffering=1)
            popen = subprocess.Popen(
                argv, cwd=str(PROJECT_ROOT),
                stdout=logf, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self._procs[phase] = _Proc(popen, argv, log_path)
            return self.status(phase)

    def stop(self, phase: str) -> dict:
        with self._lock:
            p = self._procs.get(phase)
            if p and p.popen.poll() is None:
                p.popen.terminate()
                try:
                    p.popen.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    p.popen.kill()
                self._last_exit[phase] = p.popen.returncode if p.popen.returncode is not None else -1
            return self.status(phase)

    def status(self, phase: str) -> dict:
        p = self._procs.get(phase)
        running = self.is_running(phase)
        if p and not running and p.popen.returncode is not None:
            self._last_exit[phase] = p.popen.returncode
        return {
            "phase": phase,
            "running": running,
            "pid": p.popen.pid if p and running else None,
            "started_at": p.started_at if p and running else None,
            "last_exit": self._last_exit.get(phase),
            "argv": p.argv if p else None,
        }

    def status_all(self) -> list[dict]:
        return [self.status(phase) for phase in _SCRIPTS]


run_manager = RunManager()
