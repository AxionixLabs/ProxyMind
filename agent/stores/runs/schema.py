# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

RUN_STORE_SCHEMA_VERSION: typing.Final = 3
RUN_SNAPSHOT_VERSION: typing.Final = 1

RUN_STORE_SCHEMA_SQL: typing.Final = """
CREATE TABLE IF NOT EXISTS run_events (
    run_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    event_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    PRIMARY KEY (run_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_run_events_session
    ON run_events (session_id, occurred_at);

CREATE TABLE IF NOT EXISTS run_snapshots (
    run_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    command_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    command_json TEXT NOT NULL,
    status TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    snapshot_version INTEGER NOT NULL,
    effect_id TEXT NOT NULL,
    effect_status TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (session_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_run_snapshots_recovery
    ON run_snapshots (session_id, status, updated_at);

CREATE TABLE IF NOT EXISTS run_remote_requests (
    run_id TEXT PRIMARY KEY,
    request_kind TEXT NOT NULL,
    cid TEXT NOT NULL,
    sid TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    request_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES run_snapshots(run_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_run_remote_requests_turn
    ON run_remote_requests (cid, sid, turn_id);

CREATE TABLE IF NOT EXISTS run_outbox (
    effect_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    replay TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    result_json TEXT,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES run_snapshots(run_id)
);

CREATE INDEX IF NOT EXISTS idx_run_outbox_status
    ON run_outbox (status, updated_at);

CREATE TABLE IF NOT EXISTS run_facts (
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    position INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, kind, position),
    FOREIGN KEY (run_id) REFERENCES run_snapshots(run_id)
);
"""


if __name__ == '__main__':
    pass
