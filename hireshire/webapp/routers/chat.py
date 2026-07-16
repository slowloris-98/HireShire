"""Chat endpoint — streams the LangGraph agent over SSE.

Event types emitted to the client:
  token         assistant text delta
  tool_call     the agent invoked a tool (name only, for transparency)
  job_results   search results -> populate the bottom-right job-list panel
  run_proposal  a confirmation-gated run/stop the UI renders with a Confirm button
  done / error  stream terminators
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from sse_starlette.sse import EventSourceResponse

from hireshire.webapp.agent.tools import JOB_RESULT_TOOLS, RUN_PROPOSAL_TOOLS
from hireshire.webapp.models import ChatRequest

router = APIRouter(prefix="/api", tags=["chat"])


def _text(chunk) -> str:
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return ""


def _to_lc_messages(req: ChatRequest) -> list:
    msgs = []
    for m in req.history:
        role, content = m.get("role"), m.get("content", "")
        if role == "user":
            msgs.append(HumanMessage(content))
        elif role == "assistant":
            msgs.append(AIMessage(content))
    msgs.append(HumanMessage(req.message))
    return msgs


async def _stream(req: ChatRequest, request: Request):
    # Import lazily so a missing API key surfaces as a chat error, not a boot failure.
    from hireshire.webapp.agent.graph import get_agent

    try:
        agent = get_agent()
    except Exception as exc:  # noqa: BLE001
        yield {"event": "error", "data": f"Agent init failed: {exc}"}
        return

    inputs = {"messages": _to_lc_messages(req)}
    try:
        async for mode, payload in agent.astream(inputs, stream_mode=["messages", "updates"]):
            if await request.is_disconnected():
                break
            if mode == "messages":
                chunk, _meta = payload
                # Only stream assistant text — tool messages also surface here in
                # "messages" mode and must not leak into the chat bubble.
                if isinstance(chunk, AIMessageChunk):
                    text = _text(chunk)
                    if text:
                        yield {"event": "token", "data": text}
            elif mode == "updates":
                for _node, update in payload.items():
                    for msg in update.get("messages", []) if isinstance(update, dict) else []:
                        if isinstance(msg, AIMessage):
                            for call in getattr(msg, "tool_calls", []) or []:
                                yield {"event": "tool_call",
                                       "data": json.dumps({"name": call.get("name")})}
                        elif isinstance(msg, ToolMessage):
                            name = getattr(msg, "name", "")
                            content = msg.content if isinstance(msg.content, str) else str(msg.content)
                            if name in JOB_RESULT_TOOLS:
                                yield {"event": "job_results", "data": content}
                            elif name in RUN_PROPOSAL_TOOLS:
                                yield {"event": "run_proposal", "data": content}
        yield {"event": "done", "data": ""}
    except Exception as exc:  # noqa: BLE001
        yield {"event": "error", "data": str(exc)}


@router.post("/chat")
async def chat(req: ChatRequest, request: Request) -> EventSourceResponse:
    return EventSourceResponse(_stream(req, request))
