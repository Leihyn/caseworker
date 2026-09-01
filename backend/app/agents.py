"""Caseworker agent topology on Strands.

One orchestrator delegates to six specialists (agents-as-tools). The only
tool with an external effect is send_external_message, and it cannot run
without a human answering an interrupt whose reason carries the exact
payload and its SHA-256 hash.
"""

from __future__ import annotations

import hashlib
import json
import os

from strands import Agent, tool
from strands.models import BedrockModel
from strands.types.tools import ToolContext

MODEL_ID = os.environ.get("CASEWORKER_MODEL", "global.anthropic.claude-sonnet-4-6")
REGION = os.environ.get("AWS_REGION", "us-east-1")

APPROVAL_INTERRUPT = "caseworker-approval"


def canonical_payload_hash(recipient: str, subject: str, body: str, action_type: str, evidence_ids: list[str]) -> str:
    """SHA-256 over the canonical JSON form of an outbound action.

    Approval binds to this hash. Any change to any field after approval
    produces a different hash and forces a fresh interrupt.
    """
    canonical = json.dumps(
        {
            "recipient": recipient,
            "subject": subject,
            "body": body,
            "action_type": action_type,
            "evidence_ids": sorted(evidence_ids),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@tool(context=True)
def send_external_message(
    tool_context: ToolContext,
    recipient: str,
    subject: str,
    body: str,
    action_type: str,
    evidence_ids: list[str],
) -> str:
    """Send one bounded external message (investigation request or escalation).

    Pauses the agent for human approval. The approval response must echo the
    payload hash; anything else blocks execution.
    """
    payload_hash = canonical_payload_hash(recipient, subject, body, action_type, evidence_ids)
    answer = tool_context.interrupt(
        APPROVAL_INTERRUPT,
        reason={
            "recipient": recipient,
            "subject": subject,
            "body": body,
            "action_type": action_type,
            "evidence_ids": evidence_ids,
            "payload_hash": payload_hash,
        },
    )
    if not isinstance(answer, dict) or answer.get("approved_hash") != payload_hash:
        return "BLOCKED: approval hash mismatch or approval withheld. The message was NOT sent."
    # Execution side effect happens in the repository layer (recorded, idempotent).
    return f"SENT: {action_type} to {recipient} (hash {payload_hash[:12]}…)"


def _model() -> BedrockModel:
    return BedrockModel(model_id=MODEL_ID, region_name=REGION, temperature=0.2)


def build_specialists() -> dict[str, Agent]:
    prompts = {
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
    return {name: Agent(model=_model(), system_prompt=prompt) for name, prompt in prompts.items()}


def build_orchestrator(session_manager=None) -> Agent:
    specialists = build_specialists()
    return Agent(
        model=_model(),
        session_manager=session_manager,
        system_prompt=(
            "You are Caseworker, an evidence-first agent for multi-party consumer "
            "disputes. You own the case without taking control away from the person. "
            "Delegate analysis to your specialists. Propose exactly one bounded next "
            "action at a time. External messages go only through send_external_message, "
            "which requires human approval. Never assert a fact without an evidence ID."
        ),
        tools=[
            specialists["intake"].as_tool(name="intake", description="Parse raw evidence artifacts into structured, evidence-ID-linked facts."),
            specialists["reconstruction"].as_tool(name="reconstruction", description="Build the case graph of actors, claims, contradictions, and deadlines."),
            specialists["verifier"].as_tool(name="verifier", description="Check claims and drafted messages against the evidence record; reject unsupported assertions."),
            specialists["strategy"].as_tool(name="strategy", description="Choose the single lowest-risk bounded next action and its target party."),
            specialists["correspondence"].as_tool(name="correspondence", description="Draft the exact outbound message with evidence citations."),
            specialists["response_evaluator"].as_tool(name="response_evaluator", description="Evaluate an incoming reply; decide resolved, escalate, or needs evidence."),
            send_external_message,
        ],
    )
