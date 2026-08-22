import asyncio

import pytest

from app.bus import EventBus
from app.events import Event, EventType, TranscriptSegment


@pytest.mark.asyncio
async def test_publish_reaches_subscriber():
    bus = EventBus()
    q = bus.subscribe()
    seg = TranscriptSegment(speaker="Alice", text="hello", session_id="s1", seq=0)

    await bus.publish(seg.as_event())

    got = await asyncio.wait_for(q.get(), timeout=1)
    assert got.type == EventType.TRANSCRIPT_SEGMENT
    assert got.payload["text"] == "hello"


@pytest.mark.asyncio
async def test_type_filter_excludes_other_events():
    bus = EventBus()
    only_prd = bus.subscribe({EventType.PRD_UPDATED})

    await bus.publish(Event(type=EventType.TRANSCRIPT_SEGMENT, payload={}))
    await bus.publish(Event(type=EventType.PRD_UPDATED, payload={"rev": 1}))

    got = await asyncio.wait_for(only_prd.get(), timeout=1)
    assert got.type == EventType.PRD_UPDATED
    assert only_prd.empty()


@pytest.mark.asyncio
async def test_each_subscriber_gets_its_own_copy():
    bus = EventBus()
    a, b = bus.subscribe(), bus.subscribe()

    await bus.publish(Event(type=EventType.IDEA_DETECTED, payload={"n": 1}))

    assert (await a.get()).payload["n"] == 1
    assert (await b.get()).payload["n"] == 1


@pytest.mark.asyncio
async def test_publisher_drops_rather_than_blocking_on_full_subscriber():
    bus = EventBus()
    stalled = bus.subscribe()

    # A subscriber that never drains must not wedge the pipeline: publishing
    # past the queue bound drops events instead of blocking forever.
    for i in range(1100):
        await asyncio.wait_for(
            bus.publish(Event(type=EventType.IDEA_DETECTED, payload={"i": i})),
            timeout=1,
        )

    assert stalled.full()
