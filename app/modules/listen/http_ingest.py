"""HTTP listen driver: the interface a real transcriber posts into.

The robot (or any speech-to-text process) POSTs each utterance as it is
recognised. Nothing about the pipeline downstream knows or cares whether the
segments came from a robot, a laptop mic, or a replay script — which is the
point: testing against this endpoint today is testing the real integration.

    POST /transcript  {"speaker": "Tomas", "text": "...", "ts": 1787...}
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel, Field

from app import observability as obs
from app.bus import EventBus
from app.events import TranscriptSegment

log = logging.getLogger("listen")

TRANSCRIPT_DIR = Path("transcripts")


class Utterance(BaseModel):
    text: str
    speaker: str = Field(default="speaker", description="diarised label if available")
    ts: float | None = Field(default=None, description="epoch seconds; defaults to arrival")


def build_app(bus: EventBus, state: dict[str, Any]) -> FastAPI:
    app = FastAPI(title="VoC Transcript Ingest")

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "ok": True,
            "session": state["session_id"],
            "segments": state["seq"],
            "last_at": state["last_at"],
        }

    @app.post("/transcript")
    async def transcript(u: Utterance) -> dict[str, Any]:
        seq = state["seq"]
        state["seq"] += 1
        state["last_at"] = time.time()

        segment = TranscriptSegment(
            speaker=u.speaker, text=u.text, session_id=state["session_id"], seq=seq
        )
        with obs.span(
            "listen.segment",
            attributes={
                "voc.session_id": state["session_id"],
                "voc.segment.seq": seq,
                "voc.segment.speaker": u.speaker,
                "voc.source": "http",
            },
        ):
            obs.count(obs.segments_counter, session_id=state["session_id"])
            await bus.publish(segment.as_event(trace_context=obs.inject()))

        state["sink"].write(
            json.dumps(
                {"ts": u.ts or state["last_at"], "speaker": u.speaker, "text": u.text, "seq": seq}
            )
            + "\n"
        )
        state["sink"].flush()
        log.info("%s: %s", u.speaker, u.text)
        return {"accepted": True, "seq": seq}

    return app


async def run(bus: EventBus, config: dict[str, Any], module_config: dict[str, Any]) -> None:
    session_id = config.get("session", {}).get("id") or f"session-{int(time.time())}"
    TRANSCRIPT_DIR.mkdir(exist_ok=True)
    sink = (TRANSCRIPT_DIR / f"{session_id}.jsonl").open("a")

    state: dict[str, Any] = {
        "session_id": session_id,
        "seq": 0,
        "last_at": 0.0,
        "sink": sink,
    }

    host = module_config.get("host", "0.0.0.0")
    port = int(module_config.get("port", 7003))
    server = uvicorn.Server(
        uvicorn.Config(build_app(bus, state), host=host, port=port, log_level="warning")
    )
    serving = asyncio.create_task(server.serve())
    log.info("listening for transcript on http://%s:%d/transcript", host, port)

    try:
        await asyncio.Event().wait()
    finally:
        server.should_exit = True
        with contextlib.suppress(asyncio.CancelledError):
            await serving
        sink.close()
