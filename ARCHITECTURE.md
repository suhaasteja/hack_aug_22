# Architecture

A robot sits in a sales meeting. What people say becomes a living PRD, the PRD
staffs a crew of AI agents in Port, and the whole pipeline watches itself — when
something breaks, an agent is dispatched to deal with it.

## The pipeline

```
        ┌──────────────────────────── SALES MEETING ────────────────────────────┐
        │  "Every carrier sends invoices in a different format… some still fax" │
        └───────────────────────────────────┬───────────────────────────────────┘
                                            │ audio
                                            ▼
  ┌──────────┐  transcript.segment   ┌─────────────┐   idea.detected   ┌──────────────┐
  │  LISTEN  │──────────────────────▶│   EXTRACT   │──────────────────▶│     PRD      │
  │ driver:  │                       │  Gemini     │                   │  Gemini      │
  │ synthetic│                       │  windowed   │◀──────────────────│  living doc  │
  │ (reachy  │                       │  extraction │  enrichment.found │  rewrites,   │
  │  later)  │                       └─────────────┘         │         │  never       │
  └──────────┘                              │                │         │  appends     │
                                            │ idea.detected  │         └──────┬───────┘
                                            ▼                │                │
                                    ┌───────────────┐        │                │ prd.updated
                                    │    ENRICH     │────────┘                │
                                    │  Bright Data  │                         ▼
                                    │  live search  │                 ┌───────────────┐
                                    │  + citation   │                 │    FACTORY    │
                                    │    guard      │                 │  Gemini picks │
                                    └───────────────┘                 │  the crew     │
                                                                      └───────┬───────┘
                                                                              │
                                                     ┌────────────────────────┘
                                                     ▼
                                        ╔═══════════════════════╗
                                        ║      PORT (live)      ║
                                        ║  voc_prd     ← entity ║
                                        ║  voc_product_agent ×N ║
                                        ║  _ai_agent   ← real,  ║
                                        ║     invokable agents  ║
                                        ╚═══════════════════════╝

  every module emits spans / metrics / logs
  ─────────────────────────────────────────────────────────────────────────────▶
                                        ╔═══════════════════════╗
                                        ║    SIGNOZ (OTLP)      ║
                                        ║  traces · logs        ║
                                        ║  metrics · dashboard  ║
                                        ║  alert rules          ║
                                        ╚═══════╤═══════════════╝
                                                │ alert fires (webhook)
                                                ▼
                                        ┌───────────────┐
                                        │     LOOP      │
                                        │ reads context │──┐
                                        │ back from     │  │ Gemini triage brief
                                        │ SigNoz        │  │ + picks owning role
                                        └───────────────┘  │
                                                │          │
                                                ▼          ▼
                                    invoke Port agent ─▶ chain next agent
                                                │
                                                └──────▶ back into PORT
```

## Modules

Every module is independent: it consumes typed events from a shared async bus,
publishes typed events back, and is toggled by one line in `config.yaml`. Adding
a feature means adding a module that subscribes to events that already exist.

| Module | Does | Consumes | Produces |
|---|---|---|---|
| `listen` | Driver-based transcript source (`synthetic` now, `reachy` later) | audio / script | `transcript.segment` |
| `extract` | Gemini pulls ideas from sliding transcript windows | `transcript.segment` | `idea.detected` |
| `prd` | Regenerates the whole PRD each revision, so reversals land in Out of Scope | `idea.detected`, `enrichment.found` | `prd.updated` |
| `enrich` | Bright Data live search, citations validated against real results | `idea.detected` | `enrichment.found` |
| `factory` | Gemini staffs a crew; diffs it against the last revision | `prd.updated` | `factory.dispatched` |
| `loop` | SigNoz alert → context → triage → invoke agent → chain | `alert.received` (webhook) | `loop.triggered` |
| `web` | Live dashboard at `:7000` | all events | — |

## The two ideas that matter

**The PRD rewrites itself.** Extraction is fed the ideas it already knows, so
when a customer reverses a request the model re-emits that idea as `rejected`
rather than adding a near-duplicate. The PRD is regenerated from the whole idea
set each revision, which is what lets a retracted feature move to Out of Scope
instead of lingering as a requirement.

**The crew is derived, not fixed.** A freight-audit PRD and a scheduling PRD
need different engineers, so Gemini picks which of seven roles a given PRD
requires and writes each mission. Because the PRD is live, every revision
produces a *team diff* — roles spawn when the PRD justifies them and retire
when it stops, keeping the customer's reversal legible in the Port catalog.

## Tracing

Trace context travels on the event bus, so one utterance is followable end to
end. A verified run:

```
listen.segment                      ← Marcus speaking
└── extract.window
    ├── llm.generate Extraction
    └── prd.revision
        ├── llm.generate PRDDoc
        └── factory.staff_crew
            ├── llm.generate Crew
            └── factory.spawned ×4  ← Port agents
```

One trace, 12.8 seconds, spoken sentence to staffed crew.

## The loop's context

An alert says a threshold moved; it does not say what to do. So the loop
assembles what an agent actually needs before invoking one:

- **what fired** — rule, severity, labels (module, operation, error type)
- **what happened** — error logs and failing spans read back from SigNoz,
  carrying the `voc.*` attributes that say which idea, revision or role was
  being processed at the time
- **what we're building** — the PRD as it currently stands, so the fix is
  judged against the product rather than in the abstract
- **who can act** — the live crew roster, since the crew changes with the PRD

Gemini turns that into a brief (summary, probable cause, impact, recommended
action, owning role, confidence) and the owning role's Port agent is invoked
with it. When that agent finishes, an optional follow-on role is invoked with
the first agent's findings.

Autonomous invocation needs brakes: the loop dedupes by alert fingerprint,
enforces a per-role cooldown, and caps invocations per session. SigNoz
re-notifies while an alert stays firing, so without the dedupe a single failure
would invoke agents on every notification.

## Ports

| Port | What |
|---|---|
| 7000 | Dashboard |
| 7001 | Alert webhook (binds `0.0.0.0` — SigNoz-in-Docker can't reach loopback) |
| 8080 | SigNoz UI |
| 4317 | OTLP ingest |
| 8000 | SigNoz MCP |
