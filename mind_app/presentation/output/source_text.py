# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing


def _source_value(source: typing.Any, *keys: str) -> typing.Optional[str]:
    if isinstance(source, dict):
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return None


def _source_url(source: typing.Any) -> typing.Optional[str]:
    if isinstance(source, str):
        text = source.strip()
        return text if text.startswith(("http://", "https://")) else None

    if not isinstance(source, dict):
        return None

    url = _source_value(source, "url", "uri", "href", "link")
    if url:
        return url

    nested = source.get("source")
    if isinstance(nested, str) and nested.strip().startswith(("http://", "https://")):
        return nested.strip()

    return None


def _source_title(source: typing.Any, url: typing.Optional[str]) -> str:
    if isinstance(source, str):
        text = source.strip()
        if text:
            return text
        return url or "Untitled source"

    if not isinstance(source, dict):
        return url or "Untitled source"

    title = _source_value(
        source,
        "title",
        "name",
        "label",
        "text",
        "display_title",
        "display_name"
    )
    if title:
        return title

    if isinstance(source.get("source"), str):
        text = str(source["source"]).strip()
        if text and text != url:
            return text

    return url or "Untitled source"


def _format_source_entry(index: int, source: typing.Any) -> str:
    url   = _source_url(source)
    title = _source_title(source, url)

    if url and title != url:
        return f"{index}. {title}\n   {url}"
    if url:
        return f"{index}. {url}"

    return f"{index}. {title}"


def render_sources_text(sources: typing.Iterable[typing.Any]) -> str:
    """把原始来源转换为当前纯文本展示。"""
    max_items: int   = 3
    seen: set[str]   = set()
    lines: list[str] = []
    total: int       = 0

    for source in sources:
        url = _source_url(source)
        key = url or repr(source)
        if key in seen:
            continue
        seen.add(key)
        total += 1
        if len(lines) < max_items:
            lines.append(_format_source_entry(len(lines) + 1, source))

    if not lines:
        return ""

    if total > len(lines):
        lines.append(f"... {total - len(lines)} more sources omitted")

    body = "\n".join(lines)
    return f"Sources:\n{body}"


if __name__ == '__main__':
    pass
