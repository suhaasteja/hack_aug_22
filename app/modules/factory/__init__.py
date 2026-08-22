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

import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.bus import EventBus
from app.events import Event, EventType
from app.llm import LLMUnavailable, generate_json
from app.port_client import PortClient, PortUnavailable

log = logging.getLogger("factory")

PRD_BLUEPRINT = "voc_prd"
AGENT_BLUEPRINT = "voc_product_agent"
PORT_AI_BLUEPRINT = "_ai_agent"

# Read-only Port tools: spawned agents investigate the catalog, they don't mutate it.
AGENT_TOOLS = ["^(list|get|search|track|describe)_.*"]

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

    try:
        async for event in bus.stream({EventType.PRD_UPDATED}):
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
                log.warning("PRD entity upsert failed at rev %d: %s", rev, e)
                continue

            try:
                derived = await derive_crew(doc, config)
            except LLMUnavailable as e:
                log.error("%s — factory disabled for this session", e)
                return
            except Exception as e:  # noqa: BLE001
                log.warning("crew derivation failed at rev %d: %s", rev, e)
                continue

            required = {m.role: m for m in derived.members}

            for role, member in required.items():
                previous = crew.get(role)
                if previous and previous["mission"] == member.mission and previous["status"] == "active":
                    continue  # unchanged; don't churn Port

                agent_id = f"voc_{slug(role)}_{session_id}"
                action = "updated" if previous else "spawned"
                prompt = agent_prompt(member, doc["title"], event.payload["markdown"])

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
                                "tools": AGENT_TOOLS,
                                "prompt": prompt,
                                "execution_mode": "Approval Required",
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
                    log.warning("could not %s %s: %s", action, role, e)
                    continue

                crew[role] = {
                    "mission": member.mission,
                    "status": "active",
                    "spawned_at_rev": previous["spawned_at_rev"] if previous else rev,
                    # Port validates required fields even on a merge write, so
                    # deactivating later means resending the whole agent.
                    "prompt": prompt,
                    "description": member.mission,
                }
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
                        trace_context=event.trace_context,
                    )
                )

            # Roles the PRD no longer justifies are retired, not deleted — the
            # reversal that dropped them should stay visible in the catalog.
            for role, state in list(crew.items()):
                if role in required or state["status"] == "retired":
                    continue
                agent_id = f"voc_{slug(role)}_{session_id}"
                reason = f"No longer required by PRD revision {rev}"
                try:
                    await port.upsert_entity(
                        PORT_AI_BLUEPRINT,
                        {
                            "identifier": agent_id,
                            "properties": {
                                "status": "inactive",
                                "description": state["description"],
                                "tools": AGENT_TOOLS,
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
                    log.warning("could not retire %s: %s", role, e)
                    continue

                state["status"] = "retired"
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
                        trace_context=event.trace_context,
                    )
                )
    finally:
        await port.aclose()
