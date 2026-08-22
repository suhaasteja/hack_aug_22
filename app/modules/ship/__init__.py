"""Ship module: turns a Port agent's request into a real repository and PR.

A Port AI agent cannot write code — its tools read the catalog and trigger
self-service actions. So the agent plays architect: it decides the service is
ready to build and calls Port's `scaffold_service` action with a spec. Port
webhooks that here, and this module is the coding agent that implements it.

The repository is created with an initial commit holding the spec, then the
implementation lands on a branch and opens a pull request — so the build
arrives for review the way a human contribution would, and Port's GitHub
integration can ingest the PR.

Swapping in Copilot or Devin later replaces only the generate step; the agent,
the action, and the review flow stay as they are.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app import observability as obs
from app.bus import EventBus
from app.events import Event, EventType
from app.llm import LLMUnavailable, generate_json
from app.port_client import PortClient, PortUnavailable

log = logging.getLogger("ship")

BUILD_DIR = Path("builds")
SERVICE_BLUEPRINT = "voc_service"
BRANCH = "feat/initial-implementation"

CODEGEN_SYSTEM = """You implement a small but genuinely working prototype from a product spec.

Constraints:
- A single self-contained index.html is the product: no build step, no package
  install, no external network calls. Inline all CSS and JS.
- It must actually run and demonstrate the core workflow described in the spec,
  with realistic seeded sample data. Not a mockup with dead buttons.
- Include a README.md explaining what was built and what is stubbed.
- The page must include a "Requirements" view (a tab or panel) rendering the
  product requirements document it was built from, so the running product
  carries its own provenance. Put the document text in the page verbatim.
- 3 to 5 files total. Keep each under 400 lines.
- Write real, working code. No TODO placeholders in place of logic."""


class SourceFile(BaseModel):
    path: str = Field(description="repo-relative path, e.g. index.html")
    content: str


class Build(BaseModel):
    files: list[SourceFile]
    summary: str = Field(description="one sentence on what was built")


SERVICE_BLUEPRINT_SPEC = {
    "identifier": SERVICE_BLUEPRINT,
    "title": "Shipped Service (VoC)",
    "icon": "Github",
    "schema": {
        "properties": {
            "repo_url": {"type": "string", "format": "url", "title": "Repository"},
            "pr_url": {"type": "string", "format": "url", "title": "Pull request"},
            "summary": {"type": "string", "title": "What was built"},
            "built_by_role": {"type": "string", "title": "Requested by"},
            "files": {"type": "number", "title": "Files"},
            "status": {
                "type": "string",
                "title": "Status",
                "enum": ["building", "shipped", "failed"],
                "enumColors": {"building": "yellow", "shipped": "green", "failed": "red"},
            },
        },
        "required": [],
    },
    "relations": {
        "prd": {"title": "PRD", "target": "voc_prd", "required": False, "many": False}
    },
}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")[:60] or "voc-build"


def run_cmd(args: list[str], cwd: Path | None = None) -> str:
    """Run a command, raising with its stderr so failures are diagnosable."""
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=180)
    if p.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])} failed: {(p.stderr or p.stdout)[:300]}")
    return p.stdout.strip()


async def generate(spec: str, config: dict[str, Any]) -> Build:
    return await generate_json(
        prompt=spec, schema=Build, system=CODEGEN_SYSTEM, config=config
    )


def publish_repo(name: str, build: Build, spec: str) -> dict[str, str]:
    """Create the repo with the spec, then open a PR carrying the implementation."""
    BUILD_DIR.mkdir(exist_ok=True)
    work = BUILD_DIR / name
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    # First commit is the brief the agent asked for, so the PR diff is the build.
    (work / "SPEC.md").write_text(f"# Build specification\n\n{spec}\n")
    run_cmd(["git", "init", "-b", "main"], work)
    run_cmd(["git", "add", "-A"], work)
    run_cmd(["git", "commit", "-q", "-m", "Specification from the product agent"], work)

    created = run_cmd(
        ["gh", "repo", "create", name, "--private", "--source", ".", "--push"], work
    )
    # gh prints the repo URL and then git's push output; take the URL, not the tail.
    repo_url = next(
        (l.strip() for l in created.splitlines() if l.strip().startswith("http")), ""
    )

    run_cmd(["git", "checkout", "-q", "-b", BRANCH], work)
    for f in build.files:
        target = work / f.path
        if not target.resolve().is_relative_to(work.resolve()):
            raise RuntimeError(f"refusing path outside the build directory: {f.path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f.content)

    run_cmd(["git", "add", "-A"], work)
    run_cmd(["git", "commit", "-q", "-m", f"Implement: {build.summary}"], work)
    run_cmd(["git", "push", "-q", "-u", "origin", BRANCH], work)

    pr_url = run_cmd(
        [
            "gh", "pr", "create",
            "--title", f"Implement: {build.summary}",
            "--body",
            "Generated from the product requirements captured in a live sales call.\n\n"
            f"{build.summary}\n\nSee `SPEC.md` on `main` for the brief this implements.",
            "--base", "main", "--head", BRANCH,
        ],
        work,
    )
    return {"repo_url": repo_url, "pr_url": pr_url.splitlines()[-1].strip(), "path": str(work)}


class Shipyard:
    def __init__(
        self, bus: EventBus, config: dict[str, Any], mc: dict[str, Any], port: PortClient | None
    ) -> None:
        self.bus = bus
        self.config = config
        self.mc = mc
        self.port = port
        self.max_builds = int(mc.get("max_builds", 3))
        self.builds = 0
        self.prd_id = ""
        self.prd_title = ""
        self.prd_markdown = ""

    async def watch_bus(self) -> None:
        async for event in self.bus.stream({EventType.PRD_UPDATED}):
            self.prd_title = event.payload["doc"]["title"]
            self.prd_markdown = event.payload["markdown"]
            self.prd_id = f"voc_prd_{self.config.get('session', {}).get('id', 'session')}"

    async def build(self, req: dict[str, Any]) -> None:
        if self.builds >= self.max_builds:
            log.warning("build cap of %d reached; ignoring request", self.max_builds)
            return
        self.builds += 1

        name = slug(req.get("repo_name") or self.prd_title or "voc-build")
        spec = (
            f"Product: {self.prd_title or req.get('repo_name')}\n\n"
            f"What to build:\n{req.get('summary', '')}\n\n"
            f"Implementation notes:\n{req.get('notes', '')}\n\n"
            "--- PRODUCT REQUIREMENTS DOCUMENT (render this in the Requirements "
            f"view verbatim) ---\n{self.prd_markdown}"
        )
        run_id = req.get("run_id", "")

        with obs.span("ship.build", attributes={"voc.repo": name}) as s:
            await self.bus.publish(
                Event(
                    type=EventType.BUILD_SHIPPED,
                    payload={"stage": "building", "repo": name, "summary": req.get("summary", "")},
                    trace_context=obs.inject(),
                )
            )
            try:
                build = await generate(spec, self.config)
                s.set_attribute("voc.files", len(build.files))
                # Git and gh are blocking; keep them off the event loop.
                result = await asyncio.to_thread(publish_repo, name, build, spec)
            except LLMUnavailable as e:
                log.error("%s — cannot build", e)
                await self._report(run_id, "FAILURE", str(e))
                return
            except Exception as e:  # noqa: BLE001
                obs.record_error("ship", "build", e)
                log.warning("build failed for %s: %s", name, e)
                await self._report(run_id, "FAILURE", str(e)[:400])
                await self.bus.publish(
                    Event(
                        type=EventType.BUILD_SHIPPED,
                        payload={"stage": "failed", "repo": name, "error": str(e)[:300]},
                        trace_context=obs.inject(),
                    )
                )
                return

            log.info("shipped %s — %s", name, result["pr_url"])
            s.set_attributes({"voc.repo_url": result["repo_url"], "voc.pr_url": result["pr_url"]})

            await self._register(name, build, result, req)
            await self._report(
                run_id, "SUCCESS", f"Opened {result['pr_url']} with {len(build.files)} files"
            )
            await self.bus.publish(
                Event(
                    type=EventType.BUILD_SHIPPED,
                    payload={
                        "stage": "shipped",
                        "repo": name,
                        "summary": build.summary,
                        "repo_url": result["repo_url"],
                        "pr_url": result["pr_url"],
                        "files": len(build.files),
                        "requested_by": req.get("role", ""),
                    },
                    trace_context=obs.inject(),
                )
            )

    async def _register(
        self, name: str, build: Build, result: dict[str, str], req: dict[str, Any]
    ) -> None:
        if not self.port:
            return
        try:
            await self.port.upsert_entity(
                SERVICE_BLUEPRINT,
                {
                    "identifier": name,
                    "title": name,
                    "properties": {
                        "repo_url": result["repo_url"],
                        "pr_url": result["pr_url"],
                        "summary": build.summary,
                        "built_by_role": req.get("role", ""),
                        "files": len(build.files),
                        "status": "shipped",
                    },
                    "relations": {"prd": self.prd_id} if self.prd_id else {},
                },
            )
        except Exception as e:  # noqa: BLE001 - the repo exists either way
            obs.record_error("ship", "register_service", e)
            log.warning("could not register %s in Port: %s", name, e)

    async def _report(self, run_id: str, status: str, message: str) -> None:
        """Close the Port action run so the agent sees the outcome."""
        if not (self.port and run_id):
            return
        try:
            await self.port._request(
                "PATCH",
                f"/actions/runs/{run_id}",
                json={"status": status, "statusText": message[:500]},
            )
        except Exception as e:  # noqa: BLE001
            log.warning("could not report run %s: %s", run_id, e)


def build_app(yard: Shipyard, secret: str) -> FastAPI:
    app = FastAPI(title="VoC Shipyard")

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ok": True, "builds": yard.builds, "cap": yard.max_builds}

    @app.post("/scaffold")
    async def scaffold(
        request: Request, x_voc_token: str = Header(default="")
    ) -> dict[str, Any]:
        # This endpoint is reachable from the public internet through the tunnel
        # Port needs, and it creates repositories. It requires the shared secret.
        if not secret or x_voc_token != secret:
            raise HTTPException(status_code=401, detail="bad or missing X-VoC-Token")
        req = await request.json()
        log.info("scaffold requested: %s", req.get("repo_name"))
        asyncio.create_task(yard.build(req))  # return fast; Port's run is async
        return {"accepted": True, "repo": slug(req.get("repo_name", ""))}

    return app


async def run(bus: EventBus, config: dict[str, Any], module_config: dict[str, Any]) -> None:
    secret = os.environ.get("VOC_SHIP_TOKEN", "")
    if not secret:
        log.error("VOC_SHIP_TOKEN is not set — ship disabled (the endpoint is internet-facing)")
        return

    try:
        port: PortClient | None = PortClient()
    except PortUnavailable as e:
        log.warning("%s — builds will not be registered in Port", e)
        port = None

    if port:
        with contextlib.suppress(Exception):
            await port.ensure_blueprint(SERVICE_BLUEPRINT_SPEC)

    yard = Shipyard(bus, config, module_config, port)
    host = module_config.get("host", "0.0.0.0")
    port_num = int(module_config.get("port", 7002))

    server = uvicorn.Server(
        uvicorn.Config(build_app(yard, secret), host=host, port=port_num, log_level="warning")
    )
    serving = asyncio.create_task(server.serve())
    log.info("shipyard on http://%s:%d/scaffold (cap %d builds)", host, port_num, yard.max_builds)

    try:
        await yard.watch_bus()
    finally:
        server.should_exit = True
        with contextlib.suppress(asyncio.CancelledError):
            await serving
        if port:
            await port.aclose()
