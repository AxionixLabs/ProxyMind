# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import json
import typing
from dataclasses import dataclass

PasteKind   = typing.Literal["Text", "Code", "Data", "Diff", "Log"]
PasteMetric = typing.Literal["chars", "lines"]

_PASTE_PLACEHOLDER_RE = re.compile(
    r"\[(?P<kind>Text|Code|Data|Diff|Log) #(?P<index>[1-9]\d*)"
    r"(?: · (?P<subtype>[A-Za-z][A-Za-z0-9+#.-]*))?"
    r" · (?P<count>0|[1-9]\d{0,2}(?:,\d{3})*)"
    r" (?P<metric>chars|lines)]"
)

_CODE_LANGUAGES = frozenset({
    "Go",
    "HTML",
    "JavaScript",
    "Python",
    "Rust",
    "SQL",
    "Shell",
    "TypeScript",
})

_CLASSIFY_CHAR_LIMIT = 64 * 1024
_CLASSIFY_LINE_LIMIT = 200
_JSON_PARSE_LIMIT    = 256 * 1024

_DIFF_HUNK_RE = re.compile(r"^@@ .+ @@", re.MULTILINE)

_LOG_LEVEL_RE = re.compile(
    r"(?:^|[\s\[])"
    r"(?:TRACE|DEBUG|INFO|NOTICE|WARN(?:ING)?|ERROR|CRITICAL|FATAL)"
    r"(?:[\s\]:-]|$)",
    re.IGNORECASE,
)

_LOG_TIMESTAMP_RE = re.compile(
    r"^\s*\[?(?:\d{4}-\d{2}-\d{2}[ T]|\d{2}:\d{2}:\d{2})"
)

_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+\S")
_MARKDOWN_LINK_RE    = re.compile(r"!?\[[^]\n]+]\([^)\n]+\)")


@dataclass(frozen=True, slots=True)
class PasteDescriptor(object):
    """描述折叠粘贴内容的显示分类和精确计量。"""

    kind: PasteKind
    subtype: str | None
    metric: PasteMetric
    count: int


@dataclass(frozen=True, slots=True)
class PastePlaceholder(object):
    """描述经过严格验证的折叠粘贴占位符。"""

    kind: PasteKind
    index: int
    subtype: str | None
    metric: PasteMetric
    count: int


def paste_line_count(text: str) -> int:
    """返回文本缓冲区包含的逻辑行数。"""
    return text.count("\n") + 1 if text else 0


def describe_paste(text: str) -> PasteDescriptor:
    """根据有界内容样本生成粘贴内容描述。"""
    char_count = len(text)
    line_count = paste_line_count(text)

    sample = text[:_CLASSIFY_CHAR_LIMIT]
    lines  = tuple(sample.splitlines()[:_CLASSIFY_LINE_LIMIT])

    if _looks_like_diff(sample, lines):
        return PasteDescriptor("Diff", None, "lines", line_count)

    if _is_json(text):
        return PasteDescriptor("Data", "JSON", "chars", char_count)

    if _looks_like_log(lines):
        return PasteDescriptor("Log", None, "lines", line_count)

    if _looks_like_markdown(sample, lines):
        return PasteDescriptor("Text", "Markdown", "lines", line_count)

    is_code, language = _code_language(sample, lines)

    if is_code:
        return PasteDescriptor("Code", language, "lines", line_count)

    return PasteDescriptor("Text", None, "chars", char_count)


def format_paste_placeholder(descriptor: PasteDescriptor, index: int) -> str:
    """生成带稳定编号和精确数量的粘贴占位符。"""
    parts = [f"{descriptor.kind} #{max(1, int(index))}"]
    if descriptor.subtype:
        parts.append(descriptor.subtype)
    parts.append(f"{descriptor.count:,} {descriptor.metric}")
    return f"[{' · '.join(parts)}]"


def parse_paste_placeholder(value: str) -> PastePlaceholder | None:
    """解析完整占位符，并拒绝非生成器格式的类型组合。"""
    match = _PASTE_PLACEHOLDER_RE.fullmatch(str(value or ""))
    if match is None:
        return None

    kind    = match.group("kind")
    subtype = match.group("subtype")
    metric  = match.group("metric")

    valid = (
        (kind == "Text" and subtype is None and metric == "chars")
        or (kind == "Text" and subtype == "Markdown" and metric == "lines")
        or (
            kind == "Code"
            and metric == "lines"
            and (subtype is None or subtype in _CODE_LANGUAGES)
        )
        or (kind == "Data" and subtype == "JSON" and metric == "chars")
        or (kind in {"Diff", "Log"} and subtype is None and metric == "lines")
    )
    if not valid:
        return None

    return PastePlaceholder(
        kind=kind,
        index=int(match.group("index")),
        subtype=subtype,
        metric=metric,
        count=int(match.group("count").replace(",", "")),
    )


def _looks_like_diff(sample: str, lines: tuple[str, ...]) -> bool:
    """判断样本是否具有补丁结构。"""
    if any(line.startswith("diff --git ") for line in lines):
        return True
    has_old = any(line.startswith("--- ") for line in lines)
    has_new = any(line.startswith("+++ ") for line in lines)
    return has_old and has_new and _DIFF_HUNK_RE.search(sample) is not None


def _is_json(text: str) -> bool:
    """在受限体积内验证完整 JSON 内容。"""
    if len(text) > _JSON_PARSE_LIMIT:
        return False

    stripped = text.strip()

    if len(stripped) < 2:
        return False
    if (stripped[0], stripped[-1]) not in {("{", "}"), ("[", "]")}:
        return False
    try:
        json.loads(stripped)
    except ValueError:
        return False

    return True


def _looks_like_log(lines: tuple[str, ...]) -> bool:
    """判断样本是否具有日志或调用栈结构。"""
    if not lines:
        return False

    joined = "\n".join(lines)

    if "Traceback (most recent call last):" in joined:
        return True

    if re.search(
        r"(?m)^\s*(?:Caused by:|at \S+\([^\n]+\)|File \".+\", line \d+)",
        joined,
    ):
        return True

    matched = sum(
        bool(_LOG_TIMESTAMP_RE.search(line) or _LOG_LEVEL_RE.search(line))
        for line in lines
        if line.strip()
    )

    return matched >= 3


def _looks_like_markdown(sample: str, lines: tuple[str, ...]) -> bool:
    """判断样本是否具有 Markdown 文档结构。"""
    features = 0

    if any(line.lstrip().startswith("```") for line in lines):
        features += 1
    if sum(bool(_MARKDOWN_HEADING_RE.match(line)) for line in lines) >= 2:
        features += 1
    if len(_MARKDOWN_LINK_RE.findall(sample)) >= 2:
        features += 1
    if sum(line.lstrip().startswith(("- ", "* ", "> ")) for line in lines) >= 3:
        features += 1

    return features >= 2


def _code_language(sample: str, lines: tuple[str, ...]) -> tuple[bool, str | None]:
    """根据强语法特征判断代码及常见语言。"""
    if re.search(
        r"(?m)^\s*(?:from \S+ import |import \S+|async def |def |class )",
        sample,
    ):
        if sum(bool(line[:1].isspace()) for line in lines if line.strip()) >= 2:
            return True, "Python"

    if re.search(r"(?m)^\s*(?:interface|type)\s+[A-Za-z_$]", sample):
        return True, "TypeScript"
    if len(re.findall(
        r"(?m)^\s*(?:import|export|const|let|var|function)\b",
        sample,
    )) >= 2:
        return True, "JavaScript"
    if re.search(r"(?im)^\s*(?:select|insert|update|delete|create)\b", sample) and re.search(
        r"(?im)^\s*(?:from|into|set|table|where)\b",
        sample,
    ):
        return True, "SQL"
    if re.search(r"(?im)^\s*(?:<!doctype html>|<html\b|<body\b)", sample):
        return True, "HTML"
    if re.search(r"(?m)^\s*package\s+\w+", sample) and re.search(
        r"(?m)^\s*func\s+(?:\([^)]*\)\s*)?\w+",
        sample,
    ):
        return True, "Go"
    if re.search(r"(?m)^\s*(?:pub\s+)?fn\s+\w+", sample) and re.search(
        r"(?m)^\s*(?:use|let|impl|struct|enum)\b",
        sample,
    ):
        return True, "Rust"
    if sample.startswith("#!") and len(lines) >= 3:
        return True, "Shell"

    syntax_lines = sum(
        bool(re.search(r"[{};]\s*$", line))
        for line in lines
        if line.strip()
    )
    return (True, None) if syntax_lines >= 4 else (False, None)


if __name__ == '__main__':
    pass
