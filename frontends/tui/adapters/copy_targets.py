# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
from dataclasses import dataclass

from markdown_it import MarkdownIt
from markdown_it.token import Token


@dataclass(frozen=True, slots=True)
class AssistantCopyTarget:
    """描述从最近 assistant Markdown 中提取的一项可复制内容。"""

    label: str
    text: str
    description: str


_MARKDOWN = MarkdownIt("commonmark")


def assistant_copy_targets(source: str) -> tuple[AssistantCopyTarget, ...]:
    """按 Codex 顺序返回整体回复、围栏代码和顶层引用。"""
    markdown_source = str(source or "")
    visible = visible_assistant_markdown(markdown_source)
    if not visible:
        return ()

    targets: list[AssistantCopyTarget] = [
        _target("Whole response", visible)
    ]
    tokens = tuple(_MARKDOWN.parse(markdown_source))
    source_lines = tuple(markdown_source.splitlines(keepends=True))

    for index, token in enumerate(tokens):
        if token.type == "blockquote_open" and token.level == 0:
            quote = _blockquote_target(
                tokens,
                index=index,
                source_lines=source_lines,
            )
            if quote is not None:
                targets.append(quote)
            continue
        if token.type == "fence":
            targets.append(_fenced_code_target(token, source_lines))

    return tuple(targets)


def visible_assistant_markdown(source: str) -> str:
    """生成 Codex 整体回复复制项使用的稳定可见 Markdown。"""
    normalized = str(source or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip(" \t") for line in normalized.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _target(label: str, text: str) -> AssistantCopyTarget:
    """构造带首个非空行摘要的复制候选。"""
    description = next(
        (
            line.strip()[:72]
            for line in text.splitlines()
            if line.strip()
        ),
        "",
    )
    return AssistantCopyTarget(
        label=label,
        text=text,
        description=description,
    )


def _fenced_code_target(
    token: Token,
    source_lines: tuple[str, ...],
) -> AssistantCopyTarget:
    """从围栏 Token 恢复代码内容及原始换行。"""
    info_parts = re.split(r"[, \t]", token.info)
    language = info_parts[0] if info_parts and info_parts[0] else ""
    label = f"{language} code" if language else "Code block"
    text = _restore_source_newlines(
        token.content,
        source_lines,
        start_line=(token.map or [0, 0])[0] + 1,
    )
    return _target(label, text)


def _blockquote_target(
    tokens: tuple[Token, ...],
    *,
    index: int,
    source_lines: tuple[str, ...],
) -> AssistantCopyTarget | None:
    """提取顶层引用并保留内部 Markdown 和原始换行。"""
    token = tokens[index]
    if token.map is None or not _blockquote_has_text(tokens, index=index):
        return None
    start, end = token.map
    text = "".join(
        _strip_outer_quote_marker(line)
        for line in source_lines[start:end]
    )
    if not text.strip():
        return None
    return _target("Blockquote", text)


def _blockquote_has_text(tokens: tuple[Token, ...], *, index: int) -> bool:
    """判断引用中是否存在围栏代码之外的正文。"""
    level = tokens[index].level
    for token in tokens[index + 1:]:
        if token.type == "blockquote_close" and token.level == level:
            return False
        if token.type == "inline" and token.content.strip():
            return True
    return False


def _strip_outer_quote_marker(line: str) -> str:
    """移除一层 blockquote 标记并保留剩余内容和换行。"""
    content = line.lstrip(" ")
    if not content.startswith(">"):
        return line
    content = content[1:]
    if content.startswith(" "):
        content = content[1:]
    return content


def _restore_source_newlines(
    content: str,
    source_lines: tuple[str, ...],
    *,
    start_line: int,
) -> str:
    """把 Markdown parser 规范化的换行恢复为源码换行。"""
    fragments = content.splitlines(keepends=True)
    restored: list[str] = []
    for offset, fragment in enumerate(fragments):
        if not fragment.endswith("\n"):
            restored.append(fragment)
            continue
        source_index = start_line + offset
        ending = (
            _line_ending(source_lines[source_index])
            if source_index < len(source_lines)
            else "\n"
        )
        restored.append(f"{fragment[:-1]}{ending}")
    return "".join(restored)


def _line_ending(line: str) -> str:
    """返回源码行使用的实际换行序列。"""
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    if line.endswith("\r"):
        return "\r"
    return ""


if __name__ == '__main__':
    pass
