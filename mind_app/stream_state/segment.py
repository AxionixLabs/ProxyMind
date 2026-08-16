# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_nova.stream_events import (
    TextDeltaEvent,
    TextDoneEvent,
    TextMetaEvent,
    PresentationSupersededEvent,
    TurnRetryingEvent,
    ToolBuiltinDoneEvent
)


class SegmentTracker(object):
    """维护流式文本段落及其 meta/sources 回填状态。"""

    def __init__(self) -> None:
        """初始化正文段落、远端标识和展示块索引。"""
        self.segment_seq = 0

        self.current_segment_key: typing.Optional[str] = None

        self.segment_order: list[str]                          = []
        self.segments_by_key: dict[str, dict[str, typing.Any]] = {}
        self.segments_by_remote_id: dict[str, str]             = {}

        self.pending_meta_by_segment_id: dict[str, dict[str, typing.Any]]    = {}
        self.pending_segment_sources: typing.Optional[dict[str, typing.Any]] = None
        self.pending_output_segment_keys: list[str]                          = []

        self.output_blocks: list[list[str]] = []

        self.last_committed_epoch: int = 1

    @staticmethod
    def _merge_segment_meta(
        segment: dict[str, typing.Any],
        payload: typing.Optional[dict[str, typing.Any]]
    ) -> None:
        """把有效的来源与标注字段合并到正文段落。"""
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
        """把提前到达的元数据应用到已绑定段落。"""
        if not remote_segment_id:
            return None

        if pending := self.pending_meta_by_segment_id.pop(remote_segment_id, None):
            self._merge_segment_meta(segment, pending)

    def _bind_remote_segment(
        self,
        segment: typing.Optional[dict[str, typing.Any]],
        remote_segment_id: typing.Optional[typing.Any]
    ) -> typing.Optional[dict[str, typing.Any]]:
        """把本地段落绑定到稳定的远端段落标识。"""
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
        """返回最近创建且尚未绑定远端标识的段落。"""
        for key in reversed(self.segment_order):
            if (segment := self.segments_by_key.get(key)) and not segment.get("segment_id"):
                return segment
        return None

    def _create_segment(
        self,
        remote_segment_id: typing.Optional[typing.Any] = None,
        *,
        presentation_epoch: int = 1
    ) -> dict[str, typing.Any]:
        """创建属于指定展示 epoch 的本地正文段落。"""
        self.segment_seq += 1
        local_id = f"segment-{self.segment_seq}"

        segment = {
            "local_id": local_id,
            "segment_id": None,
            "text": "",
            "done": False,
            "annotations": [],
            "citations": [],
            "sources": [],
            "source_count": 0,
            "presentation_epoch": max(1, int(presentation_epoch)),
            "superseded": False,
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
        """记录需要提交到当前 assistant 输出块的段落。"""
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
        """按远端标识和当前流式状态解析正文段落。"""
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
        """把正文增量追加到当前展示 epoch 的段落。"""
        if not event.text:
            return None

        segment = self._resolve_segment(
            event.segment_id,
            create=True,
            prefer_current=True,
        )
        if segment is not None:
            segment["presentation_epoch"] = event.presentation_epoch
            segment["text"] += event.text
            self._remember_output_segment(segment)

    def on_text_done(self, event: TextDoneEvent) -> None:
        """标记正文段落结束并断开当前流式段落。"""
        if segment := self._resolve_segment(
            event.segment_id,
            prefer_current=True,
        ):
            segment["done"] = True
        self.current_segment_key = None

    def on_text_meta(self, event: TextMetaEvent) -> None:
        """应用或暂存正文段落的来源与标注元数据。"""
        if not event.segment_id:
            return None

        payload = self._typed_segment_meta_payload(event)
        if segment := self._resolve_segment(event.segment_id, prefer_current=True):
            self._merge_segment_meta(segment, payload)
            return None

        self.pending_meta_by_segment_id[event.segment_id] = payload

    def on_builtin_done(self, event: ToolBuiltinDoneEvent) -> None:
        """把内置工具来源关联到当前或下一正文段落。"""
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

    def on_presentation_superseded(
        self,
        event: PresentationSupersededEvent
    ) -> None:
        """把旧 epoch 段落标为仅供审计展示并断开流式续接。"""
        for segment in self.segments_by_key.values():
            if int(segment.get("presentation_epoch") or 1) <= event.superseded_epoch:
                segment["superseded"] = True

        self.pending_output_segment_keys = [
            key
            for key in self.pending_output_segment_keys
            if not bool((self.segments_by_key.get(key) or {}).get("superseded"))
        ]

        self.current_segment_key     = None
        self.pending_segment_sources = None

    def on_turn_retrying(self, event: TurnRetryingEvent) -> bool:
        """隔离当前 provider attempt 的正文并返回是否存在可见输出。"""
        if not event.replace_current_response:
            return False
        had_visible_output = False
        for segment in self.segments_by_key.values():
            if int(segment.get("presentation_epoch") or 1) == event.presentation_epoch:
                if (
                    not bool(segment.get("superseded"))
                    and bool(str(segment.get("text") or "").strip())
                ):
                    had_visible_output = True
                segment["superseded"] = True

        self.pending_output_segment_keys = [
            key
            for key in self.pending_output_segment_keys
            if not bool((self.segments_by_key.get(key) or {}).get("superseded"))
        ]
        self.current_segment_key = None
        self.pending_segment_sources = None
        return had_visible_output

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

        self.last_committed_epoch = max(
            int((self.segments_by_key.get(key) or {}).get("presentation_epoch") or 1)
            for key in keys
        )

        parts = [
            str((self.segments_by_key.get(key) or {}).get("text") or "")
            for key in keys
        ]
        return _join_text_segments(parts).strip()

    def iter_sources(self) -> typing.Iterable[typing.Any]:
        """按段落顺序迭代未被取代的来源记录。"""
        for key in self.segment_order:
            segment = self.segments_by_key.get(key) or {}
            if segment.get("superseded"):
                continue
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
            if not bool((self.segments_by_key.get(key) or {}).get("superseded"))
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
