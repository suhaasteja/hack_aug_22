"""Read side of SigNoz: pull back the telemetry an alert refers to.

The alert itself only says a threshold was crossed. To act on it, an agent
needs what actually happened — the error logs and the failing spans — which
is what these queries fetch.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

log = logging.getLogger("signoz")


class SigNozUnavailable(RuntimeError):
    """Raised when the API key is missing, so the loop can degrade instead of crash."""


class SigNozClient:
    def __init__(self, base_url: str = "http://localhost:8080", api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get("SIGNOZ_API_KEY", "")
        if not self.api_key:
            raise SigNozUnavailable("SIGNOZ_API_KEY is not set")
        self._http = httpx.AsyncClient(timeout=30.0)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _query(self, signal: str, expression: str, limit: int, minutes: int) -> list[dict[str, Any]]:
        now = int(time.time() * 1000)
        payload = {
            "schemaVersion": "v1",
            "start": now - minutes * 60_000,
            "end": now,
            "requestType": "raw",
            "compositeQuery": {
                "queries": [
                    {
                        "type": "builder_query",
                        "spec": {
                            "name": "A",
                            "signal": signal,
                            "limit": limit,
                            "filter": {"expression": expression},
                        },
                    }
                ]
            },
        }
        r = await self._http.post(
            f"{self.base_url}/api/v5/query_range",
            headers={"SIGNOZ-API-KEY": self.api_key, "Content-Type": "application/json"},
            json=payload,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"signoz query failed: {r.status_code} {r.text[:200]}")
        results = r.json().get("data", {}).get("data", {}).get("results", [])
        return results[0].get("rows", []) if results else []

    async def error_logs(
        self, service: str, minutes: int = 15, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Recent warning/error log lines, trimmed to what a responder needs."""
        rows = await self._query(
            "logs",
            f"service.name = '{service}' AND severity_text IN ('WARN', 'WARNING', 'ERROR')",
            limit,
            minutes,
        )
        out = []
        for row in rows:
            d = row.get("data", {})
            out.append(
                {
                    "timestamp": d.get("timestamp"),
                    "severity": d.get("severity_text"),
                    "body": (d.get("body") or "")[:400],
                    "trace_id": d.get("trace_id") or "",
                    "logger": d.get("attributes_string", {}).get("code.function", ""),
                }
            )
        return out

    async def error_spans(
        self, service: str, minutes: int = 15, limit: int = 15
    ) -> list[dict[str, Any]]:
        """Recent failing spans, including the business attributes they carry."""
        rows = await self._query(
            "traces", f"service.name = '{service}' AND has_error = true", limit, minutes
        )
        out = []
        for row in rows:
            d = row.get("data", {})
            attrs = {
                k: v
                for k, v in {**d.get("attributes_string", {}), **d.get("attributes_number", {})}.items()
                if k.startswith("voc.") or k.startswith("gen_ai.")
            }
            out.append(
                {
                    "name": d.get("name"),
                    "trace_id": d.get("trace_id"),
                    "span_id": d.get("span_id"),
                    "status_message": d.get("status_message", "")[:300],
                    "duration_ms": round((d.get("duration_nano") or 0) / 1e6, 1),
                    "attributes": attrs,
                }
            )
        return out
