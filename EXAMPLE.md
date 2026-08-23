# Worked example — a second meeting, end to end

A different conversation in a different industry, traced through every stage
with the output each one actually produced. Everything below is from a real
run, not predicted.

The point of using a second meeting: the freight script and this one staff
**different crews**, which is the proof that the engineering team is derived
from the PRD rather than hardcoded.

## The meeting

**Meridian Live**, a concert promoter. Their ticket on-sales crash the site and
bots take a third of the good seats. 28 utterances, three speakers: Rafa (the
seller), Tomas (CTO), Nadia (VP Fan Experience).

It carries the same four beats the pipeline needs:

| Beat | In this meeting |
|---|---|
| A reversal | Tomas proposes dynamic pricing; Nadia kills it; Tomas scraps it |
| A hard constraint | Tessitura is the only source of truth; PCI scope must not grow |
| A researchable market | virtual waiting rooms and bot mitigation |
| A distinct crew | infrastructure-led, unlike the freight meeting |

## Running it

```bash
# config.yaml
modules:
  listen:
    script: app/modules/listen/data/ticketing_meeting.jsonl
session:
  replay_speed: 6.0        # ~45s. Use 2.0 for a live demo, 1.0 for real time.
```

```bash
make run      # then open http://localhost:7100
```

Expect the whole thing to settle about 90 seconds after the replay ends —
the last PRD revision and crew reconciliation trail the final utterance.

---

## Stage 1 — `listen`

**What happens.** The driver replays the script in real time, publishing one
`transcript.segment` per utterance and rooting a trace on each.

**Where to look.** Transcript pane, top left. `listen` pulses on the signal chain.

**Expected output**

```
listen  INFO  replaying 28 segments from .../ticketing_meeting.jsonl at 6.0x
listen  INFO  Tomas (CTO, Meridian Live): On-sale day is the worst day of our month...
```

- 28 segments, counter reads **28 spoken**
- `transcripts/session-<ts>.jsonl` written as it goes

---

## Stage 2 — `extract`

**What happens.** Every 4 segments, Gemini reads a sliding 8-segment window and
returns only ideas that are new or changed, having been shown the ideas it
already knows.

**Where to look.** The ticker along the bottom, and the ideas counter.

**Expected output — 15 ideas**

```
new idea [pain_point/confirmed]  Traffic spike overload during high-demand on-sales
new idea [pain_point/confirmed]  Automated bots capturing prime ticket inventory
new idea [feature/confirmed]     Virtual waiting room with queue position visibility
new idea [constraint/confirmed]  Queue infrastructure hosted externally
new idea [feature/proposed]      Dynamic pricing for high-demand on-sales     ← watch this
updated  [feature/rejected]      Dynamic pricing for high-demand on-sales     ← ~7s later
new idea [requirement/confirmed] Bot detection at queue entry point
new idea [constraint/confirmed]  Tessitura as single source of truth
new idea [constraint/confirmed]  Preserve existing PCI compliance scope
new idea [requirement/confirmed] Lightweight accessible mobile web queue page
new idea [feature/confirmed]     Real-time on-sale operations dashboard
new idea [constraint/confirmed]  Six-week timeline to pilot on-sale
```

Roughly 6 constraints, 5 features, 2 pain points, 2 requirements.

**The thing to watch.** Dynamic pricing arrives as `proposed`, then the *same
idea id* comes back as `rejected` about seven seconds later. That is the model
revising an existing idea rather than adding a contradictory second one — the
window timing in this script is deliberately arranged so the two land in
separate extraction windows.

---

## Stage 3 — `prd`

**What happens.** After a 2.5s quiet period, the whole PRD is regenerated from
the complete idea set. Not appended to — regenerated.

**Where to look.** The centre pane. It replaces itself on each revision.

**Expected output — 5 revisions**

```
PRD rev 1 — 2 features, 2 requirements, 0 out of scope
PRD rev 2 — 2 features, 3 requirements, 0 out of scope
PRD rev 3 — 2 features, 4 requirements, 1 out of scope   ← the reversal lands
PRD rev 4 — 2 features, 3 requirements, 1 out of scope
PRD rev 5 — 2 features, 2 requirements, 1 out of scope
```

Final title: **Meridian Live High-Concurrency Queue and Bot Mitigation System**

And the section that matters:

```markdown
## Out of Scope

- **Dynamic pricing for high-demand on-sales** — Rejected by leadership to
  protect brand goodwill and customer trust; all tickets must remain at face value.
```

Rev 2 shows dynamic pricing as a live feature. Rev 3 moves it to Out of Scope,
struck through in rust on the dashboard. **That transition is the demo.**

Also written to disk: `prd/PRD.md`, `prd/prd.json`, `prd/history/rev-00N.md`
(diff the history files to show the rewrite literally).

---

## Stage 4 — `enrich`

**What happens.** For up to 4 distinct ideas, Gemini writes a search query,
Bright Data runs it live, and findings are summarised — with every citation URL
checked against the URLs the search actually returned.

**Where to look.** Market Research pane, bottom left.

**Expected output — 4 searches, 9 findings**

```
searching: virtual waiting room queue management high demand ticket sales
searching: bot mitigation scalper detection ticketing on-sale
searching: Tessitura API seat hold inventory integration
```

Real vendors it found on this run:

| Source | Finding |
|---|---|
| queue-it.com | Virtual waiting room with real-time queue visibility |
| queue-fair.com | Waiting room acting as a proxy in front of the site |
| imperva.com | Scalper bots are cheap and easy to operate at scale |
| datadome.co | Holds excess traffic in a controlled queue |
| radware.com | Dedicated bot detection engine |
| secutix.com | Waiting rooms specifically in ticketing systems |

Queue-it and Queue-Fair are the actual market leaders here, which is a good
thing to point out — the system found the real competitive set unprompted.

**What to verify.** Click a citation. It should be a real, live page. A finding
whose URL was not in the search results is dropped before it reaches the PRD.

---

## Stage 5 — `factory`

**What happens.** On each PRD revision, Gemini picks which of seven fixed roles
this PRD requires and writes each mission. The result is diffed against the
current crew, and Port entities are created, updated or retired.

**Where to look.** Software Factory pane, top right. Also your Port catalog.

**Expected output — 8 crew changes**

```
spawned platform-sre                   (rev 1)
spawned backend-engineer               (rev 1)
spawned frontend-engineer              (rev 1)
spawned qa-automation-engineer         (rev 1)
spawned security-compliance-engineer   (rev 2)
retired frontend-engineer              (rev 2)
retired qa-automation-engineer         (rev 3)
spawned integrations-engineer          (rev 4)
```

Final state in Port — 5 active, 1 retired:

| Role | State | Mission (abbreviated) |
|---|---|---|
| `platform-sre` | active | Off-infrastructure queue hosting absorbing 40,000-user spikes |
| `backend-engineer` | active | Queue ordering logic and downstream traffic metering |
| `frontend-engineer` | active | Lightweight accessible mobile web queue UI |
| `security-compliance-engineer` | active | Edge bot detection and Stripe PCI scope |
| `integrations-engineer` | active | Tessitura seat holds and Stripe |
| `qa-automation-engineer` | **retired at rev 3** | Stress testing and simulated bot purchasing |

### Why this is the interesting stage

Compare the two meetings:

| | Freight meeting | Ticketing meeting |
|---|---|---|
| `platform-sre` | never staffed | **staffed at rev 1** |
| `data-ml-engineer` | staffed at rev 1 (OCR) | **never staffed** |
| Lead role | integrations | platform-sre |

Nobody configured that. The freight PRD needed document extraction, so it got a
data/ML engineer. The ticketing PRD needs to survive 40,000 concurrent users, so
it got an SRE. Same taxonomy, same code, different crew — because the crew is
read off the document.

**Verify in Port.** Each active role exists as a real `_ai_agent` entity you can
invoke, plus a `voc_product_agent` entity carrying its mission, its
justification, and a relation to the PRD entity.

---

## Stage 6 — `observability`

**What happens.** Every module emits spans, metrics and logs over OTLP, with
trace context travelling on the event bus.

**Where to look.** SigNoz at `localhost:8080`.

**Expected output**

Services → `voc-factory`. Open any trace rooted at `listen.segment`:

```
listen.segment                          ← one thing Tomas said
└── extract.window
    ├── llm.generate Extraction
    └── prd.revision
        ├── llm.generate PRDDoc
        └── factory.staff_crew
            ├── llm.generate Crew
            └── factory.spawned         ← platform-sre exists in Port
```

Metrics that should have data: `voc.segments` (28), `voc.ideas` (15),
`voc.prd.revisions` (5), `voc.enrichment.findings` (9), `voc.agents` (8),
`voc.llm.duration`, and `voc.module.errors` (**0** on a clean run).

Dashboard: **VoC Factory — Pipeline Health**.

---

## Stage 7 — `loop` (optional, needs a failure)

The loop only fires when something breaks, so inject a fault:

```yaml
chaos:
  llm_fail_rate: 1.0
  target_schema: Findings    # breaks research only; the PRD keeps building
```

**Expected sequence**

1. `enrich WARNING research failed for '...': injected fault` — real errors
2. `voc.module.errors` climbs, labelled `module=enrich, operation=research`
3. Within a minute SigNoz fires **VoC pipeline module errors** and POSTs `:7001`
4. `loop INFO received 1 firing alert(s) from SigNoz`
5. Triage, e.g.:
   `alert -> backend-engineer (high confidence): VoC enrichment research generation is failing across multiple topics due to simulated Gemini API chaos injection`
6. `backend-engineer responded (5113 chars)`
7. `chaining backend-engineer -> security-compliance-engineer`
8. Two minutes later, a repeat notification:
   `skipping alert: duplicate alert fingerprint`

The Agent Loop pane shows the brief with its evidence count — *"16 logs, 4
spans"* — which is the number of telemetry records read back out of SigNoz to
build it.

**Set `llm_fail_rate` back to `0.0` afterwards.**

---

## What good looks like

On a clean run: **0 warnings, 0 errors** in the log, 28 segments, 15 ideas,
5 revisions, 9 findings, 6 agents in Port, 1 Out of Scope entry.

## Known wobbles, so they don't surprise you live

- **The crew oscillates.** `frontend-engineer` was staffed at rev 1, retired at
  rev 2, then re-staffed later as the PRD grew. The model's judgement about
  which roles a partial PRD needs shifts while the document is still forming.
  It settles by the final revision. Worth framing as the crew tracking a
  document that is itself still changing.
- **Counts vary run to run.** Idea and revision counts move by one or two
  between runs; the model is not deterministic. The reversal beat is stable.
- **Retired-then-required roles keep their original `spawned_at_rev`**, so Port
  may show a role as active with an earlier revision than you expect.
- **Research is capped at 4 searches** per meeting and skips near-duplicate
  ideas, so a long meeting will not produce a finding per idea.
