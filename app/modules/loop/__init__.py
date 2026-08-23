"""Closed loop: a SigNoz alert becomes an agent doing something about it.

An alert on its own is nearly useless to an agent — "voc.module.errors > 0"
says a threshold moved, not what broke or what to do. So when one fires we
assemble the context an agent would actually need:

  what fired      the rule, severity, and labels (module, operation, error type)
  what happened   error logs and failing spans read back from SigNoz, including
                  the voc.* attributes that say which idea, revision, or role
                  was being processed when it failed
  what we build   the current PRD title and revision, so the fix is judged
                  against the product, not in the abstract
  who can act     the live crew roster, since the crew changes with the PRD

Gemini turns that into a triage brief and picks which crew role owns it, and
that role's Port agent is invoked with the brief. When it finishes, an
optional follow-on role is invoked with the first agent's answer as context.

Autonomous invocation needs brakes, so this dedupes by alert fingerprint,
enforces a per-role cooldown, and caps invocations per session. Port's agent
quota is finite and a loop that triggers itself would burn it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any, Literal

import uvicorn
from fastapi import FastAPI, Request
from pydantic import BaseModel

from app import observability as obs
from app.bus import EventBus
from app.events import Event, EventType
from app.llm import LLMUnavailable, generate_json
from app.port_client import PortClient, PortUnavailable
from app.signoz_client import SigNozClient, SigNozUnavailable

log = logging.getLogger("loop")

TRIAGE_SYSTEM = """You triage failures in a live pipeline that turns sales conversations
into product requirements and staffs engineering agents to build them.

You are given an alert, the telemetry behind it, the product being built, and the
crew currently staffed on it.

- summary: one sentence a responder can read at a glance.
- probable_cause: your best hypothesis, grounded in the telemetry provided. Say so
  when the evidence is thin rather than inventing a cause.
- impact: what is degraded for the product, in product terms.
- recommended_action: the concrete next step, specific enough to act on.
- target_role: which crew role should own this. Choose from the roles given to you;
  pick the one whose mission covers the failing component.
- confidence: how well the evidence supports your conclusion."""

CHAIN_SYSTEM = """You are briefing the next agent in a chain. The previous agent has
finished its work and you must tell the next one what to pick up.

Be specific about what was found and what remains. Do not repeat the whole prior
answer — state what it concluded and what the next agent should do because of it."""


class Brief(BaseModel):
    summary: str
    probable_cause: str
    impact: str
    recommended_action: str
    target_role: str
    confidence: Literal["low", "medium", "high"]


def render_brief(brief: Brief, alert_name: str) -> str:
    return (
        f"An automated alert fired on the pipeline you are staffed on.\n\n"
        f"Alert: {alert_name}\n"
        f"Summary: {brief.summary}\n"
        f"Probable cause: {brief.probable_cause}\n"
        f"Impact: {brief.impact}\n"
        f"Recommended action: {brief.recommended_action}\n"
        f"Confidence in this triage: {brief.confidence}\n\n"
        "Investigate this against the Port catalog and report what you find, "
        "whether the recommended action is right, and what you would do next."
    )


def parse_alerts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the firing alerts out of an Alertmanager-format webhook body."""
    alerts = []
    for a in payload.get("alerts", []):
        if a.get("status") and a["status"] != "firing":
            continue
        labels = a.get("labels", {})
        alerts.append(
            {
                "name": labels.get("alertname", payload.get("receiver", "unknown")),
                "severity": labels.get("severity", "warning"),
                "labels": labels,
                "annotations": a.get("annotations", {}),
                "fingerprint": a.get("fingerprint") or labels.get("alertname", "unknown"),
                "starts_at": a.get("startsAt", ""),
            }
        )
    return alerts


class Loop:
    def __init__(
        self,
        bus: EventBus,
        config: dict[str, Any],
        module_config: dict[str, Any],
        port: PortClient,
        signoz: SigNozClient | None,
    ) -> None:
        self.bus = bus
        self.config = config
        self.mc = module_config
        self.port = port
        self.signoz = signoz
        self.service = config.get("observability", {}).get("service_name", "voc-factory")

        self.max_invocations = int(module_config.get("max_invocations", 6))
        self.cooldown = float(module_config.get("cooldown_seconds", 120))
        self.dry_run = bool(module_config.get("dry_run", False))
        self.chain: dict[str, str] = module_config.get("chain", {}) or {}

        self.invocations = 0
        self.last_fired: dict[str, float] = {}
        self.seen_fingerprints: set[str] = set()

        # Kept current from the bus so a brief reflects the build as it stands now.
        self.prd: dict[str, Any] = {}
        self.crew: dict[str, dict[str, Any]] = {}

    async def watch_bus(self) -> None:
        async for event in self.bus.stream(
            {EventType.PRD_UPDATED, EventType.FACTORY_DISPATCHED}
        ):
            if event.type == EventType.PRD_UPDATED:
                self.prd = {
                    "title": event.payload["doc"]["title"],
                    "summary": event.payload["doc"]["summary"],
                    "rev": event.payload["rev"],
                }
            else:
                p = event.payload
                self.crew[p["role"]] = {
                    "agent_id": p["agent_id"],
                    "mission": p.get("mission", ""),
                    "status": "retired" if p["action"] == "retired" else "active",
                }

    def _allowed(self, alert: dict[str, Any]) -> str:
        """Return a rejection reason, or empty string when the alert may fire an agent."""
        if self.invocations >= self.max_invocations:
            return f"session cap reached ({self.max_invocations})"
        if alert["fingerprint"] in self.seen_fingerprints:
            return "duplicate alert fingerprint"
        if not [r for r, c in self.crew.items() if c["status"] == "active"]:
            return "no active crew to route to"
        return ""

    async def gather_context(self, alert: dict[str, Any]) -> dict[str, Any]:
        logs: list[dict[str, Any]] = []
        spans: list[dict[str, Any]] = []
        if self.signoz:
            minutes = int(self.mc.get("lookback_minutes", 15))
            try:
                logs, spans = await asyncio.gather(
                    self.signoz.error_logs(self.service, minutes=minutes),
                    self.signoz.error_spans(self.service, minutes=minutes),
                )
            except Exception as e:  # noqa: BLE001 - a thin brief beats no brief
                obs.record_error("loop", "gather_context", e)
                log.warning("could not read telemetry back from SigNoz: %s", e)
        return {"logs": logs, "spans": spans}

    def _prompt(self, alert: dict[str, Any], ctx: dict[str, Any]) -> str:
        roles = "\n".join(
            f"- {role}: {c['mission']}"
            for role, c in self.crew.items()
            if c["status"] == "active"
        )
        logs = "\n".join(f"[{l['severity']}] {l['body']}" for l in ctx["logs"][:12]) or "(none)"
        spans = (
            "\n".join(
                f"- {s['name']} ({s['duration_ms']}ms) {s['status_message']} {s['attributes']}"
                for s in ctx["spans"][:10]
            )
            or "(none)"
        )
        prd = (
            f"{self.prd.get('title', 'unknown')} (revision {self.prd.get('rev', '?')})\n"
            f"{self.prd.get('summary', '')}"
        )
        return (
            f"ALERT\nname: {alert['name']}\nseverity: {alert['severity']}\n"
            f"labels: {alert['labels']}\nannotations: {alert['annotations']}\n\n"
            f"ERROR LOGS FROM SIGNOZ\n{logs}\n\n"
            f"FAILING SPANS FROM SIGNOZ\n{spans}\n\n"
            f"PRODUCT BEING BUILT\n{prd}\n\n"
            f"CREW CURRENTLY STAFFED\n{roles}"
        )

    async def handle(self, alert: dict[str, Any]) -> None:
        if reason := self._allowed(alert):
            log.info("skipping alert '%s': %s", alert["name"], reason)
            return
        self.seen_fingerprints.add(alert["fingerprint"])

        with obs.span(
            "loop.handle_alert",
            attributes={"voc.alert.name": alert["name"], "voc.alert.severity": alert["severity"]},
        ) as s:
            ctx = await self.gather_context(alert)
            s.set_attributes(
                {"voc.context.logs": len(ctx["logs"]), "voc.context.spans": len(ctx["spans"])}
            )

            try:
                brief = await generate_json(
                    prompt=self._prompt(alert, ctx),
                    schema=Brief,
                    system=TRIAGE_SYSTEM,
                    config=self.config,
                )
            except LLMUnavailable:
                log.error("no LLM available — cannot triage")
                return
            except Exception as e:  # noqa: BLE001
                obs.record_error("loop", "triage", e)
                log.warning("triage failed for '%s': %s", alert["name"], e)
                return

            target = self._resolve_role(brief.target_role)
            if not target:
                log.warning("triage picked role '%s' which is not staffed", brief.target_role)
                return

            s.set_attributes(
                {"voc.loop.target_role": target, "voc.loop.confidence": brief.confidence}
            )
            log.info(
                "alert '%s' -> %s (%s confidence): %s",
                alert["name"], target, brief.confidence, brief.summary,
            )

            await self.bus.publish(
                Event(
                    type=EventType.LOOP_TRIGGERED,
                    payload={
                        "stage": "triaged",
                        "alert": alert["name"],
                        "severity": alert["severity"],
                        "target_role": target,
                        "brief": brief.model_dump(),
                        "evidence": {"logs": len(ctx["logs"]), "spans": len(ctx["spans"])},
                    },
                    trace_context=obs.inject(),
                )
            )

            result = await self._invoke(target, render_brief(brief, alert["name"]), alert["name"])
            if result and (nxt := self.chain.get(target)):
                await self._chain(target, nxt, result, alert["name"])

    def _resolve_role(self, role: str) -> str:
        if role in self.crew and self.crew[role]["status"] == "active":
            return role
        # The model may return a near-miss; fall back to the closest active role.
        wanted = set(role.lower().replace("_", "-").split("-"))
        best, score = "", 0
        for candidate, c in self.crew.items():
            if c["status"] != "active":
                continue
            overlap = len(wanted & set(candidate.split("-")))
            if overlap > score:
                best, score = candidate, overlap
        return best

    async def _invoke(self, role: str, prompt: str, alert_name: str) -> dict[str, Any] | None:
        now = time.monotonic()
        if now - self.last_fired.get(role, -1e9) < self.cooldown:
            log.info("%s is in cooldown; not invoking", role)
            return None
        if self.dry_run:
            log.info("dry run — would invoke %s", role)
            return None

        agent_id = self.crew[role]["agent_id"]
        with obs.span(
            "loop.invoke_agent", attributes={"voc.role": role, "voc.agent_id": agent_id}
        ) as s:
            try:
                result = await self.port.invoke_agent(
                    agent_id, prompt, labels={"source": "voc-loop", "alert": alert_name[:40]}
                )
            except Exception as e:  # noqa: BLE001
                obs.record_error("loop", "invoke_agent", e)
                log.warning("invoking %s failed: %s", role, e)
                return None

            self.invocations += 1
            self.last_fired[role] = now
            s.set_attribute("voc.response_chars", len(result["response"]))
            log.info("%s responded (%d chars)", role, len(result["response"]))

            await self.bus.publish(
                Event(
                    type=EventType.LOOP_TRIGGERED,
                    payload={
                        "stage": "answered",
                        "alert": alert_name,
                        "target_role": role,
                        "agent_id": agent_id,
                        "response": result["response"],
                        "invocation_id": result["invocation_id"],
                    },
                    trace_context=obs.inject(),
                )
            )
            return result

    async def _chain(
        self, source: str, target: str, result: dict[str, Any], alert_name: str
    ) -> None:
        """Fire a follow-on agent now that the first one has finished."""
        if target not in self.crew or self.crew[target]["status"] != "active":
            log.info("chain target %s is not staffed; stopping chain", target)
            return
        if reason := self._allowed({"fingerprint": f"chain:{source}->{target}:{alert_name}"}):
            log.info("not chaining to %s: %s", target, reason)
            return

        class Handoff(BaseModel):
            instruction: str

        try:
            handoff = await generate_json(
                prompt=(
                    f"The {source} agent finished investigating alert '{alert_name}'.\n\n"
                    f"Its findings:\n{result['response']}\n\n"
                    f"Write the instruction for the {target} agent, whose mission is: "
                    f"{self.crew[target]['mission']}"
                ),
                schema=Handoff,
                system=CHAIN_SYSTEM,
                config=self.config,
            )
        except Exception as e:  # noqa: BLE001
            obs.record_error("loop", "chain_handoff", e)
            log.warning("could not build handoff to %s: %s", target, e)
            return

        log.info("chaining %s -> %s", source, target)
        await self._invoke(target, handoff.instruction, f"{alert_name} (chained from {source})")


def build_app(loop: Loop, bus: EventBus) -> FastAPI:
    app = FastAPI(title="VoC Loop")

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ok": True, "invocations": loop.invocations, "crew": len(loop.crew)}

    @app.post("/alert")
    async def alert(request: Request) -> dict[str, Any]:
        payload = await request.json()
        alerts = parse_alerts(payload)
        log.info("received %d firing alert(s) from SigNoz", len(alerts))
        for a in alerts:
            await bus.publish(
                Event(type=EventType.ALERT_RECEIVED, payload=a, trace_context=obs.inject())
            )
            # Handled in the background so SigNoz's webhook call returns immediately.
            asyncio.create_task(loop.handle(a))
        return {"received": len(alerts)}

    return app


async def run(bus: EventBus, config: dict[str, Any], module_config: dict[str, Any]) -> None:
    try:
        port = PortClient()
    except PortUnavailable as e:
        log.error("%s — loop disabled for this session", e)
        return

    try:
        signoz: SigNozClient | None = SigNozClient(
            base_url=module_config.get("signoz_url", "http://localhost:8080")
        )
    except SigNozUnavailable as e:
        log.warning("%s — briefs will be built without telemetry lookback", e)
        signoz = None

    loop = Loop(bus, config, module_config, port, signoz)
    host = module_config.get("host", "127.0.0.1")
    port_num = int(module_config.get("port", 7001))

    server = uvicorn.Server(
        uvicorn.Config(build_app(loop, bus), host=host, port=port_num, log_level="warning", lifespan="off")
    )
    # The parent process owns Ctrl-C. uvicorn installs its own SIGINT
    # handler by default, which fights that and deadlocks shutdown.
    server.install_signal_handlers = lambda: None
    serving = asyncio.create_task(server.serve())
    log.info(
        "listening for SigNoz alerts on http://%s:%d/alert (cap %d invocations, %s)",
        host, port_num, loop.max_invocations, "dry run" if loop.dry_run else "live",
    )

    try:
        await loop.watch_bus()
    finally:
        server.should_exit = True
        with contextlib.suppress(asyncio.CancelledError):
            await serving
        with contextlib.suppress(Exception):
            await asyncio.shield(port.aclose())
        if signoz:
            with contextlib.suppress(Exception):
                await asyncio.shield(signoz.aclose())
