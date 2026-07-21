# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.presentation.models import (
    StyledBlock,
    TextSpan,
    TextStyle
)
from .upload import format_bytes

MUTED   = TextStyle(foreground="#7F8C9A", bold=True)
ACCENT  = TextStyle(foreground="#AFC7D8", bold=True)
BRIGHT  = TextStyle(foreground="#F4F7FA", bold=True)
SUCCESS = TextStyle(foreground="#5FD7AF", bold=True)
FAILURE = TextStyle(foreground="#FF6B6B", bold=True)
WARNING = TextStyle(foreground="#FFD166", bold=True)


def download_progress_block(
    state: dict[str, typing.Any],
    *,
    indicator: str,
) -> StyledBlock:
    """生成运行时下载过程的两行状态。"""
    stage = str(state.get("stage") or "warming").strip().lower()

    action, detail = _stage_labels(state, stage)

    spans = [
        TextSpan(indicator, SUCCESS),
        TextSpan(f" {action:<11}", ACCENT),
        TextSpan(" · ", MUTED),
        TextSpan(detail, ACCENT),
        TextSpan("\n"),
    ]

    if stage == "downloading":
        spans.extend(_transfer_spans(state, include_speed=True))
    else:
        spans.append(TextSpan(str(state.get("filename") or "runtime package"), BRIGHT))

    return _styled_block(spans)


def download_summary_block(
    state: dict[str, typing.Any],
) -> StyledBlock | None:
    """生成运行时下载结束后的稳定摘要。"""
    stage    = str(state.get("stage") or "").strip().lower()
    filename = str(state.get("filename") or "runtime package")

    if stage == "done":
        status       = "complete"
        status_style = SUCCESS
    elif stage == "failed":
        status = "failed"
        status_style = FAILURE
    elif stage == "cancelled":
        status       = "cancelled"
        status_style = WARNING
    else:
        return None

    spans = [
        TextSpan("Download ", MUTED),
        TextSpan(status, status_style),
        TextSpan(" · ", MUTED),
        TextSpan(filename, BRIGHT),
    ]

    if int(state.get("done") or 0) > 0 or int(state.get("total") or 0) > 0:
        spans.append(TextSpan("\n"))
        spans.extend(_transfer_spans(state, include_speed=False))
    return _styled_block(spans)


def _stage_labels(
    state: dict[str, typing.Any],
    stage: str,
) -> tuple[str, str]:
    """返回下载阶段对应的动作和简短说明。"""
    if stage == "connecting":
        return "connecting", "package"
    if stage == "downloading":
        return "downloading", _percent_label(state)
    if stage == "verifying":
        return "verifying", "checking"
    if stage == "extracting":
        return "extracting", "unpacking"
    if stage == "installing":
        return "installing", "applying"
    if stage == "cleaning":
        return "cleaning", "finishing"

    return "preparing", "package"


def _percent_label(state: dict[str, typing.Any]) -> str:
    """返回下载状态中的百分比文本。"""
    total = int(state.get("total") or 0)
    if total <= 0:
        return "--.-%"

    phase = max(0.0, min(1.0, float(state.get("phase") or 0.0)))
    return f"{phase * 100:.1f}%"


def _transfer_spans(
    state: dict[str, typing.Any],
    *,
    include_speed: bool,
) -> list[TextSpan]:
    """生成下载量和可选速度片段。"""
    done     = int(state.get("done") or 0)
    total    = int(state.get("total") or 0)
    transfer = format_bytes(float(done))

    if total > 0:
        transfer = f"{transfer} / {format_bytes(float(total))}"

    spans = [TextSpan(transfer, BRIGHT)]
    speed = float(state.get("speed") or 0.0)

    if include_speed and speed > 0:
        spans.extend([
            TextSpan(" · ", MUTED),
            TextSpan(f"{format_bytes(speed)}/s", ACCENT),
        ])

    return spans


def _styled_block(spans: list[TextSpan]) -> StyledBlock:
    """由有序文本片段生成中立展示块。"""
    return StyledBlock(
        plain_text="".join(span.text for span in spans),
        spans=tuple(spans),
    )


if __name__ == '__main__':
    pass
