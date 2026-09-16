# -*- coding: utf-8 -*-

import dataclasses
import multiprocessing
import sqlite3

import anyio
import httpx
import pytest
from contextlib import closing
from multiprocessing.connection import Connection
from pathlib import Path
from urllib.parse import parse_qsl

from agent.domain.mcp_oauth import (
    McpOAuthClientInfo,
    McpOAuthBinding,
    McpOAuthError,
    McpOAuthCredentialSnapshot,
    McpOAuthStorageError,
    McpOAuthTarget,
    McpOAuthToken,
    McpRegisteredClient,
)
from infrastructure.mcp.oauth_adapter import McpOAuthRefreshAdapter
from infrastructure.mcp.oauth_credentials import SystemMcpCredentialStore
from infrastructure.mcp.oauth_runtime import McpOAuthRuntimeAuth


class ProcessTestVault:
    """仅用于进程测试的共享 fake，不能被生产组合根选择。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS secrets (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def read(self, key: str) -> str | None:
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute("SELECT value FROM secrets WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        value = row[0]
        assert isinstance(value, str)
        return value

    def write_new(self, key: str, value: str) -> None:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("INSERT INTO secrets VALUES (?, ?)", (key, value))

    def delete(self, key: str) -> None:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("DELETE FROM secrets WHERE key=?", (key,))


def process_store(root: Path) -> SystemMcpCredentialStore:
    return SystemMcpCredentialStore(
        config_root=root / "config", state_root=root / "state",
        vault=ProcessTestVault(root / "fake-vault.db"), clock=lambda: 1950,
    )


def process_snapshot(target: McpOAuthTarget) -> McpOAuthCredentialSnapshot:
    return McpOAuthCredentialSnapshot(
        target, "https://issuer.example", "https://service.example/mcp", "https://issuer.example/token",
        McpOAuthClientInfo("native", ("http://127.0.0.1:8888/callback",), "registered"),
        0, McpOAuthToken("process-access", "process-refresh", 2000, ("read",)),
    )


async def worker_main(root: Path, key: str, command: str, pipe: Connection) -> None:
    store = process_store(root)
    target = McpOAuthTarget(key, "https://service.example/mcp")
    if command == "compete":
        captured = await store.read(target)
        pipe.send("captured")
        assert await anyio.to_thread.run_sync(pipe.poll, 10)
        pipe.recv()
        try:
            async with store.transaction(target) as transaction:
                await transaction.save(dataclasses.replace(process_snapshot(target), generation=captured.generation))
            pipe.send("saved")
        except McpOAuthStorageError as error:
            pipe.send(error.code)
    elif command == "hold":
        async with store.transaction(target):
            pipe.send("locked")
            assert await anyio.to_thread.run_sync(pipe.poll, 10)
            pipe.recv()
    elif command == "write":
        async with store.transaction(target) as transaction:
            await transaction.save(process_snapshot(target))
        pipe.send("saved")
    elif command == "read":
        saved = (await store.read(target)).snapshot
        assert saved is not None and saved.token is not None
        assert saved.token.access_token == "process-access"
        assert saved.token.refresh_token == "process-refresh"
        pipe.send((saved.generation, saved.token.remaining_lifetime(1950)))
    elif command == "delete":
        result = await store.delete(target)
        pipe.send((result.removed, result.generation))
    elif command in ("runtime_refresh", "abort_refresh"):
        async def handle(request: httpx.Request) -> httpx.Response:
            """用跨进程服务端计数验证 refresh token 只被消费一次。"""
            if str(request.url) == "https://issuer.example/token":
                form = dict(parse_qsl(request.content.decode("ascii")))
                assert form["refresh_token"] == "process-refresh"
                assert form["resource"] == target.server_url and form["client_id"] == "native"
                with closing(sqlite3.connect(root / "server.db")) as connection, connection:
                    connection.execute("INSERT INTO refreshes VALUES (1)")
                if command == "abort_refresh":
                    pipe.send("consumed")
                    assert await anyio.to_thread.run_sync(pipe.poll, 10)
                    pipe.recv()
                return httpx.Response(200, json={
                    "access_token": "rotated-access", "refresh_token": "rotated-refresh",
                    "token_type": "Bearer", "expires_in": 3600, "scope": "read",
                })
            assert request.headers["Authorization"] == "Bearer rotated-access"
            return httpx.Response(200)

        binding = McpOAuthBinding(target, McpRegisteredClient("native"))
        auth = McpOAuthRuntimeAuth(binding, store, failed=lambda error: None, clock=lambda: 1950,
            refresher=McpOAuthRefreshAdapter(client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle)), clock=lambda: 1950))
        pipe.send("ready")
        assert await anyio.to_thread.run_sync(pipe.poll, 10)
        pipe.recv()
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle), auth=auth) as client:
                assert (await client.get(target.server_url)).status_code == 200
            pipe.send("authorized")
        except McpOAuthError as error:
            pipe.send(error.code)


def worker(root: Path, key: str, command: str, pipe: Connection) -> None:
    with pipe:
        anyio.run(worker_main, root, key, command, pipe)


def start_worker(root: Path, key: str, command: str) -> tuple[multiprocessing.Process, Connection]:
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=worker, args=(root, key, command, child))
    process.start()
    child.close()
    return process, parent


def finish_worker(process: multiprocessing.Process, pipe: Connection) -> None:
    process.join(10)
    try:
        assert not process.is_alive()
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        process.close()
        pipe.close()


def test_fresh_process_restores_tokens_and_absolute_expiry(tmp_path: Path) -> None:
    for command, expected in (("write", "saved"), ("read", (1, 50)), ("delete", (True, 2))):
        process, pipe = start_worker(tmp_path, "sentry", command)
        try:
            assert pipe.poll(10)
            assert pipe.recv() == expected
        finally:
            finish_worker(process, pipe)
    store = process_store(tmp_path)
    restored = anyio.run(store.read, McpOAuthTarget("sentry", "https://service.example/mcp"))
    assert restored.snapshot is None and restored.generation == 2


@pytest.mark.parametrize("same_target", [True, False])
def test_concurrent_processes_use_cas_without_cross_target_damage(tmp_path: Path, same_target: bool) -> None:
    workers = [start_worker(tmp_path, key, "compete") for key in ("sentry", "sentry" if same_target else "other")]
    try:
        for _, pipe in workers:
            assert pipe.poll(10) and pipe.recv() == "captured"
        for _, pipe in workers:
            pipe.send("commit")
        results: list[str] = []
        for _, pipe in workers:
            assert pipe.poll(10)
            results.append(pipe.recv())
        assert sorted(results) == (["credential_conflict", "saved"] if same_target else ["saved", "saved"])
    finally:
        for process, pipe in workers:
            finish_worker(process, pipe)


def test_owner_process_exit_releases_lock(tmp_path: Path) -> None:
    process, pipe = start_worker(tmp_path, "sentry", "hold")
    try:
        assert pipe.poll(10) and pipe.recv() == "locked"
        process.terminate()
        process.join(5)
        assert not process.is_alive()
        store = process_store(tmp_path)
        assert anyio.run(store.read, McpOAuthTarget("sentry", "https://service.example/mcp")).generation == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        process.close()
        pipe.close()


async def seed_expired_runtime(root: Path) -> None:
    store = process_store(root)
    target = McpOAuthTarget("sentry", "https://service.example/mcp")
    snapshot = process_snapshot(target)
    assert snapshot.token is not None
    async with store.transaction(target) as transaction:
        await transaction.save(dataclasses.replace(snapshot, token=dataclasses.replace(snapshot.token, expires_at=1900)))
    with closing(sqlite3.connect(root / "server.db")) as connection, connection:
        connection.execute("CREATE TABLE refreshes (consumed INTEGER)")


def test_runtime_refresh_is_consumed_once_across_processes(tmp_path: Path) -> None:
    anyio.run(seed_expired_runtime, tmp_path)
    workers = [start_worker(tmp_path, "sentry", "runtime_refresh") for _ in range(2)]
    try:
        for _, pipe in workers:
            assert pipe.poll(10) and pipe.recv() == "ready"
        for _, pipe in workers:
            pipe.send("go")
        for _, pipe in workers:
            assert pipe.poll(10) and pipe.recv() == "authorized"
    finally:
        for process, pipe in workers:
            finish_worker(process, pipe)
    with closing(sqlite3.connect(tmp_path / "server.db")) as connection:
        assert connection.execute("SELECT count(*) FROM refreshes").fetchone()[0] == 1


def test_crash_after_remote_refresh_requires_login_in_next_process(tmp_path: Path) -> None:
    anyio.run(seed_expired_runtime, tmp_path)
    process, pipe = start_worker(tmp_path, "sentry", "abort_refresh")
    try:
        assert pipe.poll(10) and pipe.recv() == "ready"
        pipe.send("go")
        assert pipe.poll(10) and pipe.recv() == "consumed"
        process.terminate()
        process.join(5)
        assert not process.is_alive()
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        process.close()
        pipe.close()
    process, pipe = start_worker(tmp_path, "sentry", "runtime_refresh")
    try:
        assert pipe.poll(10) and pipe.recv() == "ready"
        pipe.send("go")
        assert pipe.poll(10) and pipe.recv() == "refresh_uncertain"
    finally:
        finish_worker(process, pipe)
    with closing(sqlite3.connect(tmp_path / "server.db")) as connection:
        assert connection.execute("SELECT count(*) FROM refreshes").fetchone()[0] == 1
