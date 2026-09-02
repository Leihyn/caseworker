# Agents for Humans: Building Caseworker, an Agent That Cannot Send a Message Without Your Exact Approval

> Paste-ready draft for builder.aws.com. Publish before the submission
> deadline; the title keeps "Agents for Humans" per the hackathon rules.

A package is marked delivered. The courier's proof-of-delivery photo shows a
door with the number 16 on it. The order was for number 18.

The merchant's support desk looks at the tracking status and says: delivered,
talk to the courier. The courier's support desk says: only the merchant can
open a delivery investigation. Both answers are locally correct, and together
they guarantee nothing happens. The $184.20 refund dies in the gap between
two companies, and the person who paid carries screenshots from one portal to
the other until they give up.

That gap is what I built Caseworker for during the Agents for Humans
Hackathon. It's an evidence-first agent for multi-party consumer disputes,
built on the Strands Agents SDK. This post is about the one design problem
that shaped everything else: how do you let an agent act on your behalf in
the real world without ever letting it freelance?

## The design constraint: approval binds to content, not intent

Most "human in the loop" designs get this subtly wrong. The agent asks "may I
contact the merchant?", the human says yes, and the agent then composes and
sends whatever it composes. The human approved an *intent*. What actually
left the system was *content* nobody reviewed.

Caseworker's rule is stricter: the human approves the exact bytes. The only
tool in the entire system with an external effect is `send_external_message`,
and it cannot run to completion without a person answering for the precise
payload it wants to send.

Strands made this almost embarrassingly small to build. The tool computes a
canonical SHA-256 hash over the fields being approved — recipient, subject,
body, action type, evidence IDs — and raises an interrupt carrying both the
payload and the hash:

```python
@tool(context=True)
def send_external_message(tool_context, action_type, target_actor_id,
                          target_name, subject, draft_body, evidence_ids):
    action = {...}
    payload_hash = action_payload_hash(action)
    answer = tool_context.interrupt(
        "caseworker-approval",
        reason={**action, "payload_hash": payload_hash},
    )
    if answer.get("approved_hash") != payload_hash:
        return "BLOCKED: approval withheld or hash mismatch. NOT sent."
    return f"SENT: {action_type} to {target_name}"
```

The agent loop stops with `stop_reason == "interrupt"`. To resume, the
approval response must echo the hash back. If anything about the draft
changes after approval — one character of the body, one evidence ID — the
hash no longer matches and execution is blocked until a fresh approval
happens. The approval contract is about 30 lines on top of Strands
interrupts.

The part I care about most: this property is enforced twice. Once in the
agent layer through the interrupt, and again in a deterministic Python state
machine that re-verifies the hash at execution time. The test suite tampers
with an approved draft and proves the send is blocked. The safety property
holds before any model is involved, which means it holds regardless of what
the model does.

## Agents-as-tools: six specialists, one owner

Caseworker is one orchestrator that owns the case, delegating to six
specialist Strands `Agent`s exposed via `.as_tool()`: intake (raw artifacts →
structured facts), reconstruction (the case graph of actors, claims, and
contradictions), a verifier, strategy, correspondence, and a response
evaluator.

The verifier is the one I'd defend in a design review: it rejects any claim
that cannot cite an evidence ID, and rejects drafted messages containing
legal or policy assertions absent from the case record. The model's job is to
*organize* evidence, never to *invent* facts. When the escalation letter to
the card provider says "the order address is number 18 [EV-01], while the
courier proof shows number 16 [EV-02]", every bracket is a claim the verifier
checked against a source artifact.

An `AfterToolCallEvent` hook records every specialist invocation into the
case's audit trail, so the finished case file reads like an execution record:
which agent did what, when, and what a human approved in between.

## The agent works while the person lives their life

The second thing Strands gave me for free is durability. After an approved
message goes out, the case doesn't sit in a running process — it goes
dormant. Session managers (`FileSessionManager` locally, `S3SessionManager`
in the cloud design) persist the paused agent, so an approval or a reply can
arrive days later, across restarts, and the case resumes exactly where it
paused.

Wake events are idempotent by construction: processed event IDs are recorded
in the case record before any state transition, so a duplicated webhook or
replayed deadline can't double-process a reply. When the merchant's denial
arrives, the response evaluator judges it against the unresolved
contradictions — the denial repeats "tracking says delivered" without
addressing the address conflict — and the strategy agent escalates to the
card provider. Which pauses for approval again, with a new hash.

The person makes two decisions in the whole flow. The agent does everything
else, in the background, which is exactly the hackathon's brief.

## The AWS shape

The model is Claude Sonnet on Amazon Bedrock via Strands' `BedrockModel`.
The repo ships an Amazon Bedrock AgentCore Runtime entrypoint
(`agentcore_app.py` — the `POST /invocations` contract routed by action) and
a one-command deploy script using Direct Code Deploy, so no container build
is needed. The cloud design puts case records in DynamoDB (revisioned),
paused sessions in S3, and wake events on EventBridge deadline schedules plus
an HTTPS endpoint for replies.

One decision I'd recommend to anyone building agents with real-world side
effects: make local mode mirror cloud mode exactly. Caseworker runs the full
demo path — evidence, approval, hash tampering, dormancy, wake, escalation,
resolution — with zero AWS credentials, using a file session manager and an
in-memory repository behind the same interfaces. That's what made the safety
machine testable in CI, and it's what lets anyone clone the repo and watch
the interrupt loop work in two commands.

## What I learned

**Interrupts are a better approval primitive than chat.** Asking the user a
question in conversation invites drift between what was discussed and what
gets executed. An interrupt that carries the payload and dies without the
matching hash is a contract, not a conversation.

**Put the safety property below the model.** If the guarantee you're selling
is "nothing sends without approval," it should be enforced by code you can
unit test, with the agent layer adding the same rule on top — not instead.

**Boring state machines make exciting agents shippable.** The demo works
every single time because the thing under the LLM is deterministic:
revisioned records, idempotent events, canonical hashes.

The code is at https://github.com/Leihyn/caseworker (MIT), built for the
Agents for Humans Hackathon with the Strands Agents SDK and Amazon Bedrock.

*Disclosure: the concept and React case-file UI come from an earlier
unsubmitted prototype of mine on a different agent framework; the Strands
implementation, approval contract, session persistence, and AWS runtime work
are new for this hackathon.*
