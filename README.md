# hack_aug_22 — Voice-of-Customer Factory

A robot listens to your sales meeting and turns the conversation into a living PRD, enriches it with live web research (Bright Data), hands it to a software factory (Port) that scaffolds the product, and traces the whole pipeline end-to-end in SigNoz.

**Spoken idea → PRD → product, fully observable.**

Someone says *"scratch the mobile app"* and the PRD rewrites itself, the mobile
engineer is retired from the crew, and a trace in SigNoz connects that sentence
to the agents it changed.

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — how it fits together, with a diagram
- **[DEMO.md](DEMO.md)** — the 3-minute demo runbook
- **[EXAMPLE.md](EXAMPLE.md)** — a second meeting traced through every stage, with real outputs
- **[PLAN.md](PLAN.md)** — the staged build plan

## Stack

| | |
|---|---|
| Transcript | driver-based (`synthetic` replay now, Reachy robot later) |
| Ideas + PRD | Gemini `gemini-3.7-flash`, structured output |
| Market research | Bright Data MCP, live search with citation validation |
| Software factory | Port — `_ai_agent` entities, real invokable agents |
| Observability | self-hosted SigNoz — OTLP traces, metrics, logs, alerts |
| Closed loop | SigNoz alert → context → Gemini triage → Port agent → chained agent |

## Run it

```bash
make setup
make run          # dashboard on http://localhost:7000
make test
```

Needs `GEMINI_API_KEY`, `BRIGHTDATA_API_TOKEN`, `PORT_CLIENT_ID`,
`PORT_CLIENT_SECRET`, `SIGNOZ_API_KEY` in the environment, and SigNoz running
locally (`~/signoz-selfhost`, `foundryctl cast -f casting.yaml`).

Every module is toggled by one line in `config.yaml`.
