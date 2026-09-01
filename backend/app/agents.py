"""Caseworker agent topology on the Strands Agents SDK.

Strands features doing real work here:

- agents-as-tools: one orchestrator delegates to six specialist Agents.
- interrupts: send_external_message pauses the loop for human approval bound
  to the same canonical SHA-256 payload hash the deterministic state machine
  enforces (integrity.action_payload_hash).
- hooks: every specialist/tool invocation is recorded to the case audit trail
  through an AfterToolCallEvent hook, so the delivered case record shows
  which agent did what.
- sessions: File/S3 session managers make a paused case durable across
  restarts (wired in strands_runtime).
"""

from __future__ import annotations

import os
from typing import Any, Callable

from strands import Agent, tool
from strands.hooks import AfterToolCallEvent, HookProvider, HookRegistry
from strands.models import BedrockModel
from strands.types.tools import ToolContext

from .integrity import action_payload_hash

MODEL_ID = os.environ.get("CASEWORKER_MODEL", "global.anthropic.claude-sonnet-4-6")
REGION = os.environ.get("AWS_REGION", "us-east-1")

APPROVAL_INTERRUPT = "caseworker-approval"

SPECIALIST_PROMPTS = {
    "intake": (
        "You are the intake specialist. Parse raw evidence artifacts (receipts, "
        "photographs, chats, emails) into structured facts. Every fact carries the "
        "evidence ID it came from. Never infer beyond what an artifact states."
    ),
    "reconstruction": (
        "You are the reconstruction specialist. Build the case graph: actors, claims, "
        "contradictions, deadlines. Link every claim to supporting and contradicting "
        "evidence IDs."
    ),
    "verifier": (
        "You are the verifier. Reject any claim that cannot cite an evidence ID. "
        "Reject drafted messages containing legal or policy assertions absent from "
        "the case record. Understate, never overstate."
    ),
    "strategy": (
        "You are the strategy specialist. Given the case graph, choose the single "
        "lowest-risk next action most likely to move the dispute forward, and the "
        "party who can actually act on it. One bounded action only."
    ),
    "correspondence": (
        "You are the correspondence specialist. Draft the exact outbound message: "
        "recipient, subject, body, and the evidence IDs it cites. Neutral, factual, "
        "no threats, no invented entitlements."
    ),
    "response_evaluator": (
        "You are the response evaluator. Judge an incoming reply against the "
        "unresolved contradictions. Decide: resolved (with verification), "
        "unresolved (escalate), or needs more evidence."
    ),
}

ORCHESTRATOR_PROMPT = (
    "You are Caseworker, an evidence-first agent for multi-party consumer disputes. "
    "You own the case without taking control away from the person. Read the case "
    "record with read_case_record before reasoning. Delegate analysis to your "
    "specialists. Propose exactly one bounded next action at a time. External "
    "messages go only through send_external_message, which pauses for human "
    "approval. Never assert a fact without an evidence ID."
)


@tool(context=True)
def send_external_message(
    tool_context: ToolContext,
    action_type: str,
    target_actor_id: str,
    target_name: str,
    subject: str,
    draft_body: str,
    evidence_ids: list[str],
) -> str:
    """Send one bounded external message (investigation request or escalation).

    Pauses the agent for human approval via a Strands interrupt. The approval
    response must echo the canonical payload hash; anything else blocks the
    send. Use ONLY after the verifier has cleared the draft.
    """
    action = {
        "action_type": action_type,
        "target_actor_id": target_actor_id,
        "target_name": target_name,
        "subject": subject,
        "draft_body": draft_body,
        "evidence_ids": evidence_ids,
    }
    payload_hash = action_payload_hash(action)
    answer = tool_context.interrupt(
        APPROVAL_INTERRUPT,
        reason={**action, "payload_hash": payload_hash},
    )
    approved_hash = answer.get("approved_hash") if isinstance(answer, dict) else None
    if approved_hash != payload_hash:
        return (
            "BLOCKED: approval was withheld or the approved hash does not match the "
            "payload. The message was NOT sent. Do not retry without changes."
        )
    return f"SENT: {action_type} to {target_name} (payload {payload_hash[:19]}…). Record a checkpoint and wait for the reply."


def make_case_reader(get_case: Callable[[], dict[str, Any]]):
    """Build a read_case_record tool over the live repository."""

    @tool(name="read_case_record")
    def read_case_record() -> dict[str, Any]:
        """Read the full current case record: evidence, claims, contradictions,
        correspondence, deadlines, current action, and audit trail."""
        return get_case()

    return read_case_record


class AuditTrailHook(HookProvider):
    """Record every tool/specialist invocation for the case audit trail."""

    def __init__(self) -> None:
        self.entries: list[dict[str, str]] = []

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(AfterToolCallEvent, self._after_tool)

    def _after_tool(self, event: AfterToolCallEvent) -> None:
        name = event.tool_use.get("name", "unknown") if isinstance(event.tool_use, dict) else "unknown"
        status = "error" if event.exception else "ok"
        self.entries.append({"tool": str(name), "status": status})


def _model() -> BedrockModel:
    return BedrockModel(model_id=MODEL_ID, region_name=REGION, temperature=0.2)


def build_orchestrator(
    get_case: Callable[[], dict[str, Any]],
    session_manager=None,
    audit_hook: AuditTrailHook | None = None,
) -> Agent:
    specialists = {
        name: Agent(model=_model(), system_prompt=prompt)
        for name, prompt in SPECIALIST_PROMPTS.items()
    }
    descriptions = {
        "intake": "Parse raw evidence artifacts into structured, evidence-ID-linked facts.",
        "reconstruction": "Build the case graph of actors, claims, contradictions, and deadlines.",
        "verifier": "Check claims and drafted messages against the evidence record; reject unsupported assertions.",
        "strategy": "Choose the single lowest-risk bounded next action and its target party.",
        "correspondence": "Draft the exact outbound message with evidence citations.",
        "response_evaluator": "Evaluate an incoming reply; decide resolved, escalate, or needs evidence.",
    }
    return Agent(
        model=_model(),
        system_prompt=ORCHESTRATOR_PROMPT,
        session_manager=session_manager,
        hooks=[audit_hook] if audit_hook else None,
        tools=[
            make_case_reader(get_case),
            *[
                specialists[name].as_tool(name=name, description=descriptions[name])
                for name in SPECIALIST_PROMPTS
            ],
            send_external_message,
        ],
    )
