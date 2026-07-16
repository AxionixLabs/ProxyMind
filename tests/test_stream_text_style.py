# -*- coding: utf-8 -*-

import asyncio

from mind_app.stream_render.coordinator import RenderCoord
from mind_app.stream_state.text import TextState
from mind_core.design import Design
from mind_core.design import utils as design_utils
from mind_core.live_session import TypewriterStreamSession


def is_bold(renderable, offset: int) -> bool:
    """判断 Rich 文本指定位置是否启用了粗体。"""
    return renderable.get_style_at_offset(Design.console, offset).bold is True


def test_typewriter_live_body_uses_regular_weight() -> None:
    """打字机 Live 正文默认使用常规字重。"""
    session = TypewriterStreamSession()
    session.out = "reply"

    renderable = session._live_renderable()

    assert is_bold(renderable, 0) is False


def test_typewriter_frame_keeps_only_cursor_bold(monkeypatch) -> None:
    """逐字动画只保留光标的强调样式。"""
    frames = []

    class FakeLive(object):
        def update(self, renderable) -> None:
            frames.append(renderable)

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(design_utils.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(design_utils.random, "random", lambda: 1.0)

    asyncio.run(design_utils.typewriter(
        FakeLive(),
        "a",
        "",
        0.0,
        0.0,
        "█",
        max_lines=8
    ))

    renderable = frames[-1]
    assert is_bold(renderable, 0) is False
    assert is_bold(renderable, 1) is True


def test_status_composition_keeps_plain_body_regular() -> None:
    """正文与状态组合显示时不会把正文重新加粗。"""
    coordinator = RenderCoord()
    coordinator.text_state.append("reply", display=TextState.STREAM)

    renderable = coordinator._compose_status_renderable()

    assert is_bold(renderable, 0) is False


def test_explicit_stream_bold_style_is_preserved() -> None:
    """流式内容显式声明的粗体样式保持生效。"""
    state = TextState()
    state.append(
        "important",
        display=TextState.STREAM,
        display_style="bold"
    )

    renderable = state.renderable_for_text(state.display_text)

    assert is_bold(renderable, 0) is True
