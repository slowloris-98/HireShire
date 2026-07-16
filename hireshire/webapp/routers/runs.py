"""Run-control endpoints: start/stop any phase, poll status, tail logs over SSE."""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from hireshire.webapp.models import RunRequest
from hireshire.webapp.runner import _SCRIPTS, run_manager

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("/status")
def status_all() -> list[dict]:
    return run_manager.status_all()


@router.post("/{phase}")
def start_run(phase: str, req: RunRequest) -> dict:
    if phase not in _SCRIPTS:
        raise HTTPException(404, f"Unknown phase '{phase}'.")
    try:
        return run_manager.start(phase, req.flags)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@router.post("/{phase}/stop")
def stop_run(phase: str) -> dict:
    if phase not in _SCRIPTS:
        raise HTTPException(404, f"Unknown phase '{phase}'.")
    return run_manager.stop(phase)


async def _tail(path: Path, request: Request, phase: str):
    """Yield the last chunk of the log, then stream appended lines."""
    # Prime with the tail of the existing file so the UI has context immediately.
    last_size = 0
    if path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        tail = "\n".join(text.splitlines()[-80:])
        if tail:
            yield {"event": "log", "data": tail}
        last_size = path.stat().st_size

    while True:
        if await request.is_disconnected():
            break
        await asyncio.sleep(0.6)
        if not path.exists():
            continue
        size = path.stat().st_size
        if size > last_size:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(last_size)
                chunk = fh.read()
            last_size = size
            if chunk.strip():
                yield {"event": "log", "data": chunk.rstrip("\n")}
        # Surface run completion so the UI can flip the status badge.
        yield {"event": "status", "data": str(run_manager.is_running(phase)).lower()}


@router.get("/{phase}/logs")
async def stream_logs(phase: str, request: Request) -> EventSourceResponse:
    if phase not in _SCRIPTS:
        raise HTTPException(404, f"Unknown phase '{phase}'.")
    return EventSourceResponse(_tail(run_manager.log_path(phase), request, phase))
