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
