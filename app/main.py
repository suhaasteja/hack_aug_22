"""Pipeline entrypoint: boot the bus, start enabled modules, run until Ctrl-C.

Each module package exposes `async def run(bus, config, module_config)` and is
discovered by name under app.modules — adding a feature means adding a module
directory and a config block, nothing else.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from app.bus import EventBus

log = logging.getLogger("main")


def load_config(path: str = "config.yaml") -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text())


async def run(config_path: str = "config.yaml") -> None:
    config = load_config(config_path)
    # One id for the whole meeting. Modules key their external records off this
    # rather than off PRD content, which changes from revision to revision.
    config.setdefault("session", {})["id"] = f"m{int(time.time())}"
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

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    for t in done:
        if t.exception():
            log.error("module '%s' crashed", t.get_name(), exc_info=t.exception())
    for t in pending:
        t.cancel()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-10s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    try:
        asyncio.run(run(config_path))
    except KeyboardInterrupt:
        log.info("shutting down")


if __name__ == "__main__":
    main()
