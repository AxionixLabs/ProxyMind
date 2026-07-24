# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import typing
from dataclasses import dataclass
from mcp import types as mcp_types

_ENVELOPE_KEYS = frozenset({"ok", "text", "attachments", "data"})

_DISPLAY_JSON_LIMIT = 12000


@dataclass(frozen=True, slots=True)
class NormalizedToolResult(object):
    """描述经过统一归一化的 MCP 工具结果。"""

    fields: dict[str, typing.Any]
    display_text: str

    @property
    def ok(self) -> bool:
        """返回工具结果是否成功。"""
        return bool(self.fields.get("ok"))

    @property
    def data(self) -> typing.Any:
        """返回工具结果的结构化数据。"""
        return self.fields.get("data")


def normalize_call_tool_result(
    result: mcp_types.CallToolResult
) -> NormalizedToolResult:
    """把 MCP CallToolResult 转换为统一结果结构。"""
    text_parts, content_attachments, content_summaries = _content_parts(result.content)
    structured = (
        dict(result.structuredContent)
        if isinstance(result.structuredContent, dict)
        else {}
    )

    text = "\n".join(part for part in text_parts if part)
    if not text:
        text = _structured_text(structured)

    return _normalize_fields(
        structured,
        ok=not bool(result.isError),
        text=text,
        attachments=content_attachments,
        display_source=structured,
        content_summaries=content_summaries,
    )


def normalize_tool_fields(
    value: typing.Any,
    *,
    ok: bool,
    fallback_text: str = "",
    display_fallback: str = "",
    attachments: typing.Iterable[typing.Any] = (),
) -> NormalizedToolResult:
    """把现有工具字段或文本转换为统一结果结构。"""
    if isinstance(value, dict):
        raw_fields = dict(value)
        text = str(raw_fields.get("text") or fallback_text or "")
        display_source: typing.Any = raw_fields
    elif value is None:
        raw_fields = {}
        text = str(fallback_text or "")
        display_source = None
    else:
        raw_fields = {}
        text = str(value)
        display_source = value

    return _normalize_fields(
        raw_fields,
        ok=ok,
        text=text,
        attachments=attachments,
        display_source=display_source,
        display_fallback=display_fallback,
    )


def _normalize_fields(
    raw_fields: dict[str, typing.Any],
    *,
    ok: bool,
    text: str,
    attachments: typing.Iterable[typing.Any],
    display_source: typing.Any,
    display_fallback: str = "",
    content_summaries: typing.Iterable[str] = (),
) -> NormalizedToolResult:
    """建立稳定字段，并生成独立于模型回填内容的展示文本。"""
    is_envelope = _ENVELOPE_KEYS.issubset(raw_fields)
    if is_envelope:
        fields = dict(raw_fields)
        structured_attachments = fields.get("attachments")
    else:
        fields = {"data": dict(raw_fields) if raw_fields else {}}
        structured_attachments = ()

    normalized_attachments = _normalize_attachments(structured_attachments)
    normalized_attachments.extend(_normalize_attachments(attachments))

    fields["ok"]          = bool(ok)
    fields["text"]        = str(text or "")
    fields["attachments"] = normalized_attachments

    fields.setdefault("data", {})

    display_text = fields["text"].strip()
    if not display_text:
        display_text = str(display_fallback or "").strip()
    if not display_text:
        display_text = _structured_preview(display_source)
    if not display_text:
        display_text = "\n".join(item for item in content_summaries if item)
    if not display_text:
        display_text = (
            "Tool completed with no textual output."
            if ok
            else "Tool failed with no textual error details."
        )

    return NormalizedToolResult(fields=fields, display_text=display_text)


def _content_parts(
    content: list[mcp_types.ContentBlock]
) -> tuple[list[str], list[dict[str, typing.Any]], list[str]]:
    """提取 MCP 内容块中的文本、附件和无文本摘要。"""
    texts: list[str]                         = []
    attachments: list[dict[str, typing.Any]] = []
    summaries: list[str]                     = []

    for item in content:
        if isinstance(item, mcp_types.TextContent):
            if item.text:
                texts.append(item.text)
            continue

        if isinstance(item, mcp_types.ImageContent):
            attachments.append({
                "kind"      : "image",
                "mime_type" : item.mimeType,
                "data_url"  : _data_url(item.mimeType, item.data),
            })
            summaries.append(f"Image output ({item.mimeType}).")
            continue

        if isinstance(item, mcp_types.AudioContent):
            attachments.append({
                "kind"      : "audio",
                "mime_type" : item.mimeType,
                "data_url"  : _data_url(item.mimeType, item.data),
            })
            summaries.append(f"Audio output ({item.mimeType}).")
            continue

        if isinstance(item, mcp_types.ResourceLink):
            attachment: dict[str, typing.Any] = {
                "kind"      : "resource_link",
                "name"      : item.name,
                "uri"       : str(item.uri),
                "mime_type" : item.mimeType,
            }
            if item.title:
                attachment["title"] = item.title
            if item.description:
                attachment["description"] = item.description
            if item.size is not None:
                attachment["size"] = item.size
            attachments.append(attachment)
            summaries.append(f"Resource: {item.title or item.name} ({item.uri}).")
            continue

        if isinstance(item, mcp_types.EmbeddedResource):

            resource = item.resource
            uri      = str(resource.uri)

            if isinstance(resource, mcp_types.TextResourceContents):
                if resource.text:
                    texts.append(resource.text)
                attachments.append({
                    "kind"      : "embedded_resource",
                    "uri"       : uri,
                    "mime_type" : resource.mimeType,
                })
                summaries.append(f"Embedded resource: {uri}.")
                continue

            attachments.append({
                "kind"      : "embedded_resource",
                "uri"       : uri,
                "mime_type" : resource.mimeType,
                "data_url"  : _data_url(
                    resource.mimeType or "application/octet-stream",
                    resource.blob,
                ),
            })
            summaries.append(f"Embedded resource: {uri}.")

    return texts, attachments, summaries


def _normalize_attachments(value: typing.Any) -> list[dict[str, typing.Any]]:
    """把附件描述转换为可序列化字典列表。"""
    if not isinstance(value, (list, tuple)):
        return []

    normalized: list[dict[str, typing.Any]] = []
    for item in value:
        if isinstance(item, dict):
            normalized.append(dict(item))
            continue
        if hasattr(item, "model_dump"):
            dumped = item.model_dump(by_alias=True, exclude_none=True)
            if isinstance(dumped, dict):
                normalized.append(dumped)
            continue
        if hasattr(item, "to_dict"):
            dumped = item.to_dict()
            if isinstance(dumped, dict):
                normalized.append(dumped)
    return normalized


def _structured_text(structured: dict[str, typing.Any]) -> str:
    """读取结构化结果显式提供的文本字段。"""
    value = structured.get("text")
    return str(value) if value is not None else ""


def _structured_preview(value: typing.Any) -> str:
    """生成有界的结构化展示文本。"""
    if value in (None, {}, [], ""):
        return ""
    if isinstance(value, str):
        return value.strip()

    try:
        rendered = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        rendered = str(value)

    if len(rendered) <= _DISPLAY_JSON_LIMIT:
        return rendered
    return f"{rendered[:_DISPLAY_JSON_LIMIT]}\n... (truncated)"


def _data_url(mime_type: str, data: str) -> str:
    """把 MCP base64 内容转换为标准 data URL。"""
    return f"data:{mime_type};base64,{data}"


if __name__ == '__main__':
    pass
