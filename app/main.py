"""Pipeline entrypoint: boot the bus, start enabled modules, run until Ctrl-C.

Each module package exposes `async def run(bus, config, module_config)` and is
discovered by name under app.modules — adding a feature means adding a module
directory and a config block, nothing else.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from app import observability
from app.bus import EventBus

log = logging.getLogger("main")


def load_config(path: str = "config.yaml") -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text())


async def run(config_path: str = "config.yaml") -> None:
    config = load_config(config_path)
    # One id for the whole meeting. Modules key their external records off this
    # rather than off PRD content, which changes from revision to revision.
    config.setdefault("session", {})["id"] = f"m{int(time.time())}"
    observability.setup(config)
    bus = EventBus()

    modules = {
        name: mod_cfg
        for name, mod_cfg in config.get("modules", {}).items()
        if mod_cfg and mod_cfg.get("enabled")
    }
    log.info("bus ready, modules loaded: %s", sorted(modules))

    tasks = []
    for name, mod_cfg in modules.items():
        module = importlib.import_module(f"app.modules.{name}")
        if not hasattr(module, "run"):
            raise RuntimeError(f"module '{name}' has no run(bus, config, module_config)")
        tasks.append(asyncio.create_task(module.run(bus, config, mod_cfg), name=name))

    if not tasks:
        log.info("no modules enabled; idling (Ctrl-C to exit)")
        await asyncio.Event().wait()

    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for t in done:
            if not t.cancelled() and t.exception():
                log.error("module '%s' crashed", t.get_name(), exc_info=t.exception())
    finally:
        # Cancelling is not enough: a task destroyed while still unwinding logs
        # a scary traceback on Ctrl-C and leaves its server socket open. Wait
        # for each one to actually finish cancelling.
        for t in tasks:
            t.cancel()
        # Bounded: one module refusing to unwind must not hang the exit.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=5
            )


async def _serve(config_path: str) -> None:
    """Run the pipeline until a signal asks it to stop.

    Signals are handled on the loop rather than through KeyboardInterrupt:
    the modules embed HTTP servers and sit in network calls, and relying on
    the exception arriving at the right await is what made Ctrl-C hang.
    """
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    pipeline = asyncio.create_task(run(config_path), name="pipeline")
    waiter = asyncio.create_task(stop.wait(), name="signal")
    done, pending = await asyncio.wait(
        {pipeline, waiter}, return_when=asyncio.FIRST_COMPLETED
    )

    if waiter in done:
        log.info("shutting down")
    for t in pending:
        t.cancel()
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True), timeout=6
        )
    if pipeline in done and not pipeline.cancelled() and pipeline.exception():
        raise pipeline.exception()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-10s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    try:
        asyncio.run(_serve(config_path))
    except (KeyboardInterrupt, asyncio.CancelledError):
        # Ctrl-C is how this is meant to end. Exit 0 so `make run` does not
        # report a failure for a normal shutdown.
        log.info("shutting down")


if __name__ == "__main__":
    main()
