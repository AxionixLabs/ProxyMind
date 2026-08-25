# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_nova.stream_events import (
    TextDeltaEvent,
    TextDoneEvent,
    TextMetaEvent,
    PresentationSupersededEvent,
    StreamEvent,
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

        self.pending_output_by_item: dict[
            tuple[tuple[int, int, int], str], list[str]
        ] = {}

        self.pending_output_item_order: list[
            tuple[tuple[int, int, int], str]
        ] = []

        self.completed_item_ids: set[str]  = set()
        self.superseded_item_ids: set[str] = set()
        self.drained_item_ids: set[str]    = set()

        self.item_identities: dict[str, tuple[int, int, int]] = {}

        self.active_attempts: dict[tuple[int, int], int] = {}

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

    @staticmethod
    def _has_response_identity(
        segment: dict[str, typing.Any],
        identity: tuple[int, int, int]
    ) -> bool:
        """判断段落是否属于指定展示代次、模型 round 和 provider attempt。"""
        return (
            int(segment.get("presentation_epoch") or 1),
            int(segment.get("round") or 1),
            int(segment.get("attempt") or 1),
        ) == identity

    @staticmethod
    def _event_item_id(
        event: TextDeltaEvent | TextDoneEvent | TextMetaEvent
    ) -> str:
        """返回正文事件的稳定 item 身份。"""
        item_id = str(event.item_id or "").strip()
        if not item_id:
            raise ValueError(f"{event.type} segment_id is required")
        return item_id

    @staticmethod
    def _typed_sources_payload(
        *,
        sources: tuple[typing.Any, ...] | None,
        source_count: int | None
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

    def _latest_unbound_segment(
        self,
        identity: tuple[int, int, int]
    ) -> typing.Optional[dict[str, typing.Any]]:
        """返回同一 response 中最近创建且尚未绑定远端标识的段落。"""
        for key in reversed(self.segment_order):
            if (
                (segment := self.segments_by_key.get(key))
                and not segment.get("segment_id")
                and not bool(segment.get("superseded"))
                and self._has_response_identity(segment, identity)
            ):
                return segment
        return None

    def _create_segment(
        self,
        remote_segment_id: typing.Optional[typing.Any] = None,
        *,
        presentation_epoch: int = 1,
        round_no: int = 1,
        attempt: int = 1
    ) -> dict[str, typing.Any]:
        """创建属于指定 response 身份的本地正文段落。"""
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
            "round": max(1, int(round_no)),
            "attempt": max(1, int(attempt)),
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
        """把段落放入其 response identity 对应的待提交桶。"""
        local_id = segment.get("local_id")
        if not isinstance(local_id, str) or not local_id:
            return None

        identity = (
            int(segment.get("presentation_epoch") or 1),
            int(segment.get("round") or 1),
            int(segment.get("attempt") or 1),
        )

        item_id = str(segment.get("segment_id") or "").strip()
        if not item_id or item_id in self.superseded_item_ids:
            return None

        bucket = (identity, item_id)
        keys   = self.pending_output_by_item.setdefault(bucket, [])

        if bucket not in self.pending_output_item_order:
            self.pending_output_item_order.append(bucket)
        if local_id not in keys:
            keys.append(local_id)

    def _discard_superseded_pending_outputs(self) -> None:
        """移除已被 supersede 的待提交段落并清理空桶。"""
        active_order: list[tuple[tuple[int, int, int], str]] = []
        active_buckets: dict[tuple[tuple[int, int, int], str], list[str]] = {}
        for bucket in self.pending_output_item_order:
            keys = [
                key
                for key in self.pending_output_by_item.get(bucket, [])
                if not bool((self.segments_by_key.get(key) or {}).get("superseded"))
            ]
            if keys:
                active_order.append(bucket)
                active_buckets[bucket] = keys
        self.pending_output_item_order = active_order
        self.pending_output_by_item = active_buckets

    def _forget_pending_output_segment(self, local_id: str) -> None:
        """暂时移除一个 segment，等待其 item 边界完成后重新登记。"""
        for bucket in list(self.pending_output_item_order):
            keys = [
                key
                for key in self.pending_output_by_item.get(bucket, [])
                if key != local_id
            ]
            if keys:
                self.pending_output_by_item[bucket] = keys
            else:
                self.pending_output_by_item.pop(bucket, None)
                self.pending_output_item_order.remove(bucket)

    def _resolve_segment(
        self,
        remote_segment_id: typing.Optional[typing.Any] = None,
        *,
        create: bool = False,
        prefer_current: bool = False,
        presentation_epoch: int = 1,
        round_no: int = 1,
        attempt: int = 1
    ) -> typing.Optional[dict[str, typing.Any]]:
        """按远端标识和当前流式状态解析正文段落。"""
        remote_id = str(remote_segment_id or "").strip()
        identity  = (presentation_epoch, round_no, attempt)

        if remote_id and remote_id in self.segments_by_remote_id:
            segment = self.segments_by_key.get(self.segments_by_remote_id[remote_id])
            if segment is not None and bool(segment.get("superseded")):
                return None
            if segment is not None and not self._has_response_identity(segment, identity):
                raise ValueError("segment_id cannot cross response identity")
            return segment

        if remote_id and remote_id in self.superseded_item_ids:
            return None

        if prefer_current and self.current_segment_key:
            segment = self.segments_by_key.get(self.current_segment_key)
            if (
                segment
                and self._has_response_identity(segment, identity)
                and (
                    not remote_id
                    or not segment.get("segment_id")
                    or segment.get("segment_id") == remote_id
                )
            ):
                return self._bind_remote_segment(segment, remote_id)

        if remote_id and (segment := self._latest_unbound_segment(identity)):
            return self._bind_remote_segment(segment, remote_id)

        if self.current_segment_key and (segment := self.segments_by_key.get(self.current_segment_key)):
            if (
                self._has_response_identity(segment, identity)
                and (
                    not remote_id
                    or not segment.get("segment_id")
                    or segment.get("segment_id") == remote_id
                )
            ):
                return segment

        if create:
            return self._create_segment(
                remote_id,
                presentation_epoch=presentation_epoch,
                round_no=round_no,
                attempt=attempt,
            )

        return None

    def remember_current_output(self) -> None:
        """在旧 item 刷新后登记当前 item 的待提交正文。"""
        if self.current_segment_key:
            segment = self.segments_by_key.get(self.current_segment_key)
            if segment is not None:
                self._remember_output_segment(segment)

    def defer_current_output(self) -> None:
        """暂缓当前 item，供 runtime 先提交旧 item 边界。"""
        if self.current_segment_key:
            self._forget_pending_output_segment(self.current_segment_key)

    def on_text_delta(self, event: TextDeltaEvent) -> bool:
        """把正文增量追加到当前展示 epoch 的段落。"""
        item_id = self._event_item_id(event)
        presentation_epoch, round_no, attempt = self.response_identity(event)
        if item_id in self.superseded_item_ids:
            return False
        self._bind_item_identity(
            item_id,
            (presentation_epoch, round_no, attempt),
        )
        if not event.text:
            return False
        if item_id in self.completed_item_ids:
            return False

        previous_segment_key = self.current_segment_key

        segment = self._resolve_segment(
            item_id,
            create=True,
            prefer_current=True,
            presentation_epoch=presentation_epoch,
            round_no=round_no,
            attempt=attempt,
        )
        if segment is not None:
            segment["text"] += event.text
            self._remember_output_segment(segment)
        item_changed = bool(
            previous_segment_key
            and segment is not None
            and segment.get("local_id") != previous_segment_key
        )
        return item_changed

    def on_text_done(self, event: TextDoneEvent) -> str:
        """标记正文段落结束并断开当前流式段落。"""
        item_id = self._event_item_id(event)
        presentation_epoch, round_no, attempt = self.response_identity(event)
        if item_id in self.superseded_item_ids:
            self.current_segment_key = None
            return ""
        self._bind_item_identity(
            item_id,
            (presentation_epoch, round_no, attempt),
        )

        segment = self._resolve_segment(
            item_id,
            create=event.final_text is not None,
            prefer_current=True,
            presentation_epoch=presentation_epoch,
            round_no=round_no,
            attempt=attempt,
        )
        if segment:
            if event.final_text is not None:
                segment["text"] = event.final_text
            if (
                str(segment.get("text") or "").strip()
                and item_id not in self.drained_item_ids
            ):
                self._remember_output_segment(segment)
            else:
                self._forget_pending_output_segment(str(segment.get("local_id") or ""))
            segment["done"] = True
        self.completed_item_ids.add(item_id)
        self.current_segment_key = None
        return str((segment or {}).get("text") or "")

    def on_text_meta(self, event: TextMetaEvent) -> None:
        """应用或暂存正文段落的来源与标注元数据。"""
        item_id = self._event_item_id(event)
        presentation_epoch, round_no, attempt = self.response_identity(event)
        if item_id in self.superseded_item_ids:
            return None
        self._bind_item_identity(
            item_id,
            (presentation_epoch, round_no, attempt),
        )

        payload = self._typed_segment_meta_payload(event)

        if segment := self._resolve_segment(
            item_id,
            prefer_current=True,
            presentation_epoch=presentation_epoch,
            round_no=round_no,
            attempt=attempt,
        ):
            self._merge_segment_meta(segment, payload)
            return None

        self.pending_meta_by_segment_id[item_id] = payload

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
                item_id = str(segment.get("segment_id") or "").strip()
                if item_id:
                    self.superseded_item_ids.add(item_id)

        self.active_attempts = {
            key: attempt
            for key, attempt in self.active_attempts.items()
            if key[0] > event.superseded_epoch
        }

        self._discard_superseded_pending_outputs()

        self.current_segment_key     = None
        self.pending_segment_sources = None

    def on_turn_retrying(self, event: TurnRetryingEvent) -> bool:
        """隔离当前 provider attempt 的正文并返回是否存在可见输出。"""
        if event.round is None:
            raise ValueError("turn.retrying round is required")

        response_key     = (event.presentation_epoch, event.round)
        previous_attempt = self.active_attempts.get(response_key, 1)

        if event.attempt <= previous_attempt:
            raise ValueError("turn.retrying attempt must increase within the response")
        self.active_attempts[response_key] = event.attempt

        if event.supersedes_item_id:
            self.superseded_item_ids.add(event.supersedes_item_id)

        had_visible_output: bool = False

        for segment in self.segments_by_key.values():
            matches_named_item = (
                bool(event.supersedes_item_id)
                and str(segment.get("segment_id") or "")
                == event.supersedes_item_id
            )
            if event.supersedes_item_id and not matches_named_item:
                continue
            is_older_attempt = (
                int(segment.get("presentation_epoch") or 1) == event.presentation_epoch
                and int(segment.get("round") or 1) == event.round
                and int(segment.get("attempt") or 1) < event.attempt
            )
            if matches_named_item or is_older_attempt:
                if (
                    not bool(segment.get("superseded"))
                    and bool(str(segment.get("text") or "").strip())
                ):
                    had_visible_output = True
                segment["superseded"] = True
                item_id = str(segment.get("segment_id") or "").strip()
                if item_id:
                    self.superseded_item_ids.add(item_id)

        self._discard_superseded_pending_outputs()

        self.current_segment_key     = None
        self.pending_segment_sources = None

        return had_visible_output

    def was_output_drained(self, item_id: str) -> bool:
        """判断指定 assistant item 是否已经写入 transcript。"""
        return str(item_id or "").strip() in self.drained_item_ids

    def should_ignore_item(
        self,
        item_id: str,
        *,
        identity: tuple[int, int, int] | None = None,
    ) -> bool:
        """判断正文事件是否属于已完成或已取代的 item。"""
        normalized = str(item_id or "").strip()
        if normalized in self.superseded_item_ids:
            return True
        if identity is not None:
            self._bind_item_identity(normalized, identity)
        return (
            normalized in self.completed_item_ids
        )

    def _bind_item_identity(
        self,
        item_id: str,
        identity: tuple[int, int, int],
    ) -> None:
        """绑定 item 到首次观察到的 response identity。"""
        previous = self.item_identities.get(item_id)
        if previous is not None and previous != identity:
            raise ValueError("item_id cannot cross response identity")
        self.item_identities[item_id] = identity

    def drain_assistant_outputs(
        self,
        *,
        complete_only: bool = False,
    ) -> list[tuple[tuple[int, int, int], str, str]]:
        """按 item 身份原子取出待提交 assistant 输出。"""
        outputs: list[tuple[tuple[int, int, int], str, str]] = []
        drained: set[tuple[tuple[int, int, int], str]] = set()
        for bucket in self.pending_output_item_order:
            identity, item_id = bucket
            keys = [
                key
                for key in self.pending_output_by_item.get(bucket, [])
                if str((self.segments_by_key.get(key) or {}).get("text") or "").strip()
                and not bool((self.segments_by_key.get(key) or {}).get("superseded"))
            ]
            if not keys:
                continue
            if complete_only and not all(
                bool((self.segments_by_key.get(key) or {}).get("done"))
                for key in keys
            ):
                continue
            parts = [
                str((self.segments_by_key.get(key) or {}).get("text") or "")
                for key in keys
            ]
            outputs.append((identity, item_id, _join_text_segments(parts).strip()))
            drained.add(bucket)
            self.drained_item_ids.add(item_id)

        self.pending_output_item_order = [
            bucket
            for bucket in self.pending_output_item_order
            if bucket not in drained
        ]
        self.pending_output_by_item = {
            bucket: keys
            for bucket, keys in self.pending_output_by_item.items()
            if bucket not in drained
        }
        if not complete_only:
            self.current_segment_key = None

        return outputs

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

    def response_identity(
        self,
        event: StreamEvent
    ) -> tuple[int, int, int]:
        """返回事件当前所属的 response 身份。"""
        presentation_epoch = max(1, int(event.presentation_epoch))

        round_no = max(1, int(event.round or 1))
        attempt = self.active_attempts.get((presentation_epoch, round_no), 1)

        return presentation_epoch, round_no, attempt


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
