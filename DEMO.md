# Demo runbook

Three minutes, one browser tab, no manual steps once it starts.

## Before you present

```bash
open -a Docker                                   # SigNoz lives here
until docker info >/dev/null 2>&1; do sleep 2; done
curl -fsS localhost:8080 >/dev/null && echo "signoz up"

source ~/.zshrc                                  # all five API keys
make setup                                       # first time only
```

Open two tabs:

| Tab | URL |
|---|---|
| Dashboard | http://localhost:7000 |
| SigNoz | http://localhost:8080 |

Optionally a third on Port for the catalog: https://app.port.io

Set the replay speed in `config.yaml`. `1.0` is meeting-real-time (~4 min);
`2.0` fits a 3-minute slot and still feels like a conversation.

```bash
make run
```

## The 3-minute path

**0:00 — "A robot is sitting in a sales meeting."**
The transcript pane starts filling. Marcus and Priya are describing how
Ridgeline Freight reconciles 11,000 freight invoices a month by hand. Point at
the signal chain along the top — `listen` is pulsing.

**0:30 — "It's already writing the PRD."**
The centre pane fills in. Problem, Users, Features. Note that nobody typed
this. `extract` and `prd` are pulsing on the chain, and the revision counter
is climbing.

**1:00 — "It's checking the market while they talk."**
Bright Data findings appear bottom-left with real vendor citations — Cass,
Trimble, Intelligent Audit. One of them independently corroborates Marcus's
claim that 3–5% of freight spend is overbilled. Worth saying out loud: those
URLs are live search results, and a citation the model can't trace to a real
result is dropped before it reaches the PRD.

**1:30 — the moment. "Watch what happens when the customer changes their mind."**
Marcus asks for a mobile app. Priya pushes back. Marcus says *"scratch the
mobile app."*

The PRD **rewrites** — the mobile feature moves to **Out of Scope**, struck
through in rust, with the reason attached. It does not append a correction; the
document is regenerated from the whole idea set every revision.

**2:00 — "The PRD staffs the engineering team."**
The Software Factory pane fills with agents. These are real Port AI agents,
invokable, each with a mission Gemini wrote from this specific PRD. Watch
`frontend-engineer` appear when the dispute queue lands and
`security-compliance-engineer` appear when SOC 2 does.

Then: **`data-ml-engineer` goes grey and struck through** — retired, because the
PRD no longer justifies it. The crew changes with the document.

**2:30 — "And it watches itself."**
Switch to SigNoz. Open the trace for one utterance:

```
listen.segment → extract.window → prd.revision → factory.staff_crew → factory.spawned
```

One trace, one spoken sentence, twelve seconds, ending in agents staffed in
Port. Then show the **VoC Factory — Pipeline Health** dashboard.

**2:50 — the closing line.**
"Everything you just saw came out of people talking. Nobody wrote a ticket."

## Optional: show the loop closing (add ~90s)

This needs a failure, so enable fault injection before the run:

```yaml
# config.yaml
chaos:
  llm_fail_rate: 1.0
  target_schema: Findings   # breaks enrichment only; the PRD keeps building
```

Then the sequence is:

1. Enrichment starts failing — real errors, real error spans, `voc.module.errors` climbing.
2. SigNoz's alert rule fires within a minute and POSTs the webhook.
3. The Agent Loop pane shows the triage: what broke, probable cause, which role
   owns it, how much evidence it read back from SigNoz (*"16 logs, 4 spans"*).
4. That role's Port agent is invoked and replies.
5. A follow-on agent is chained with the first one's findings.

Say the important part: **the alert alone is useless to an agent.** It only says
a threshold moved. The loop reads the error logs and failing spans back out of
SigNoz — including the attributes that say which idea and which revision was
being processed — adds the current PRD and the live crew roster, and gives the
agent something it can actually act on.

Also worth pointing out: SigNoz re-notifies every two minutes while the alert
stays firing, and the loop ignores the repeats by fingerprint. Autonomous agent
invocation has a session cap and a per-role cooldown too.

**Turn chaos back to `0.0` before the main demo.**

## If something goes wrong

| Symptom | Fix |
|---|---|
| Dashboard blank | Pipeline not running. `make run`. It replays history to late joiners, so a refresh is safe. |
| No agents in the factory pane | Port credentials missing. `echo $PORT_CLIENT_ID`. |
| No findings | Bright Data token missing, or all 4 research queries already spent. |
| SigNoz shows no traces | Docker stopped. `open -a Docker`, wait, containers auto-restart. |
| Alert never reaches the loop | The loop must bind `0.0.0.0` (SigNoz is in Docker and cannot reach loopback), and the channel URL must be `host.docker.internal:7001`. |
| Alert fired but no agent ran | Same alert fingerprint already handled, or the session cap of 6 was hit. Restart the pipeline. |

## Facts worth having ready

- Gemini model: `gemini-3.7-flash` (`gemini-2.5-flash` now 404s for new keys).
- Fixed taxonomy of seven engineering roles; Gemini picks which a PRD needs.
- Retired agents are kept visible, not deleted, so a reversal stays legible.
- Agent identifiers are keyed to the meeting, not the PRD title — the title is
  rewritten every revision, which would otherwise spawn a duplicate crew.
- Port agent quota: 500 invocations/month.
