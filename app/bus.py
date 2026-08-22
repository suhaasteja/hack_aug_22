"""In-process async pub/sub event bus.

Modules only ever see publish()/subscribe(), so this can be swapped for
Redis/NATS later without touching module logic. Subscribers get their own
queue; a slow subscriber never blocks publishers or other subscribers.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from app.events import Event, EventType

log = logging.getLogger("bus")

_QUEUE_MAX = 1000


class EventBus:
    def __init__(self) -> None:
        self._subs: list[tuple[set[EventType] | None, asyncio.Queue[Event]]] = []

    def subscribe(self, types: set[EventType] | None = None) -> asyncio.Queue[Event]:
        """Subscribe to a set of event types (None = all events)."""
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._subs.append((types, q))
        return q

    async def publish(self, event: Event) -> None:
        for types, q in self._subs:
            if types is not None and event.type not in types:
                continue
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("subscriber queue full, dropping %s", event.type)

    async def stream(self, types: set[EventType] | None = None) -> AsyncIterator[Event]:
        """Convenience: subscribe and iterate events forever."""
        q = self.subscribe(types)
        while True:
            yield await q.get()
