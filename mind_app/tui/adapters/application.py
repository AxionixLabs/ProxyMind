# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_app.frontend.contracts import (
    ApplicationSink,
    ApplicationView,
    Viewport
)
from mind_app.presentation.models import (
    StyledBlock,
    TextSpan,
    TextStyle
)
from mind_nova import const
from ..core.models import FragmentBlock
from ..core.runtime import TuiRuntime
from ..core.styles import styled_block_fragments
from ..core.styles import text_block

MUTED        = TextStyle(foreground="#7F8C9A", dim=True)
ACCENT       = TextStyle(foreground="#AFC7D8", bold=True)
BRIGHT       = TextStyle(foreground="#F4F7FA", bold=True)
SUCCESS      = TextStyle(foreground="#5FD7AF", bold=True)
WARNING      = TextStyle(foreground="#FFD75F", bold=True)
FAILURE      = TextStyle(foreground="#FF6B6B", bold=True)
FAILURE_BODY = TextStyle(foreground="#FF6B6B")

_BACKGROUND_VIEW_TYPES = frozenset({
    "tui.background.error",
    "tui.external_mcp.status",
    "tui.helix.status",
})


class TuiApplicationSink(ApplicationSink):
    """把运行期应用展示写入 TUI 正文状态树。"""

    def __init__(self, runtime: TuiRuntime) -> None:
        self.runtime = runtime
        self.pending_views: list[ApplicationView] = []
        self.runtime.add_open_callback(self.flush_pending)

    @property
    def viewport(self) -> Viewport:
        """返回 TUI 当前画布尺寸。"""
        return Viewport(
            width=self.runtime.terminal_width,
            height=self.runtime.terminal_height,
        )

    def emit(self, view: ApplicationView) -> None:
        """缓存启动前事件，并在运行期统一写入正文画布。"""
        if not self.runtime.active:
            self.pending_views.append(view)
            return None
        self._emit_active(view)

    def flush_pending(self) -> None:
        """按接收顺序提交启动前缓存的应用展示。"""
        if not self.runtime.active or not self.pending_views:
            return None
        pending = self.pending_views
        self.pending_views = []
        for view in pending:
            self._emit_active(view)

    def _emit_active(self, view: ApplicationView) -> None:
        """把单项应用展示写入已启动的 TUI。"""
        if view.type in {"tui.gap", "run.gap", "spacer"}:
            return None
        if view.type == "intro":
            self.runtime.append_block(_intro_block())
            return None
        if view.type == "startup_logo":
            self.runtime.append_block(_startup_logo_block())
            return None
        if view.type == "error":
            self.runtime.append_block(_error_block(str(view.renderable or "")))
            return None
        if view.type == "tui.background.error":
            block = _error_block(str(view.renderable or ""))
            self.runtime.queue_background_block(block)
            return None
        block_kind = "notice" if view.type == "tui.interrupted" else "system"
        if isinstance(view.renderable, FragmentBlock):
            self._commit_block(view.type, view.renderable, block_kind)
            return None
        if isinstance(view.renderable, StyledBlock):
            self._commit_block(
                view.type,
                FragmentBlock(styled_block_fragments(view.renderable)),
                block_kind,
            )
            return None
        if isinstance(view.renderable, str):
            self._commit_block(
                view.type,
                text_block(view.renderable),
                block_kind,
            )
            return None
        if view.renderable is not None:
            raise TypeError(
                f"unsupported TUI application renderable: "
                f"{type(view.renderable).__name__}"
            )

    def _commit_block(
        self,
        view_type: str,
        block: FragmentBlock,
        block_kind: str,
    ) -> None:
        """按展示类型提交正文块或延迟后台结果。"""
        if view_type in _BACKGROUND_VIEW_TYPES:
            self.runtime.queue_background_block(block)
            return None
        self.runtime.append_block(block, kind=block_kind)


def _intro_block() -> FragmentBlock:
    """生成 TUI 启动标题。"""
    return _fragment_block([
        TextSpan(">_ ", MUTED),
        TextSpan(const.APP_DESC, BRIGHT),
        TextSpan(f" (v{const.APP_VERSION})", MUTED),
    ])


def _startup_logo_block() -> FragmentBlock:
    """生成 TUI 授权信息块。"""
    banner = (
        " __  __ _           _\n"
        "|  \\/  (_)_ __   __| |\n"
        "| |\\/| | | '_ \\ / _` |\n"
        "| |  | | | | | | (_| |\n"
        "|_|  |_|_|_| |_|\\__,_|\n"
    )
    return _fragment_block([
        TextSpan(banner, ACCENT),
        TextSpan(f">>> {const.APP_DESC} :: {const.APP_CN} <<<\n", SUCCESS),
        TextSpan("Copyright (C) ", FAILURE),
        TextSpan(f"{const.APP_YEAR} {const.APP_DESC}. All rights reserved.\n"),
        TextSpan("Version "),
        TextSpan(const.APP_VERSION, WARNING),
        TextSpan(" :: Licensed software. Authorization required.\n"),
        TextSpan("-" * 59, MUTED),
    ])


def _error_block(message: str) -> FragmentBlock:
    """生成 TUI 入口错误块。"""
    return _fragment_block([
        TextSpan(f"{const.APP_DESC} :: ", MUTED),
        TextSpan(message, FAILURE_BODY),
    ])


def _fragment_block(spans: list[TextSpan]) -> FragmentBlock:
    """把有序中立文本片段转换为 TUI 块。"""
    block = StyledBlock(
        plain_text="".join(span.text for span in spans),
        spans=tuple(spans),
    )
    return FragmentBlock(styled_block_fragments(block))


if __name__ == '__main__':
    pass
