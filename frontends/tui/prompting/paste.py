# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from dataclasses import dataclass

_PASTE_PLACEHOLDER_RE = re.compile(
    r"\[Pasted Content (?P<count>[1-9]\d*) chars]"
    r"(?: #(?P<index>[2-9]|[1-9]\d+))?"
)


@dataclass(frozen=True, slots=True)
class PastePlaceholder(object):
    """描述折叠粘贴内容的字符数和显示编号。"""

    count: int
    index: int


def paste_line_count(text: str) -> int:
    """返回文本缓冲区包含的逻辑行数。"""
    return text.count("\n") + 1 if text else 0


def format_paste_placeholder(text: str, index: int) -> str:
    """生成折叠粘贴内容的统一占位文本。"""
    sequence = max(1, int(index))
    suffix = "" if sequence == 1 else f" #{sequence}"

    return f"[Pasted Content {len(text)} chars]{suffix}"


def parse_paste_placeholder(value: str) -> PastePlaceholder | None:
    """解析完整的折叠粘贴占位文本。"""
    match = _PASTE_PLACEHOLDER_RE.fullmatch(str(value or ""))
    if match is None:
        return None

    return PastePlaceholder(
        count=int(match.group("count")),
        index=int(match.group("index") or 1),
    )


def iter_paste_placeholders(
    text: str,
) -> typing.Iterator[tuple[int, int, str]]:
    """迭代文本中结构完整的折叠粘贴占位符。"""
    for match in _PASTE_PLACEHOLDER_RE.finditer(str(text or "")):
        yield match.start(), match.end(), match.group(0)


if __name__ == '__main__':
    pass
