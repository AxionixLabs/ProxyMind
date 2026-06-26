# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import time
import typing
import sqlite3
from pathlib import Path
from urllib.parse import urlparse
from backend.utilities.storage.prefs import pref_path

TABLE_SERVICE_SETTINGS = r"service_settings"

DEFAULT_SCHEMA_VERSION = 1

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_SERVICE_SETTINGS} (
    setting_key TEXT PRIMARY KEY,
    value       TEXT NOT NULL DEFAULT '',
    updated_at  INTEGER NOT NULL
);
"""


def load_service_config() -> dict[str, typing.Any]:
    """从本地数据库读取服务配置。"""
    conn = _connect()
    try:
        with conn:
            _init_schema(conn)
            row = conn.execute(
                f"SELECT value FROM {TABLE_SERVICE_SETTINGS} WHERE setting_key = ?",
                ("domain",)
            ).fetchone()
    finally:
        conn.close()

    domain = "" if row is None else normalize_domain(row["value"])

    return {
        "schema_version" : DEFAULT_SCHEMA_VERSION,
        "domain"         : domain,
        "configured"     : bool(domain)
    }


def save_service_config(raw: typing.Any) -> dict[str, typing.Any]:
    """保存服务配置并返回规范化结果。"""
    config = normalize_service_config(raw)
    now    = _now_ms()
    conn   = _connect()

    try:
        with conn:
            _init_schema(conn)
            conn.execute(
                f"""
                INSERT INTO {TABLE_SERVICE_SETTINGS} (setting_key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    value      = excluded.value,
                    updated_at = excluded.updated_at
                """,
                ("domain", config["domain"], now)
            )
    finally:
        conn.close()

    return config


def normalize_service_config(raw: typing.Any) -> dict[str, typing.Any]:
    """将输入值转换为服务配置结构。"""
    domain: str = ""

    if isinstance(raw, dict):
        domain = normalize_domain(raw.get("domain"))

    return {
        "schema_version" : DEFAULT_SCHEMA_VERSION,
        "domain"         : domain,
        "configured"     : bool(domain)
    }


def normalize_domain(value: typing.Any) -> str:
    """规范化服务域名；空值或非法值返回空字符串。"""
    domain = str(value or "").strip().rstrip("/")
    if not domain:
        return ""

    parsed = urlparse(domain)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""

    return domain


def _service_config_path_candidates() -> list[Path]:
    """返回服务配置数据库路径候选。"""
    candidates = [pref_path()]

    seen: set[str]     = set()
    result: list[Path] = []

    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)

    return result


def _connect() -> sqlite3.Connection:
    """建立服务配置数据库连接。"""
    last_error: BaseException | None = None
    for target in _service_config_path_candidates():
        conn: sqlite3.Connection | None = None
        try:
            os.makedirs(target.parent, exist_ok=True)
            conn = sqlite3.connect(target)
            conn.row_factory = sqlite3.Row
            return conn
        except (OSError, sqlite3.Error) as exc:
            last_error = exc
            if conn is not None:
                conn.close()
            continue

    if last_error is not None:
        raise last_error
    raise sqlite3.OperationalError("no writable service config database candidates")


def _init_schema(conn: sqlite3.Connection) -> None:
    """初始化服务配置表结构。"""
    conn.executescript(SCHEMA_SQL)


def _now_ms() -> int:
    """返回当前时间戳，单位为毫秒。"""
    return int(time.time() * 1000)


if __name__ == '__main__':
    pass
