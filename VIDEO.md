# Video script — keep this open while recording

Target: **under 4 minutes.** Everything below is what you say out loud, with the
exact lines to type.

---

## Setup before you hit record

```bash
make run                                   # terminal 1
python scripts/fake_robot.py --interactive # terminal 2
```

Browser: **http://localhost:7100** — check the dot top-left is **amber and
pulsing** before you start. Grey means it isn't connected.

Have a second tab ready on the built repo (link at the bottom).

---

## 0:00 — The problem (20s)

> "Sales calls are where customers tell you exactly what they need. That
> information almost never survives the trip to engineering. Somebody writes
> half of it into a CRM field and the reasoning is gone.
>
> So we put a robot in the meeting. This is a live transcript feed — right now
> I'm typing, but it's the same endpoint the robot posts to."

---

## 0:20 — Type the first three lines

Type these one at a time. Say what they mean as you go.

```
Tomas: on sale day the site falls over, forty thousand people hit us in ninety seconds
Nadia: and bots take thirty percent of the good seats in the first minute
Tomas: we need a virtual waiting room that holds the queue off our infrastructure
```

> "A ticketing company. Their site dies on every on-sale and bots take a third
> of the good seats."

**Now switch to the browser and wait ~15 seconds.** Talk over the gap:

> "Extraction runs on a clock, not on every sentence — a real transcriber bursts
> while people talk and goes quiet while they think."

---

## 0:50 — The PRD writes itself

Point at the centre pane as it fills.

> "That's a product requirements document. Problem, users, features,
> requirements. Nobody typed it — it came out of three spoken lines.
>
> Bottom left, that's Bright Data. It's searching the live web for how other
> people solve this, and those citations are real vendors. Every URL is checked
> against what the search actually returned, so a made-up source can't get into
> a document engineers will build from.
>
> Right side is Port. Those are real AI agents, and the PRD decided which ones.
> This one needs an SRE because it has to survive forty thousand concurrent
> users. A different meeting gets a different crew."

---

## 1:20 — THE MOMENT. Type the proposal, then **STOP**

```
Tomas: and while we're here we should add dynamic pricing when demand spikes
```

### ⚠️ WAIT 20 SECONDS. Do not type the next line yet.

This is the one thing that can break the demo. The proposal has to be extracted
*before* the retraction arrives, or the model marks it rejected outright and the
document never visibly rewrites.

Talk over the gap:

> "Watch the features list. Dynamic pricing is about to appear."

Once you see **dynamic pricing show up as a feature**, type:

```
Nadia: absolutely not, the artists would hate that, it makes us look like scalpers
Tomas: fair, scrap dynamic pricing, face value stays face value
```

Wait ~15s, then:

> "And there it goes. It doesn't append a correction — the whole document is
> regenerated, so the feature moves into **Out of Scope** with the reason
> attached. The customer changed their mind and the requirements changed with
> them.
>
> That's the thing that normally gets lost."

---

## 2:00 — Two more lines, then the crew shifts

```
Nadia: everything settles in Tessitura, we're not building a second source of truth
Tomas: zero downtime on an on-sale, that's the number
```

> "The crew tracks the document too. Roles get staffed when the PRD justifies
> them and **retired** when it stops — struck through, with the reason, so the
> reversal stays readable in the catalog months later."

---

## 2:20 — SigNoz (40s)

Switch to **http://localhost:8080**. Open a trace rooted at `listen.segment`.

> "Everything emits OpenTelemetry into a self-hosted SigNoz. This is one trace,
> from one spoken sentence, through extraction, through the PRD, ending in
> agents being staffed in Port. Twelve seconds, one sentence to an engineering
> crew.
>
> And it's a closed loop — if a stage fails, SigNoz fires an alert that reads
> the error logs and failing spans back out, works out which agent owns the
> problem, and dispatches it. Self-healing, with real telemetry as the context."

---

## 3:00 — The payoff: show the built repo (40s)

Switch to the built repo tab.

> "And then an agent decides the requirements have settled enough to build.
> It writes the spec, triggers a Port self-service action, and this happens."

Show the **pull request**, then click into `index.html` / open it rendered.

> "This repository did not exist a few minutes ago. The first commit is the
> spec the agent wrote. The pull request is the implementation.
>
> And look at this line in the product —" *(point at the face-value line)*
> "— *dynamic surges and price escalations are disabled*. That's the thing the
> customer rejected out loud, and it propagated all the way into the shipped
> code. Nobody typed it."

---

## 3:40 — Close (15s)

> "Bright Data grounds it in the real market. Port derives the engineering team
> and builds the product. SigNoz makes every step traceable and dispatches an
> agent when something breaks.
>
> All of it from people talking. Nobody wrote a ticket."

---

## Links to show

| What | Where |
|---|---|
| **The pipeline** (submission repo) | https://github.com/suhaasteja/hack_aug_22 |
| **The app it built** ← show this one | https://github.com/suhaasteja/meridian-live-high-demand-ticketing-queu/pull/1 |

Show the **built repo** as the payoff — it's the more surprising artifact. Show
the pipeline repo only briefly, or just mention it.

---

## If something goes wrong mid-record

- **Nothing appears:** check the dot top-left. Grey = refresh the tab.
- **Script prints `FAILED`:** pipeline died. Restart `make run`.
- **PRD doesn't update:** you typed fewer than 2 lines, or fewer than 15s have
  passed. Both are normal.
- **The reversal didn't move to Out of Scope:** you typed the retraction too
  fast after the proposal. Re-record just that beat with a longer pause.
