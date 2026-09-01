"""Session-managed runtime: run the orchestrator, surface interrupts, resume.

The approval loop is the product: an external message can only leave the
system when a human answers the pending interrupt with the exact payload hash.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from strands.session.file_session_manager import FileSessionManager

from .agents import build_orchestrator

SESSIONS_DIR = os.environ.get("CASEWORKER_SESSIONS_DIR", ".sessions")


def _session_manager(case_id: str):
    if bucket := os.environ.get("CASEWORKER_SESSIONS_BUCKET"):
        from strands.session.s3_session_manager import S3SessionManager

        return S3SessionManager(session_id=case_id, bucket=bucket, prefix="caseworker/")
    return FileSessionManager(session_id=case_id, storage_dir=SESSIONS_DIR)


@dataclass
class AgentTurn:
    stop_reason: str
    message: str
    pending_approval: dict | None  # interrupt reason incl. payload_hash, or None
    interrupt_id: str | None


def run_turn(case_id: str, prompt: str) -> AgentTurn:
    """Run one orchestrator turn. If it pauses for approval, return the payload."""
    agent = build_orchestrator(session_manager=_session_manager(case_id))
    result = agent(prompt)
    return _to_turn(result)


def resume_with_approval(case_id: str, interrupt_id: str, approved_hash: str | None) -> AgentTurn:
    """Answer the pending approval interrupt. approved_hash=None means declined."""
    agent = build_orchestrator(session_manager=_session_manager(case_id))
    responses = [
        {
            "interruptResponse": {
                "interruptId": interrupt_id,
                "response": {"approved_hash": approved_hash} if approved_hash else {"approved_hash": None},
            }
        }
    ]
    result = agent(responses)
    return _to_turn(result)


def _to_turn(result) -> AgentTurn:
    if getattr(result, "stop_reason", None) == "interrupt" and result.interrupts:
        first = result.interrupts[0]
        return AgentTurn(
            stop_reason="interrupt",
            message="",
            pending_approval=first.reason,
            interrupt_id=first.id,
        )
    return AgentTurn(
        stop_reason=str(getattr(result, "stop_reason", "end_turn")),
        message=str(result.message),
        pending_approval=None,
        interrupt_id=None,
    )
