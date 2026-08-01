# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import math
import typing
from dataclasses import (
    dataclass,
    field
)
from datetime import (
    datetime,
    timezone
)
from pathlib import Path
from engine.observability import observe_exception
from mind_app.paths import sessions_dir
from mind_nova import const
from .contracts import (
    TranscriptActor,
    TranscriptSink
)
from .ids import SID_RE


@dataclass(frozen=True, slots=True)
class TranscriptEntry:
    """描述会话记录中的单个结构化事件。"""
    timestamp: str
    event: str
    session_id: str
    turn_id: str | None
    actor: TranscriptActor | None
    payload: dict[str, typing.Any] = field(default_factory=dict)

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

class TranscriptWriter(TranscriptSink):
    """向单个会话文件追加结构化事件。"""

    def __init__(
        self,
        path: str | Path,
        *,
        session_id: str,
        turn_id: str | None = None
    ) -> None:
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
        self.root = Path(root or sessions_dir()).expanduser()

    def path_for_session(self, session_id: str) -> str:
        """返回会话固定使用的记录路径，失败时返回空路径。"""
        normalized = str(session_id or "").strip()

        try:
            created_at = _session_datetime(normalized)

            directory = self.root / created_at.strftime("%Y/%m/%d")
            directory.mkdir(parents=True, exist_ok=True)

            path = directory / f"session-{normalized}.jsonl"
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
