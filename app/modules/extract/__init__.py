"""Extract module: turns rolling transcript windows into structured ideas.

Runs on a sliding window rather than per-utterance, because requirements
usually span a few turns of conversation. Known ideas are fed back into the
prompt so the model can supersede or reject an earlier idea (the customer
changing their mind) instead of emitting a near-duplicate.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from app import observability as obs
from app.bus import EventBus
from app.events import Event, EventType
from app.llm import LLMUnavailable, generate_json

log = logging.getLogger("extract")

IdeaKind = Literal[
    "pain_point", "feature", "requirement", "constraint", "decision", "open_question"
]
IdeaStatus = Literal["proposed", "confirmed", "rejected"]

SYSTEM = """You listen to B2B sales calls and extract product requirements for a PRD.

Return only ideas that are NEW or CHANGED relative to the known ideas given to you.
Do not restate known ideas that are unchanged — return them only if their status or
detail actually changed in this excerpt.

Rules:
- idea_id is a stable kebab-case slug. To update or reject a known idea, REUSE its exact id.
- status "rejected" means the customer explicitly dropped or reversed it. This matters:
  when someone retracts a request, emit the existing idea with status "rejected".
- quote must be a verbatim span from the excerpt that justifies the idea.
- Prefer few, substantial ideas over many trivial ones. Skip pleasantries and logistics.
"""


class ExtractedIdea(BaseModel):
    idea_id: str = Field(description="stable kebab-case slug; reuse to update a known idea")
    kind: IdeaKind
    title: str
    detail: str
    status: IdeaStatus
    quote: str
    speaker: str


class Extraction(BaseModel):
    ideas: list[ExtractedIdea]


def _render_window(segments: list[dict[str, Any]]) -> str:
    return "\n".join(f"{s['speaker']}: {s['text']}" for s in segments)


def _render_known(known: dict[str, dict[str, Any]]) -> str:
    if not known:
        return "(none yet)"
    return "\n".join(
        f"- {i['idea_id']} [{i['kind']}/{i['status']}] {i['title']}" for i in known.values()
    )


async def run(bus: EventBus, config: dict[str, Any], module_config: dict[str, Any]) -> None:
    """Extract on a timer rather than per-utterance.

    A live transcriber emits whenever someone speaks — bursts during a heated
    exchange, nothing during a pause. Firing per-utterance would spend a model
    call on "mm-hm" and several during one sentence, so extraction runs on an
    interval and only when enough new speech has actually accumulated.
    """
    window_size = int(module_config.get("window_size", 8))
    interval = float(module_config.get("interval_seconds", 15))
    min_new = int(module_config.get("min_new_segments", 2))

    segments: list[dict[str, Any]] = []
    known: dict[str, dict[str, Any]] = {}
    pending = 0
    latest_ctx: dict[str, str] = {}

    log.info(
        "extracting every %.0fs when at least %d new segments have arrived (window %d)",
        interval, min_new, window_size,
    )

    async def collect() -> None:
        nonlocal pending
        async for event in bus.stream({EventType.TRANSCRIPT_SEGMENT}):
            segments.append(event.payload)
            pending += 1
            latest_ctx.clear()
            latest_ctx.update(event.trace_context)

    async def tick() -> None:
        nonlocal pending
        while True:
            await asyncio.sleep(interval)
            if pending < min_new:
                continue
            pending = 0
            await extract_window(
                bus, config, segments[-window_size:], known, dict(latest_ctx), len(segments)
            )

    await asyncio.gather(collect(), tick())


async def extract_window(
    bus: EventBus,
    config: dict[str, Any],
    window: list[dict[str, Any]],
    known: dict[str, dict[str, Any]],
    parent: dict[str, str],
    total: int,
) -> None:
    prompt = (
        f"Known ideas so far:\n{_render_known(known)}\n\n"
        f"New excerpt from the call:\n{_render_window(window)}"
    )

    with obs.span(
        "extract.window",
        parent=parent,
        attributes={
            "voc.window.size": len(window),
            "voc.window.end_seq": total - 1,
            "voc.known_ideas": len(known),
        },
    ) as s:
        try:
            result = await generate_json(
                prompt=prompt, schema=Extraction, system=SYSTEM, config=config
            )
        except LLMUnavailable as e:
            log.error("%s — extract disabled for this session", e)
            raise
        except Exception as e:  # noqa: BLE001 - a bad window must not kill the pipeline
            obs.record_error("extract", "generate", e)
            log.warning("extraction failed on window ending at seg %d: %s", total, e)
            return

        emitted = 0
        for idea in result.ideas:
            payload = idea.model_dump()
            prior = known.get(idea.idea_id)
            if prior == payload:
                continue  # model restated something unchanged
            known[idea.idea_id] = payload
            verb = "updated" if prior else "new"
            emitted += 1
            log.info("%s idea [%s/%s] %s", verb, idea.kind, idea.status, idea.title)
            obs.count(
                obs.ideas_counter,
                kind=idea.kind,
                status=idea.status,
                is_update=prior is not None,
            )
            await bus.publish(
                Event(
                    type=EventType.IDEA_DETECTED,
                    payload={**payload, "is_update": prior is not None},
                    trace_context=obs.inject(),
                )
            )
        s.set_attribute("voc.ideas.emitted", emitted)
