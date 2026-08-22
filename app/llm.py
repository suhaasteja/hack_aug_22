"""Shared Gemini access for every module that needs generation.

Structured output only: callers pass a Pydantic schema and get a validated
model back, so no module hand-parses model text. Model name comes from
config (`llm.model`) rather than being hardcoded per module.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from typing import Any, TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from app import observability as obs

log = logging.getLogger("llm")

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "gemini-3.7-flash"

_client: genai.Client | None = None


class LLMUnavailable(RuntimeError):
    """Raised when the API key is missing, so callers can degrade instead of crash."""


def get_client() -> genai.Client:
    global _client
    if _client is None:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise LLMUnavailable("GEMINI_API_KEY is not set")
        _client = genai.Client(api_key=key)
    return _client


async def generate_json(
    *,
    prompt: str,
    schema: type[T],
    system: str,
    config: dict[str, Any] | None = None,
    retries: int = 2,
) -> T:
    """Generate a response validated against `schema`, retrying transient failures."""
    model = (config or {}).get("llm", {}).get("model", DEFAULT_MODEL)
    client = get_client()

    # Fault injection, off unless chaos.llm_fail_rate is set. Every module
    # generates through here, so this produces genuine failures — real error
    # spans, real metrics, a real alert — rather than a faked one.
    # target_schema narrows the blast radius to one module's call, so a demo
    # can break enrichment without also breaking the PRD it feeds.
    chaos = (config or {}).get("chaos", {})
    target = chaos.get("target_schema")
    fail_rate = (
        float(chaos.get("llm_fail_rate", 0.0))
        if not target or target == schema.__name__
        else 0.0
    )

    last: Exception | None = None
    for attempt in range(retries + 1):
        if fail_rate and random.random() < fail_rate:
            last = RuntimeError("injected fault: simulated Gemini API failure")
            log.warning("chaos: failing generate for %s", schema.__name__)
            if attempt < retries:
                continue
            raise RuntimeError(f"generation failed after {retries + 1} attempts: {last}") from last

        started = time.monotonic()
        with obs.span(
            f"llm.generate {schema.__name__}",
            attributes={
                "gen_ai.system": "gemini",
                "gen_ai.request.model": model,
                "gen_ai.response.schema": schema.__name__,
                "llm.attempt": attempt,
            },
        ) as s:
            try:
                response = await client.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=schema,
                        system_instruction=system,
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(
                            disable=True
                        ),
                    ),
                )
                if response.parsed is None:
                    raise ValueError("model returned no parseable JSON")
                if usage := getattr(response, "usage_metadata", None):
                    s.set_attribute("gen_ai.usage.input_tokens", usage.prompt_token_count or 0)
                    s.set_attribute("gen_ai.usage.output_tokens", usage.candidates_token_count or 0)
                obs.observe(
                    obs.llm_latency,
                    (time.monotonic() - started) * 1000,
                    model=model,
                    schema=schema.__name__,
                    outcome="ok",
                )
                return response.parsed
            except Exception as e:  # noqa: BLE001 - retry any transient API/parse failure
                last = e
                obs.observe(
                    obs.llm_latency,
                    (time.monotonic() - started) * 1000,
                    model=model,
                    schema=schema.__name__,
                    outcome="error",
                )
                if attempt < retries:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    log.warning("generate retry %d/%d: %s", attempt + 1, retries, e)

    raise RuntimeError(f"generation failed after {retries + 1} attempts: {last}") from last
