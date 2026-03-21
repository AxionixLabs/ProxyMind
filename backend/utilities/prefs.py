#  ____            __
# |  _ \ _ __ ___ / _|___
# | |_) | '__/ _ \ |_/ __|
# |  __/| | |  __/  _\__ \
# |_|   |_|  \___|_| |___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import sys
import time
import typing
import sqlite3
from pathlib import Path

Slot = dict[str, typing.Any]

# ========【入口与命名】========
APP_ENTRY_NAMES    = {"helix", "helix.exe"}
SCRIPT_ENTRY_NAMES = {"helix.py"}
PROFILE_TITLE      = "Default"
SLOT_KEYS          = ("primary", "secondary")

# ========【表与字段】========
TABLE_PROFILES  = "pref_profiles"
TABLE_SLOTS     = "pref_model_slots"
SLOT_COLUMN_MAP = {
    "api"      : "provider",
    "base_url" : "base_url",
    "apikey"   : "api_key",
    "model"    : "model_name",
    "type"     : "model_type",
    "input"    : "input_type",
    "notes"    : "notes",
}

# ========【本地数据目录】========
DATA_ROOT_DIR = f"data"
DATA_FILENAME = "helix.db"

# ========【默认偏好元数据】========
DEFAULT_SCHEMA_VERSION = 2
DEFAULT_PROFILE_KEY    = "default"
DEFAULT_PROVIDER       = "OpenAI"
DEFAULT_MODEL_TYPE     = "Text"
DEFAULT_INPUT_TYPE     = "Text"


def _default_slot() -> Slot:
    return {
        "api"      : DEFAULT_PROVIDER,
        "base_url" : "",
        "apikey"   : "",
        "model"    : "",
        "type"     : DEFAULT_MODEL_TYPE,
        "input"    : DEFAULT_INPUT_TYPE,
        "notes"    : "",
    }


def _default_prefs() -> dict[str, typing.Any]:
    return {
        "schema_version" : DEFAULT_SCHEMA_VERSION,
        "profile_key"    : DEFAULT_PROFILE_KEY,
        "primary"        : _default_slot(),
        "secondary"      : _default_slot(),
    }

# ========【SQLite Schema】========
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
    api_key       TEXT NOT NULL DEFAULT '',
    model_name    TEXT NOT NULL DEFAULT '',
    model_type    TEXT NOT NULL DEFAULT 'Text',
    input_type    TEXT NOT NULL DEFAULT 'Text',
    notes         TEXT NOT NULL DEFAULT '',
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL,
    UNIQUE(profile_id, slot_key),
    FOREIGN KEY(profile_id) REFERENCES pref_profiles(profile_id) ON DELETE CASCADE
);
"""

SCHEMA_SQL = SCHEMA_SQL.replace("pref_profiles", TABLE_PROFILES).replace("pref_model_slots", TABLE_SLOTS)


def _app_root() -> Path:
    software = Path(sys.argv[0]).name.strip().lower()

    if software in APP_ENTRY_NAMES:
        return Path(sys.argv[0]).resolve().parent.parent

    if software in SCRIPT_ENTRY_NAMES:
        return Path(__file__).resolve().parents[2]

    return Path.cwd()


def pref_path() -> Path:
    root = _app_root()
    return root / DATA_ROOT_DIR / DATA_FILENAME


def _connect() -> sqlite3.Connection:
    target = pref_path()
    os.makedirs(target.parent, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _ensure_profile(conn: sqlite3.Connection, profile_key: str = DEFAULT_PROFILE_KEY) -> int:
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
    for slot_key in SLOT_KEYS:
        conn.execute(
            f"""
            INSERT INTO {TABLE_SLOTS} (
                profile_id, slot_key, provider, base_url, api_key, model_name,
                model_type, input_type, notes, created_at, updated_at
            )
            VALUES (?, ?, ?, '', '', '', ?, ?, '', ?, ?)
            ON CONFLICT(profile_id, slot_key) DO NOTHING
            """,
            (profile_id, slot_key, DEFAULT_PROVIDER, DEFAULT_MODEL_TYPE, DEFAULT_INPUT_TYPE, now, now)
        )

    return profile_id


def _slot_record(slot: Slot) -> tuple[str, str, str, str, str, str, str]:
    return (
        slot["api"],
        slot["base_url"],
        slot["apikey"],
        slot["model"],
        slot["type"],
        slot["input"],
        slot["notes"],
    )


def _row_to_slot(row: sqlite3.Row | None) -> Slot:
    slot = _default_slot()
    if row is None:
        return slot

    for slot_key, column_name in SLOT_COLUMN_MAP.items():
        fallback = slot.get(slot_key, "")
        slot[slot_key] = str(row[column_name] or fallback)
    return slot


def _merge_slot(base: Slot, incoming: typing.Any) -> Slot:
    merged = dict(base)
    if not isinstance(incoming, dict):
        return merged

    for key in merged:
        if key in incoming and incoming[key] is not None:
            merged[key] = str(incoming[key])

    return merged


def normalize_pref(raw: typing.Any) -> dict[str, typing.Any]:
    prefs = _default_prefs()
    if not isinstance(raw, dict):
        return prefs

    prefs["primary"] = _merge_slot(prefs["primary"], raw.get("primary"))
    prefs["secondary"] = _merge_slot(prefs["secondary"], raw.get("secondary"))

    prefs["primary"]["type"] = prefs["primary"]["type"] or DEFAULT_MODEL_TYPE
    prefs["primary"]["input"] = prefs["primary"]["input"] or DEFAULT_INPUT_TYPE
    prefs["secondary"]["type"] = prefs["secondary"]["type"] or DEFAULT_MODEL_TYPE
    prefs["secondary"]["input"] = prefs["secondary"]["input"] or DEFAULT_INPUT_TYPE

    return prefs


def load_pref() -> dict[str, typing.Any]:
    with _connect() as conn:
        _init_schema(conn)
        profile_id = _ensure_profile(conn)

        rows = conn.execute(
            f"""
            SELECT slot_key, provider, base_url, api_key, model_name, model_type, input_type, notes
            FROM {TABLE_SLOTS}
            WHERE profile_id = ?
            """,
            (profile_id,)
        ).fetchall()

    slots = {str(row["slot_key"]): _row_to_slot(row) for row in rows}
    prefs = _default_prefs()
    prefs["primary"] = slots.get("primary", _default_slot())
    prefs["secondary"] = slots.get("secondary", _default_slot())
    return prefs


def save_pref(raw: typing.Any) -> dict[str, typing.Any]:
    prefs = normalize_pref(raw)
    now = _now_ms()

    with _connect() as conn:
        _init_schema(conn)
        profile_id = _ensure_profile(conn, str(prefs.get("profile_key") or DEFAULT_PROFILE_KEY))

        for slot_key in SLOT_KEYS:
            slot = prefs[slot_key]
            provider, base_url, api_key, model_name, model_type, input_type, notes = _slot_record(slot)
            conn.execute(
                f"""
                INSERT INTO {TABLE_SLOTS} (
                    profile_id, slot_key, provider, base_url, api_key, model_name,
                    model_type, input_type, notes, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, slot_key) DO UPDATE SET
                    provider   = excluded.provider,
                    base_url   = excluded.base_url,
                    api_key    = excluded.api_key,
                    model_name = excluded.model_name,
                    model_type = excluded.model_type,
                    input_type = excluded.input_type,
                    notes      = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (
                    profile_id, slot_key, provider, base_url, api_key, model_name,
                    model_type, input_type, notes, now, now
                )
            )

    return prefs


if __name__ == '__main__':
    pass
