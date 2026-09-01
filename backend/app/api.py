"""HTTP API over the case service and the Strands runtime.

Two layers on purpose:

- /api/cases/* is the deterministic state machine (approve, execute, wake
  events). It runs with zero AWS credentials, so a judge can walk the full
  demo path locally.
- /api/agent/* is the live Strands path: a verification turn, and the full
  interrupt loop (run a turn, pause on send_external_message, resume with the
  approved hash).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .case_service import ApprovalRequiredError, CaseService, InvalidTransitionError
from .repository import CaseNotFoundError, create_repository

api = FastAPI(title="Caseworker", version="0.1.0")
api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

service = CaseService(create_repository())


class ApproveRequest(BaseModel):
    decided_by: str = "Maya Okeke"


class WakeEventRequest(BaseModel):
    event_id: str | None = None


class AnalyzeRequest(BaseModel):
    case_id: str = "CW-1042"
    question: str


class TurnRequest(BaseModel):
    case_id: str = "CW-1042"
    prompt: str


class ResumeRequest(BaseModel):
    case_id: str = "CW-1042"
    interrupt_id: str
    approved_hash: str | None = None


def _run(fn, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except CaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Case {exc.args[0]} was not found.") from exc
    except ApprovalRequiredError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@api.post("/api/cases/demo")
def reset_demo() -> dict[str, Any]:
    return service.reset_demo()


@api.get("/api/cases/{case_id}")
def get_case(case_id: str) -> dict[str, Any]:
    return _run(service.get_case, case_id)


@api.post("/api/cases/{case_id}/actions/approve")
def approve(case_id: str, body: ApproveRequest | None = None) -> dict[str, Any]:
    decided_by = body.decided_by if body else "Maya Okeke"
    return _run(service.approve_current_action, case_id, decided_by)


@api.post("/api/cases/{case_id}/actions/execute")
def execute(case_id: str) -> dict[str, Any]:
    return _run(service.execute_current_action, case_id)


@api.post("/api/cases/{case_id}/events/demo-denial")
def demo_denial(case_id: str, body: WakeEventRequest | None = None) -> dict[str, Any]:
    event_id = (body.event_id if body else None) or "DEMO-DENIAL-01"
    return _run(service.ingest_demo_denial, case_id, event_id)


@api.post("/api/cases/{case_id}/events/demo-resolution")
def demo_resolution(case_id: str, body: WakeEventRequest | None = None) -> dict[str, Any]:
    event_id = (body.event_id if body else None) or "DEMO-RESOLUTION-01"
    return _run(service.ingest_demo_resolution, case_id, event_id)


@api.post("/api/agent/analyze")
def analyze(body: AnalyzeRequest) -> dict[str, Any]:
    from .strands_runtime import analyze_case

    try:
        result = analyze_case(lambda: service.get_case(body.case_id), body.question)
    except Exception as exc:  # Bedrock/credential failures should degrade, not 500
        raise HTTPException(
            status_code=503,
            detail=f"Live Strands verification is unavailable: {exc}",
        ) from exc
    return {"session_id": f"case-{body.case_id}-{uuid.uuid4().hex[:8]}", **result}


@api.post("/api/agent/turn")
def agent_turn(body: TurnRequest) -> dict[str, Any]:
    from .strands_runtime import run_case_turn

    try:
        turn = run_case_turn(body.case_id, lambda: service.get_case(body.case_id), body.prompt)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Strands turn failed: {exc}") from exc
    return turn.to_dict()


@api.post("/api/agent/approve")
def agent_approve(body: ResumeRequest) -> dict[str, Any]:
    from .strands_runtime import resume_with_approval

    try:
        turn = resume_with_approval(
            body.case_id,
            lambda: service.get_case(body.case_id),
            body.interrupt_id,
            body.approved_hash,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Strands resume failed: {exc}") from exc
    return turn.to_dict()
