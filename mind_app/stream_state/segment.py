# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing


class SegmentTracker(object):
    """维护流式文本段落及其 meta/sources 回填状态。"""

    def __init__(self) -> None:
        self.segment_seq = 0
        self.current_segment_key: typing.Optional[str] = None
        self.segment_order: list[str] = []
        self.segments_by_key: dict[str, dict[str, typing.Any]] = {}
        self.segments_by_remote_id: dict[str, str] = {}
        self.pending_meta_by_segment_id: dict[str, dict[str, typing.Any]] = {}
        self.pending_segment_sources: typing.Optional[dict[str, typing.Any]] = None

    @staticmethod
    def _segment_sources_payload(payload: dict[str, typing.Any]) -> dict[str, typing.Any]:
        sources = payload.get("sources")
        source_count = payload.get("source_count")

        data: dict[str, typing.Any] = {}
        if isinstance(sources, list):
            data["sources"] = sources
        if isinstance(source_count, int) and source_count >= 0:
            data["source_count"] = source_count
        elif isinstance(sources, list):
            data["source_count"] = len(sources)
        return data

    @classmethod
    def _segment_meta_payload(cls, payload: dict[str, typing.Any]) -> dict[str, typing.Any]:
        data = cls._segment_sources_payload(payload)

        annotations = payload.get("annotations")
        citations = payload.get("citations")
        if isinstance(annotations, list):
            data["annotations"] = annotations
        if isinstance(citations, list):
            data["citations"] = citations
        return data

    @staticmethod
    def _merge_segment_meta(
        segment: dict[str, typing.Any],
        payload: typing.Optional[dict[str, typing.Any]]
    ) -> None:
        if not segment or not isinstance(payload, dict):
            return None

        if isinstance(payload.get("annotations"), list):
            segment["annotations"] = payload["annotations"]

        if isinstance(payload.get("citations"), list):
            segment["citations"] = payload["citations"]

        if isinstance(payload.get("sources"), list):
            segment["sources"] = payload["sources"]

        source_count = payload.get("source_count")
        if isinstance(source_count, int) and source_count >= 0:
            segment["source_count"] = source_count
        elif isinstance(segment.get("sources"), list):
            segment["source_count"] = len(segment["sources"])

    def _apply_pending_meta(
        self,
        segment: dict[str, typing.Any],
        remote_segment_id: typing.Optional[str]
    ) -> None:
        if not remote_segment_id:
            return None

        if pending := self.pending_meta_by_segment_id.pop(remote_segment_id, None):
            self._merge_segment_meta(segment, pending)

    def _bind_remote_segment(
        self,
        segment: typing.Optional[dict[str, typing.Any]],
        remote_segment_id: typing.Optional[typing.Any]
    ) -> typing.Optional[dict[str, typing.Any]]:
        if not segment:
            return None

        if not (remote_segment_id := str(remote_segment_id or "").strip()):
            return segment

        if remote_segment_id in self.segments_by_remote_id:
            return self.segments_by_key.get(self.segments_by_remote_id[remote_segment_id])

        previous = segment.get("segment_id")
        if previous and previous in self.segments_by_remote_id:
            self.segments_by_remote_id.pop(previous, None)

        segment["segment_id"] = remote_segment_id
        self.segments_by_remote_id[remote_segment_id] = segment["local_id"]
        self._apply_pending_meta(segment, remote_segment_id)
        return segment

    def _latest_unbound_segment(self) -> typing.Optional[dict[str, typing.Any]]:
        for key in reversed(self.segment_order):
            if (segment := self.segments_by_key.get(key)) and not segment.get("segment_id"):
                return segment
        return None

    def _create_segment(
        self,
        remote_segment_id: typing.Optional[typing.Any] = None
    ) -> dict[str, typing.Any]:
        self.segment_seq += 1
        local_id = f"segment-{self.segment_seq}"

        segment = {
            "local_id"      : local_id,
            "segment_id"    : None,
            "text"          : "",
            "done"          : False,
            "annotations"   : [],
            "citations"     : [],
            "sources"       : [],
            "source_count"  : 0,
        }
        self.segment_order.append(local_id)
        self.segments_by_key[local_id] = segment
        self.current_segment_key = local_id

        self._bind_remote_segment(segment, remote_segment_id)

        if self.pending_segment_sources:
            self._merge_segment_meta(segment, self.pending_segment_sources)
            self.pending_segment_sources = None

        return segment

    def _resolve_segment(
        self,
        remote_segment_id: typing.Optional[typing.Any] = None,
        *,
        create: bool = False,
        prefer_current: bool = False
    ) -> typing.Optional[dict[str, typing.Any]]:
        remote_id = str(remote_segment_id or "").strip()

        if remote_id and remote_id in self.segments_by_remote_id:
            return self.segments_by_key.get(self.segments_by_remote_id[remote_id])

        if prefer_current and self.current_segment_key:
            segment = self.segments_by_key.get(self.current_segment_key)
            if segment:
                return self._bind_remote_segment(segment, remote_id)

        if remote_id and (segment := self._latest_unbound_segment()):
            return self._bind_remote_segment(segment, remote_id)

        if self.current_segment_key and (segment := self.segments_by_key.get(self.current_segment_key)):
            return segment

        if create:
            return self._create_segment(remote_id)

        return None

    def on_text_delta(self, event: dict[str, typing.Any]) -> None:
        text = str(event.get("text") or "")
        if not text:
            return None

        segment = self._resolve_segment(event.get("segment_id"), create=True, prefer_current=True)
        if segment is not None:
            segment["text"] += text

    def on_text_done(self, event: dict[str, typing.Any]) -> None:
        if segment := self._resolve_segment(event.get("segment_id"), prefer_current=True):
            segment["done"] = True
        self.current_segment_key = None

    def on_text_meta(self, event: dict[str, typing.Any]) -> None:
        remote_segment_id = str(event.get("segment_id") or "").strip()
        if not remote_segment_id:
            return None

        payload = self._segment_meta_payload(event)
        if segment := self._resolve_segment(remote_segment_id, prefer_current=True):
            self._merge_segment_meta(segment, payload)
            return None

        self.pending_meta_by_segment_id[remote_segment_id] = payload

    def on_builtin_done(self, event: dict[str, typing.Any]) -> None:
        meta = self._segment_sources_payload(event)
        if not meta:
            return None

        if self.current_segment_key and (segment := self.segments_by_key.get(self.current_segment_key)):
            self._merge_segment_meta(segment, meta)
            return None

        self.pending_segment_sources = {
            **(self.pending_segment_sources or {}),
            **meta
        }

    def iter_sources(self) -> typing.Iterable[typing.Any]:
        for key in self.segment_order:
            segment = self.segments_by_key.get(key) or {}
            sources = segment.get("sources")
            if not isinstance(sources, list):
                continue
            for source in sources:
                yield source

    def assistant_text(self) -> str:
        """返回当前回合模型正文原文。"""
        parts = [
            str((self.segments_by_key.get(key) or {}).get("text") or "")
            for key in self.segment_order
        ]
        return _join_text_segments(parts).strip()


def _join_text_segments(parts: list[str]) -> str:
    """按流式段落边界拼接正文。"""
    out = ""
    for part in parts:
        if not part:
            continue
        if out and not out.endswith("\n") and not part.startswith("\n"):
            out += "\n"
        out += part
    return out


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
    url = _source_url(source)
    title = _source_title(source, url)
    if url and title != url:
        return f"{index}. {title}\n   {url}"
    if url:
        return f"{index}. {url}"
    return f"{index}. {title}"


def build_sources_text(tracker: "SegmentTracker") -> str:
    max_items = 3
    seen: set[str] = set()
    lines: list[str] = []
    total = 0

    for source in tracker.iter_sources():
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
