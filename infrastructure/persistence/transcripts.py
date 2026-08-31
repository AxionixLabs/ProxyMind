# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import typing
from datetime import (
    datetime,
    timezone
)
from pathlib import Path
from observability import observe_exception
from infrastructure.config.runtime_paths import sessions_dir
from metadata import const
from agent.ports.transcript import (
    TranscriptActor,
    TranscriptSink
)
from agent.stores.transcripts import (
    TranscriptEntry,
)
from protocol.schema.identifiers import SID_RE


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


if __name__ == '__main__':
    pass
