"""Typed event contracts for the pipeline.

Every event on the bus is an Event with a `type` from EventType and a
payload dataclass. trace_context carries W3C traceparent data so a single
spoken idea can be traced across every module (wired up in Stage 5).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    TRANSCRIPT_SEGMENT = "transcript.segment"
    IDEA_DETECTED = "idea.detected"
    PRD_UPDATED = "prd.updated"
    ENRICHMENT_FOUND = "enrichment.found"
    FACTORY_DISPATCHED = "factory.dispatched"
    PIPELINE_STATUS = "pipeline.status"


@dataclass
class Event:
    type: EventType
    payload: dict[str, Any]
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = field(default_factory=time.time)
    trace_context: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["type"] = str(self.type)
        return d


@dataclass
class TranscriptSegment:
    """One utterance from the meeting."""

    speaker: str
    text: str
    session_id: str
    seq: int

    def as_event(self, trace_context: dict[str, str] | None = None) -> Event:
        return Event(
            type=EventType.TRANSCRIPT_SEGMENT,
            payload=asdict(self),
            trace_context=trace_context or {},
        )
