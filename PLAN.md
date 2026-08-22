# Voice-of-Customer Factory — Build Plan

**One-liner:** A robot sits in a sales meeting, listens, and continuously turns the conversation into a living PRD — enriched with live web research — then hands the PRD to a software factory (Port) that scaffolds the product, with the entire pipeline observable end-to-end in SigNoz.

**Industry framing:** This automates the *Voice of the Customer (VoC) → product discovery → delivery* loop. Sales conversations are the richest, least-captured source of product requirements; this pipeline captures them in real time and pushes them all the way to running software.

---

## Architecture

The system is an event-driven pipeline of independent modules. Every module:

- consumes events from and publishes events to a shared **event bus**
- conforms to a typed event contract (`events/schemas.py`)
- can be run, tested, and replaced in isolation
- emits OpenTelemetry spans so every event is traceable in SigNoz

```
 ┌──────────┐   transcript   ┌───────────┐   ideas    ┌───────────┐
 │  LISTEN   │ ─────────────▶ │  EXTRACT  │ ─────────▶ │    PRD    │◀─┐
 │ (mic→STT) │    segments    │  (ideas)  │            │ (living   │  │ enrichment
 └──────────┘                └───────────┘            │  document)│  │
                                                      └─────┬─────┘  │
                                                            │        │
                                              prd.updated   │   ┌────┴─────┐
                                                            │   │  ENRICH  │
                                                            ▼   │(BrightData)
                                                      ┌───────────┐└──────────┘
                                                      │  FACTORY  │
                                                      │  (Port)   │
                                                      └─────┬─────┘
                                                            │
                    every module emits OTel spans/logs      ▼
                  ┌──────────────────────────────────────────────┐
                  │              OBSERVE (SigNoz)                │
                  └──────────────────────────────────────────────┘
```

### Modules

| Module | Responsibility | Consumes | Produces |
|---|---|---|---|
| `listen` | Driver-based transcript source: `synthetic` (scripted meeting replay) now, `reachy`/mic later | audio / script | `transcript.segment` |
| `web` | Live dashboard: rolling transcript, rendered PRD, enrichment feed, Port build status, event ticker | all events (read-only) | — |
| `extract` | LLM pulls product ideas/requirements/decisions from transcript windows | `transcript.segment` | `idea.detected` |
| `prd` | Maintains the living PRD document; rewrites sections as ideas evolve | `idea.detected`, `enrichment.found` | `prd.updated` |
| `enrich` | Bright Data web research: competitors, prior art, market context per idea | `idea.detected` | `enrichment.found` |
| `factory` | Registers PRD in Port catalog; triggers Port self-service action to scaffold the product | `prd.updated` (stable) | `factory.dispatched` |
| `observe` | OTel SDK wiring shared by all modules; exports to local SigNoz | (cross-cutting) | traces, logs, metrics |

### Key design decisions

- **Event bus:** in-process async pub/sub (`asyncio.Queue`-based) to start — zero infra, swap for Redis/NATS later without touching module logic since modules only see `bus.publish()` / `bus.subscribe()`.
- **The PRD is the source of truth**, stored as `prd/PRD.md` + structured `prd/prd.json`. It is *always changing* during the meeting; the factory only fires on a debounced "stable" signal (no PRD changes for N seconds, or explicit user confirm).
- **Every event carries a `trace_context`** so one spoken sentence can be traced: audio → transcript → idea → PRD edit → enrichment → factory dispatch. That trace waterfall *is* the demo for the observability judges.

---

## Stages

Each stage ends with a **✅ Verify** checklist the user can run/see, and a git commit. No stage depends on unbuilt future stages.

### Stage 0 — Skeleton & contracts
Repo scaffold: module directories, event schemas, event bus, config loading (API keys from env), `make run` entrypoint that boots the bus and loads enabled modules from `config.yaml`.

**✅ Verify:**
- `make run` starts, logs "bus ready, modules loaded: []"
- `pytest` passes on bus publish/subscribe round-trip test
- Toggling a module in `config.yaml` enables/disables it (modularity proof)

### Stage 1 — Listen (synthetic driver) + web dashboard
`listen` module with a driver interface. First driver is `synthetic`: replays a scripted multi-speaker sales meeting (with realistic timing and an evolving/contradicting idea arc) as `transcript.segment` events. The `reachy` (robot) and mic drivers plug in later behind the same interface. Alongside it, the `web` module ships early: a FastAPI + WebSocket dashboard showing the rolling transcript and pipeline event ticker (PRD/enrichment/factory panes light up as later stages land).

**✅ Verify:**
- `make run` replays the synthetic meeting; transcript lines stream in the console
- Open `http://localhost:7000` — transcript scrolls live in the browser
- `transcripts/session-<ts>.jsonl` accumulates segments on disk
- Switching `listen.driver` in `config.yaml` between `synthetic` and an unimplemented driver fails loudly with a clear message (driver interface proof)

### Stage 2 — Extract + living PRD
LLM (Claude API) watches a sliding window of transcript segments, emits `idea.detected` events (feature, requirement, constraint, decision, open question). The `prd` module folds ideas into a sectioned PRD (Problem, Users, Features, Requirements, Open Questions, ...) and rewrites it as the conversation evolves.

**✅ Verify:**
- Talk through a fake product idea for 60s; open `prd/PRD.md` and see correctly sectioned content
- Contradict yourself ("actually, make it mobile-only") — the PRD updates, not appends
- `prd/history/` keeps a revision per update (diffable)

### Stage 3 — Enrich (Bright Data)
For each significant `idea.detected`, run Bright Data search + scrape for competitors/prior art/market context. Results are summarized and folded into the PRD's "Market Context / Prior Art" section with source links.

**✅ Verify:**
- Mention a known product space aloud (e.g. "expense tracking for freelancers"); within ~30s the PRD gains a Market Context section citing real URLs
- Enrichment failures degrade gracefully (PRD still updates without it)

### Stage 4 — Factory (Port)
Model the pipeline in Port: a `product-idea` blueprint (PRD contents as properties, lifecycle status), entities created/updated per PRD revision, and a Port self-service action ("Scaffold product") triggered when the PRD stabilizes — creating the downstream repo/scaffold and tracking build status back on the entity.

**✅ Verify:**
- PRD appears as an entity in the Port catalog with live status
- Stable PRD triggers the scaffold action; action run + status visible in Port
- Re-running with a changed PRD updates the same entity (no duplicates)

### Stage 5 — Observe (SigNoz)
OTel tracing/logging/metrics in every module, exported to the local SigNoz (OTLP `localhost:4317`). One trace per spoken-idea lifecycle. Dashboard: segments/min, ideas detected, PRD revisions, enrichment latency, factory dispatches. One alert: pipeline stalled (no transcript segments for 2 min during an active session).

**✅ Verify:**
- SigNoz UI shows the app under Services; a single trace spans listen→extract→prd→enrich→factory
- Dashboard tiles populate during a live session
- Killing the mic feed fires the "pipeline stalled" alert

### Stage 6 — Demo polish
Design pass on the web dashboard (it exists since Stage 1): left = rolling transcript, right = live-rendering PRD, enrichment citations, Port build card linking to the catalog entity and scaffolded repo, status bar = pipeline events. Scripted 3-minute demo path.

**✅ Verify:**
- Full run-through: speak → PRD forms on screen → enrichment links appear → Port entity updates → SigNoz trace shown, no manual intervention

---

## Deferred / future (the modularity payoff)

- Speaker diarization (who said what → requirement attribution)
- Real message broker (NATS) for multi-machine deployment
- Multiple factory targets (Port action variants per product type)
- Meeting-platform ingestion (Zoom/Meet bot) instead of local mic
- `reachy` listen driver: real audio from the Reachy robot replaces the synthetic feed (same driver interface)

## Environment

API keys live in env vars (`~/.zshrc`), never in the repo: `BRIGHTDATA_API_TOKEN`, `SIGNOZ_API_KEY`, `ANTHROPIC_API_KEY`, plus a speech-to-text provider key (chosen in Stage 1). SigNoz runs self-hosted via Foundry (`~/signoz-selfhost`), UI at `localhost:8080`, OTLP at `localhost:4317`, MCP at `localhost:8000`.
