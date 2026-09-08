# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio

from agent.ports.presentation import (
    ApplicationSink,
    ApplicationView,
    StyledBlock,
    TextSpan,
    Viewport,
)
from frontends.terminal.intro import (
    IntroFrame,
    intro_frames,
)
from frontends.terminal.semantic_styles import (
    TerminalSemanticRole,
    semantic_text_style,
)
from metadata import const
from ..core.document import TuiBlockKind
from ..core.models import (
    FragmentBlock,
    LineFill,
)
from ..core.runtime import TuiRuntime
from ..core.styles import (
    assistant_block,
    styled_block_fragments,
    text_block,
)

MUTED = semantic_text_style(TerminalSemanticRole.SECONDARY)
ACCENT = semantic_text_style(TerminalSemanticRole.ACCENT, bold=True)
BRIGHT = semantic_text_style(TerminalSemanticRole.PRIMARY, bold=True)
SUCCESS = semantic_text_style(TerminalSemanticRole.SUCCESS, bold=True)
WARNING = semantic_text_style(TerminalSemanticRole.ATTENTION, bold=True)
FAILURE = semantic_text_style(TerminalSemanticRole.FAILURE, bold=True)
FAILURE_BODY = semantic_text_style(TerminalSemanticRole.FAILURE)

_BACKGROUND_VIEW_TYPES = frozenset({
    "tui.background.error",
    "tui.compact.interrupted",
    "tui.compact.status",
    "tui.external_mcp.interrupted",
    "tui.external_mcp.status",
    "tui.helix.interrupted",
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
            if view.type == "intro":
                self.runtime.set_startup_animation(
                    self._animate_intro,
                    final_frame=self._commit_intro,
                )
                return None
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

    def _commit_intro(self) -> None:
        """不播放动画并直接提交启动标题最终帧。"""
        self.runtime.append_block(_intro_block(), kind="system")

    def _emit_active(self, view: ApplicationView) -> None:
        """把单项应用展示写入已启动的 TUI。"""
        if view.type == "run.worked":
            # 耗时页脚只提交完成信息；wait 由轮次生命周期统一清理。
            return None
        self._emit_view(view)

    def _emit_view(self, view: ApplicationView) -> None:
        """把单项应用展示转换为正文或运行期状态。"""
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
        block_kind: TuiBlockKind = (
            "assistant"
            if view.type == "review.completed"
            else "notice"
            if view.type in {
                "review.cancelled",
                "review.failed",
                "review.reconciliation_required",
                "tui.interrupted",
            }
            else "system"
        )
        if isinstance(view.renderable, FragmentBlock):
            self._commit_block(view.type, view.renderable, block_kind)
            return None
        if isinstance(view.renderable, StyledBlock):
            fill_character = str(view.payload.get("line_fill_character") or "")
            line_fill = (
                LineFill(
                    character=fill_character,
                    margin=int(view.payload.get("line_fill_margin") or 0),
                )
                if fill_character
                else None
            )
            block = FragmentBlock(
                styled_block_fragments(view.renderable),
                line_fill=line_fill,
            )
            self._commit_block(
                view.type,
                assistant_block(block) if block_kind == "assistant" else block,
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
        block_kind: TuiBlockKind,
    ) -> None:
        """按展示类型提交正文块或延迟后台结果。"""
        if view_type in _BACKGROUND_VIEW_TYPES:
            self.runtime.queue_background_block(block)
            return None
        self.runtime.append_block(block, kind=block_kind)

    async def _animate_intro(self) -> None:
        """在主 TUI Application 中播放并定格启动标题。"""
        frames = intro_frames(const.APP_DESC)
        try:
            for frame in frames:
                self.runtime.set_active_renderable(
                    _intro_frame_block(frame),
                    kind="system",
                )
                await asyncio.sleep(frame.delay_after)
        except BaseException:
            self.runtime.clear_active_renderable()
            raise
        self.runtime.commit_active_renderable(
            _intro_frame_block(frames[-1])
        )


def _intro_block() -> FragmentBlock:
    """生成 TUI 启动标题。"""
    return _intro_frame_block(intro_frames(const.APP_DESC)[-1])


def _intro_frame_block(frame: IntroFrame) -> FragmentBlock:
    """把中立启动帧转换为 TUI 标题块。"""
    spans = [TextSpan(">_" if frame.prompt_on else "> ", MUTED)]
    if frame.title_visible:
        spans.extend([
            TextSpan(" "),
            TextSpan(const.APP_DESC[:frame.title_visible], BRIGHT),
        ])
    if frame.version_visible:
        spans.append(TextSpan(f" (v{const.APP_VERSION})", MUTED))
    return _fragment_block(spans)


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
