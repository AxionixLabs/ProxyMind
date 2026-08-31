# -*- coding: utf-8 -*-

import typing
from dataclasses import replace

from agent.domain.tool_policy import merges_tool_start_event
from .records import TranscriptEntry


class TranscriptReplay(object):
    """把持久事件归并为可恢复的消息和工具记录。"""

    def __init__(self, entries: typing.Iterable[TranscriptEntry]) -> None:
        """保存待归并的结构化会话事件。"""
        self.entries = tuple(entries)

    @staticmethod
    def _supersedes_assistant(
        marker: TranscriptEntry,
        item: TranscriptEntry,
    ) -> bool:
        """判断替换标记是否覆盖指定助手消息。"""
        if item.actor != "assistant" or item.turn_id != marker.turn_id:
            return False

        scope = _payload_text(marker.payload, "scope")

        supersedes_item_id = _payload_text(marker.payload, "supersedes_item_id")
        if supersedes_item_id:
            return _payload_text(item.payload, "item_id") == supersedes_item_id

        marker_epoch = _payload_positive_int(marker.payload, "presentation_epoch")
        item_epoch = _payload_positive_int(item.payload, "presentation_epoch")

        if marker_epoch is None or item_epoch is None:
            return False

        if scope == "presentation":
            return item_epoch <= marker_epoch
        if scope != "response" or item_epoch != marker_epoch:
            return False

        marker_round = _payload_positive_int(marker.payload, "round")
        marker_attempt = _payload_positive_int(marker.payload, "attempt")
        item_round = _payload_positive_int(item.payload, "round")
        item_attempt = _payload_positive_int(item.payload, "attempt")

        return (
            marker_round is not None
            and marker_attempt is not None
            and item_round == marker_round
            and item_attempt is not None
            and item_attempt < marker_attempt
        )

    def build(self) -> tuple[TranscriptEntry, ...]:
        """返回完成更新合并和工具调用配对后的事件。"""
        replay: list[TranscriptEntry] = []
        user_by_turn: dict[str, int] = {}
        last_user_index: int | None = None
        pending_tools: dict[str, int] = {}

        pending_unmerged_tools: dict[str, dict[str, typing.Any]] = {}

        for entry in self.entries:
            if entry.event == "message.superseded" and entry.actor == "assistant":
                replay = [
                    item
                    for item in replay
                    if not self._supersedes_assistant(entry, item)
                ]
                continue

            if entry.event == "message.created":
                content = entry.payload.get("content")
                if entry.actor not in {"user", "assistant"}:
                    continue
                if not isinstance(content, str) or not content:
                    continue

                replay.append(entry)
                if entry.actor == "user":
                    last_user_index = len(replay) - 1
                    if entry.turn_id:
                        user_by_turn[entry.turn_id] = last_user_index
                continue

            if entry.event == "message.updated" and entry.actor == "assistant":
                item_id = _payload_text(entry.payload, "item_id")
                content = entry.payload.get("content")
                if not item_id or not isinstance(content, str):
                    continue
                target = next(
                    (
                        index
                        for index in range(len(replay) - 1, -1, -1)
                        if (
                            replay[index].actor == "assistant"
                            and _payload_text(replay[index].payload, "item_id")
                            == item_id
                        )
                    ),
                    None,
                )
                if target is not None:
                    if content:
                        previous = replay[target]
                        replay[target] = replace(
                            previous,
                            payload={**previous.payload, **entry.payload},
                        )
                    else:
                        replay.pop(target)
                continue

            if entry.event == "message.updated" and entry.actor == "user":
                content = entry.payload.get("content")
                if not isinstance(content, str) or not content:
                    continue

                target = (
                    user_by_turn.get(entry.turn_id)
                    if entry.turn_id
                    else last_user_index
                )
                if target is not None:
                    previous = replay[target]
                    replay[target] = replace(
                        previous,
                        payload={**previous.payload, **entry.payload},
                    )
                continue

            if entry.event == "tool.started":
                if (
                    _payload_text(entry.payload, "name") == "apply_patch"
                    and not isinstance(entry.payload.get("patch_preview"), dict)
                ):
                    continue
                replay.append(entry)

                call_id = _payload_text(entry.payload, "call_id")
                name = _payload_text(entry.payload, "name")

                if call_id and merges_tool_start_event(name):
                    pending_tools[call_id] = len(replay) - 1
                elif call_id:
                    pending_unmerged_tools[call_id] = {
                        key: entry.payload[key]
                        for key in ("name", "arguments")
                        if key in entry.payload
                    }
                continue

            if entry.event not in {"tool.completed", "tool.failed"}:
                if entry.event in {
                    "context.compacted",
                    "context.compaction.failed",
                    "turn.failed",
                    "turn.incomplete",
                    "turn.interrupted",
                }:
                    replay.append(entry)
                continue

            call_id = _payload_text(entry.payload, "call_id")

            target = pending_tools.pop(call_id, None) if call_id else None
            if target is None:
                metadata = (
                    pending_unmerged_tools.pop(call_id, None)
                    if call_id
                    else None
                )
                if metadata:
                    replay.append(replace(
                        entry,
                        payload={**metadata, **entry.payload},
                    ))
                    continue
                replay.append(entry)
                continue

            started = replay[target]

            if (
                entry.event == "tool.failed"
                and _payload_text(started.payload, "name") == "apply_patch"
            ):
                replay.append(entry)
                continue

            replay[target] = replace(
                entry,
                turn_id=entry.turn_id or started.turn_id,
                payload={**started.payload, **entry.payload},
            )

        return tuple(replay)


def _payload_text(payload: dict[str, typing.Any], key: str) -> str:
    """返回事件载荷中的非空文本字段。"""
    value = payload.get(key)
    return str(value).strip() if isinstance(value, str) else ""


def _payload_positive_int(payload: dict[str, typing.Any], key: str) -> int | None:
    """返回事件载荷中的正整数字段。"""
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


if __name__ == '__main__':
    pass
