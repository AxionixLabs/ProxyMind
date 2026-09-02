# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sqlite3
import typing
from collections.abc import (
    Callable,
    Collection,
)
from pathlib import Path

from agent.domain.transcripts import TranscriptEntry
from agent.stores.sessions import (
    HISTORY_LIMIT,
    ConversationHistoryStore,
)
from observability import observe_exception
from protocol.schema.identifiers import (
    short_uid,
    valid_session_ids,
)

__all__ = (
    "ConversationForkPersistenceError",
    "LocalConversationHistory",
)


TranscriptExistingPathProvider: typing.TypeAlias = Callable[[str], str]
TranscriptEntriesReader: typing.TypeAlias = Callable[
    [str],
    tuple[TranscriptEntry, ...],
]


class ConversationForkPersistenceError(RuntimeError):
    """表示本地分支幂等请求无法持久化。"""


class LocalConversationHistory:
    """持久化本地会话游标、分支请求并读取历史 Transcript。"""

    def __init__(
        self,
        store: ConversationHistoryStore,
        *,
        existing_transcript_path_for: TranscriptExistingPathProvider,
        transcript_entries_for: TranscriptEntriesReader,
    ) -> None:
        self._store = store
        self._existing_transcript_path_for = existing_transcript_path_for
        self._transcript_entries_for = transcript_entries_for

    @property
    def ttl_ms(self) -> int:
        """返回历史游标的保留时间。"""
        return self._store.ttl_ms

    @property
    def max_items(self) -> int:
        """返回历史游标的容量上限。"""
        return self._store.max_items

    def touch(
        self,
        metadata: dict[str, str],
        *,
        workspace: str,
        title: str = "",
        source: str,
    ) -> None:
        """把会话坐标写入本地 history。"""
        try:
            self._store.touch_session(
                cid=metadata["cid"],
                sid=metadata["sid"],
                title=title,
                workspace=workspace,
                source=source,
                branch=metadata.get("branch", ""),
                status=metadata.get("status", "active"),
            )
        except (OSError, sqlite3.Error, ValueError, KeyError) as error:
            observe_exception(
                "history.write.failed",
                error,
                level="WARNING",
                source=source,
            )

    def recent(
        self,
        *,
        workspace: str | Path | None = None,
        sources: Collection[str] | None = None,
        status: str | None = None,
        limit: int = HISTORY_LIMIT,
    ) -> list[dict[str, typing.Any]]:
        """返回可恢复的本地会话游标。"""
        try:
            records = self._store.list_sessions(
                workspace=workspace,
                sources=sources,
                status=status,
                limit=limit,
            )
        except (OSError, sqlite3.Error, ValueError) as error:
            observe_exception("history.list.failed", error, level="WARNING")
            return []
        return [
            record
            for record in records
            if valid_session_ids(record.get("cid"), record.get("sid"))
        ]

    def find(
        self,
        session_id: str,
        *,
        workspace: str | Path | None = None,
        sources: Collection[str] | None = None,
        status: str | None = None,
    ) -> dict[str, typing.Any] | None:
        """按会话标识返回可恢复的本地会话游标。"""
        try:
            record = self._store.find_session(
                session_id,
                workspace=workspace,
                sources=sources,
                status=status,
            )
        except (OSError, sqlite3.Error, ValueError) as error:
            observe_exception("history.find.failed", error, level="WARNING")
            return None
        if record is None or not valid_session_ids(
            record.get("cid"),
            record.get("sid"),
        ):
            return None
        return record

    def read_transcript(self, session_id: str) -> tuple[TranscriptEntry, ...]:
        """读取指定会话已经持久化的结构化事件。"""
        path = self._existing_transcript_path_for(session_id)
        return self._transcript_entries_for(path) if path else ()

    def prepare_fork(
        self,
        cid: str,
        sid: str,
        before_turn_id: str = "",
    ) -> str:
        """持久化并返回当前源会话的稳定分支请求标识。"""
        candidate = f"fork_{short_uid(20)}"
        try:
            return self._store.get_or_create_fork_request(
                cid=cid,
                sid=sid,
                request_id=candidate,
                before_turn_id=before_turn_id,
            )
        except (OSError, sqlite3.Error, ValueError) as error:
            observe_exception("conversation.fork.prepare_failed", error)
            raise ConversationForkPersistenceError(
                "Unable to persist the conversation fork request."
            ) from error

    def clear_fork(
        self,
        cid: str,
        sid: str,
        request_id: str,
        before_turn_id: str = "",
    ) -> None:
        """清除已完成或不可重试的本地分支请求。"""
        try:
            self._store.clear_fork_request(
                cid=cid,
                sid=sid,
                request_id=request_id,
                before_turn_id=before_turn_id,
            )
        except (OSError, sqlite3.Error, ValueError) as error:
            observe_exception(
                "conversation.fork.clear_failed",
                error,
                level="WARNING",
            )

    def archive(self, *, cid: str, sid: str) -> dict[str, typing.Any]:
        """把指定会话迁移到 archived 集合。"""
        return self._store.archive_session(cid=cid, sid=sid)

    def unarchive(self, *, cid: str, sid: str) -> dict[str, typing.Any]:
        """把指定 archived 会话迁移回 active 集合。"""
        return self._store.unarchive_session(cid=cid, sid=sid)


if __name__ == '__main__':
    pass
