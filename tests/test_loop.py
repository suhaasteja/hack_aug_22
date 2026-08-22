from app.bus import EventBus
from app.modules.loop import Brief, Loop, parse_alerts, render_brief

ALERTMANAGER_BODY = {
    "receiver": "voc-loop",
    "status": "firing",
    "alerts": [
        {
            "status": "firing",
            "labels": {"alertname": "VoC pipeline module errors", "severity": "warning", "module": "enrich"},
            "annotations": {"summary": "Module enrich is failing"},
            "fingerprint": "abc123",
            "startsAt": "2026-08-22T21:03:03Z",
        },
        {
            "status": "resolved",
            "labels": {"alertname": "Old alert", "severity": "info"},
            "fingerprint": "def456",
        },
    ],
}


def make_loop(**mc) -> Loop:
    return Loop(EventBus(), {}, {"max_invocations": 2, **mc}, port=None, signoz=None)


def test_parses_firing_alerts_and_skips_resolved():
    alerts = parse_alerts(ALERTMANAGER_BODY)
    assert len(alerts) == 1
    assert alerts[0]["name"] == "VoC pipeline module errors"
    assert alerts[0]["labels"]["module"] == "enrich"
    assert alerts[0]["fingerprint"] == "abc123"


def test_parses_empty_body_without_raising():
    assert parse_alerts({}) == []


def test_duplicate_fingerprint_is_rejected():
    """SigNoz re-sends the same alert while it stays firing; agents must not re-fire."""
    loop = make_loop()
    loop.crew = {"backend-engineer": {"agent_id": "a", "mission": "m", "status": "active"}}
    alert = parse_alerts(ALERTMANAGER_BODY)[0]

    assert loop._allowed(alert) == ""
    loop.seen_fingerprints.add(alert["fingerprint"])
    assert "duplicate" in loop._allowed(alert)


def test_session_cap_stops_runaway_invocation():
    loop = make_loop(max_invocations=2)
    loop.crew = {"backend-engineer": {"agent_id": "a", "mission": "m", "status": "active"}}
    loop.invocations = 2
    assert "session cap" in loop._allowed({"fingerprint": "new"})


def test_no_active_crew_means_nothing_to_route_to():
    loop = make_loop()
    loop.crew = {"data-ml-engineer": {"agent_id": "a", "mission": "m", "status": "retired"}}
    assert "no active crew" in loop._allowed({"fingerprint": "new"})


def test_resolve_role_prefers_exact_active_match():
    loop = make_loop()
    loop.crew = {
        "backend-engineer": {"agent_id": "a", "mission": "m", "status": "active"},
        "frontend-engineer": {"agent_id": "b", "mission": "m", "status": "active"},
    }
    assert loop._resolve_role("frontend-engineer") == "frontend-engineer"


def test_resolve_role_falls_back_when_model_returns_a_near_miss():
    loop = make_loop()
    loop.crew = {"data-ml-engineer": {"agent_id": "a", "mission": "m", "status": "active"}}
    assert loop._resolve_role("data_ml_engineer") == "data-ml-engineer"


def test_resolve_role_never_returns_a_retired_agent():
    loop = make_loop()
    loop.crew = {"data-ml-engineer": {"agent_id": "a", "mission": "m", "status": "retired"}}
    assert loop._resolve_role("data-ml-engineer") == ""


def test_brief_carries_the_actionable_fields():
    brief = Brief(
        summary="Enrichment is failing on every research call.",
        probable_cause="Gemini returned an unparseable response.",
        impact="The PRD has no market context.",
        recommended_action="Check the response schema for Findings.",
        target_role="data-ml-engineer",
        confidence="high",
    )
    text = render_brief(brief, "VoC pipeline module errors")
    assert "Enrichment is failing" in text
    assert "Check the response schema" in text
    assert "high" in text
