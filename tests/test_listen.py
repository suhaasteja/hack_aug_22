import asyncio

import pytest

from app.bus import EventBus
from app.events import EventType
from app.modules import listen
from app.modules.listen.synthetic import load_script

SCRIPT = "app/modules/listen/data/sales_meeting.jsonl"
TICKETING = "app/modules/listen/data/ticketing_meeting.jsonl"


def test_script_is_ordered_and_well_formed():
    script = load_script(SCRIPT)
    assert len(script) > 10
    assert [s["t"] for s in script] == sorted(s["t"] for s in script)
    assert all(s["speaker"] and s["text"] for s in script)


def test_script_covers_the_demo_arc():
    """The synthetic meeting must exercise what downstream stages need to show."""
    text = " ".join(s["text"] for s in load_script(SCRIPT)).lower()
    assert "scratch the mobile app" in text, "needs a reversal so the PRD must rewrite, not append"
    assert "soc 2" in text, "needs a hard constraint"
    assert "freight" in text, "needs a researchable market space for enrichment"


def test_ticketing_script_covers_the_demo_arc():
    """The second meeting must exercise the same beats in a different domain."""
    text = " ".join(s["text"] for s in load_script(TICKETING)).lower()
    assert "scrap dynamic pricing" in text, "needs a reversal so the PRD must rewrite"
    assert "pci" in text, "needs a hard constraint"
    assert "waiting room" in text, "needs a researchable market space"


@pytest.mark.asyncio
async def test_unknown_driver_fails_loudly():
    with pytest.raises(RuntimeError, match="not implemented"):
        await listen.run(EventBus(), {}, {"driver": "reachy"})


@pytest.mark.asyncio
async def test_synthetic_driver_publishes_segments(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    script = tmp_path / "s.jsonl"
    script.write_text(
        '{"t": 0.0, "speaker": "A", "text": "one"}\n{"t": 0.02, "speaker": "B", "text": "two"}\n'
    )

    bus = EventBus()
    q = bus.subscribe({EventType.TRANSCRIPT_SEGMENT})
    task = asyncio.create_task(
        listen.run(bus, {"session": {"replay_speed": 1.0}}, {"driver": "synthetic", "script": str(script)})
    )

    first = await asyncio.wait_for(q.get(), timeout=2)
    second = await asyncio.wait_for(q.get(), timeout=2)
    task.cancel()

    assert [first.payload["text"], second.payload["text"]] == ["one", "two"]
    assert first.payload["seq"] == 0 and second.payload["seq"] == 1
    assert first.payload["session_id"] == second.payload["session_id"]
    assert list((tmp_path / "transcripts").glob("*.jsonl")), "transcript must persist to disk"
