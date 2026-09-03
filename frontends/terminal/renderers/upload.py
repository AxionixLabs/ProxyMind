# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.ports.presentation import (
    StyledBlock,
    TextSpan,
)
from frontends.terminal.semantic_styles import (
    TerminalSemanticRole,
    semantic_text_style,
)

MUTED = semantic_text_style(TerminalSemanticRole.SECONDARY)
ACCENT = semantic_text_style(TerminalSemanticRole.ACCENT)
BRIGHT = semantic_text_style(TerminalSemanticRole.PRIMARY)
INDICATOR = semantic_text_style(TerminalSemanticRole.ACCENT)
SUCCESS = semantic_text_style(TerminalSemanticRole.SUCCESS, bold=True)
FAILURE = semantic_text_style(TerminalSemanticRole.FAILURE)


def format_bytes(value: float) -> str:
    """把字节数格式化为短单位文本。"""
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


def upload_idle_block(
    *,
    indicator: str,
    item_total: int = 0,
    total_bytes: int = 0,
) -> StyledBlock:
    """生成尚未收到上传事件时的等待状态。"""
    spans = [
        TextSpan(indicator, INDICATOR),
        TextSpan(" preparing attach", ACCENT),
    ]

    if item_total > 0 or total_bytes > 0:
        spans.extend([
            TextSpan(" · ", MUTED),
            TextSpan(f"{int(max(0, item_total))} file(s)", BRIGHT),
            TextSpan(" · ", MUTED),
            TextSpan(format_bytes(float(max(0, total_bytes))), ACCENT),
        ])

    return _styled_block(spans)


def upload_progress_block(
    event: dict[str, typing.Any],
    *,
    indicator: str,
) -> StyledBlock:
    """生成单个上传事件对应的单行状态。"""
    phase = str(event.get("phase") or "")
    item_index = int(event.get("item_index") or 1)
    item_total = int(event.get("item_total") or 1)
    filename = str(event.get("filename") or "-")
    action = "processing" if phase == "processing" else "attaching"

    if bool(event.get("done")):
        action = "attached"
        indicator = "✓"

    spans = [
        TextSpan(indicator, INDICATOR),
        TextSpan(f" {action} {item_index}/{item_total}", ACCENT),
        TextSpan(" · ", MUTED),
        TextSpan(filename, BRIGHT),
    ]

    uploaded = float(event.get("aggregate_uploaded_bytes") or 0.0)
    total = float(event.get("aggregate_total_bytes") or 0.0)
    speed = float(event.get("aggregate_speed_bytes_per_sec") or 0.0)

    if phase != "processing" and (uploaded > 0 or total > 0):
        transfer = format_bytes(uploaded)
        if total > 0:
            transfer = f"{transfer} / {format_bytes(total)}"
        spans.extend([TextSpan(" · ", MUTED), TextSpan(transfer, ACCENT)])
        if speed > 0 and not bool(event.get("done")):
            spans.extend([
                TextSpan(" · ", MUTED),
                TextSpan(f"{format_bytes(speed)}/s", ACCENT),
            ])
    elif phase == "processing":
        spans.extend([TextSpan(" · ", MUTED), TextSpan("processing", ACCENT)])

    return _styled_block(spans)


def upload_summary_block(event: dict[str, typing.Any]) -> StyledBlock:
    """生成上传完成后的单行摘要。"""
    item_total = int(event.get("item_total") or 0)
    total = format_bytes(float(event.get("aggregate_total_bytes", 0.0) or 0.0))
    elapsed = float(event.get("aggregate_elapsed_sec") or 0.0)
    speed = format_bytes(float(event.get("aggregate_speed_bytes_per_sec") or 0.0))

    return _styled_block([
        TextSpan("Attach ", MUTED),
        TextSpan("done", SUCCESS),
        TextSpan(" · ", MUTED),
        TextSpan(f"{item_total} file(s)", BRIGHT),
        TextSpan(" · ", MUTED),
        TextSpan(total, ACCENT),
        TextSpan(" · ", MUTED),
        TextSpan(f"{elapsed:.1f}s", ACCENT),
        TextSpan(" · ", MUTED),
        TextSpan(f"{speed}/s", ACCENT),
    ])


def upload_failure_block(
    *,
    message: str,
    event: dict[str, typing.Any] | None = None,
) -> StyledBlock:
    """生成上传失败后的两行摘要。"""
    spans = [
        TextSpan("■ ", FAILURE),
        TextSpan("Attach ", MUTED),
        TextSpan("fail", FAILURE),
    ]

    if event is not None:
        spans.extend([
            TextSpan(" · ", MUTED),
            TextSpan(str(event.get("filename") or "-"), BRIGHT),
        ])
    spans.append(TextSpan("\n"))

    reason = _short_failure_reason(message)

    if event is None:
        spans.append(TextSpan(reason or "-", BRIGHT))
        return _styled_block(spans)

    uploaded = format_bytes(float(event.get("aggregate_uploaded_bytes", 0.0) or 0.0))
    total = format_bytes(float(event.get("aggregate_total_bytes", 0.0) or 0.0))

    spans.append(TextSpan(f"{uploaded} / {total}", ACCENT))

    if reason:
        spans.extend([TextSpan(" · ", MUTED), TextSpan(reason, BRIGHT)])
    return _styled_block(spans)


def _styled_block(spans: list[TextSpan]) -> StyledBlock:
    """由有序文本片段生成中立展示块。"""
    return StyledBlock(
        plain_text="".join(span.text for span in spans),
        spans=tuple(spans),
    )


def _short_failure_reason(value: str, *, limit: int = 32) -> str:
    """截断附件失败原因。"""
    text = " ".join(str(value or "").split())
    size = max(8, int(limit))

    if len(text) <= size:
        return text
    return f"{text[:max(0, size - 4)].rstrip()} ..."


if __name__ == '__main__':
    pass
