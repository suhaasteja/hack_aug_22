# Hackathon submission — draft answers

Copy-paste into the Google Form. Fields marked **YOU** need your input.

---

**Email:** suhaastejav@gmail.com

**Team name:** **YOU** (or `SOLO`)

**Name of the person submitting:** **YOU**

**Which sponsor tools did you use?** ☑ Bright Data ☑ Port ☑ SigNoz

**GitHub link:** https://github.com/suhaasteja/hack_aug_22
*(must be flipped to public before submitting)*

**Deployed link:** *(optional — see note at the bottom)*

**Video demo link:** **YOU**

**Present live on stage?** **YOU**

---

## What does your project do?

**Voice-of-Customer Factory** turns a sales conversation into working software,
autonomously, while the meeting is still happening.

Sales calls are the richest source of product requirements a company has, and
the least captured. Someone describes exactly what they need, an AE writes half
of it into a CRM field, and by the time it reaches engineering the nuance is
gone. Nobody writes the ticket that says *"they explicitly rejected dynamic
pricing because it would damage artist relationships."*

A robot sits in the meeting and listens. From there:

1. **The conversation becomes a living PRD.** Speech is extracted into
   structured ideas — pain points, features, requirements, constraints — and
   folded into a document that is regenerated on every revision. When someone
   changes their mind, the document *rewrites* rather than appends. In our demo
   a CTO proposes dynamic pricing, his VP of Fan Experience kills it, and the
   feature moves into an "Out of Scope" section with the reason attached.
2. **The PRD staffs an engineering crew.** Not a fixed template — the model
   reads the document and decides which of seven engineering roles this
   particular product needs, writing each one's mission. Each becomes a real,
   invokable AI agent in Port.
3. **The crew changes as the conversation does.** Every PRD revision produces a
   *team diff*: roles are staffed when the document justifies them and retired
   when it stops. A retired agent stays visible with its reason, so the
   customer's reversal remains legible months later.
4. **An agent decides it's ready and builds it.** Once the document has
   materially settled, the lead agent writes an implementation spec and triggers
   a Port self-service action, which produces a real GitHub repository and a
   pull request containing a working prototype.
5. **The whole pipeline watches itself.** Every stage emits OpenTelemetry to
   SigNoz. One spoken sentence is traceable end to end, through extraction and
   the PRD, all the way to agents being staffed. And when something breaks,
   SigNoz fires an alert that dispatches an agent to investigate it — with the
   actual error logs and failing spans as context.

**Who it's for:** B2B product teams where requirements originate in sales calls
and degrade on the way to engineering — and any team that wants the *reasoning*
behind a requirement preserved, not just the requirement.

**The thing we're proudest of:** the reversal propagates all the way through. A
customer says "scrap dynamic pricing," and thirty seconds later the generated
product carries the line *"Face-value guarantee: dynamic surges and price
escalations are disabled."* Nobody typed that.

---

## How did you use Bright Data?

Bright Data is how the PRD stops being just a transcription of the room and
starts carrying market reality.

**What we scrape.** As ideas are extracted from the conversation, the model
writes a targeted search query for each significant one and Bright Data runs it
live via `search_engine`. In the ticketing demo it searched for virtual waiting
room vendors, scalper-bot mitigation, and Tessitura's integration API — and
returned the actual market leaders unprompted: **Queue-it, Queue-Fair, Imperva,
DataDome, Radware, SecuTix**. In the freight demo it surfaced Cass, Trimble and
Intelligent Audit, and independently corroborated the customer's own claim that
3–5% of freight spend is overbilled.

**How it fits.** Findings are summarised into the PRD's Market Context section
with live citations, so the document that reaches engineering already knows who
the competition is and what the standard approaches are.

**Two things we did deliberately:**

- **Citation validation.** Search results are untrusted web content, so they're
  framed to the model strictly as data to summarise, and every citation URL is
  checked against the URLs the search actually returned. A finding citing a URL
  the model invented is dropped before it can reach the PRD. This is real
  protection against a hallucinated source ending up in a document engineers
  will build from.
- **Connected over MCP rather than the REST API**, so no account-specific zone
  configuration is needed — and with a fresh connection per query, because a
  stale SSE stream silently stops producing results partway through a long
  meeting.

Research is capped per meeting and skips near-duplicate ideas so the budget goes
to genuinely distinct themes.

---

## How did you use Port?

Port is the software factory — it does three distinct jobs.

**1. It holds the derived engineering crew.**

The core idea: *a PRD should decide its own team.* A freight-invoice auditor and
a high-concurrency ticketing platform need different engineers, so rather than
firing a fixed scaffold action we ask the model which of seven roles this
specific document requires and why.

Each role becomes:
- a real **`_ai_agent`** entity — a genuinely invokable Port AI agent with a
  mission prompt written from this PRD, scoped tools, and an execution mode
- a **`voc_product_agent`** entity recording its mission, the specific PRD
  clauses that justify it, and its lifecycle
- both related to a **`voc_prd`** entity holding the live document

**The evidence this is derived and not configured:** run the freight meeting and
you get a `data-ml-engineer` (OCR for faxed invoices) and no SRE. Run the
ticketing meeting and you get a `platform-sre` (40,000 concurrent users) and no
data/ML engineer. Same taxonomy, same code, opposite crews — because the crew is
read off the document.

Because the PRD is live, the crew is live. Every revision produces a team diff:
`frontend-engineer` appears when a dispute queue lands, `security-compliance-engineer`
when SOC 2 does, and roles are **retired rather than deleted** when the document
no longer justifies them — kept visible with the reason, so a customer's
reversal stays readable in the catalog.

**2. It builds the product.**

A **`scaffold_service`** self-service action with a webhook backend. When a
dispatch gate decides the PRD has materially moved, the lead agent writes an
implementation spec, the action runs, and a real GitHub repository is created
with a pull request containing a working prototype. The result is registered
back as a **`voc_service`** entity related to the PRD, so the catalog links
conversation → requirements → crew → shipped artifact.

**3. It's the actuator for the observability loop.**

When SigNoz alerts, the triage picks which crew role owns the failure and
invokes *that role's* Port agent with a brief.

**Something we learned worth sharing:** Port exposes a single generic
`run_action` tool rather than one tool per action — a regex naming the action
matches nothing and the agent silently has no way to act while still talking as
though it does. And Port AI agents execute under an identity our workspace grants
no execute rights to, even with permissions opened and `ownedByTeam` cleared. So
the agent *specifies* the build and the pipeline triggers the same Port action on
its behalf, carrying the agent's own words as the spec.

Agent invocations are quota'd, so a dispatch gate refuses cheaply first — too few
revisions since the last build, too small a diff, cap reached — and only then
pays for a model call on whether the change is genuinely material.

---

## How did you use SigNoz?

Self-hosted via Foundry, receiving OTLP from every module. Two roles: making one
spoken sentence traceable to the software it produced, and closing a loop that
dispatches an agent when something breaks.

**Distributed tracing across an async pipeline.** Trace context travels on the
internal event bus, so the pipeline isn't a set of disconnected spans — it's one
trace per utterance. A verified example:

```
listen.segment                      ← the customer speaking
└── extract.window
    ├── llm.generate Extraction
    └── prd.revision
        ├── llm.generate PRDDoc
        └── factory.staff_crew
            ├── llm.generate Crew
            └── factory.spawned ×4  ← agents now exist in Port
```

One trace, 12.8 seconds, from a sentence to a staffed engineering crew. Spans
carry business attributes — which idea, which revision, which role — not just
timing.

**Custom metrics** driving a *VoC Factory — Pipeline Health* dashboard:
`voc.segments`, `voc.ideas` (by kind and status), `voc.prd.revisions`,
`voc.enrichment.findings`, `voc.agents` (by lifecycle action), `voc.llm.duration`
(by model and call type), and `voc.module.errors`.

**Why the error metric matters more than usual here.** Every module deliberately
swallows its own failures so one bad model response can't kill a live meeting.
That means handled errors are *invisible by design* — `voc.module.errors` is the
only thing that makes them observable at all.

**The closed loop.** An alert on its own is nearly useless to an agent:
"errors > 0" says a threshold moved, not what broke or what to do. So when the
rule fires and posts its webhook, we assemble what an agent actually needs:

- the rule, severity and labels (module, operation, error type)
- **error logs and failing spans read back out of SigNoz**, carrying the `voc.*`
  attributes that say which idea and which revision was being processed when it
  failed
- the PRD as it currently stands, so the fix is judged against the product
- the live crew roster, since the crew changes with the document

Gemini turns that into a triage brief, picks the owning role, and that role's
Port agent is invoked. When it finishes, a follow-on agent is chained with its
findings. Verified end to end: an injected fault produced a real alert, triaged
at high confidence to the correct role citing *"16 logs, 4 spans"* of evidence,
which invoked an agent that chained to a second one.

Autonomous invocation needs brakes, so the loop dedupes by alert fingerprint,
enforces a per-role cooldown, and caps invocations per session — SigNoz
re-notifies every two minutes while an alert stays firing, and without the
dedupe a single failure would invoke an agent on every notification.

---

## Notes

**Deployed link.** The pipeline runs locally (it needs mic/robot input and a
self-hosted SigNoz), so there's no hosted URL. What you *can* link is the
software it built — a repository and pull request generated live from a meeting:
https://github.com/suhaasteja/meridian-live-high-demand-ticketing-queu/pull/1
That repo needs to be public too if you link it.

**Before submitting:**
- [ ] Make `hack_aug_22` public (verified: no keys in the tree or git history)
- [ ] Record the video
- [ ] Optionally make the generated build repo public and link it

---

# Page 2 — sponsor tool feedback

## How was your experience using Bright Data?

**What worked well.** This was the fastest of the three to integrate — about ten
minutes from nothing to real results. Connecting over the MCP server rather than
the REST API meant we never had to configure a zone or learn an account-specific
setup, and the standard Python MCP SDK talked to it without any special handling.

Result quality genuinely surprised us. We're generating a search query from a
live sales conversation, so the queries are messy and unrehearsed, and it still
returned the actual market leaders unprompted — Queue-it, Queue-Fair, Imperva,
DataDome and Radware for virtual waiting rooms, and Cass, Trimble and Intelligent
Audit for freight audit. Not SEO filler. One result independently corroborated a
number the customer had said out loud in the meeting, which was a genuinely
delightful moment in testing.

We also want to call out the security notice wrapped around returned content,
explicitly framing it as untrusted. For anyone piping scraped content into an
LLM that is exactly the right instinct, and we don't see it from other providers.

**What could be improved.**

1. **Long-lived SSE connections go quiet.** We initially held one connection open
   for the length of a session, and it silently stopped producing results partway
   through. No error — just nothing. We now open a fresh connection per query,
   which works fine, but a note in the docs about connection lifetime, or a
   keepalive, would have saved us a confusing half hour.
2. **Results are JSON wrapped in prose.** Because the security notice surrounds
   the payload, we string-search for the JSON boundaries rather than parsing
   cleanly. A structured field alongside the wrapper would make programmatic
   consumption more robust.
3. **No per-call usage readout.** We cap searches per meeting to control cost,
   but we're guessing. Returning credit or quota usage in the response — the way
   Port does on agent invocation — would let us budget properly.

---

## How was your experience using Port?

**What worked well.** The built-in `_ai_agent` blueprint was the single best
primitive we used all hackathon. Creating an entity creates a genuinely invokable
agent, which meant our "the PRD staffs its own engineering crew" idea went from
concept to working in one sitting. Blueprints, entities and relations modelled our
domain cleanly, and creating custom blueprints via the API was straightforward.

Streaming an agent run over SSE and finishing with a `done` event was a good
design choice — the reply arrives inline, so chaining one agent into the next
needed no polling at all. And returning rate limit and monthly quota usage in the
invoke response was genuinely useful; it's how we knew to build a gate that only
invokes an agent when a document has materially changed.

**What could be improved.** Three of these cost us real time, and we think they'd
cost anyone else the same.

1. **Action tool naming is misleading, and fails silently.** The docs show tools
   like `^run__createJiraIssue$` and `^run_create_github_issue$`, which reads as
   one tool per action. In fact there's a single generic **`run_action`** tool
   taking the identifier as an argument. Our regex matched nothing — so the agent
   had no way to act, while still *responding as though it would*. It kept saying
   it would trigger the build and then didn't. Silent capability failure is the
   worst kind to debug. **Suggestion:** warn or reject when a tools regex matches
   zero available tools.

2. **We could not get an AI agent to execute a self-service action.** We set
   execute permissions for Admin and Member and our user, `requiredApproval:
   false`, `ownedByTeam: false`, `execution_mode: Automatic`, and the correct
   `run_action` tool. The agent still replied "contact your Port admin to grant
   execute access to this action." We couldn't find a way to see or grant rights
   to the *agent's own identity*. We worked around it by having the agent specify
   the build and triggering the action on its behalf — but the demo we wanted was
   the agent pulling the trigger itself. If there's a documented path here we'd
   love to know it; if not, it feels like a gap for agentic use cases.

3. **Action runs vanish rather than fail.** `POST /v1/actions/{id}/runs` returned
   202 with a run id, but `GET /v1/actions/runs` came back empty and fetching that
   id 404'd. We believe this was because our webhook backend had `agent: true`
   with no Port agent registered — but the run disappearing entirely, rather than
   appearing as failed with a reason, made it very hard to diagnose.

4. **Minor:** Port is SaaS, so a webhook backend can't reach a local dev machine.
   We used ngrok, which is fine, but a documented local-development path would
   help (and a clearer note that `agent: true` requires the Port agent running).
   Also, `_ai_agent` prompts cap at 5000 characters — reasonable, but we only
   discovered it from a write error.

---

## How was your experience using SigNoz?

**What worked well.** The Foundry install was the smoothest self-hosting
experience of the event — one small `casting.yaml`, one `foundryctl cast`, and the
whole stack was healthy in a couple of minutes, MCP server included. Nothing to
debug.

The MCP server deserves particular praise. Being able to query traces, metrics,
alerts and docs conversationally *while building* meant we could verify
instrumentation without leaving the editor, and it caught several problems early.
The Query Builder v5 API was also clean to drive programmatically — our pipeline
reads its own error logs and failing spans back out of SigNoz to build context for
an automated agent, and that worked first try.

Trace correlation across our async event bus worked exactly as hoped. We can point
at a single spoken sentence and follow it through extraction, document generation
and into agents being created — one trace, twelve seconds wide. That's the demo.

**What could be improved / things that bit us.**

1. **`increase()` reads zero for short-lived processes.** Our pipeline runs for a
   few minutes per meeting. A burst of errors exports a single cumulative data
   point, so `increase()` over the evaluation window is 0 and the alert never
   fires. We switched to `latest` and it worked, but this took a while to work out
   because the metric clearly *had* data. A docs note on short-lived processes, or
   guidance on cumulative vs delta in this situation, would help a lot.

2. **Alertmanager spends its one notification even when the receiver is down.**
   Our webhook consumer wasn't running when an alert first fired. With renotify
   off it never retried, and because the alert stayed continuously firing it never
   re-fired either — so the webhook was never delivered at all. We had to delete
   and recreate the rule to get a fresh notification. **Retry with backoff on
   failed webhook delivery would be genuinely valuable**, and is probably the
   single change we'd most like to see.

3. **Schema versions are inconsistent between resources.** Alerts use
   `v2alpha1`; dashboards rejected that and required `v6`. Both errors were clear
   once hit, but we discovered the requirement by trial. The dashboard grid being
   12 columns wide (not 24) was likewise found by error message.

4. **Worth documenting:** SigNoz running in Docker cannot reach a webhook bound to
   the host's loopback. Obvious in hindsight, but we lost time to it — a line in
   the webhook docs pointing at `host.docker.internal` and reminding people to
   bind `0.0.0.0` would save others the same detour.

Overall: of the three tools, SigNoz was the one we spent the least time fighting
and got the most demo value from. The trace waterfall is the thing that makes the
whole project legible to someone seeing it for the first time.
