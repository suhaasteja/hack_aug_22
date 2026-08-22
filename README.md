# hack_aug_22 — Voice-of-Customer Factory

A robot listens to your sales meeting and turns the conversation into a living PRD, enriches it with live web research (Bright Data), hands it to a software factory (Port) that scaffolds the product, and traces the whole pipeline end-to-end in SigNoz.

**Spoken idea → PRD → product, fully observable.**

See [PLAN.md](PLAN.md) for architecture and staged build plan.

## Stack

- **Transcription:** streaming speech-to-text (mic)
- **PRD generation:** Claude API
- **Research enrichment:** Bright Data MCP
- **Software factory:** Port (blueprints, entities, self-service actions)
- **Observability:** self-hosted SigNoz (OTel traces/logs/metrics)
