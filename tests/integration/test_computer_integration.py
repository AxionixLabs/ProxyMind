import asyncio
import base64
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agent.application.turns.context import ToolInvocation
from agent.ports.desktop import (
    DesktopBounds,
    DesktopError,
    DesktopImage,
    DesktopObservation,
    DesktopSessionPort,
    DesktopWindow,
    DesktopWindows,
)
from tests.fixtures.computer import computer_harness


def native_session() -> AsyncMock:
    session = AsyncMock(spec=DesktopSessionPort)
    bounds = DesktopBounds(-100, 20, 1, 1)
    session.list_windows.return_value = DesktopWindows((DesktopWindow("window", "fixture", 10, bounds, 1, False),), False)
    session.observe.return_value = DesktopObservation("window", "observation", "2026-11-05T00:00:00Z", bounds,
        1, "physical_screen_pixels", (), False, None, DesktopImage(1, 1, base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg=="), "image/png"))
    return session


@pytest.mark.anyio
async def test_js_computer_preserves_image_metadata_and_reset_scope(tmp_path: Path) -> None:
    native = native_session()
    factory = AsyncMock(return_value=native)
    async with computer_harness(tmp_path, factory) as harness:
        first = await harness.execute("""
            var listing = await host.tool('computer', {action:'list_windows'});
            var observed = await host.tool('computer', {action:'observe', window:listing.data.windows[0].window, screenshot:true});
            console.log(JSON.stringify(observed.data));
            await host.emitImage(observed);
        """, "call_observe")
        assert first.result.ok, first.result.text
        assert json.loads(first.result.fields["data"]["output"])["observation"] == "observation"
        assert first.result.fields["attachments"][0]["mime_type"] == "image/png"
        assert factory.await_count == 1
        assert harness.interaction.present_approval.await_count == 2
        requests = [call.args[0] for call in harness.interaction.present_approval.await_args_list]
        assert all(request.presentation.context.kind == "computer" for request in requests)
        assert len({request.key.call_id for request in requests}) == 2
        assert [call.args[0].name for call in harness.hooks.run_invocation.await_args_list] == ["js_repl", "computer", "computer"]

        reset = await harness.execute("await host.tool('computer', {action:'reset'}); console.log(observed.data.observation);", "call_reset")
        assert reset.result.ok and reset.result.fields["data"]["output"] == "observation"
        native.close.assert_awaited_once()
        assert harness.interaction.present_approval.await_count == 2
        assert (await harness.execute("await host.tool('computer', {action:'list_windows'});", "call_after_reset")).result.ok
        assert factory.await_count == 2
    assert native.close.await_count == 2


@pytest.mark.anyio
@pytest.mark.parametrize("screenshot", [False, True])
async def test_js_observation_uses_same_data_path_with_or_without_image(tmp_path: Path, screenshot: bool) -> None:
    native = native_session()
    if not screenshot:
        native.observe.return_value = replace(native.observe.return_value, image=None)
    async with computer_harness(tmp_path, AsyncMock(return_value=native)) as harness:
        result = await harness.execute(f"""
            var listing = await host.tool('computer', {{action:'list_windows'}});
            var shot = await host.tool('computer', {{action:'observe', window:listing.data.windows[0].window,
                screenshot:{str(screenshot).lower()}}});
            console.log(JSON.stringify({{token:shot.data.observation, nodes:shot.data.tree.nodes,
                image:shot.data.image}}));
            if (shot.data.image) await host.emitImage(shot);
        """, "call_metadata")
        assert result.result.ok, result.result.text
        metadata = json.loads(result.result.fields["data"]["output"])
        assert metadata["token"] == "observation" and metadata["nodes"] == []
        assert (metadata["image"] is not None) == screenshot
        assert bool(result.result.fields.get("attachments")) == screenshot


@pytest.mark.anyio
async def test_js_input_failure_preserves_code_without_replay(tmp_path: Path) -> None:
    native = native_session()
    native.activate.side_effect = DesktopError("input_busy", "Windows reports virtual key 0x10 held; no input was submitted.")
    async with computer_harness(tmp_path, AsyncMock(return_value=native)) as harness:
        result = await harness.execute("""
            try { await host.tool('computer', {action:'activate',window:'window'}); }
            catch (error) { console.log(error.message); }
        """, "call_busy")
        assert result.result.ok
        assert "input_busy:" in result.result.fields["data"]["output"]
        assert "0x10" in result.result.fields["data"]["output"]
        native.activate.assert_awaited_once_with("window")
        native.input.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(("enabled", "decision", "arguments"), [
    (False, "accept", "{action:'list_windows'}"),
    (True, "decline", "{action:'list_windows'}"),
    (True, "accept", "{action:'list_windows', extra:true}"),
])
async def test_nested_visibility_schema_and_denial_prevent_native_start(
    tmp_path: Path, enabled: bool, decision: str, arguments: str,
) -> None:
    factory = AsyncMock(return_value=native_session())
    async with computer_harness(tmp_path, factory, enabled=enabled, decision=decision) as harness:
        result = await harness.execute(f"await host.tool('computer', {arguments});", "call_denied")
        assert not result.result.ok
        factory.assert_not_called()
        assert harness.interaction.present_approval.await_count == (1 if decision == "decline" else 0)


@pytest.mark.anyio
async def test_cancelling_js_nested_observation_closes_native_once(tmp_path: Path) -> None:
    native = native_session()
    started = asyncio.Event()

    async def blocked() -> DesktopWindows:
        started.set()
        await asyncio.Future()
        return DesktopWindows((), False)

    native.list_windows.side_effect = blocked
    async with computer_harness(tmp_path, AsyncMock(return_value=native)) as harness:
        running = asyncio.create_task(harness.execute("await host.tool('computer', {action:'list_windows'});", "call_cancel"))
        try:
            await asyncio.wait_for(started.wait(), 10)
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
    native.list_windows.assert_awaited_once()
    native.close.assert_awaited_once()


@pytest.mark.anyio
async def test_approval_wait_does_not_consume_js_budget_and_cancel_retires_card(tmp_path: Path) -> None:
    factory = AsyncMock(return_value=native_session())
    async with computer_harness(tmp_path, factory) as harness:
        entered, release = asyncio.Event(), asyncio.Event()

        async def decide(request) -> str:
            entered.set()
            await release.wait()
            return "accept"

        harness.interaction.present_approval.side_effect = decide
        call = ToolInvocation(turn=harness.turn, name="js_repl", call_id="call_wait_approval",
            arguments={"code": "await host.tool('computer', {action:'list_windows'});", "timeout_ms": 400})
        pending = asyncio.create_task(harness.runner.execute(call, use_coding_trace=True))
        await asyncio.wait_for(entered.wait(), 5)
        await asyncio.sleep(0.6)
        assert not pending.done()
        release.set()
        assert (await pending).result.ok

        entered.clear()
        release.clear()
        pending = asyncio.create_task(harness.execute("await host.tool('computer', {action:'list_windows'});", "call_cancel_approval"))
        await asyncio.wait_for(entered.wait(), 5)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert factory.await_count == 1
        assert harness.interaction.present_approval.await_count == 2
        assert harness.approvals.snapshot.unresolved_count == 0
