# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing

from frontends.terminal.text import sanitize_terminal_text


def normalize_shell_output_text(value: typing.Any) -> str:
    """清理命令输出里的终端控制序列并展开水平制表符。"""
    return sanitize_terminal_text(value)


def shell_output_lines(value: typing.Any, *, keep_empty: bool = False) -> list[str]:
    """把 shell 输出归一化成已清理 ANSI 的文本行。"""
    lines = normalize_shell_output_text(value).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if keep_empty:
        return lines
    return [line for line in lines if line.strip()]


def shell_error_diagnostic_lines(
    lines: list[str],
    *,
    max_context_lines: int
) -> list[str]:
    """优先提取跨 shell 的结构化错误诊断块。"""
    lines = shell_output_lines("\n".join(str(line or "") for line in lines))

    extractors = (
        _powershell_line_diagnostic_block,
        _powershell_at_line_diagnostic_block,
        _python_traceback_diagnostic_block,
        _posix_shell_diagnostic_block,
        _tool_error_diagnostic_block
    )

    for extractor in extractors:
        block = extractor(lines)
        if block:
            return _clip_diagnostic_block(block, max_lines=max_context_lines)

    return []


def shell_error_compact_summary(
    value: typing.Any
) -> str:
    """从真实 shell 错误输出中提取适合树形视图的一行诊断摘要。"""
    lines = _normalize_lines(value)
    if not lines:
        return ""

    diagnostic = shell_error_diagnostic_lines(lines, max_context_lines=8)
    if diagnostic:
        summary = _diagnostic_summary(diagnostic)
        if summary:
            return summary

    return lines[0].strip()


def _diagnostic_summary(lines: list[str]) -> str:
    """把结构化诊断块压缩成最有价值的一行。"""
    clean = [str(line or "").strip() for line in lines if str(line or "").strip()]
    if not clean:
        return ""

    head = clean[0]

    for line in reversed(clean):
        stripped = _strip_marker_prefix(line)
        if not stripped:
            continue
        if stripped in {"Line |"}:
            continue
        if _is_location_or_marker_line(stripped):
            continue
        if line == head:
            break
        return _join_summary_head(head, stripped)

    return head


def _join_summary_head(head: str, message: str) -> str:
    """合并错误头和具体原因，避免重复。"""
    head = str(head or "").strip()
    message = str(message or "").strip()

    if not head:
        return message
    if not message:
        return head
    if message.startswith(head):
        return message
    if head.endswith(":"):
        return f"{head} {message}"

    return f"{head}: {message}"


def _strip_marker_prefix(line: str) -> str:
    """去掉 PowerShell 管道前缀，保留真实错误消息。"""
    return re.sub(r"^\|\s*", "", str(line or "").strip())


def _is_location_or_marker_line(line: str) -> bool:
    """判断诊断行是否只是位置或指针信息。"""
    stripped = str(line or "").strip()

    return bool(
        stripped == "Line |"
        or re.match(r"^\d+\s+\|", stripped)
        or re.match(r"^\+\s", stripped)
        or re.match(r"^[~^]+$", stripped)
        or re.match(r"^At line:\d+ char:\d+", stripped, re.IGNORECASE)
        or stripped.startswith("File ")
        or stripped in {"Traceback (most recent call last):"}
    )


def _powershell_line_diagnostic_block(lines: list[str]) -> list[str]:
    """提取 PowerShell 的 Header/Line 管道错误块。"""
    for index, line in enumerate(lines):
        if str(line or "").strip() != "Line |":
            continue

        start = index
        if index > 0 and _is_error_header_line(lines[index - 1]):
            start = index - 1

        end = index + 1
        while end < len(lines) and _is_powershell_line_detail(lines[end]):
            end += 1

        if end > index + 1:
            return lines[start:end]

    return []


def _powershell_at_line_diagnostic_block(lines: list[str]) -> list[str]:
    """提取 Windows PowerShell 的 At line:char 错误块。"""
    for index, line in enumerate(lines):
        if not re.match(r"^At line:\d+ char:\d+", str(line or "").strip(), re.IGNORECASE):
            continue

        end = index + 1
        while end < len(lines):
            current = str(lines[end] or "")
            stripped = current.strip()
            if not stripped:
                break
            if end > index + 1 and re.match(r"^[A-Za-z][A-Za-z0-9_. -]*:$", stripped):
                break
            end += 1

        return lines[index:end]

    return []


def _python_traceback_diagnostic_block(lines: list[str]) -> list[str]:
    """提取 Python traceback，避免长堆栈只保留尾部。"""
    for index, line in enumerate(lines):
        if str(line or "").strip() == "Traceback (most recent call last):":
            return lines[index:]

    return []


def _posix_shell_diagnostic_block(lines: list[str]) -> list[str]:
    """提取 bash/zsh/sh 等 POSIX shell 常见语法错误。"""
    for index, line in enumerate(lines):
        stripped = str(line or "").strip()
        if re.match(
            r"^(bash|zsh|sh|dash|fish|ksh)(:|\b).*(syntax error|parse error|not found|permission denied)",
            stripped,
            re.IGNORECASE
        ):
            return lines[index:min(len(lines), index + 3)]

    return []


def _tool_error_diagnostic_block(lines: list[str]) -> list[str]:
    """提取常见命令行工具错误块。"""
    for index, line in enumerate(lines):
        stripped = str(line or "").strip()
        if not re.match(
            r"^[A-Za-z0-9_.-]+: .*(error|failed|cannot|missing)",
            stripped,
            re.IGNORECASE
        ):
            continue

        end = index + 1
        while end < len(lines):
            current = str(lines[end] or "").strip()
            if not current:
                break
            if end > index + 1 and re.match(
                r"^[A-Za-z0-9_.-]+: ", current
            ) and not current.lower().startswith("error:"):
                break

            end += 1

        return lines[index:end]

    return []


def _clip_diagnostic_block(block: list[str], *, max_lines: int) -> list[str]:
    """按诊断块语义裁剪，保留头部和最终错误信息。"""
    limit = max(1, int(max_lines or 1))
    if len(block) <= limit:
        return block
    if limit <= 2:
        return block[:limit]

    head_count = max(1, limit // 2)
    tail_count = max(1, limit - head_count - 1)

    omitted = len(block) - head_count - tail_count

    return [
        *block[:head_count],
        f"… +{omitted} lines",
        *block[-tail_count:],
    ]


def _is_error_header_line(line: str) -> bool:
    """判断是否是错误块标题行。"""
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9_. -]*:$", str(line or "").strip()))


def _is_powershell_line_detail(line: str) -> bool:
    """判断是否是 PowerShell Line | 块的详情行。"""
    text = str(line or "")
    stripped = text.strip()

    return bool(
        re.match(r"^\d+\s+\|", stripped)
        or re.match(r"^\|", stripped)
        or re.match(r"^[~^]+$", stripped)
    )


def _normalize_lines(value: typing.Any) -> list[str]:
    """把 shell 输出归一化成非空行。"""
    return shell_output_lines(value)


if __name__ == '__main__':
    pass
