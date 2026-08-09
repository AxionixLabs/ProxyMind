# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace

import pytest

from mind_app.subscription.models import AgentConfig
from mind_app.subscription.runtime import AgentRuntime


def _config() -> AgentConfig:
    return AgentConfig(
        base_url="https://example.test",
        device_id="device-1",
        agent_id="agent-1",
        client_version="1.0.0",
        platform="darwin",
        arch="arm64",
    )


@pytest.mark.anyio
async def test_agent_runtime_starts_once_and_stops_listener() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def run() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    runtime = AgentRuntime(
        SimpleNamespace(),
        config=_config(),
        client=SimpleNamespace(),
        supervisor=SimpleNamespace(run=run),
    )

    first = runtime.start_background()
    second = runtime.start_background()
    await started.wait()

    assert first is second
    assert runtime.is_running()

    await runtime.stop()

    assert cancelled.is_set()
    assert not runtime.is_running()


@pytest.mark.anyio
async def test_agent_runtime_consumes_background_failure() -> None:
    async def run() -> None:
        raise RuntimeError("listener failed")

    runtime = AgentRuntime(
        SimpleNamespace(),
        config=_config(),
        client=SimpleNamespace(),
        supervisor=SimpleNamespace(run=run),
    )

    task = runtime.start_background()
    results = await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)

    assert isinstance(results[0], RuntimeError)
    assert not runtime.is_running()
