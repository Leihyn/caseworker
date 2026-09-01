"""Session-managed Strands runtime: analyze, run, pause on approval, resume.

Two live paths:

- analyze_case: one bounded verification turn (no external effects) used by
  the UI's "Run live Strands verification" button.
- run_case_turn / resume_with_approval: the full interrupt loop. A turn that
  reaches send_external_message stops with stop_reason == "interrupt" and a
  payload hash; resume_with_approval answers it. Sessions persist the paused
  agent, so approval can arrive minutes or days later, across restarts.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Callable

from strands.session.file_session_manager import FileSessionManager

from .agents import AuditTrailHook, build_orchestrator

SESSIONS_DIR = os.environ.get("CASEWORKER_SESSIONS_DIR", ".sessions")


def _session_manager(session_id: str):
    if bucket := os.environ.get("CASEWORKER_SESSIONS_BUCKET"):
        from strands.session.s3_session_manager import S3SessionManager

        return S3SessionManager(session_id=session_id, bucket=bucket, prefix="caseworker/")
    return FileSessionManager(session_id=session_id, storage_dir=SESSIONS_DIR)


@dataclass
class AgentTurn:
    stop_reason: str
    message: str
    invocation_id: str
    tool_calls: list[dict[str, str]]
    pending_approval: dict[str, Any] | None
    interrupt_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_case(get_case: Callable[[], dict[str, Any]], question: str) -> dict[str, Any]:
    """One live verification turn. Fresh session, no persistence, no sends."""
    hook = AuditTrailHook()
    agent = build_orchestrator(get_case, session_manager=None, audit_hook=hook)
    result = agent(
        f"{question}\n\nDo NOT call send_external_message in this verification turn; "
        "analysis only."
    )
    return {
        "answer": _text(result),
        "invocation_id": f"strands-{uuid.uuid4().hex[:12]}",
        "specialists_invoked": [e["tool"] for e in hook.entries],
        "model": os.environ.get("CASEWORKER_MODEL", "global.anthropic.claude-sonnet-4-6"),
    }


def run_case_turn(case_id: str, get_case: Callable[[], dict[str, Any]], prompt: str) -> AgentTurn:
    """Run one full orchestrator turn under a durable session."""
    hook = AuditTrailHook()
    agent = build_orchestrator(get_case, session_manager=_session_manager(case_id), audit_hook=hook)
    return _to_turn(agent(prompt), hook)


def resume_with_approval(
    case_id: str,
    get_case: Callable[[], dict[str, Any]],
    interrupt_id: str,
    approved_hash: str | None,
) -> AgentTurn:
    """Answer the pending approval interrupt. approved_hash=None declines."""
    hook = AuditTrailHook()
    agent = build_orchestrator(get_case, session_manager=_session_manager(case_id), audit_hook=hook)
    responses = [
        {
            "interruptResponse": {
                "interruptId": interrupt_id,
                "response": {"approved_hash": approved_hash},
            }
        }
    ]
    return _to_turn(agent(responses), hook)


def _text(result: Any) -> str:
    message = getattr(result, "message", None)
    if isinstance(message, dict):
        parts = message.get("content", [])
        return "\n".join(p.get("text", "") for p in parts if isinstance(p, dict) and "text" in p)
    return str(message)


def _to_turn(result: Any, hook: AuditTrailHook) -> AgentTurn:
    interrupts = getattr(result, "interrupts", None) or []
    if getattr(result, "stop_reason", None) == "interrupt" and interrupts:
        first = interrupts[0]
        return AgentTurn(
            stop_reason="interrupt",
            message="",
            invocation_id=f"strands-{uuid.uuid4().hex[:12]}",
            tool_calls=hook.entries,
            pending_approval=first.reason,
            interrupt_id=first.id,
        )
    return AgentTurn(
        stop_reason=str(getattr(result, "stop_reason", "end_turn")),
        message=_text(result),
        invocation_id=f"strands-{uuid.uuid4().hex[:12]}",
        tool_calls=hook.entries,
        pending_approval=None,
        interrupt_id=None,
    )
