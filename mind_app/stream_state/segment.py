# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_nova.stream_events import (
    TextDeltaEvent,
    TextDoneEvent,
    TextMetaEvent,
    ToolBuiltinDoneEvent
)


class SegmentTracker(object):
    """维护流式文本段落及其 meta/sources 回填状态。"""

    def __init__(self) -> None:
        self.segment_seq = 0

        self.current_segment_key: typing.Optional[str] = None

        self.segment_order: list[str]                          = []
        self.segments_by_key: dict[str, dict[str, typing.Any]] = {}
        self.segments_by_remote_id: dict[str, str]             = {}

        self.pending_meta_by_segment_id: dict[str, dict[str, typing.Any]]    = {}
        self.pending_segment_sources: typing.Optional[dict[str, typing.Any]] = None
        self.pending_output_segment_keys: list[str]                          = []

        self.output_blocks: list[list[str]] = []

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

    def _remember_output_segment(self, segment: dict[str, typing.Any]) -> None:
        local_id = segment.get("local_id")
        if not isinstance(local_id, str) or not local_id:
            return None
        if local_id not in self.pending_output_segment_keys:
            self.pending_output_segment_keys.append(local_id)

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

    def on_text_delta(self, event: TextDeltaEvent) -> None:
        if not event.text:
            return None

        segment = self._resolve_segment(
            event.segment_id,
            create=True,
            prefer_current=True,
        )
        if segment is not None:
            segment["text"] += event.text
            self._remember_output_segment(segment)

    def on_text_done(self, event: TextDoneEvent) -> None:
        if segment := self._resolve_segment(
            event.segment_id,
            prefer_current=True,
        ):
            segment["done"] = True
        self.current_segment_key = None

    def on_text_meta(self, event: TextMetaEvent) -> None:
        if not event.segment_id:
            return None

        payload = self._typed_segment_meta_payload(event)
        if segment := self._resolve_segment(event.segment_id, prefer_current=True):
            self._merge_segment_meta(segment, payload)
            return None

        self.pending_meta_by_segment_id[event.segment_id] = payload

    def on_builtin_done(self, event: ToolBuiltinDoneEvent) -> None:
        meta = self._typed_sources_payload(
            sources=event.sources,
            source_count=event.source_count,
        )
        if not meta:
            return None

        if self.current_segment_key and (segment := self.segments_by_key.get(self.current_segment_key)):
            self._merge_segment_meta(segment, meta)
            return None

        self.pending_segment_sources = {
            **(self.pending_segment_sources or {}),
            **meta
        }

    @classmethod
    def _typed_segment_meta_payload(
        cls,
        event: TextMetaEvent
    ) -> dict[str, typing.Any]:
        """把类型化正文元数据转换为段落存储字段。"""
        data = cls._typed_sources_payload(
            sources=event.sources,
            source_count=event.source_count,
        )
        if event.annotations is not None:
            data["annotations"] = list(event.annotations)
        if event.citations is not None:
            data["citations"] = list(event.citations)
        return data

    @staticmethod
    def _typed_sources_payload(
        *,
        sources: tuple[typing.Any, ...] | None,
        source_count: int | None,
    ) -> dict[str, typing.Any]:
        """把类型化来源元数据转换为段落存储字段。"""
        data: dict[str, typing.Any] = {}
        if sources is not None:
            data["sources"] = list(sources)
        if source_count is not None:
            data["source_count"] = source_count
        elif sources is not None:
            data["source_count"] = len(sources)
        return data

    def commit_assistant_output(self) -> str:
        """提交当前待复制的 assistant 输出块并返回正文。"""
        keys = [
            key for key in self.pending_output_segment_keys
            if str((self.segments_by_key.get(key) or {}).get("text") or "").strip()
        ]

        self.pending_output_segment_keys = []
        self.current_segment_key         = None

        if not keys:
            return ""

        self.output_blocks.append(keys)

        parts = [
            str((self.segments_by_key.get(key) or {}).get("text") or "")
            for key in keys
        ]
        return _join_text_segments(parts).strip()

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

    def latest_assistant_output_text(self) -> str:
        """返回最近一次 assistant 输出块原文。"""
        self.commit_assistant_output()
        if not self.output_blocks:
            return ""

        parts = [
            str((self.segments_by_key.get(key) or {}).get("text") or "")
            for key in self.output_blocks[-1]
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


if __name__ == '__main__':
    pass
