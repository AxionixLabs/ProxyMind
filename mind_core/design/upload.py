# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from rich.console import Console
from rich.live import Live
from rich.text import Text


def format_bytes(value: float) -> str:
    size = float(max(0.0, value))
    units = ("B", "KB", "MB", "GB", "TB")
    unit = units[0]
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            break
        size /= 1024.0

    if unit == "B":
        return f"{int(size)} {unit}"
    return f"{size:.1f} {unit}"


class UploadProgressReporter(object):
    """把上传进度事件格式化为可读文本，并做简单节流。"""

    def __init__(
        self,
        emit: typing.Callable[[str], None],
        *,
        min_interval_sec: float = 0.25,
        min_percent_step: float = 0.05
    ) -> None:
        self.emit = emit

        self.min_interval_sec = min_interval_sec
        self.min_percent_step = min_percent_step

        self.last_key: typing.Optional[tuple[typing.Any, typing.Any]] = None

        self.last_ts: float      = 0.0
        self.last_percent: float = -1.0

    @classmethod
    def format_progress(cls, event: dict[str, typing.Any]) -> str:
        filename   = str(event.get("filename") or "-")
        item_index = int(event.get("item_index") or 1)
        item_total = int(event.get("item_total") or 1)
        phase      = str(event.get("phase") or "")
        percent    = max(0.0, min(100.0, float(event.get("percent") or 0.0) * 100.0))
        uploaded   = format_bytes(float(event.get("uploaded_bytes") or 0.0))
        total      = format_bytes(float(event.get("total_bytes") or 0.0))
        speed      = format_bytes(float(event.get("speed_bytes_per_sec") or 0.0))

        action = "Uploaded" if bool(event.get("done")) else (
            "Processing" if phase == "processing" else "Uploading"
        )

        return (
            f"{action} {item_index}/{item_total}: {filename} "
            f"{percent:5.1f}% · {uploaded}/{total} · {speed}/s"
        )

    async def __call__(self, event: dict[str, typing.Any]) -> None:
        now     = time.monotonic()
        key     = (event.get("item_index"), event.get("filename"))
        percent = float(event.get("percent") or 0.0)
        done    = bool(event.get("done"))

        should_emit = (
            done
            or self.last_key != key
            or self.last_percent < 0.0
            or (percent - self.last_percent) >= self.min_percent_step
            or (now - self.last_ts) >= self.min_interval_sec
        )
        if not should_emit:
            return None

        self.last_key     = key
        self.last_ts      = now
        self.last_percent = percent

        self.emit(self.format_progress(event))
        return None


class UploadProgressLiveReporter(object):
    """单块刷新的附件上传进度视图。"""

    BAR_WIDTH: typing.ClassVar[int] = 28

    def __init__(
        self,
        console: Console,
        *,
        item_total: int = 0,
        total_bytes: int = 0
    ) -> None:
        self.console = console

        self.item_total  = int(max(0, item_total))
        self.total_bytes = int(max(0, total_bytes))
        self.live: typing.Optional[Live] = None

        self.last_event: typing.Optional[dict[str, typing.Any]] = None

    def __enter__(self) -> "UploadProgressLiveReporter":
        self.live = Live(
            self.render_idle(),
            console=self.console,
            refresh_per_second=12,
            transient=True
        )
        self.live.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self.live is not None:
            self.live.__exit__(exc_type, exc, tb)
            self.live = None
        return False

    async def __call__(self, event: dict[str, typing.Any]) -> None:
        self.last_event = event
        if self.live is not None:
            self.live.update(self.render_progress(event), refresh=True)
        return None

    @staticmethod
    def _format_eta(seconds: float | None) -> str:
        if seconds is None or seconds < 0:
            return "ETA --:--"
        total = int(round(seconds))
        minutes, secs = divmod(total, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"ETA {hours:d}:{minutes:02d}:{secs:02d}"
        return f"ETA {minutes:02d}:{secs:02d}"

    @classmethod
    def _render_bar(
        cls,
        percent: float
    ) -> str:
        clamped = max(0.0, min(1.0, float(percent)))
        filled  = int(round(clamped * cls.BAR_WIDTH))
        filled  = max(0, min(cls.BAR_WIDTH, filled))

        return ("█" * filled) + ("·" * (cls.BAR_WIDTH - filled))

    @classmethod
    def build_summary(
        cls,
        event: dict[str, typing.Any]
    ) -> str:
        item_total = int(event.get("item_total") or 0)
        total      = format_bytes(float(event.get("aggregate_total_bytes", 0.0) or 0.0))
        elapsed    = float(event.get("aggregate_elapsed_sec") or 0.0)
        speed      = format_bytes(float(event.get("aggregate_speed_bytes_per_sec") or 0.0))

        return (
            f"Uploaded {item_total} attachment(s) · {total} · "
            f"{elapsed:.1f}s · {speed}/s"
        )

    @classmethod
    def render_idle_block(
        cls,
        *,
        item_total: int = 0,
        total_bytes: int = 0
    ) -> Text:
        text = Text()
        text.append("Preparing attachment upload…", style="bold #AFC7D8")
        if item_total > 0 or total_bytes > 0:
            text.append("\n")
            text.append(f"{int(max(0, item_total))} attachment(s)", style="bold #F4F7FA")
            text.append(" · ", style="bold #7F8C9A")
            text.append(format_bytes(float(max(0, total_bytes))), style="bold #AFC7D8")
        return text

    @classmethod
    def render_progress(
        cls,
        event: dict[str, typing.Any]
    ) -> Text:
        aggregate_percent  = float(event.get("aggregate_percent", event.get("percent") or 0.0))
        aggregate_uploaded = float(event.get("aggregate_uploaded_bytes", event.get("uploaded_bytes") or 0.0))

        aggregate_total = float(event.get("aggregate_total_bytes", event.get("total_bytes") or 0.0))
        aggregate_speed = float(event.get("aggregate_speed_bytes_per_sec", event.get("speed_bytes_per_sec") or 0.0))
        aggregate_eta   = event.get("aggregate_eta_sec")
        phase           = str(event.get("phase") or "")

        item_index = int(event.get("item_index") or 1)
        item_total = int(event.get("item_total") or 1)

        filename      = str(event.get("filename") or "-")
        file_percent  = max(0.0, min(100.0, float(event.get("percent") or 0.0) * 100.0))
        file_uploaded = format_bytes(float(event.get("uploaded_bytes") or 0.0))
        file_total    = format_bytes(float(event.get("total_bytes") or 0.0))

        overall_percent = max(0.0, min(100.0, aggregate_percent * 100.0))

        bar = cls._render_bar(aggregate_percent)

        text = Text()
        title = "Processing" if phase == "processing" else "Upload"
        text.append(f"{title} {item_index}/{item_total} ", style="bold #AFC7D8")
        text.append("[", style="bold #7F8C9A")
        text.append(bar, style="bold #5FD7AF")
        text.append("]", style="bold #7F8C9A")
        text.append(
            f" {overall_percent:5.1f}%  {format_bytes(aggregate_uploaded)}/{format_bytes(aggregate_total)}",
            style="bold #F4F7FA",
        )
        text.append(f"  {format_bytes(aggregate_speed)}/s", style="bold #AFC7D8")
        text.append(f"  {cls._format_eta(aggregate_eta)}", style="bold #7F8C9A")
        text.append("\n")
        text.append(f"{filename}", style="bold #F4F7FA")
        text.append(f"  {file_percent:5.1f}%  {file_uploaded}/{file_total}", style="#AFC7D8")
        if phase == "processing":
            text.append("  waiting for server response", style="bold #D3C27C")
        return text

    @classmethod
    def render_summary(
        cls,
        event: dict[str, typing.Any]
    ) -> Text:
        item_total = int(event.get("item_total") or 0)
        total      = format_bytes(float(event.get("aggregate_total_bytes", 0.0) or 0.0))
        elapsed    = float(event.get("aggregate_elapsed_sec") or 0.0)
        speed      = format_bytes(float(event.get("aggregate_speed_bytes_per_sec") or 0.0))
        last_file  = str(event.get("filename") or "-")

        text = Text()
        text.append("Upload complete", style="bold #5FD7AF")
        text.append("\n")
        text.append(f"{item_total} attachment(s)", style="bold #F4F7FA")
        text.append(" · ", style="bold #7F8C9A")
        text.append(total, style="bold #AFC7D8")
        text.append(" · ", style="bold #7F8C9A")
        text.append(f"{elapsed:.1f}s", style="bold #AFC7D8")
        text.append(" · ", style="bold #7F8C9A")
        text.append(f"{speed}/s", style="bold #AFC7D8")
        text.append("\n")
        text.append("Last file: ", style="bold #7F8C9A")
        text.append(last_file, style="bold #F4F7FA")
        return text

    @classmethod
    def render_failure(
        cls,
        *,
        message: str,
        event: typing.Optional[dict[str, typing.Any]] = None
    ) -> Text:
        text = Text()
        text.append("Upload failed", style="bold #FF6B6B")
        text.append("\n")
        text.append(str(message or "-"), style="bold #F4F7FA")

        if event is not None:
            item_index = int(event.get("item_index") or 1)
            item_total = int(event.get("item_total") or 1)
            filename   = str(event.get("filename") or "-")

            aggregate_uploaded = format_bytes(
                float(event.get("aggregate_uploaded_bytes", 0.0) or 0.0)
            )
            aggregate_total = format_bytes(
                float(event.get("aggregate_total_bytes", 0.0) or 0.0)
            )

            text.append("\n")
            text.append("During: ", style="bold #7F8C9A")
            text.append(f"{item_index}/{item_total} {filename}", style="bold #F4F7FA")
            text.append(" · ", style="bold #7F8C9A")
            text.append(f"{aggregate_uploaded}/{aggregate_total}", style="bold #AFC7D8")

        return text

    def render_idle(self) -> Text:
        return self.render_idle_block(item_total=self.item_total, total_bytes=self.total_bytes)


def render_upload_frame(
    *,
    event: typing.Optional[dict[str, typing.Any]],
    item_total: int = 0,
    total_bytes: int = 0
) -> Text:
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
