# -*- coding: utf-8 -*-

import io
import asyncio
from rich.console import Console
from mind_app.assets import EntryUpgradeProgress
from mind_core.design import Design


def test_design_instance_uses_bound_console() -> None:
    """Design 实例展示只写入构造时绑定的控制台。"""
    stream = io.StringIO()
    console = Console(
        file=stream,
        force_terminal=False,
        color_system=None,
    )
    design = Design(console=console)

    design.show_done()

    assert design.console is console
    assert "console" not in Design.__dict__
    assert "Task Done" in stream.getvalue()


def test_entry_upgrade_progress_uses_injected_design() -> None:
    """入口升级动画通过注入的 Design 实例启动。"""
    calls: list[tuple[dict, asyncio.Event]] = []

    class DummyDesign(object):
        async def download_animation(self, state: dict, stop_event: asyncio.Event) -> None:
            calls.append((state, stop_event))

    class DummyAnimationManager(object):
        async def start(self, factory) -> None:
            stop_event = asyncio.Event()
            await factory(stop_event)

        async def stop(self) -> None:
            return None

    state = {"progress": 0.5}
    progress = EntryUpgradeProgress(
        DummyAnimationManager(),
        DummyDesign(),
    )

    asyncio.run(progress.start(state))

    assert len(calls) == 1
    assert calls[0][0] is state
