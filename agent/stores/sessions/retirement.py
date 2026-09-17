# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import sqlite3

from agent.ports.session_deletion import LocalDeletionTarget


RETIREMENT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS retired_session_coordinates (
    cid TEXT NOT NULL, sid TEXT NOT NULL, PRIMARY KEY (cid, sid)
);
CREATE TABLE IF NOT EXISTS retired_session_keys (
    kind TEXT NOT NULL, identity TEXT NOT NULL, PRIMARY KEY (kind, identity)
);
"""


def coordinate_guard_sql(table: str) -> str:
    """为持有线上坐标的表生成数据库级停写约束。"""
    return _guard_sql(table, "EXISTS (SELECT 1 FROM retired_session_coordinates "
                      "WHERE cid = NEW.cid AND sid = NEW.sid)", "coordinates")


def key_guard_sql(table: str, column: str, kind: str) -> str:
    """为持有本地身份的表生成数据库级停写约束。"""
    for identifier in (column, kind):
        _validate_identifier(identifier)
    return _guard_sql(table, "EXISTS (SELECT 1 FROM retired_session_keys "
                      f"WHERE kind = '{kind}' AND identity = NEW.{column})", kind)


def _guard_sql(table: str, predicate: str, guard: str) -> str:
    """使用内部固定表名生成插入与更新触发器，冲突忽略也不能绕过停写。"""
    _validate_identifier(table)
    return RETIREMENT_SCHEMA_SQL + "\n".join(
        f"CREATE TRIGGER IF NOT EXISTS retire_{table}_{guard}_{operation.lower()} "
        f"BEFORE {operation} ON {table} WHEN {predicate} "
        "BEGIN SELECT RAISE(ABORT, 'session has been deleted'); END;"
        for operation in ("INSERT", "UPDATE")
    )


def _validate_identifier(value: str) -> None:
    """拒绝非内部 SQL 标识符。"""
    if re.fullmatch(r"[a-z_]+", value) is None:
        raise ValueError("invalid retirement schema identifier")


def retire_coordinates(
    connection: sqlite3.Connection, targets: tuple[LocalDeletionTarget, ...],
) -> None:
    """在调用方事务内持久保存线上坐标，标记不参与历史 TTL 裁剪。"""
    connection.executemany(
        "INSERT OR IGNORE INTO retired_session_coordinates (cid, sid) VALUES (?, ?)",
        ((target.cid, target.sid) for target in targets),
    )


def retire_keys(connection: sqlite3.Connection, kind: str, identities: tuple[str, ...]) -> None:
    """在调用方事务内保存最小身份标记，完整状态目录显式重置前不回收。"""
    connection.executemany(
        "INSERT OR IGNORE INTO retired_session_keys (kind, identity) VALUES (?, ?)",
        ((kind, identity) for identity in identities),
    )


if __name__ == '__main__':
    pass
