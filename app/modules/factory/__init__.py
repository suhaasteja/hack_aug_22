"""Factory module: turns each PRD revision into a crew of Port agents.

The PRD decides the team. A freight-invoice auditor needs a different crew
than a scheduling app, so rather than firing one fixed scaffold action we ask
the model which roles this specific PRD requires and why.

Because the PRD is live, the crew is live: every revision produces a team
diff, not just a document diff. A role whose justifying clauses moved to Out
of Scope is retired — kept visible with its reason rather than deleted, so
the reversal stays legible in the catalog.

Each active role becomes a real Port AI agent (the built-in `_ai_agent`
blueprint), scoped to read-only Port tools, plus a `voc_product_agent` entity
that records why it exists and which PRD it serves.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app import observability as obs
from app.bus import EventBus
from app.events import Event, EventType
from app.llm import LLMUnavailable, generate_json
from app.port_client import PortClient, PortUnavailable

log = logging.getLogger("factory")

PRD_BLUEPRINT = "voc_prd"
AGENT_BLUEPRINT = "voc_product_agent"
PORT_AI_BLUEPRINT = "_ai_agent"

# Every agent reads the catalog. Only roles that could plausibly own the build
# also get the action that opens a pull request — a Port agent cannot write code
# itself, so calling that action is how it delegates the implementation.
READ_TOOLS = ["^(list|get|search|track|describe)_.*"]
# Port exposes ONE generic tool for triggering actions — run_action, which takes
# the action identifier as an argument. There is no per-action run_<name> tool,
# so a regex naming the action matches nothing and the agent silently has no way
# to act.
SCAFFOLD_TOOL = "^run_action$"

# Which role gets asked to build, in preference order. Whichever of these is
# staffed first owns it; letting every role scaffold would open one repository
# per agent for the same product.
BUILDER_ROLE_ORDER = [
    "backend-engineer",
    "frontend-engineer",
    "platform-sre",
    "integrations-engineer",
]


def agent_capabilities(role: str) -> tuple[list[str], str]:
    """Tools and execution mode for a role.

    A builder runs unattended: under "Approval Required" it proposes the
    scaffold action and waits for a human click, which stalls the build.
    Advisory roles keep the approval gate, since nothing depends on them acting.
    """
    if role in BUILDER_ROLE_ORDER:
        return [*READ_TOOLS, SCAFFOLD_TOOL], "Automatic"
    return list(READ_TOOLS), "Approval Required"

PROMPT_LIMIT = 5000  # Port's cap on _ai_agent prompt length

# A fixed taxonomy keeps the crew legible across revisions; the model chooses
# which of these a given PRD actually needs, and writes each one's mission.
Role = Literal[
    "backend-engineer",
    "frontend-engineer",
    "integrations-engineer",
    "data-ml-engineer",
    "security-compliance-engineer",
    "platform-sre",
    "qa-automation-engineer",
]

CREW_SYSTEM = """You staff a software factory from a product requirements document.

Choose ONLY the roles this PRD actually requires. A role must be justified by
specific content in the PRD — never staff a role speculatively.

For each role you choose:
- mission: 1-2 sentences on what this role delivers for THIS product. Be concrete
  and name the actual systems, formats, or standards involved.
- justification: the specific PRD items that require this role.

Do not staff a role whose only justification appears under Out of Scope.
Prefer a small crew: most products need 3 to 5 roles."""


class CrewMember(BaseModel):
    role: Role
    mission: str
    justification: list[str] = Field(description="PRD items requiring this role")


class Crew(BaseModel):
    members: list[CrewMember]


GATE_SYSTEM = """You decide whether a product requirements document has changed enough
since it was last built to justify re-tasking an engineering agent.

Re-tasking is expensive and rate limited, so hold unless the change is material.

Material: a new or removed feature, a changed hard constraint, a reversed decision,
a new integration or compliance requirement — anything that changes what should be
built or how.

Not material: wording, reordering, added detail on something already captured, a
sharper summary, extra open questions.

Say why in one sentence, in terms of what actually changed."""


class GateDecision(BaseModel):
    dispatch: bool
    reason: str


PRD_BLUEPRINT_SPEC = {
    "identifier": PRD_BLUEPRINT,
    "title": "Product Requirement (VoC)",
    "icon": "Document",
    "schema": {
        "properties": {
            "summary": {"type": "string", "title": "Summary"},
            "revision": {"type": "number", "title": "Revision"},
            "markdown": {"type": "string", "format": "markdown", "title": "Document"},
            "meeting_id": {"type": "string", "title": "Meeting"},
            "feature_count": {"type": "number", "title": "Features"},
            "requirement_count": {"type": "number", "title": "Requirements"},
            "out_of_scope_count": {"type": "number", "title": "Dropped"},
        },
        "required": [],
    },
    "relations": {},
}

AGENT_BLUEPRINT_SPEC = {
    "identifier": AGENT_BLUEPRINT,
    "title": "Product Agent (VoC)",
    "icon": "Microservice",
    "schema": {
        "properties": {
            "role": {"type": "string", "title": "Role"},
            "mission": {"type": "string", "title": "Mission"},
            "justification": {
                "type": "array",
                "items": {"type": "string"},
                "title": "Justified by",
            },
            "status": {
                "type": "string",
                "title": "Status",
                "enum": ["active", "retired"],
                "enumColors": {"active": "green", "retired": "red"},
            },
            "spawned_at_rev": {"type": "number", "title": "Spawned at revision"},
            "retired_at_rev": {"type": "number", "title": "Retired at revision"},
            "retire_reason": {"type": "string", "title": "Retire reason"},
            "port_agent_id": {"type": "string", "title": "Port AI agent"},
        },
        "required": [],
    },
    "relations": {
        "prd": {
            "title": "PRD",
            "target": PRD_BLUEPRINT,
            "required": False,
            "many": False,
        }
    },
}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_")


def agent_prompt(member: CrewMember, prd_title: str, prd_markdown: str) -> str:
    head = (
        f"You are the {member.role} on the software factory crew building: {prd_title}.\n\n"
        f"Your mission: {member.mission}\n\n"
        f"You were staffed because of these requirements:\n"
        + "\n".join(f"- {j}" for j in member.justification)
    )
    tail = (
        "\n\nAnswer questions about your slice of this build and investigate the Port "
        "catalog for services, repositories, and dependencies relevant to it."
    )
    # Port caps agent prompts at 5000 characters, and the PRD grows all meeting.
    room = PROMPT_LIMIT - len(head) - len(tail) - len("\n\nThe product requirements document:\n\n")
    doc = prd_markdown[:room] if room > 0 else ""
    return f"{head}\n\nThe product requirements document:\n\n{doc}{tail}"


def _shape(doc: dict[str, Any]) -> dict[str, Any]:
    """The parts of a PRD that decide whether a rebuild is warranted."""
    return {
        "features": sorted(f["title"] for f in doc.get("features", [])),
        "requirements": sorted(doc.get("requirements", [])),
        "constraints": sorted(doc.get("constraints", [])),
        "dropped": sorted(d["item"] for d in doc.get("out_of_scope", [])),
    }


def raw_delta(before: dict[str, Any] | None, after: dict[str, Any]) -> int:
    """How many tracked items changed. A cheap floor before spending an LLM call."""
    if before is None:
        return sum(len(v) for v in after.values())
    return sum(
        len(set(after[k]) ^ set(before.get(k, []))) for k in after
    )


async def should_dispatch(
    before: dict[str, Any] | None, after: dict[str, Any], config: dict[str, Any]
) -> GateDecision:
    if before is None:
        return GateDecision(dispatch=True, reason="first build of this product")
    return await generate_json(
        prompt=(
            f"Previously built from:\n{before}\n\nThe document now reads:\n{after}"
        ),
        schema=GateDecision,
        system=GATE_SYSTEM,
        config=config,
    )


async def derive_crew(doc: dict[str, Any], config: dict[str, Any]) -> Crew:
    dropped = "\n".join(f"- {d['item']}: {d['reason']}" for d in doc.get("out_of_scope", []))
    prompt = (
        f"Product: {doc['title']}\n\n{doc['summary']}\n\n"
        f"Features:\n" + "\n".join(f"- {f['title']}: {f['detail']}" for f in doc.get("features", []))
        + "\n\nRequirements:\n" + "\n".join(f"- {r}" for r in doc.get("requirements", []))
        + "\n\nConstraints:\n" + "\n".join(f"- {c}" for c in doc.get("constraints", []))
        + (f"\n\nOut of scope (do NOT staff for these):\n{dropped}" if dropped else "")
    )
    return await generate_json(prompt=prompt, schema=Crew, system=CREW_SYSTEM, config=config)


async def run(bus: EventBus, config: dict[str, Any], module_config: dict[str, Any]) -> None:
    try:
        port = PortClient()
    except PortUnavailable as e:
        log.error("%s — factory disabled for this session", e)
        return

    try:
        await port.ensure_blueprint(PRD_BLUEPRINT_SPEC)
        await port.ensure_blueprint(AGENT_BLUEPRINT_SPEC)
    except Exception as e:  # noqa: BLE001 - without blueprints nothing downstream works
        log.error("could not set up Port blueprints: %s", e)
        await port.aclose()
        return

    # role -> what we last pushed to Port, so each revision only writes real changes
    crew: dict[str, dict[str, Any]] = {}

    # Identifiers are keyed to the meeting, never to PRD content: the PRD's title
    # is rewritten between revisions, and title-derived ids would make every
    # revision spawn a fresh duplicate crew instead of updating the existing one.
    session_id = config.get("session", {}).get("id", "session")
    prd_id = f"voc_prd_{session_id}"

    # Re-deriving the crew costs a model call plus a dozen catalog writes. In a
    # live meeting the PRD revises every few seconds, and the crew rarely
    # changes that fast, so skip revisions that moved nothing structural.
    restaff = {
        "min_revs": int(module_config.get("restaff_min_revs", 1)),
        "last_rev": -99,
        "last_shape": None,
    }

    gate = {
        "built_shape": None,           # PRD shape as of the last dispatch
        "dispatches": 0,
        "last_rev": -99,
        "min_delta": int(module_config.get("dispatch_min_delta", 2)),
        "min_revs": int(module_config.get("dispatch_min_revs", 2)),
        "after_rev": int(module_config.get("dispatch_after_rev", 3)),
        "max": int(module_config.get("max_dispatches", 2)),
        "enabled": bool(module_config.get("dispatch_builds", False)),
    }

    try:
        async for event in bus.stream({EventType.PRD_UPDATED}):
            with obs.span(
                "factory.staff_crew",
                parent=event.trace_context,
                attributes={
                    "voc.rev": event.payload["rev"],
                    "voc.session_id": session_id,
                },
            ) as s:
                rev_now = event.payload["rev"]
                shape_now = _shape(event.payload["doc"])
                fresh = (
                    rev_now - restaff["last_rev"] >= restaff["min_revs"]
                    and shape_now != restaff["last_shape"]
                )
                if fresh:
                    restaff["last_rev"] = rev_now
                    restaff["last_shape"] = shape_now
                    stop = await _handle_revision(
                        bus, port, config, event, crew, session_id, prd_id, s
                    )
                    if stop:
                        return
                else:
                    s.set_attribute("voc.restaff.skipped", True)
                if gate["enabled"]:
                    await _maybe_dispatch_build(bus, port, config, event, crew, gate)
    finally:
        # Shielded: this runs while the task is being cancelled, and a
        # bare await there re-raises before the client is closed.
        with contextlib.suppress(Exception):
            await asyncio.shield(port.aclose())


async def _maybe_dispatch_build(
    bus: EventBus,
    port: PortClient,
    config: dict[str, Any],
    event: Event,
    crew: dict[str, dict[str, Any]],
    gate: dict[str, Any],
) -> None:
    """Ask an agent to build, but only once the document has really moved.

    Every dispatch spends a Port agent invocation against a finite monthly
    quota, so this refuses cheaply first — too few revisions since the last
    build, too small a diff, cap reached — and only pays for a judgement call
    on materiality once those pass.
    """
    rev = event.payload["rev"]
    doc = event.payload["doc"]
    shape = _shape(doc)

    if gate["dispatches"] >= gate["max"]:
        return
    # A rev-1 PRD is a couple of bullet points; building from it wastes an
    # invocation on a product that is about to change substantially.
    if rev < gate["after_rev"]:
        return
    if rev - gate["last_rev"] < gate["min_revs"]:
        return
    delta = raw_delta(gate["built_shape"], shape)
    if delta < gate["min_delta"]:
        return

    builder = next(
        (r for r in BUILDER_ROLE_ORDER if crew.get(r, {}).get("status") == "active"), ""
    )
    if not builder:
        return

    with obs.span(
        "factory.dispatch_gate", attributes={"voc.rev": rev, "voc.delta": delta}
    ) as s:
        try:
            decision = await should_dispatch(gate["built_shape"], shape, config)
        except Exception as e:  # noqa: BLE001 - holding is the safe default
            obs.record_error("factory", "dispatch_gate", e)
            return

        s.set_attributes({"voc.dispatch": decision.dispatch, "voc.builder": builder})
        if not decision.dispatch:
            log.info("holding build at rev %d: %s", rev, decision.reason)
            return

        log.info("dispatching %s to build at rev %d: %s", builder, rev, decision.reason)
        gate["dispatches"] += 1
        gate["last_rev"] = rev
        gate["built_shape"] = shape

        instruction = (
            f"The requirements for '{doc['title']}' have reached a point worth building. "
            f"{decision.reason}\n\n"
            "Use the run_action tool with actionIdentifier \"scaffold_service\" to "
            "create the repository and open a pull request. Supply these inputs:\n"
            f"- repo_name: {slug(doc['title'])[:40]}\n"
            f"- role: {builder}\n"
            "- summary: what the prototype must demonstrate, in your own words\n"
            "- notes: the constraints it must respect, including anything the customer "
            "explicitly ruled out\n\n"
            f"Features: {[f['title'] for f in doc.get('features', [])]}\n"
            f"Requirements: {doc.get('requirements', [])}\n"
            f"Constraints: {doc.get('constraints', [])}\n"
            f"Ruled out: {[d['item'] for d in doc.get('out_of_scope', [])]}"
        )

        try:
            result = await port.invoke_agent(
                crew[builder]["agent_id"],
                instruction,
                labels={"source": "voc-factory", "rev": str(rev)},
            )
        except Exception as e:  # noqa: BLE001
            obs.record_error("factory", "dispatch_build", e)
            log.warning("could not dispatch %s: %s", builder, e)
            return

        # The agent decided what to build and specified it. It cannot execute the
        # action itself — Port AI agents run under an identity this workspace
        # does not grant execute rights to — so the action is triggered on its
        # behalf, carrying the agent's own words as the build spec.
        try:
            run_id = await port.trigger_action(
                "scaffold_service",
                {
                    "repo_name": slug(doc["title"])[:40].replace("_", "-"),
                    "summary": result["response"][:1500],
                    "notes": (
                        f"Constraints: {doc.get('constraints', [])}. "
                        f"Explicitly ruled out: {[d['item'] for d in doc.get('out_of_scope', [])]}"
                    )[:1500],
                    "role": builder,
                },
            )
            log.info("%s requested the build (port run %s)", builder, run_id)
        except Exception as e:  # noqa: BLE001
            obs.record_error("factory", "trigger_scaffold", e)
            log.warning("could not trigger scaffold_service: %s", e)

        await bus.publish(
            Event(
                type=EventType.LOOP_TRIGGERED,
                payload={
                    "stage": "answered",
                    "alert": f"build requested at rev {rev}",
                    "target_role": builder,
                    "agent_id": crew[builder]["agent_id"],
                    "response": result["response"],
                    "invocation_id": result["invocation_id"],
                },
                trace_context=obs.inject(),
            )
        )


async def _handle_revision(
    bus: EventBus,
    port: PortClient,
    config: dict[str, Any],
    event: Event,
    crew: dict[str, dict[str, Any]],
    session_id: str,
    prd_id: str,
    span: Any,
) -> bool:
    """Reconcile the crew against one PRD revision. Returns True to stop the module."""
    rev = event.payload["rev"]
    doc = event.payload["doc"]

    try:
        await port.upsert_entity(
            PRD_BLUEPRINT,
            {
                "identifier": prd_id,
                "title": doc["title"],
                "properties": {
                    "summary": doc["summary"],
                    "revision": rev,
                    "markdown": event.payload["markdown"],
                    "meeting_id": session_id,
                    "feature_count": len(doc.get("features", [])),
                    "requirement_count": len(doc.get("requirements", [])),
                    "out_of_scope_count": len(doc.get("out_of_scope", [])),
                },
            },
        )
    except Exception as e:  # noqa: BLE001
        obs.record_error("factory", "prd_upsert", e)
        log.warning("PRD entity upsert failed at rev %d: %s", rev, e)
        return False

    try:
        derived = await derive_crew(doc, config)
    except LLMUnavailable as e:
        log.error("%s — factory disabled for this session", e)
        return True
    except Exception as e:  # noqa: BLE001
        obs.record_error("factory", "derive_crew", e)
        log.warning("crew derivation failed at rev %d: %s", rev, e)
        return False

    required = {m.role: m for m in derived.members}
    span.set_attribute("voc.crew.required", len(required))

    for role, member in required.items():
        previous = crew.get(role)
        if previous and previous["mission"] == member.mission and previous["status"] == "active":
            continue  # unchanged; don't churn Port

        agent_id = f"voc_{slug(role)}_{session_id}"
        action = "updated" if previous else "spawned"
        prompt = agent_prompt(member, doc["title"], event.payload["markdown"])
        tools, mode = agent_capabilities(role)

        with obs.span(
            f"factory.{action}",
            attributes={"voc.role": role, "voc.agent_id": agent_id, "voc.rev": rev},
        ):
            try:
                # The real, invokable Port AI agent.
                await port.upsert_entity(
                    PORT_AI_BLUEPRINT,
                    {
                        "identifier": agent_id,
                        "title": f"{role} — {doc['title'][:40]}",
                        "properties": {
                            "description": member.mission,
                            "status": "active",
                            "tools": tools,
                            "prompt": prompt,
                            "execution_mode": mode,
                        },
                    },
                )
                # Our record of why this agent exists and which PRD it serves.
                await port.upsert_entity(
                    AGENT_BLUEPRINT,
                    {
                        "identifier": agent_id,
                        "title": role,
                        "properties": {
                            "role": role,
                            "mission": member.mission,
                            "justification": member.justification,
                            "status": "active",
                            "spawned_at_rev": previous["spawned_at_rev"] if previous else rev,
                            "port_agent_id": agent_id,
                        },
                        "relations": {"prd": prd_id},
                    },
                )
            except Exception as e:  # noqa: BLE001
                obs.record_error("factory", f"agent_{action}", e)
                log.warning("could not %s %s: %s", action, role, e)
                continue

        crew[role] = {
            "mission": member.mission,
            "status": "active",
            "agent_id": agent_id,
            "spawned_at_rev": previous["spawned_at_rev"] if previous else rev,
            # Port validates required fields even on a merge write, so
            # deactivating later means resending the whole agent.
            "prompt": prompt,
            "description": member.mission,
        }
        obs.count(obs.agents_counter, action=action, role=role)
        log.info("%s %s (rev %d)", action, role, rev)
        await bus.publish(
            Event(
                type=EventType.FACTORY_DISPATCHED,
                payload={
                    "action": action,
                    "role": role,
                    "mission": member.mission,
                    "justification": member.justification,
                    "agent_id": agent_id,
                    "rev": rev,
                    "url": PortClient.entity_url(AGENT_BLUEPRINT, agent_id),
                },
                trace_context=obs.inject(),
            )
        )

    # Roles the PRD no longer justifies are retired, not deleted — the
    # reversal that dropped them should stay visible in the catalog.
    for role, state in list(crew.items()):
        if role in required or state["status"] == "retired":
            continue
        agent_id = f"voc_{slug(role)}_{session_id}"
        reason = f"No longer required by PRD revision {rev}"

        with obs.span(
            "factory.retired",
            attributes={"voc.role": role, "voc.agent_id": agent_id, "voc.rev": rev},
        ):
            try:
                await port.upsert_entity(
                    PORT_AI_BLUEPRINT,
                    {
                        "identifier": agent_id,
                        "properties": {
                            "status": "inactive",
                            "description": state["description"],
                            "tools": agent_capabilities(role)[0],
                            "prompt": state["prompt"],
                        },
                    },
                )
                await port.upsert_entity(
                    AGENT_BLUEPRINT,
                    {
                        "identifier": agent_id,
                        "properties": {
                            "status": "retired",
                            "retired_at_rev": rev,
                            "retire_reason": reason,
                        },
                    },
                )
            except Exception as e:  # noqa: BLE001
                obs.record_error("factory", "agent_retire", e)
                log.warning("could not retire %s: %s", role, e)
                continue

        state["status"] = "retired"
        obs.count(obs.agents_counter, action="retired", role=role)
        log.info("retired %s (rev %d)", role, rev)
        await bus.publish(
            Event(
                type=EventType.FACTORY_DISPATCHED,
                payload={
                    "action": "retired",
                    "role": role,
                    "mission": state["mission"],
                    "reason": reason,
                    "agent_id": agent_id,
                    "rev": rev,
                    "url": PortClient.entity_url(AGENT_BLUEPRINT, agent_id),
                },
                trace_context=obs.inject(),
            )
        )

    return False
