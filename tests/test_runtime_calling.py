# -*- coding: utf-8 -*-

import asyncio
import typing
from types import SimpleNamespace

from mind_app.runtime.support import calling as calling_module
from mind_app.stream_events.worked import emit_worked_footer


class DummyMind(object):
    """提供 calling 测试所需的最小 Mind 接口。"""

    def __init__(self) -> None:
        self.session_called = False
        self.views: list[typing.Any] = []
        self.frontend = SimpleNamespace(
            application=SimpleNamespace(
                viewport=SimpleNamespace(width=48, height=24),
                emit=self.views.append,
            ),
        )

    async def stream_looper(self, *_: typing.Any, **__: typing.Any) -> None:
        """占位流式执行器。"""
        return None

    async def fresh_pref_config(self, *, ttl_sec: float | None = None) -> dict[str, typing.Any]:
        """返回空偏好配置。"""
        _ = ttl_sec
        return {}

    def begin_session(
        self,
        cid: str | None = None,
        sid: str | None = None,
        **_: typing.Any
    ) -> dict[str, str]:
        """返回稳定会话游标。"""
        return {
            "cid": cid or "cid_test",
            "sid": sid or "sid_test",
        }

    async def with_mcp_session(
        self,
        pref_config: dict[str, typing.Any],
        function: typing.Callable[..., typing.Awaitable[None]],
    ) -> None:
        """执行传入的 MCP 会话回调。"""
        _ = pref_config
        self.session_called = True
        await function(object(), [])


def test_calling_mcp_session_callback_annotations_are_runtime_safe(monkeypatch) -> None:
    """calling 内部 MCP 回调的类型注解不依赖运行时导入。"""
    mind = DummyMind()
    calls: list[dict[str, typing.Any]] = []

    async def fake_run_mode_lifecycle(*args: typing.Any, **kwargs: typing.Any) -> None:
        """记录调用参数。"""
        _ = args
        calls.append(kwargs)

    monkeypatch.setattr(calling_module, "run_mode_lifecycle", fake_run_mode_lifecycle)

    asyncio.run(
        calling_module.calling(
            mind,
            message="你好",
            mode="chat",
            ev_report=object()
        )
    )

    assert mind.session_called is True
    assert calls
    assert calls[0]["message"] == "你好"
    assert calls[0]["metadata"]["cid"] == "cid_test"
    assert calls[0]["metadata"]["sid"] == "sid_test"


def test_emit_worked_footer_uses_frontend_viewport() -> None:
    """耗时页脚按 Frontend 宽度发送应用展示。"""
    mind = DummyMind()

    emit_worked_footer(mind.frontend.application, 1.25)

    assert [view.type for view in mind.views] == ["run.worked", "run.gap"]
    assert "Worked for 1.2s" in mind.views[0].renderable.plain
    assert len(mind.views[0].renderable.plain) == 48
