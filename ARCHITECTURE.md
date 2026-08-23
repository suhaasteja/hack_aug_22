# Architecture

A robot sits in a sales meeting. What people say becomes a living PRD, the PRD
staffs a crew of AI agents in Port, an agent decides the product is ready and
Port's factory builds it into a real repository — and the whole pipeline watches
itself, dispatching an agent when something breaks.

## The pipeline

```
        ┌──────────────────────── SALES MEETING ────────────────────────┐
        │  "Scrap dynamic pricing, it's not worth the goodwill."         │
        └───────────────────────────────┬───────────────────────────────┘
                                        │ audio
                                        ▼
  ┌──────────┐ transcript.segment ┌───────────┐  idea.detected  ┌──────────────┐
  │  LISTEN  │───────────────────▶│  EXTRACT  │────────────────▶│     PRD      │
  │ driver:  │                    │  Gemini,  │                 │  regenerated │
  │ synthetic│                    │  windowed │◀────────────────│  every rev — │
  │ (reachy  │                    └─────┬─────┘ enrichment.found│  rewrites,   │
  │  later)  │                          │                  │    │  never       │
  └──────────┘                          │ idea.detected    │    │  appends     │
                                        ▼                  │    └──────┬───────┘
                                ┌───────────────┐          │           │
                                │    ENRICH     │──────────┘           │ prd.updated
                                │  Bright Data  │                      ▼
                                │  live search  │              ┌───────────────┐
                                │  + citation   │              │    FACTORY    │
                                │    guard      │              │ Gemini staffs │
                                └───────────────┘              │  the crew;    │
                                                               │ diffs it each │
                                                               │   revision    │
                                                               └───┬───────┬───┘
                                        ┌──────────────────────────┘       │
                                        ▼                                  │
                            ╔═══════════════════════╗                      │
                            ║      PORT (live)      ║                      │
                            ║  voc_prd              ║   DISPATCH GATE ─────┘
                            ║  voc_product_agent ×N ║   material change?
                            ║  _ai_agent  ← real,   ║          │
                            ║    invokable agents   ║          ▼
                            ║  voc_service          ║   builder agent
                            ║  scaffold_service ────╫──▶ specifies the build
                            ╚═══════════╤═══════════╝          │
                                        │ webhook (ngrok)      │
                                        ▼                      │
                                ┌───────────────┐              │
                                │     SHIP      │◀─────────────┘
                                │ Gemini writes │
                                │  the code     │
                                └───────┬───────┘
                                        ▼
                            ┌───────────────────────────┐
                            │  GitHub: repo + PR        │
                            │  main = SPEC.md           │
                            │  branch = implementation  │
                            └───────────────────────────┘

  every module emits spans / metrics / logs
  ──────────────────────────────────────────────────────────────────────────▶
                            ╔═══════════════════════╗
                            ║    SIGNOZ (OTLP)      ║
                            ║  traces · logs        ║
                            ║  metrics · dashboard  ║
                            ║  alert rules          ║
                            ╚═══════════╤═══════════╝
                                        │ alert fires → webhook :7001
                                        ▼
                                ┌───────────────┐
                                │     LOOP      │  reads logs + spans back
                                │ Gemini triage │  from SigNoz, picks the
                                │  → invoke →   │  owning role, invokes it,
                                │  chain next   │  chains a follow-on agent
                                └───────┬───────┘
                                        └──────────▶ back into PORT
```

## Files by block

### Core (the spine everything plugs into)

| File | Does |
|---|---|
| `app/main.py` | Boots the bus, mints the meeting id, starts modules named in config |
| `app/bus.py` | In-process async pub/sub. Bounded queues, drops rather than blocking |
| `app/events.py` | Typed event contracts and `trace_context` that travels with each event |
| `app/llm.py` | All Gemini access. Structured output only, retries, chaos injection |
| `app/observability.py` | OTel traces/metrics/logs, trace propagation across the bus |
| `app/port_client.py` | Port API: blueprints, entities, agent invocation, action triggering |
| `app/signoz_client.py` | Reads logs and failing spans back out of SigNoz for the loop |
| `config.yaml` | One line per module toggles it; all tuning lives here |

### LISTEN — the meeting

| File | Does |
|---|---|
| `app/modules/listen/__init__.py` | Driver interface; fails loudly on an unimplemented driver |
| `app/modules/listen/synthetic.py` | Replays a scripted meeting in real time, persists the transcript |
| `.../data/sales_meeting.jsonl` | Freight invoice auditing, 29 utterances |
| `.../data/ticketing_meeting.jsonl` | Ticket on-sale queue, 28 utterances |

### EXTRACT + PRD — the document

| File | Does |
|---|---|
| `app/modules/extract/__init__.py` | Sliding window → ideas; fed known ideas so it revises rather than duplicates |
| `app/modules/prd/__init__.py` | Regenerates the whole PRD each revision; renders markdown |

Writes `prd/PRD.md`, `prd/prd.json`, `prd/history/rev-NNN.md`.

### ENRICH — market research

| File | Does |
|---|---|
| `app/modules/enrich/__init__.py` | Bright Data over MCP; validates every citation against real search results |

### FACTORY — the crew and the build decision

| File | Does |
|---|---|
| `app/modules/factory/__init__.py` | Staffs the crew from the PRD, diffs it per revision, runs the dispatch gate, asks the builder agent to specify the build, triggers `scaffold_service` |

Key pieces inside it: `agent_capabilities()` (tools and execution mode per role),
`_shape()` / `raw_delta()` / `should_dispatch()` (the gate),
`_maybe_dispatch_build()` (the build decision), `_handle_revision()` (crew diff).

### SHIP — the product

| File | Does |
|---|---|
| `app/modules/ship/__init__.py` | Secret-protected `/scaffold` on :7002, Gemini codegen, `gh repo create`, branch, PR, registers `voc_service` in Port, closes the Port run |

### LOOP — the closed feedback loop

| File | Does |
|---|---|
| `app/modules/loop/__init__.py` | `/alert` on :7001, gathers context, Gemini triage, invokes the owning agent, chains a follow-on. Dedupe, cooldown, session cap |

### WEB — the dashboard

| File | Does |
|---|---|
| `app/modules/web/__init__.py` | WebSocket fan-out of the bus, replays history to late joiners |
| `.../static/index.html` | Signal chain, five panes, counters, ticker |
| `.../static/style.css` | All-monospace instrumentation aesthetic |
| `.../static/app.js` | One handler per event type; stage and pane illumination |

### Tests

`tests/test_bus.py`, `test_module_loading.py`, `test_listen.py`, `test_prd.py`,
`test_enrich.py`, `test_factory.py`, `test_loop.py`, `test_port_client.py` — 38 passing.

### Docs

`README.md` · `ARCHITECTURE.md` (this) · `DEMO.md` (3-minute runbook) ·
`EXAMPLE.md` (a second meeting traced stage by stage) · `PLAN.md`

## Ports and external services

| Port | What |
|---|---|
| 7100 | Dashboard (not 7000: macOS AirPlay owns it) |
| 7001 | SigNoz alert webhook — binds `0.0.0.0`, SigNoz-in-Docker can't reach loopback |
| 7002 | Scaffold webhook — reached by Port SaaS through an ngrok tunnel |
| 8080 | SigNoz UI |
| 4317 | OTLP ingest |

Env: `GEMINI_API_KEY`, `BRIGHTDATA_API_TOKEN`, `PORT_CLIENT_ID`,
`PORT_CLIENT_SECRET`, `SIGNOZ_API_KEY`, `VOC_SHIP_TOKEN`.

## Three things worth knowing

**The PRD rewrites itself.** Extraction is fed the ideas it already knows, so a
reversal comes back as the *same idea* marked rejected rather than a duplicate.
The document is regenerated from the whole idea set each revision, which is what
moves a retracted feature into Out of Scope instead of leaving it as a feature.

**The crew is derived, not configured.** The freight PRD staffs a data/ML
engineer and no SRE; the ticketing PRD staffs an SRE and no data/ML engineer.
Same seven-role taxonomy, same code — the crew is read off the document.

**Agent invocations are quota'd at 500/month**, so the dispatch gate refuses
cheaply first (too few revisions since the last build, too small a diff, cap
reached) and only pays for a judgement call on materiality once those pass.

## Known limitation

Port AI agents execute under an identity this workspace grants no execute rights
to, so an agent cannot pull the trigger on `scaffold_service` itself — even with
permissions opened, `ownedByTeam` cleared, `execution_mode: Automatic`, and the
correct `run_action` tool. The agent therefore *specifies* the build and the
factory triggers the same Port action on its behalf, carrying the agent's own
words as the spec. Granting agent identities execute rights is a Port-side
change and would close that last gap.
