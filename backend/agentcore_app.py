"""Amazon Bedrock AgentCore Runtime entrypoint for Caseworker.

One HTTP contract (`POST /invocations`) routed by `payload["action"]`:

- "analyze": one bounded verification turn — no external effects.
- "turn":    a full orchestrator turn under a durable session; if it reaches
             send_external_message it returns stop_reason == "interrupt" with
             the payload hash awaiting human approval.
- "approve": answer a pending interrupt with the approved hash (or decline).
- "get_case": read the current case record (deterministic, no model call).
- "reset_demo": restore the demo fixture.

The same CaseService/Strands runtime that backs the local FastAPI serves
here, so cloud and judge-path behavior stay identical. In the cloud, set
CASEWORKER_REPOSITORY=dynamodb and CASEWORKER_SESSIONS_BUCKET so case records
and paused agent sessions survive instance recycling.
"""

from __future__ import annotations

from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from app.case_service import CaseService
from app.repository import create_repository

app = BedrockAgentCoreApp()
service = CaseService(create_repository())

DEFAULT_CASE_ID = "CW-1042"


@app.entrypoint
def invoke(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action", "analyze"))
    case_id = str(payload.get("case_id", DEFAULT_CASE_ID))

    if action == "get_case":
        return {"case": service.get_case(case_id)}
    if action == "reset_demo":
        return {"case": service.reset_demo()}

    if action == "analyze":
        from app.strands_runtime import analyze_case

        question = str(
            payload.get(
                "question",
                "Independently verify the material contradiction and recommend "
                "exactly one bounded next action. Cite evidence IDs.",
            )
        )
        return analyze_case(lambda: service.get_case(case_id), question)

    if action == "turn":
        from app.strands_runtime import run_case_turn

        prompt = str(payload.get("prompt", "Review the case and take the next bounded step."))
        return run_case_turn(case_id, lambda: service.get_case(case_id), prompt).to_dict()

    if action == "approve":
        from app.strands_runtime import resume_with_approval

        interrupt_id = payload.get("interrupt_id")
        if not interrupt_id:
            return {"error": "approve requires interrupt_id"}
        return resume_with_approval(
            case_id,
            lambda: service.get_case(case_id),
            str(interrupt_id),
            payload.get("approved_hash"),
        ).to_dict()

    return {"error": f"Unknown action '{action}'. Use analyze | turn | approve | get_case | reset_demo."}


if __name__ == "__main__":
    app.run()
