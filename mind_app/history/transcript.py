# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import math
import typing
from dataclasses import (
    dataclass,
    field,
    replace
)
from datetime import (
    datetime,
    timezone
)
from pathlib import Path
from mind_app.presentation.tool_policy import merges_tool_start_event
from observability import observe_exception
from infrastructure.config.runtime_paths import sessions_dir
from metadata import const
from agent.ports.transcript import (
    TranscriptActor,
    TranscriptSink
)
from protocol.schema.identifiers import SID_RE


@dataclass(frozen=True, slots=True)
class TranscriptEntry:
    """描述会话记录中的单个结构化事件。"""
    timestamp: str
    event: str
    session_id: str
    turn_id: str | None
    actor: TranscriptActor | None
    payload: dict[str, typing.Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: typing.Any) -> "TranscriptEntry":
        """把结构化对象解析为会话事件。"""
        if not isinstance(value, dict):
            raise ValueError("transcript entry must be an object")

        timestamp  = _required_text(value.get("timestamp"), "timestamp")
        event      = _required_text(value.get("event"), "event")
        session_id = _required_text(value.get("session_id"), "session_id")

        turn_value = value.get("turn_id")
        if turn_value is not None and not isinstance(turn_value, str):
            raise ValueError("transcript turn_id must be a string or null")

        turn_id = str(turn_value or "").strip() or None

        actor_value = value.get("actor")
        if actor_value is not None and (
            not isinstance(actor_value, str)
            or actor_value not in ("user", "assistant", "system", "tool")
        ):
            raise ValueError("transcript actor is invalid")

        payload = value.get("payload", {})
        if not isinstance(payload, dict):
            raise ValueError("transcript payload must be an object")

        return cls(
            timestamp=timestamp,
            event=event,
            session_id=session_id,
            turn_id=turn_id,
            actor=actor_value,
            payload=dict(payload),
        )

    def to_dict(self) -> dict[str, typing.Any]:
        """返回可逐行序列化的事件对象。"""
        return {
            "timestamp": self.timestamp,
            "event": self.event,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "actor": self.actor,
            "payload": _json_value(self.payload),
        }


class TranscriptReader(object):
    """从单个会话文件读取有效的结构化事件。"""

    def __init__(self, path: str | Path) -> None:
        """绑定可选的结构化会话记录路径。"""
        self.path = Path(path) if str(path or "").strip() else None

    def read(self) -> tuple[TranscriptEntry, ...]:
        """返回文件中的有效事件，损坏行不会中断后续读取。"""
        if self.path is None:
            return ()

        entries: list[TranscriptEntry] = []

        try:
            with self.path.open(
                "r",
                encoding=const.CHARSET,
                newline="",
            ) as file:
                for line_number, line in enumerate(file, start=1):
                    raw = line.strip()
                    if not raw:
                        continue

                    try:
                        entries.append(TranscriptEntry.from_dict(
                            json.loads(raw)
                        ))
                    except (
                        json.JSONDecodeError,
                        TypeError,
                        ValueError,
                        RecursionError,
                    ) as error:
                        observe_exception(
                            "transcript.read.line.failed",
                            error,
                            level="WARNING",
                            path=str(self.path),
                            line_number=line_number,
                        )
        except (OSError, UnicodeError) as error:
            observe_exception(
                "transcript.read.failed",
                error,
                level="WARNING",
                path=str(self.path),
            )

        return tuple(entries)

    def read_tail(self, limit: int) -> tuple[TranscriptEntry, ...]:
        """从文件尾部返回有限数量的有效事件。"""
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("transcript tail limit must be an integer")
        if limit <= 0 or self.path is None:
            return ()

        entries: list[TranscriptEntry] = []

        try:
            with self.path.open("rb") as file:
                file.seek(0, 2)
                remaining = file.tell()
                buffer = b""

                while remaining > 0:
                    chunk_size = min(8192, remaining)
                    remaining -= chunk_size
                    file.seek(remaining)
                    buffer = file.read(chunk_size) + buffer
                    if buffer.count(b"\n") > limit:
                        break

            lines = buffer.splitlines()
            if remaining > 0 and lines:
                lines = lines[1:]

            for tail_index, line in enumerate(lines[-limit:], start=1):
                raw = line.decode(const.CHARSET).strip()
                if not raw:
                    continue
                try:
                    entries.append(TranscriptEntry.from_dict(json.loads(raw)))
                except (
                    json.JSONDecodeError,
                    TypeError,
                    ValueError,
                    RecursionError,
                ) as error:
                    observe_exception(
                        "transcript.read.line.failed",
                        error,
                        level="WARNING",
                        path=str(self.path),
                        tail_index=tail_index,
                    )
        except (OSError, UnicodeError) as error:
            observe_exception(
                "transcript.read.failed",
                error,
                level="WARNING",
                path=str(self.path),
            )

        return tuple(entries)


class TranscriptReplay(object):
    """把持久事件归并为可恢复的消息和工具记录。"""

    def __init__(self, entries: typing.Iterable[TranscriptEntry]) -> None:
        """保存待归并的结构化会话事件。"""
        self.entries = tuple(entries)

    @staticmethod
    def _supersedes_assistant(
        marker: TranscriptEntry,
        item: TranscriptEntry
    ) -> bool:
        """判断替换标记是否覆盖指定助手消息。"""
        if item.actor != "assistant" or item.turn_id != marker.turn_id:
            return False

        scope = _payload_text(marker.payload, "scope")

        supersedes_item_id = _payload_text(marker.payload, "supersedes_item_id")
        if supersedes_item_id:
            return _payload_text(item.payload, "item_id") == supersedes_item_id

        marker_epoch = _payload_positive_int(marker.payload, "presentation_epoch")
        item_epoch   = _payload_positive_int(item.payload, "presentation_epoch")

        if marker_epoch is None or item_epoch is None:
            return False

        if scope == "presentation":
            return item_epoch <= marker_epoch
        if scope != "response" or item_epoch != marker_epoch:
            return False

        marker_round   = _payload_positive_int(marker.payload, "round")
        marker_attempt = _payload_positive_int(marker.payload, "attempt")
        item_round     = _payload_positive_int(item.payload, "round")
        item_attempt   = _payload_positive_int(item.payload, "attempt")

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
        user_by_turn: dict[str, int]  = {}
        last_user_index: int | None   = None
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
                            and _payload_text(replay[index].payload, "item_id") == item_id
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
                name    = _payload_text(entry.payload, "name")

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


class TranscriptWriter(TranscriptSink):
    """向单个会话文件追加结构化事件。"""

    def __init__(
        self,
        path: str | Path,
        *,
        session_id: str,
        turn_id: str | None = None
    ) -> None:
        """绑定记录路径及默认会话和轮次标识。"""
        self.path       = Path(path) if str(path or "").strip() else None
        self.session_id = str(session_id or "").strip()
        self.turn_id    = str(turn_id or "").strip() or None

        self._file: typing.TextIO | None = None

    def open(self) -> None:
        """打开追加写入文件，失败时禁用当前记录器。"""
        if self._file is not None or self.path is None:
            return None

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self.path.open(
                "a",
                encoding=const.CHARSET,
                buffering=1,
                newline="",
            )
        except OSError as error:
            self._file = None
            observe_exception(
                "transcript.open.failed",
                error,
                level="WARNING",
                path=str(self.path),
            )

    def append(
        self,
        event: str,
        *,
        actor: TranscriptActor | None = None,
        payload: dict[str, typing.Any] | None = None
    ) -> None:
        """追加一个完整事件并立即刷新到磁盘。"""
        if self._file is None:
            return None

        event_name = str(event or "").strip()

        try:
            entry = TranscriptEntry(
                timestamp=_timestamp(),
                event=event_name,
                session_id=self.session_id,
                turn_id=self.turn_id,
                actor=actor,
                payload=dict(payload or {}),
            )
            line = json.dumps(
                entry.to_dict(),
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            self._file.write(line + "\n")
            self._file.flush()
        except (OSError, TypeError, ValueError, RecursionError) as error:
            observe_exception(
                "transcript.write.failed",
                error,
                level="WARNING",
                path=str(self.path),
                transcript_event=event_name,
            )

    def close(self) -> None:
        """刷新并关闭会话记录文件。"""
        if self._file is None:
            return None
        try:
            try:
                self._file.flush()
            except OSError as error:
                observe_exception(
                    "transcript.close.failed",
                    error,
                    level="WARNING",
                    path=str(self.path),
                )
        finally:
            try:
                self._file.close()
            except OSError as error:
                observe_exception(
                    "transcript.close.failed",
                    error,
                    level="WARNING",
                    path=str(self.path),
                )
            self._file = None


class ConversationTranscriptStore:
    """按会话创建稳定的日期分层记录路径。"""

    def __init__(self, root: str | Path | None = None) -> None:
        """绑定会话记录的根目录。"""
        self.root = Path(root or sessions_dir()).expanduser()

    @staticmethod
    def writer(
        path: str | Path,
        *,
        session_id: str,
        turn_id: str | None = None
    ) -> TranscriptWriter:
        """创建绑定会话和轮次的追加记录器。"""
        return TranscriptWriter(
            path,
            session_id=session_id,
            turn_id=turn_id,
        )

    @staticmethod
    def reader(path: str | Path) -> TranscriptReader:
        """创建绑定单个会话文件的读取器。"""
        return TranscriptReader(path)

    def _session_path(self, session_id: str) -> Path:
        """返回会话标识对应的日期分层文件路径。"""
        created_at = _session_datetime(session_id)
        directory  = self.root / created_at.strftime("%Y/%m/%d")

        return directory / f"session-{session_id}.jsonl"

    def path_for_session(self, session_id: str) -> str:
        """返回会话固定使用的记录路径，失败时返回空路径。"""
        normalized = str(session_id or "").strip()

        try:
            path = self._session_path(normalized)

            directory = path.parent
            directory.mkdir(parents=True, exist_ok=True)

            path.touch(exist_ok=True)

            return str(path)

        except (OSError, ValueError) as error:
            observe_exception(
                "transcript.path.failed",
                error,
                level="WARNING",
                session_id=normalized,
            )
            return ""

    def existing_path_for_session(self, session_id: str) -> str:
        """返回已经存在的会话记录路径。"""
        normalized = str(session_id or "").strip()

        try:
            path = self._session_path(normalized)
            return str(path) if path.is_file() else ""
        except (OSError, ValueError) as error:
            observe_exception(
                "transcript.lookup.failed",
                error,
                level="WARNING",
                session_id=normalized,
            )
            return ""


def _required_text(value: typing.Any, field_name: str) -> str:
    """返回必填文本字段并拒绝空值。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"transcript {field_name} must be a non-empty string")
    return value.strip()


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


def _session_datetime(session_id: str) -> datetime:
    """从会话标识中的毫秒时间戳返回本地创建时间。"""
    matched = SID_RE.fullmatch(session_id)
    if matched is None:
        raise ValueError("session id is invalid")

    parts        = session_id.split("_")
    timestamp_ms = int(parts[2], 36)

    return datetime.fromtimestamp(timestamp_ms / 1000).astimezone()


def _timestamp() -> str:
    """返回毫秒精度的 UTC 时间文本。"""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _json_value(value: typing.Any) -> typing.Any:
    """递归转换为可稳定写入 JSON 的普通值。"""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, dict):
        return {
            str(key): _json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]

    return str(value)


if __name__ == '__main__':
    pass
