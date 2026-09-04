# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

QUEUE_STORE_SCHEMA_VERSION: typing.Final = 1

QUEUE_STORE_SCHEMA_SQL: typing.Final = """
CREATE TABLE IF NOT EXISTS local_queue_submissions (
    submission_id TEXT PRIMARY KEY,
    client_message_id TEXT NOT NULL,
    cid TEXT NOT NULL,
    sid TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    add_request_id TEXT NOT NULL UNIQUE,
    start_request_id TEXT UNIQUE,
    command_json TEXT NOT NULL,
    request_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    revision INTEGER NOT NULL,
    queue_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (cid, sid, client_message_id),
    UNIQUE (cid, sid, turn_id)
);

CREATE INDEX IF NOT EXISTS idx_local_queue_session
    ON local_queue_submissions (cid, sid, status, created_at);
"""


if __name__ == '__main__':
    pass
