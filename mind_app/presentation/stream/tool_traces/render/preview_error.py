# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from agent.ports.presentation import (
    TextSpan,
    TextStyle,
)

from mind_app.presentation.styles import (
    ERROR_PREVIEW_HEAD_STYLE,
    ERROR_PREVIEW_LINE_STYLE,
    ERROR_PREVIEW_MESSAGE_STYLE,
    ERROR_PREVIEW_TEXT_STYLE,
    PREVIEW_LINE_STYLE,
    PREVIEW_STYLE
)


def error_preview_line_parts(
    line: str,
    *,
    part: typing.Callable[[str, TextStyle | None], TextSpan],
) -> list[TextSpan] | None:
    """按命令错误输出常见结构分层着色。"""
    stripped = line.strip()
    if not stripped:
        return [part(line, PREVIEW_STYLE)]

    if re.match(r"^[A-Za-z][A-Za-z0-9_. -]*:$", stripped):
        return [part(line, ERROR_PREVIEW_HEAD_STYLE)]

    if stripped == "Line |":
        return [part(line, ERROR_PREVIEW_LINE_STYLE)]

    if stripped == "Traceback (most recent call last):":
        return [part(line, ERROR_PREVIEW_HEAD_STYLE)]

    if re.match(r"^[A-Za-z]+Error:$", stripped):
        return [part(line, ERROR_PREVIEW_HEAD_STYLE)]

    if re.match(r"^At line:\d+ char:\d+", stripped, re.IGNORECASE):
        return [part(line, ERROR_PREVIEW_LINE_STYLE)]

    exception = re.match(r"^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Warning))(:)(.*)$", line)
    if exception:
        return [
            part(f"{exception.group(1)}{exception.group(2)}", ERROR_PREVIEW_HEAD_STYLE),
            part(exception.group(3), ERROR_PREVIEW_TEXT_STYLE)
        ]

    if re.match(r"^[A-Za-z0-9_.-]+: .*(error|failed|cannot|missing|exception)", stripped, re.IGNORECASE):
        return [part(line, ERROR_PREVIEW_HEAD_STYLE)]

    if re.match(r"^\s*[\^~]+$", line):
        leading_len = len(line) - len(line.lstrip())
        return [
            part(line[:leading_len], ERROR_PREVIEW_LINE_STYLE),
            part(line[leading_len:], ERROR_PREVIEW_MESSAGE_STYLE)
        ]

    powershell_at_marker = re.match(r"^(\+\s*)([\^~]+)$", line)
    if powershell_at_marker:
        return [
            part(powershell_at_marker.group(1), ERROR_PREVIEW_LINE_STYLE),
            part(powershell_at_marker.group(2), ERROR_PREVIEW_MESSAGE_STYLE)
        ]

    if re.match(r"^\+\s", line):
        return [part(line, ERROR_PREVIEW_LINE_STYLE)]

    if re.match(r"^\s*\d+\s+\|\s", line):
        prefix, sep, body = line.partition("|")
        return [
            part(prefix, PREVIEW_LINE_STYLE),
            part(sep, ERROR_PREVIEW_LINE_STYLE),
            part(body, ERROR_PREVIEW_LINE_STYLE)
        ]

    if line.startswith("  ") and not line.lstrip().startswith("|"):
        return [part(line, ERROR_PREVIEW_LINE_STYLE)]

    if re.match(r"^\s*\|", line):
        prefix, sep, body = line.partition("|")
        if "~" in line or "^" in line:
            return [
                part(f"{prefix}{sep}", ERROR_PREVIEW_LINE_STYLE),
                part(body, ERROR_PREVIEW_MESSAGE_STYLE)
            ]
        return [
            part(f"{prefix}{sep}", ERROR_PREVIEW_LINE_STYLE),
            part(body, ERROR_PREVIEW_TEXT_STYLE)
        ]

    if re.match(r"(?i)^\s*(error|fatal|warning|cannot|missing|failed|exception)\b", stripped):
        return [part(line, ERROR_PREVIEW_TEXT_STYLE)]

    if re.match(r"(?i)^Command failed\b", stripped):
        return [part(line, ERROR_PREVIEW_TEXT_STYLE)]

    if re.match(r"(?i)^stderr:\s*empty$", stripped):
        return [part(line, ERROR_PREVIEW_TEXT_STYLE)]

    if re.search(r"(?i)\b(error|fatal|warning|cannot|missing|failed|failure|exception|not found|not recognized|permission denied|no such file|syntax error|parse error|not a valid)\b", stripped):
        return [part(line, ERROR_PREVIEW_TEXT_STYLE)]

    return None


if __name__ == '__main__':
    pass
