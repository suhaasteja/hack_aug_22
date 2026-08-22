"""Modules are discovered from config, so a feature can be toggled off without code changes."""

import asyncio
import sys
import types

import pytest

from app import main


@pytest.fixture
def fake_module(monkeypatch):
    """Register an importable app.modules.<name> that records when it runs."""
    started = asyncio.Event()

    def register(name: str):
        mod = types.ModuleType(f"app.modules.{name}")

        async def run(bus, config, module_config):
            started.set()
            await asyncio.Event().wait()  # module runs until cancelled

        mod.run = run
        monkeypatch.setitem(sys.modules, f"app.modules.{name}", mod)
        return started

    return register


@pytest.mark.asyncio
async def test_disabled_module_never_starts(fake_module, tmp_path):
    started = fake_module("ghost")
    cfg = tmp_path / "config.yaml"
    cfg.write_text("modules:\n  ghost:\n    enabled: false\n")

    task = asyncio.create_task(main.run(str(cfg)))
    await asyncio.sleep(0.05)
    task.cancel()

    assert not started.is_set()


@pytest.mark.asyncio
async def test_enabled_module_starts(fake_module, tmp_path):
    started = fake_module("ghost")
    cfg = tmp_path / "config.yaml"
    cfg.write_text("modules:\n  ghost:\n    enabled: true\n")

    task = asyncio.create_task(main.run(str(cfg)))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()


@pytest.mark.asyncio
async def test_module_without_run_fails_loudly(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "app.modules.broken", types.ModuleType("app.modules.broken"))
    cfg = tmp_path / "config.yaml"
    cfg.write_text("modules:\n  broken:\n    enabled: true\n")

    with pytest.raises(RuntimeError, match="has no run"):
        await main.run(str(cfg))
