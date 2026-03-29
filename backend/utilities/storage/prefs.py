# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import sys
import time
import typing
import sqlite3
from pathlib import Path
from backend.utilities import const

Slot = dict[str, typing.Any]

APP_ENTRY_NAMES    = {const.APP_NAME, f"{const.APP_NAME}.exe"}
SCRIPT_ENTRY_NAMES = {f"{const.APP_NAME}.py"}
PROFILE_TITLE      = f"Default"
SLOT_KEYS          = ("primary", "secondary")
REQUIRED_SLOT_KEYS = ("primary",)
APP_DATA_DIR_NAME  = f"{const.APP_DESC}"

TABLE_PROFILES  = "pref_profiles"
TABLE_SLOTS     = "pref_model_slots"
SLOT_COLUMN_MAP = {
    "api"      : "provider",
    "base_url" : "base_url",
    "route"    : "route",
    "apikey"   : "api_key",
    "model"    : "model_name",
    "type"     : "model_type",
    "notes"    : "notes",
}

DATA_STORAGE_DIR = f"storage"
DATA_FILENAME    = f"{const.APP_NAME}.db"

DEFAULT_SCHEMA_VERSION = 2
DEFAULT_PROFILE_KEY    = "default"
DEFAULT_PROVIDER       = "OpenAI"
DEFAULT_ROUTE          = "responses"
ALLOWED_ROUTES         = {"responses", "chat_completions"}
DEFAULT_MODEL_TYPE     = "Text"


def _default_slot() -> Slot:
    """返回单个模型槽位的默认配置。"""
    return {
        "api"      : DEFAULT_PROVIDER,
        "base_url" : "",
        "route"    : DEFAULT_ROUTE,
        "apikey"   : "",
        "model"    : "",
        "type"     : DEFAULT_MODEL_TYPE,
        "notes"    : "",
    }


def _default_prefs() -> dict[str, typing.Any]:
    """返回整份偏好配置的默认结构。"""
    return {
        "schema_version" : DEFAULT_SCHEMA_VERSION,
        "profile_key"    : DEFAULT_PROFILE_KEY,
        "primary"        : _default_slot(),
        "secondary"      : None,
    }


def _normalize_route(value: typing.Any, *, allow_chat_completions: bool = False) -> str:
    """把路由值收敛到受支持的接口类型。"""
    route = str(value or "").strip()
    if route == DEFAULT_ROUTE:
        return route

    if allow_chat_completions and route in ALLOWED_ROUTES:
        return route

    return DEFAULT_ROUTE


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pref_profiles (
    profile_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_key  TEXT NOT NULL UNIQUE,
    title        TEXT NOT NULL DEFAULT '',
    is_default   INTEGER NOT NULL DEFAULT 0,
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS pref_model_slots (
    slot_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id    INTEGER NOT NULL,
    slot_key      TEXT NOT NULL,
    provider      TEXT NOT NULL DEFAULT '',
    base_url      TEXT NOT NULL DEFAULT '',
    route         TEXT NOT NULL DEFAULT 'responses',
    api_key       TEXT NOT NULL DEFAULT '',
    model_name    TEXT NOT NULL DEFAULT '',
    model_type    TEXT NOT NULL DEFAULT 'Text',
    notes         TEXT NOT NULL DEFAULT '',
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL,
    UNIQUE(profile_id, slot_key),
    FOREIGN KEY(profile_id) REFERENCES pref_profiles(profile_id) ON DELETE CASCADE
);
"""

SCHEMA_SQL = SCHEMA_SQL.replace("pref_profiles", TABLE_PROFILES).replace("pref_model_slots", TABLE_SLOTS)


def _app_root() -> Path:
    """推断当前应用入口所在根目录。"""
    software = Path(sys.argv[0]).name.strip().lower()

    if software in APP_ENTRY_NAMES:
        return Path(sys.argv[0]).resolve().parent

    if software in SCRIPT_ENTRY_NAMES:
        return Path(__file__).resolve().parents[3]

    return Path.cwd()


def _data_root() -> Path:
    """根据当前平台推断偏好数据目录。"""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DATA_DIR_NAME

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_DATA_DIR_NAME

    return _app_root() / APP_DATA_DIR_NAME


def pref_path() -> Path:
    """返回偏好数据库文件的标准路径。"""
    return _data_root() / DATA_STORAGE_DIR / DATA_FILENAME


def _connect() -> sqlite3.Connection:
    """建立并返回偏好数据库连接。"""
    target = pref_path()
    os.makedirs(target.parent, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    """初始化偏好数据库结构，并补齐增量字段。"""
    conn.executescript(SCHEMA_SQL)
    _ensure_slot_columns(conn)


def _ensure_slot_columns(conn: sqlite3.Connection) -> None:
    """确保模型槽位表中存在当前版本需要的列。"""
    columns = {
        str(row["name"]) for row in conn.execute(
            f"PRAGMA table_info({TABLE_SLOTS})"
        ).fetchall()
    }

    if "route" not in columns:
        conn.execute(
            f"ALTER TABLE {TABLE_SLOTS} ADD COLUMN route TEXT NOT NULL DEFAULT '{DEFAULT_ROUTE}'"
        )


def _now_ms() -> int:
    """返回当前时间的毫秒时间戳。"""
    return int(time.time() * 1000)


def _ensure_profile(conn: sqlite3.Connection, profile_key: str = DEFAULT_PROFILE_KEY) -> int:
    """确保默认配置档和必需槽位存在，并返回 profile_id。"""
    now = _now_ms()
    conn.execute(
        f"""
        INSERT INTO {TABLE_PROFILES} (profile_key, title, is_default, created_at, updated_at)
        VALUES (?, ?, 1, ?, ?)
        ON CONFLICT(profile_key) DO UPDATE SET
            updated_at = excluded.updated_at,
            is_default = 1
        """,
        (profile_key, PROFILE_TITLE, now, now)
    )

    row = conn.execute(
        f"SELECT profile_id FROM {TABLE_PROFILES} WHERE profile_key = ?",
        (profile_key,)
    ).fetchone()
    if row is None:
        raise RuntimeError("failed to initialize pref profile")

    profile_id = int(row["profile_id"])
    for slot_key in REQUIRED_SLOT_KEYS:
        conn.execute(
            f"""
            INSERT INTO {TABLE_SLOTS} (
                profile_id, slot_key, provider, base_url, route, api_key, model_name,
                model_type, notes, created_at, updated_at
            )
            VALUES (?, ?, ?, '', ?, '', '', ?, '', ?, ?)
            ON CONFLICT(profile_id, slot_key) DO NOTHING
            """,
            (profile_id, slot_key, DEFAULT_PROVIDER, DEFAULT_ROUTE, DEFAULT_MODEL_TYPE, now, now)
        )

    return profile_id


def _slot_record(slot: Slot) -> tuple[str, str, str, str, str, str, str]:
    """把槽位字典转换成数据库写入元组。"""
    return (
        slot["api"],
        slot["base_url"],
        slot["route"],
        slot["apikey"],
        slot["model"],
        slot["type"],
        slot["notes"],
    )


def _row_to_slot(row: sqlite3.Row | None) -> Slot:
    """把数据库行对象转换成标准槽位字典。"""
    slot = _default_slot()
    if row is None:
        return slot

    for slot_key, column_name in SLOT_COLUMN_MAP.items():
        fallback = slot.get(slot_key, "")
        slot[slot_key] = str(row[column_name] or fallback)

    allow_chat_completions = str(row["slot_key"] or "") == "secondary"
    slot["route"] = _normalize_route(slot.get("route"), allow_chat_completions=allow_chat_completions)
    return slot


def _merge_slot(base: Slot, incoming: typing.Any, *, allow_chat_completions: bool = False) -> Slot:
    """把传入槽位配置合并到基准槽位上。"""
    merged = dict(base)
    if not isinstance(incoming, dict):
        return merged

    for key in merged:
        if key in incoming and incoming[key] is not None:
            merged[key] = (
                _normalize_route(incoming[key], allow_chat_completions=allow_chat_completions)
                if key == "route" else str(incoming[key])
            )

    return merged


def _is_slot_configured(slot: typing.Any) -> bool:
    """判断槽位是否已经具备可视为已配置的关键信息。"""
    if not isinstance(slot, dict):
        return False

    meaningful_keys = ("base_url", "apikey", "model", "notes")
    return any(str(slot.get(key, "")).strip() for key in meaningful_keys)


def normalize_pref(raw: typing.Any) -> dict[str, typing.Any]:
    """把任意输入归一化为标准偏好配置结构。"""
    prefs = _default_prefs()
    if not isinstance(raw, dict):
        return prefs

    prefs["primary"] = _merge_slot(prefs["primary"], raw.get("primary"))
    prefs["primary"]["type"] = prefs["primary"]["type"] or DEFAULT_MODEL_TYPE
    prefs["primary"]["route"] = _normalize_route(prefs["primary"].get("route"))

    secondary = _merge_slot(_default_slot(), raw.get("secondary"), allow_chat_completions=True)
    secondary["type"] = secondary["type"] or DEFAULT_MODEL_TYPE
    secondary["route"] = _normalize_route(secondary.get("route"), allow_chat_completions=True)
    prefs["secondary"] = secondary if _is_slot_configured(secondary) else None

    return prefs


def load_pref() -> dict[str, typing.Any]:
    """从数据库加载当前偏好配置。"""
    with _connect() as conn:
        _init_schema(conn)
        profile_id = _ensure_profile(conn)

        rows = conn.execute(
            f"""
            SELECT slot_key, provider, base_url, api_key, model_name, model_type, notes
                 , route
            FROM {TABLE_SLOTS}
            WHERE profile_id = ?
            """,
            (profile_id,)
        ).fetchall()

    slots = {str(row["slot_key"]): _row_to_slot(row) for row in rows}
    prefs = _default_prefs()
    prefs["primary"] = slots.get("primary", _default_slot())
    secondary = slots.get("secondary")
    prefs["secondary"] = secondary if _is_slot_configured(secondary) else None
    return prefs


def save_pref(raw: typing.Any) -> dict[str, typing.Any]:
    """把偏好配置归一化后写入数据库并返回最终结果。"""
    prefs = normalize_pref(raw)
    now = _now_ms()

    with _connect() as conn:
        _init_schema(conn)
        profile_id = _ensure_profile(conn, str(prefs.get("profile_key") or DEFAULT_PROFILE_KEY))

        for slot_key in REQUIRED_SLOT_KEYS:
            slot = prefs[slot_key]
            provider, base_url, route, api_key, model_name, model_type, notes = _slot_record(slot)
            conn.execute(
                f"""
                INSERT INTO {TABLE_SLOTS} (
                    profile_id, slot_key, provider, base_url, route, api_key, model_name,
                    model_type, notes, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, slot_key) DO UPDATE SET
                    provider   = excluded.provider,
                    base_url   = excluded.base_url,
                    route      = excluded.route,
                    api_key    = excluded.api_key,
                    model_name = excluded.model_name,
                    model_type = excluded.model_type,
                    notes      = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (
                    profile_id, slot_key, provider, base_url, route, api_key, model_name,
                    model_type, notes, now, now
                )
            )

        secondary = prefs.get("secondary")
        if _is_slot_configured(secondary):
            provider, base_url, route, api_key, model_name, model_type, notes = _slot_record(secondary)
            conn.execute(
                f"""
                INSERT INTO {TABLE_SLOTS} (
                    profile_id, slot_key, provider, base_url, route, api_key, model_name,
                    model_type, notes, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, slot_key) DO UPDATE SET
                    provider   = excluded.provider,
                    base_url   = excluded.base_url,
                    route      = excluded.route,
                    api_key    = excluded.api_key,
                    model_name = excluded.model_name,
                    model_type = excluded.model_type,
                    notes      = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (
                    profile_id, "secondary", provider, base_url, route, api_key, model_name,
                    model_type, notes, now, now
                )
            )
        else:
            conn.execute(
                f"DELETE FROM {TABLE_SLOTS} WHERE profile_id = ? AND slot_key = ?",
                (profile_id, "secondary")
            )

    return prefs


if __name__ == '__main__':
    pass
