# Connecting the robot

The pipeline now takes transcript over HTTP. Testing against that endpoint
today *is* testing the robot integration — nothing downstream knows or cares
where the utterances came from.

## The interface the robot posts to

```
POST http://<host>:7003/transcript
Content-Type: application/json

{"speaker": "Tomas", "text": "The site falls over on every on-sale.", "ts": 1787441098.2}
```

`speaker` defaults to `"speaker"` if you have no diarisation. `ts` defaults to
arrival time. That is the whole contract.

```
GET /healthz  →  {"ok": true, "session": "...", "segments": 41, "last_at": ...}
```

Switch the driver in `config.yaml`:

```yaml
modules:
  listen:
    driver: http        # was: synthetic
    host: 0.0.0.0       # 0.0.0.0 so the robot can reach it over the LAN
    port: 7003
```

## Testing it without a robot

`scripts/fake_robot.py` posts a meeting to that endpoint the way a transcriber
would — irregular timing, transcriber lag, and occasional silences, so the
interval-based extraction is exercised the way it will be in the room.

```bash
# terminal 1
make run

# terminal 2 — replay a meeting as if a robot were transcribing it
python scripts/fake_robot.py app/modules/listen/data/ticketing_meeting.jsonl --speed 4
```

Watch http://localhost:7100. It should behave exactly as the synthetic replay
did, except extraction now fires on a clock rather than per-utterance.

Useful flags:

| Flag | Does |
|---|---|
| `--speed 4` | 4× real time. Use `1` to feel true meeting pace |
| `--jitter 0.6` | ± seconds of transcriber lag |
| `--silence 6 --silence-chance 0.2` | longer, more frequent pauses |
| `--interactive` | type `Speaker: what they said` yourself, one line at a time |

Interactive mode is the fastest way to test a specific beat:

```bash
python scripts/fake_robot.py --interactive
Tomas: we should do dynamic pricing on high demand shows
Nadia: absolutely not, the artists would hate it
Tomas: fine, scrap dynamic pricing
```

Then watch the PRD move that feature into Out of Scope.

## What changed for live use

**Extraction runs on a clock.** It used to fire every 4 segments. A live
transcriber bursts while people talk over each other and goes quiet while they
think, so it now runs on an interval and only when enough new speech has
actually arrived:

```yaml
extract:
  interval_seconds: 15
  min_new_segments: 2      # don't spend a model call on a single "mm-hm"
```

**The crew is re-derived less often.** Re-staffing costs a model call plus a
dozen catalog writes, and the crew rarely changes as fast as the PRD revises:

```yaml
factory:
  restaff_min_revs: 1      # raise to 2-3 for a long meeting
```

Revisions that moved nothing structural are skipped regardless.

## Settings for a real 30-minute meeting

The defaults are tuned for a fast demo. For a real meeting:

```yaml
session:
  replay_speed: 1.0        # ignored by the http driver

modules:
  extract:
    interval_seconds: 20
    min_new_segments: 3
  prd:
    debounce_seconds: 20   # was 2.5 — far too eager for a live room
  factory:
    restaff_min_revs: 3
    max_dispatches: 2
  enrich:
    max_queries: 6
```

That takes a 30-minute meeting from roughly 360 model calls down to about 120.

## What costs what

| | Limit | Notes |
|---|---|---|
| Gemini requests | 20,000/min | You will not get near this |
| Port agent invocations | **500/month** | The real constraint |
| Bright Data searches | capped in config | `enrich.max_queries` |

Only two things invoke Port agents: the **dispatch gate** (max 2 builds per
meeting) and the **alert loop** (deduped, cooled down, session-capped). Staffing
the crew does not invoke anything — it only writes catalog entities.

## Things worth knowing before the room

- **The reversal beat needs time to separate.** If a proposal and its retraction
  land inside one extraction window, the model marks the idea rejected outright
  and the PRD never visibly rewrites. At `interval_seconds: 15` and real
  speaking pace they will separate naturally; at high replay speeds they may not.
- **Silence is fine.** No new speech means no extraction, no revision, no cost.
- **A restart loses in-memory state** (known ideas, crew, PRD revision number).
  The transcript on disk survives; the PRD is rebuilt from scratch on the next run.
- **`0.0.0.0` on port 7003** means anything on your network can post transcript.
  Fine on a hackathon LAN; put it behind something real before it leaves the room.
