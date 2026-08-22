"""Listen module: emits transcript.segment events from a pluggable driver.

Drivers implement `async def run(bus, config, module_config)`. The synthetic
driver replays a scripted meeting; reachy/mic drivers plug in later behind
the same interface.
"""

from __future__ import annotations

import importlib
from typing import Any

from app.bus import EventBus

DRIVERS = {
    "synthetic": "app.modules.listen.synthetic",
    # What a real transcriber posts into. The robot will use this one.
    "http": "app.modules.listen.http_ingest",
}


async def run(bus: EventBus, config: dict[str, Any], module_config: dict[str, Any]) -> None:
    driver_name = module_config.get("driver", "synthetic")
    if driver_name not in DRIVERS:
        raise RuntimeError(
            f"listen driver '{driver_name}' not implemented; available: {sorted(DRIVERS)}"
        )
    driver = importlib.import_module(DRIVERS[driver_name])
    await driver.run(bus, config, module_config)
