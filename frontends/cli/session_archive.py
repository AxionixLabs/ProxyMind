# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from agent.stores.sessions import (
    ConversationHistoryStore,
    HISTORY_LIMIT,
)
from infrastructure.errors import AppError
from infrastructure.config.runtime_paths import mind_history_db_path
from .commands import SessionArchiveCommand


def run_session_archive_command(command: SessionArchiveCommand) -> int:
    """执行本地会话归档或恢复命令。"""
    store = ConversationHistoryStore(mind_history_db_path())

    record = _find_target(
        store,
        command.target,
        status="active" if command.action == "archive" else "archived",
    )

    try:
        if command.action == "archive":
            updated = store.archive_session(
                cid=str(record["cid"]),
                sid=str(record["sid"]),
            )
            verb = "Archived"
        else:
            updated = store.unarchive_session(
                cid=str(record["cid"]),
                sid=str(record["sid"]),
            )
            verb = "Unarchived"
    except (LookupError, ValueError) as exc:
        raise AppError(str(exc)) from exc

    print(f"{verb} session {updated['sid']}.")
    return 0


def _find_target(
    store: ConversationHistoryStore,
    target: str,
    *,
    status: str
) -> dict[str, typing.Any]:
    """按会话 id 或全局历史标题解析归档目标。"""
    normalized_target = str(target or "").strip()
    if not normalized_target:
        raise AppError("A session id or title is required.")

    by_id = store.find_session(
        normalized_target,
        status=status,
    )
    if by_id is not None:
        return by_id

    matches = [
        record
        for record in store.list_sessions(
            status=status,
            limit=HISTORY_LIMIT,
        )
        if str(record.get("title") or "").strip() == normalized_target
    ]
    if not matches:
        raise AppError(
            f"No {status} session found matching '{normalized_target}'."
        )
    if len(matches) > 1:
        raise AppError(
            f"More than one {status} session is named "
            f"'{normalized_target}'; use its session id."
        )
    return matches[0]


if __name__ == '__main__':
    pass
