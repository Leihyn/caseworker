# Caseworker on Strands — Port Architecture

## The Problem

A consumer dispute dies in the gap between companies. The merchant points to
the courier, the courier points back, and the person carries evidence between
systems that never talk. Caseworker owns the case: it reconstructs the
evidence, proposes one bounded action, pauses for approval, and keeps working
in the background until the dispute is verifiably resolved.

## Why Strands fits this design

The original Caseworker enforced a human approval boundary and durable
checkpoints with custom state-machine code. Strands provides both natively:

- **Interrupts** are the approval gate. The `send_external_message` tool calls
  `tool_context.interrupt()` with the exact payload; the agent loop stops with
  `stop_reason == "interrupt"` and resumes only with the human's response.
  No external effect can execute without a person answering.
- **Session managers** are the checkpoint. `S3SessionManager` persists agent
  state across process restarts; the case record itself lives in DynamoDB.
  A dormant case survives redeploys and resumes when a wake event arrives.

## Agent topology (agents-as-tools)

One orchestrator, six specialists — a direct port of the original roles:

```
                          ┌─────────────────────────┐
  user / wake event ────► │ Caseworker orchestrator │
                          └───────────┬─────────────┘
        ┌──────────┬──────────┬──────┴───┬────────────┬──────────────┐
        ▼          ▼          ▼          ▼            ▼              ▼
     intake   reconstruct  verifier   strategy   correspondence  response
     (parse    (case graph (claims ←  (next       (draft exact    evaluator
     evidence)  + actors)   evidence   bounded     message)       (judge reply,
                            IDs only)  action)                     escalate?)
```

- Specialists are `Agent` instances exposed to the orchestrator via
  `agent.as_tool(...)`.
- The **verifier** rejects any claim that cannot cite an evidence ID — the
  model organizes evidence; it never invents facts.
- The **correspondence** agent's output feeds `send_external_message`, the
  only tool with an external effect, and the only one that interrupts.

## The approval contract

Approval binds to a canonical SHA-256 hash over (recipient, subject, body,
action type, evidence IDs). The interrupt `reason` carries the payload and its
hash; the resume response must echo the hash. Any edit after approval changes
the hash and forces a fresh interrupt. This is the original Caseworker
guarantee, expressed in ~30 lines on top of Strands interrupts.

## State machine

```
evidence → analysis → approval (interrupt) → execution → checkpoint
   ▲                                                        │
   └── evaluation ◄── wake event (reply | deadline) ◄───────┘
            │
            └─► escalation (new interrupt) | verified resolution
```

Wake events are idempotent: processed event IDs are recorded in the case
record before any state transition.

## AWS runtime

| Concern            | Service                                  |
|--------------------|------------------------------------------|
| Agent runtime      | Amazon Bedrock **AgentCore Runtime**     |
| Model              | Claude Sonnet on **Amazon Bedrock** (via Strands `BedrockModel`) |
| Agent session state| **S3SessionManager**                     |
| Case records       | **DynamoDB** (single table, revisioned)  |
| Wake events        | **EventBridge** (deadline schedules) + HTTPS event endpoint (replies) |
| Frontend           | React case file, static hosting (S3/Amplify) |

Local mode mirrors cloud mode with `FileSessionManager` and an in-memory case
repository, so the full judge path runs deterministically without AWS
credentials.

## Prior-work disclosure

Caseworker's concept, React case-file UI, demo fixture, and state-machine
design come from an earlier unsubmitted prototype by the same author (built on
Google ADK; never entered into any hackathon). The Strands agent
implementation, interrupt-based approval gate, session persistence, DynamoDB
repository, and AWS deployment are new work for the Agents for Humans
Hackathon.
