"""Minimal Port API client.

Port access tokens are short-lived, so the client exchanges client
credentials on demand and refreshes before expiry rather than holding one
token for the length of a meeting.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

log = logging.getLogger("port")

API = "https://api.port.io/v1"
REFRESH_MARGIN = 120.0  # refresh this many seconds before the token actually expires


class PortUnavailable(RuntimeError):
    """Raised when credentials are missing, so the factory can degrade instead of crash."""


class PortClient:
    def __init__(self, client_id: str | None = None, client_secret: str | None = None) -> None:
        self.client_id = client_id or os.environ.get("PORT_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("PORT_CLIENT_SECRET", "")
        if not (self.client_id and self.client_secret):
            raise PortUnavailable("PORT_CLIENT_ID / PORT_CLIENT_SECRET are not set")
        self._http = httpx.AsyncClient(timeout=30.0)
        self._token = ""
        self._expires_at = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _auth_header(self) -> dict[str, str]:
        if time.time() >= self._expires_at:
            r = await self._http.post(
                f"{API}/auth/access_token",
                json={"clientId": self.client_id, "clientSecret": self.client_secret},
            )
            r.raise_for_status()
            body = r.json()
            self._token = body["accessToken"]
            self._expires_at = time.time() + float(body.get("expiresIn", 3600)) - REFRESH_MARGIN
            log.debug("refreshed Port token")
        return {"Authorization": f"Bearer {self._token}"}

    async def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        headers = {**(await self._auth_header()), **kw.pop("headers", {})}
        return await self._http.request(method, f"{API}{path}", headers=headers, **kw)

    async def blueprint_exists(self, identifier: str) -> bool:
        r = await self._request("GET", f"/blueprints/{identifier}")
        return r.status_code == 200

    async def ensure_blueprint(self, blueprint: dict[str, Any]) -> None:
        """Create the blueprint, or update it in place if it already exists."""
        identifier = blueprint["identifier"]
        if await self.blueprint_exists(identifier):
            r = await self._request("PUT", f"/blueprints/{identifier}", json=blueprint)
            action = "updated"
        else:
            r = await self._request("POST", "/blueprints", json=blueprint)
            action = "created"
        if r.status_code >= 400:
            raise RuntimeError(f"blueprint {identifier} {action} failed: {r.status_code} {r.text[:300]}")
        log.info("blueprint %s %s", identifier, action)

    async def upsert_entity(self, blueprint: str, entity: dict[str, Any]) -> dict[str, Any]:
        r = await self._request(
            "POST",
            f"/blueprints/{blueprint}/entities",
            params={"upsert": "true", "merge": "true", "create_missing_related_entities": "true"},
            json=entity,
        )
        if r.status_code >= 400:
            raise RuntimeError(
                f"entity {entity.get('identifier')} upsert failed: {r.status_code} {r.text[:300]}"
            )
        return r.json().get("entity", {})

    async def list_entities(self, blueprint: str) -> list[dict[str, Any]]:
        r = await self._request("GET", f"/blueprints/{blueprint}/entities")
        if r.status_code >= 400:
            return []
        return r.json().get("entities", [])

    async def invoke_agent(
        self, agent_id: str, prompt: str, labels: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Invoke a Port AI agent and collect its streamed answer.

        Port streams the run as server-sent events and finishes with `done`,
        so the reply is available inline — no polling needed to know an agent
        has finished and a follow-on can start.
        """
        headers = {**(await self._auth_header()), "Content-Type": "application/json"}
        chunks: list[str] = []
        invocation_id = ""
        finished = False

        async with self._http.stream(
            "POST",
            f"{API}/agent/{agent_id}/invoke",
            headers=headers,
            json={"prompt": prompt, "labels": labels or {}},
            timeout=180.0,
        ) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode()[:300]
                raise RuntimeError(f"agent {agent_id} invoke failed: {response.status_code} {body}")

            event_name = ""
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    # SSE strips exactly one space after the colon, and Port
                    # streams one token per event — stripping all whitespace
                    # here runs the reply's words together.
                    data = line[5:]
                    if data.startswith(" "):
                        data = data[1:]
                    if event_name == "execution":
                        # SSE cannot carry raw newlines either, so restore the
                        # escaped ones or the reply arrives as one long line.
                        chunks.append(data.replace("\\n", "\n").replace("\\t", "\t"))
                    elif event_name == "invocationIdentifier":
                        invocation_id = data.strip()
                    elif event_name == "done":
                        finished = True

        return {
            "agent_id": agent_id,
            "invocation_id": invocation_id,
            "response": "".join(chunks).strip(),
            "finished": finished,
        }

    @staticmethod
    def entity_url(blueprint: str, identifier: str) -> str:
        return f"https://app.port.io/{blueprint}Entity?identifier={identifier}"
