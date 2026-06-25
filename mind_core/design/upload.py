# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from rich.console import Console
from rich.live import Live
from rich.text import Text


def format_bytes(value: float) -> str:
    """把字节数格式化为短单位文本。"""
    size  = float(max(0.0, value))
    units = ("B", "KB", "MB", "GB", "TB")
    unit  = units[0]

    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            break
        size /= 1024.0

    if unit == "B":
        return f"{int(size)} {unit}"

    return f"{size:.1f} {unit}"


class UploadProgressLiveReporter(object):
    """单块刷新的附件上传状态视图。"""

    FAILURE_REASON_MAX_CHARS: typing.ClassVar[int] = 32

    SPINNER_FRAMES: typing.ClassVar[tuple[str, ...]] = (
        "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"
    )

    def __init__(
        self,
        console: Console,
        *,
        item_total: int = 0,
        total_bytes: int = 0
    ) -> None:
        """初始化上传状态视图的输出目标和基础统计。"""
        self.console = console

        self.item_total  = int(max(0, item_total))
        self.total_bytes = int(max(0, total_bytes))

        self.live: typing.Optional[Live]                        = None
        self.last_event: typing.Optional[dict[str, typing.Any]] = None

    def __enter__(self) -> "UploadProgressLiveReporter":
        """启动 Rich Live 渲染并返回当前视图对象。"""
        self.live = Live(
            self.render_idle(),
            console=self.console,
            refresh_per_second=12,
            transient=True
        )
        self.live.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        """关闭 Rich Live 渲染并保留异常传播语义。"""
        if self.live is not None:
            self.live.__exit__(exc_type, exc, tb)
            self.live = None
        return False

    async def __call__(self, event: dict[str, typing.Any]) -> None:
        """接收上传事件并刷新当前 live 视图。"""
        self.last_event = event
        if self.live is not None:
            self.live.update(self.render_progress(event), refresh=True)
        return None

    @classmethod
    def _spinner_frame(cls) -> str:
        """按当前时间返回一个稳定循环的状态帧。"""
        frames = cls.SPINNER_FRAMES
        index  = int(time.monotonic() * 12) % len(frames)
        return frames[index]

    @classmethod
    def _short_failure_reason(cls, value: str) -> str:
        """截断附件失败原因，避免状态行过长。"""
        limit = max(8, int(cls.FAILURE_REASON_MAX_CHARS))

        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text

        return f"{text[:max(0, limit - 4)].rstrip()} ..."

    @classmethod
    def render_idle_block(
        cls,
        *,
        item_total: int = 0,
        total_bytes: int = 0
    ) -> Text:
        """渲染尚未收到上传事件时的等待状态。"""
        text = Text()
        text.append(cls._spinner_frame(), style="bold #5FD7AF")
        text.append(" preparing attach", style="bold #AFC7D8")

        if item_total > 0 or total_bytes > 0:
            text.append(" · ", style="bold #7F8C9A")
            text.append(f"{int(max(0, item_total))} file(s)", style="bold #F4F7FA")
            text.append(" · ", style="bold #7F8C9A")
            text.append(format_bytes(float(max(0, total_bytes))), style="bold #AFC7D8")

        return text

    @classmethod
    def render_progress(
        cls,
        event: dict[str, typing.Any]
    ) -> Text:
        """渲染单个上传事件对应的两行状态。"""
        phase = str(event.get("phase") or "")

        item_index = int(event.get("item_index") or 1)
        item_total = int(event.get("item_total") or 1)

        filename = str(event.get("filename") or "-")
        action   = "processing" if phase == "processing" else "attaching"
        detail   = "waiting" if phase == "processing" else "sending"

        if bool(event.get("done")):
            action = "attached"
            detail = "ready"

        action_label = f"{action:<10}"

        text = Text()
        text.append("✓" if action == "attached" else cls._spinner_frame(), style="bold #5FD7AF")
        text.append(f" {action_label} {item_index}/{item_total}", style="bold #AFC7D8")
        text.append(" · ", style="bold #7F8C9A")
        text.append(detail, style="bold #AFC7D8")
        text.append("\n")
        text.append(filename, style="bold #F4F7FA")

        return text

    @classmethod
    def render_summary(
        cls,
        event: dict[str, typing.Any]
    ) -> Text:
        """渲染上传完成后的两行摘要。"""
        item_total = int(event.get("item_total") or 0)
        total      = format_bytes(float(event.get("aggregate_total_bytes", 0.0) or 0.0))
        elapsed    = float(event.get("aggregate_elapsed_sec") or 0.0)
        speed      = format_bytes(float(event.get("aggregate_speed_bytes_per_sec") or 0.0))

        text = Text()
        text.append("Attach ", style="bold #7F8C9A")
        text.append("done", style="bold #5FD7AF")
        text.append(" · ", style="bold #7F8C9A")
        text.append(f"{item_total} file(s)", style="bold #F4F7FA")
        text.append("\n")
        text.append(total, style="bold #AFC7D8")
        text.append(" · ", style="bold #7F8C9A")
        text.append(f"{elapsed:.1f}s", style="bold #AFC7D8")
        text.append(" · ", style="bold #7F8C9A")
        text.append(f"{speed}/s", style="bold #AFC7D8")

        return text

    @classmethod
    def render_failure(
        cls,
        *,
        message: str,
        event: typing.Optional[dict[str, typing.Any]] = None
    ) -> Text:
        """渲染上传失败后的两行摘要。"""
        text = Text()
        text.append("Attach ", style="bold #7F8C9A")
        text.append("fail", style="bold #FF6B6B")

        if event is not None:
            filename = str(event.get("filename") or "-")
            text.append(" · ", style="bold #7F8C9A")
            text.append(filename, style="bold #F4F7FA")
        text.append("\n")

        if event is not None:
            aggregate_uploaded = format_bytes(
                float(event.get("aggregate_uploaded_bytes", 0.0) or 0.0)
            )
            aggregate_total = format_bytes(
                float(event.get("aggregate_total_bytes", 0.0) or 0.0)
            )
            text.append(f"{aggregate_uploaded} / {aggregate_total}", style="bold #AFC7D8")

            reason = cls._short_failure_reason(str(message or ""))
            if reason:
                text.append(" · ", style="bold #7F8C9A")
                text.append(reason, style="bold #F4F7FA")

            return text

        text.append(cls._short_failure_reason(str(message or "-")), style="bold #F4F7FA")

        return text

    def render_idle(self) -> Text:
        """使用实例统计渲染等待状态。"""
        return self.render_idle_block(item_total=self.item_total, total_bytes=self.total_bytes)


def render_upload_frame(
    *,
    event: typing.Optional[dict[str, typing.Any]],
    item_total: int = 0,
    total_bytes: int = 0
) -> Text:
    """根据当前上传事件渲染 live 帧。"""
    if event is None:
        return UploadProgressLiveReporter.render_idle_block(
            item_total=item_total,
            total_bytes=total_bytes
        )

    return UploadProgressLiveReporter.render_progress(event)


async def upload_progress_live(
    *,
    console: Console,
    stop_event: asyncio.Event,
    snapshot: typing.Callable[[], dict[str, typing.Any]],
    refresh_per_second: int = 12,
) -> None:
    """按快照函数持续刷新附件上传 live 视图。"""
    state = snapshot()
    event = state.get("event")

    with Live(
        render_upload_frame(
            event=event if isinstance(event, dict) else None,
            item_total=int(state.get("item_total") or 0),
            total_bytes=int(state.get("total_bytes") or 0)
        ),
        console=console,
        refresh_per_second=refresh_per_second,
        transient=True
    ) as live:
        while not stop_event.is_set():
            state = snapshot()
            event = state.get("event")

            live.update(
                render_upload_frame(
                    event=event if isinstance(event, dict) else None,
                    item_total=int(state.get("item_total") or 0),
                    total_bytes=int(state.get("total_bytes") or 0)
                ),
                refresh=True
            )

            await asyncio.sleep(1 / max(1, refresh_per_second))


if __name__ == "__main__":
    pass
