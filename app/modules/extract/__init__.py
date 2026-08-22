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
    window_size = int(module_config.get("window_size", 8))
    stride = int(module_config.get("stride", 4))

    segments: list[dict[str, Any]] = []
    known: dict[str, dict[str, Any]] = {}
    pending = 0

    log.info("extracting every %d segments over a %d-segment window", stride, window_size)

    async for event in bus.stream({EventType.TRANSCRIPT_SEGMENT}):
        segments.append(event.payload)
        pending += 1
        if pending < stride:
            continue
        pending = 0

        window = segments[-window_size:]
        prompt = (
            f"Known ideas so far:\n{_render_known(known)}\n\n"
            f"New excerpt from the call:\n{_render_window(window)}"
        )

        try:
            result = await generate_json(
                prompt=prompt, schema=Extraction, system=SYSTEM, config=config
            )
        except LLMUnavailable as e:
            log.error("%s — extract disabled for this session", e)
            return
        except Exception as e:  # noqa: BLE001 - a bad window must not kill the pipeline
            log.warning("extraction failed on window ending at seg %d: %s", len(segments), e)
            continue

        for idea in result.ideas:
            payload = idea.model_dump()
            prior = known.get(idea.idea_id)
            if prior == payload:
                continue  # model restated something unchanged
            known[idea.idea_id] = payload
            verb = "updated" if prior else "new"
            log.info("%s idea [%s/%s] %s", verb, idea.kind, idea.status, idea.title)
            await bus.publish(
                Event(
                    type=EventType.IDEA_DETECTED,
                    payload={**payload, "is_update": prior is not None},
                    trace_context=event.trace_context,
                )
            )
