"""Synthetic listen driver: replays a scripted meeting in real time.

Stands in for the Reachy robot until it's wired up. The robot driver will
publish the same TranscriptSegment shape (speaker label + text), so nothing
downstream changes when we swap this out.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from app import observability as obs
from app.bus import EventBus
from app.events import TranscriptSegment

log = logging.getLogger("listen")

TRANSCRIPT_DIR = Path("transcripts")


def load_script(path: str) -> list[dict[str, Any]]:
    lines = Path(path).read_text().strip().splitlines()
    script = [json.loads(line) for line in lines if line.strip()]
    return sorted(script, key=lambda s: s["t"])


async def run(bus: EventBus, config: dict[str, Any], module_config: dict[str, Any]) -> None:
    script = load_script(module_config["script"])
    speed = float(config.get("session", {}).get("replay_speed", 1.0))

    session_id = f"session-{int(time.time())}"
    TRANSCRIPT_DIR.mkdir(exist_ok=True)
    sink = TRANSCRIPT_DIR / f"{session_id}.jsonl"

    log.info("replaying %d segments from %s at %.1fx", len(script), module_config["script"], speed)

    started = time.monotonic()
    with sink.open("a") as f:
        for seq, entry in enumerate(script):
            target = started + entry["t"] / speed
            await asyncio.sleep(max(0.0, target - time.monotonic()))

            segment = TranscriptSegment(
                speaker=entry["speaker"],
                text=entry["text"],
                session_id=session_id,
                seq=seq,
            )
            # The span opened here is the root of the trace that the rest of the
            # pipeline continues, so one utterance is followable end to end.
            with obs.span(
                "listen.segment",
                attributes={
                    "voc.session_id": session_id,
                    "voc.segment.seq": seq,
                    "voc.segment.speaker": segment.speaker,
                },
            ):
                obs.count(obs.segments_counter, session_id=session_id)
                await bus.publish(segment.as_event(trace_context=obs.inject()))

            f.write(json.dumps({"ts": time.time(), **entry, "seq": seq}) + "\n")
            f.flush()
            log.info("%s: %s", segment.speaker, segment.text)

    log.info("replay complete (%d segments) — transcript at %s", len(script), sink)
    # Hold the module open so the dashboard stays live after the meeting ends.
    await asyncio.Event().wait()
