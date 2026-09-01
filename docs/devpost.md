# Devpost Submission — Caseworker

> Paste-ready text for the Devpost form. Track: **Everyday Agents**.

## Elevator pitch (one line)

An evidence-first agent that owns your multi-party consumer dispute — it finds
the contradictions, drafts the next move, waits for your approval, and keeps
working in the background until the case is verifiably resolved.

## What it does

A package is marked delivered, but the courier photo shows the wrong door. The
merchant says "talk to the courier." The courier says "only the merchant can
open an investigation." The $184 refund dies in the gap between two companies,
and the person carries screenshots between support portals until they give up.

Caseworker takes ownership of that case. It parses the raw evidence — receipt,
courier photo, chat logs, emails — into structured facts, each tied to an
evidence ID. It builds the case graph and finds the material contradiction
(the order says house 18; the delivery proof shows house 16). It picks exactly
one bounded next action, drafts the exact outbound message, and then stops:
nothing leaves the system until the human approves the exact payload.

After the message is sent, the case goes dormant. The agent wakes on a reply
or a deadline, judges the response against the unresolved contradictions, and
either verifies resolution or proposes an escalation — which again pauses for
approval. The person makes two or three decisions; the agent does everything
else.

## Who it's for

Anyone stuck in a blame loop between companies: wrong-address deliveries,
double charges, warranty ping-pong, subscription cancellations that never
stick. These disputes are winnable — the evidence usually exists — but they
die because no single company owns them and the consumer runs out of patience
before the companies run out of redirects.

## How we built it (Strands Agents SDK)

- **Agents-as-tools.** One Caseworker orchestrator delegates to six
  specialist `Agent`s exposed via `.as_tool()`: intake, reconstruction,
  verifier, strategy, correspondence, and response evaluator. The verifier
  rejects any claim that cannot cite an evidence ID — the model organizes
  evidence; it never invents facts.
- **Interrupts as the approval gate.** The only tool with an external effect,
  `send_external_message`, raises a Strands interrupt carrying the payload and
  its canonical SHA-256 hash. The agent loop stops with
  `stop_reason == "interrupt"`. The approval response must echo the exact
  hash; any edit after approval changes the hash and forces a fresh
  interrupt. No message leaves without a person answering.
- **Sessions as durable checkpoints.** `S3SessionManager` (cloud) /
  `FileSessionManager` (local) persist the paused agent, so an approval or a
  wake event can arrive days later, across restarts, and the case resumes
  exactly where it paused.
- **Hooks for the audit trail.** An `AfterToolCallEvent` hook records every
  specialist invocation into the case record, so the case file shows which
  agent did what.
- **AWS runtime.** Claude Sonnet on Amazon Bedrock via Strands
  `BedrockModel`; deployed to **Amazon Bedrock AgentCore Runtime** (Direct
  Code Deploy); case records in DynamoDB (revisioned); wake events via
  EventBridge deadline schedules plus an HTTPS endpoint for replies.

The deterministic state machine (approval binding, hash verification, wake
idempotency) is enforced in plain Python with 10 passing tests, so the safety
properties hold even before a model is involved. Local mode mirrors cloud mode
with a file session manager and in-memory repository — the full demo path runs
with zero AWS credentials.

## Challenges we ran into

Binding approval to *content* rather than *intent* was the core design
problem. "The user approved sending a message" is not the same as "the user
approved sending THIS message." Hashing the canonical payload inside the
interrupt reason, and re-verifying the hash at execution time, closes the gap
where an agent could redraft after approval.

## Accomplishments we're proud of

The approval contract is ~30 lines on top of Strands interrupts, and it is
mechanically testable: the test suite tampers with an approved draft and
proves execution is blocked until a fresh approval.

## What's next

Real channel integrations (email in/out), multi-case management, and evidence
capture from photos of paper correspondence.

## Prior-work disclosure

The concept, React case-file UI, and demo fixture come from an earlier
unsubmitted prototype by the same author (built on a different agent
framework; never entered into any hackathon). The Strands implementation —
agent topology, interrupt approval gate, session persistence, repository, and
AWS runtime — is new work for this hackathon.

## Built with

`strands-agents` · Amazon Bedrock (Claude Sonnet) · Amazon Bedrock AgentCore
Runtime · DynamoDB · S3 · EventBridge · FastAPI · React · TypeScript · Vite
