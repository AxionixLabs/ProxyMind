# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import base64
import binascii
import hashlib
import json
import math
import os
import sqlite3
import time
import typing
import uuid

import anyio
from contextlib import (
    asynccontextmanager,
    closing,
)
from dataclasses import replace
from pathlib import Path

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
)

from agent.domain.mcp_oauth import (
    McpOAuthCredentialRecord,
    McpOAuthCredentialSnapshot,
    McpOAuthCredentialView,
    McpOAuthLogoutResult,
    McpOAuthStorageError,
    McpOAuthTarget,
)
from agent.ports.mcp_credentials import McpCredentialTransaction
from infrastructure.platform.credential_vault import (
    CredentialVault,
    SystemCredentialVault,
)
from metadata import const


_CHUNK_SIZE = 1000
_MAX_CHUNKS = 256


class _Envelope(BaseModel):
    """校验系统凭据载荷的完整结构，拒绝未知字段和旧格式的隐式兼容。"""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    format: typing.Literal[1]
    snapshot: McpOAuthCredentialSnapshot = Field(repr=False)


class _Revision(BaseModel):
    """保存不含机密的片段索引与完整性摘要。"""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    revision: str = Field(pattern=r"^[0-9a-f]{32}$")
    chunks: int = Field(ge=1, le=_MAX_CHUNKS)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class _IndexState(BaseModel):
    """保存唯一活动版本或退出墓碑，不保存令牌或服务地址。"""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    generation: int = Field(ge=0)
    revision: str | None = Field(pattern=r"^[0-9a-f]{32}$")


def _fingerprint(parts: tuple[str, ...]) -> str:
    """使用结构化编码构造身份摘要，避免路径和配置键分隔歧义。"""
    encoded = json.dumps(parts, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode(const.CHARSET)).hexdigest()


def _storage_error(error: OSError | sqlite3.Error) -> McpOAuthStorageError:
    """只按结构化错误码区分损坏和不可用，不传播数据库路径或原始消息。"""
    if isinstance(error, sqlite3.DatabaseError) and getattr(error, "sqlite_errorcode", None) in (
        sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB,
    ):
        return McpOAuthStorageError("storage_corrupt")
    return McpOAuthStorageError("storage_unavailable")


class _CredentialIndex:
    """在外层单目标锁内操作索引；每次同步操作拥有并关闭 SQLite 连接。"""

    def __init__(self, path: Path, address: str, target: McpOAuthTarget, vault: CredentialVault) -> None:
        """冻结索引路径、机密命名空间和目标身份。"""
        self.path = path
        self.address = address
        self.target = target
        self.vault = vault

    def initialize(self) -> None:
        """创建独立索引，已存在但格式不明的数据库不得被当作空记录。"""
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                if tables:
                    raise McpOAuthStorageError("storage_corrupt")
                connection.execute(
                    "CREATE TABLE state (id INTEGER PRIMARY KEY CHECK (id=1), "
                    "generation INTEGER NOT NULL, revision TEXT)"
                )
                connection.execute(
                    "CREATE TABLE revisions (revision TEXT PRIMARY KEY, "
                    "chunks INTEGER NOT NULL, digest TEXT NOT NULL)"
                )
                connection.execute("INSERT INTO state VALUES (1, 0, NULL)")
                connection.execute("PRAGMA user_version=1")
            elif version != 1:
                raise McpOAuthStorageError("storage_corrupt")

    def _state(self, connection: sqlite3.Connection) -> _IndexState:
        """校验唯一的活动状态，不将缺失行解释成尚未登录。"""
        rows = connection.execute("SELECT generation, revision FROM state").fetchall()
        if len(rows) != 1:
            raise McpOAuthStorageError("storage_corrupt")
        return _IndexState.model_validate({"generation": rows[0][0], "revision": rows[0][1]})

    def _revisions(self, connection: sqlite3.Connection) -> tuple[_Revision, ...]:
        """在使用索引生成系统记录名之前严格验证其结构。"""
        return tuple(
            _Revision.model_validate({"revision": row[0], "chunks": row[1], "digest": row[2]})
            for row in connection.execute("SELECT revision, chunks, digest FROM revisions")
        )

    def _key(self, revision: str, chunk: int) -> str:
        """每片使用唯一服务名，禁止后端生成覆盖旧凭据的影子副本。"""
        return f"{const.APP_NAME}.mcp.oauth.v1.{self.address}.{revision}.{chunk}"

    def cleanup(self) -> None:
        """幂等清理未提交或已退休的机密，失败保留索引以便下次恢复。"""
        with closing(sqlite3.connect(self.path)) as connection:
            state = self._state(connection)
            revisions = self._revisions(connection)
            if state.revision is not None and not any(
                item.revision == state.revision for item in revisions
            ):
                raise McpOAuthStorageError("storage_corrupt")
            for item in revisions:
                if item.revision == state.revision:
                    continue
                for chunk in range(item.chunks):
                    self.vault.delete(self._key(item.revision, chunk))
                with connection:
                    connection.execute("DELETE FROM revisions WHERE revision=?", (item.revision,))

    def read(self) -> McpOAuthCredentialRecord:
        """校验全部片段与绑定，返回绝对有效期，绝不重置过期时间。"""
        self.cleanup()
        with closing(sqlite3.connect(self.path)) as connection:
            state = self._state(connection)
            if state.revision is None:
                self.vault.read(f"{const.APP_NAME}.mcp.oauth.v1.{self.address}.availability")
                return McpOAuthCredentialRecord(state.generation, None)
            revision = next(
                item for item in self._revisions(connection)
                if item.revision == state.revision
            )
        chunks: list[str] = []
        for chunk in range(revision.chunks):
            value = self.vault.read(self._key(revision.revision, chunk))
            if value is None or not 0 < len(value) <= _CHUNK_SIZE:
                raise McpOAuthStorageError("storage_corrupt")
            chunks.append(value)
        payload = base64.b64decode("".join(chunks), validate=True)
        if hashlib.sha256(payload).hexdigest() != revision.digest:
            raise McpOAuthStorageError("storage_corrupt")
        snapshot = _Envelope.model_validate_json(payload).snapshot
        if snapshot.target != self.target or snapshot.generation != state.generation:
            raise McpOAuthStorageError("storage_corrupt")
        return McpOAuthCredentialRecord(state.generation, snapshot)

    def save(self, snapshot: McpOAuthCredentialSnapshot) -> McpOAuthCredentialSnapshot:
        """先登记待清理记录，再写机密，最后原子切换活动版本。"""
        self.cleanup()
        with closing(sqlite3.connect(self.path)) as connection:
            state = self._state(connection)
            if snapshot.target != self.target or snapshot.generation != state.generation:
                raise McpOAuthStorageError("credential_conflict")
            saved = replace(snapshot, generation=state.generation + 1)
            payload = _Envelope(format=1, snapshot=saved).model_dump_json().encode(const.CHARSET)
            encoded = base64.b64encode(payload).decode("ascii")
            chunks = tuple(
                encoded[start:start + _CHUNK_SIZE]
                for start in range(0, len(encoded), _CHUNK_SIZE)
            )
            if len(chunks) > _MAX_CHUNKS:
                raise McpOAuthStorageError("storage_unavailable")
            revision = uuid.uuid4().hex
            with connection:
                connection.execute(
                    "INSERT INTO revisions VALUES (?, ?, ?)",
                    (revision, len(chunks), hashlib.sha256(payload).hexdigest()),
                )
            for number, chunk in enumerate(chunks):
                self.vault.write_new(self._key(revision, number), chunk)
            with connection:
                changed = connection.execute(
                    "UPDATE state SET generation=?, revision=? WHERE id=1 AND generation=?",
                    (saved.generation, revision, state.generation),
                ).rowcount
                if changed != 1:
                    raise McpOAuthStorageError("credential_conflict")
        self.cleanup()
        return saved

    def delete(self) -> McpOAuthLogoutResult:
        """先提交墓碑阻止迟到写入，清除全部旧机密后才返回成功。"""
        with closing(sqlite3.connect(self.path)) as connection, connection:
            state = self._state(connection)
            removed = state.revision is not None or bool(self._revisions(connection))
            generation = state.generation + 1
            connection.execute("UPDATE state SET generation=?, revision=NULL WHERE id=1", (generation,))
        self.cleanup()
        return McpOAuthLogoutResult(self.target, removed, generation)


_Result = typing.TypeVar("_Result")


async def _run_io(
    operation: typing.Callable[[], _Result],
    *,
    on_cancel: typing.Callable[[_Result], None] | None = None,
) -> _Result:
    """同时收束作用域取消和直接任务取消；未交付的资源由指定回调关闭。"""
    try:
        with anyio.CancelScope(shield=True):
            # Executor Future 不参与事件循环关闭时的 Task 批量取消。
            work = asyncio.get_running_loop().run_in_executor(None, operation)
            cancelled = False
            while True:
                try:
                    result = await asyncio.shield(work)
                    break
                except asyncio.CancelledError:
                    if work.cancelled():
                        raise
                    cancelled = True
            if cancelled:
                if on_cancel is not None:
                    on_cancel(result)
                raise asyncio.CancelledError
            return result
    except (ValidationError, ValueError, binascii.Error, UnicodeError):
        raise McpOAuthStorageError("storage_corrupt") from None
    except (OSError, sqlite3.Error) as error:
        raise _storage_error(error) from None


def _close_undelivered_lock(connection: sqlite3.Connection | None) -> None:
    """取消发生在取锁线程中时关闭尚未交付给上下文的连接。"""
    if connection is not None:
        connection.close()


class _Transaction:
    """持有一次上下文的最新快照，禁止退出后或跨任务继续使用。"""

    def __init__(self, index: _CredentialIndex, record: McpOAuthCredentialRecord) -> None:
        """绑定单次事务与创建任务。"""
        self._index = index
        self._record = record
        self._task = anyio.get_current_task().id
        self.active = True

    def _check_active(self) -> None:
        """拒绝超出 owner 生命周期的读取和写入。"""
        if not self.active or self._task != anyio.get_current_task().id:
            raise McpOAuthStorageError("credential_conflict")

    @property
    def record(self) -> McpOAuthCredentialRecord:
        """返回在锁内读取或提交后的最新快照。"""
        self._check_active()
        return self._record

    async def save(self, snapshot: McpOAuthCredentialSnapshot) -> McpOAuthCredentialSnapshot:
        """提交当前 generation 的替换记录，完成清理后更新事务快照。"""
        self._check_active()
        saved = await _run_io(lambda: self._index.save(snapshot))
        self._record = McpOAuthCredentialRecord(saved.generation, saved)
        return saved

    async def delete(self) -> McpOAuthLogoutResult:
        """删除后保留最新 generation 供调用方继续裁决。"""
        self._check_active()
        result = await _run_io(self._index.delete)
        self._record = McpOAuthCredentialRecord(result.generation, None)
        return result


class SystemMcpCredentialStore:
    """系统凭据库是机密存储，SQLite 是版本和清理索引的唯一权威。

    每个目标使用独立 SQLite 锁文件，使不同服务可并发；锁文件不能在运行中删除。
    索引与不可变机密记录以先登记、后写入、再切换的顺序提交，崩溃后按索引恢复。
    """

    def __init__(
        self,
        *,
        config_root: Path,
        state_root: Path,
        vault: CredentialVault | None = None,
        clock: typing.Callable[[], float] = time.time,
        lock_timeout: float = 10.0,
    ) -> None:
        """由组合边界注入已解析的配置根和状态根，不读取环境或推测用户目录。"""
        if not math.isfinite(lock_timeout) or lock_timeout <= 0:
            raise ValueError("MCP credential lock timeout must be positive")
        self._root = state_root.resolve() / "mcp" / "oauth"
        self._namespace = _fingerprint((
            os.path.normcase(str(config_root.resolve())),
            os.path.normcase(str(state_root.resolve())),
        ))
        self._vault = SystemCredentialVault() if vault is None else vault
        self._clock = clock
        self._lock_timeout = lock_timeout

    def _address(self, target: McpOAuthTarget) -> str:
        """为原始键及完整 URL 构造不可逆的命名空间内身份。"""
        return _fingerprint((self._namespace, target.config_key, target.server_url))

    def _try_lock(self, address: str) -> sqlite3.Connection | None:
        """尝试取得跨平台进程锁，连接存活期间保持独占写事务。"""
        self._root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._root / f"{address}.lock", timeout=0, check_same_thread=False)
        try:
            connection.execute("BEGIN IMMEDIATE")
            return connection
        except sqlite3.OperationalError as error:
            connection.close()
            if getattr(error, "sqlite_errorcode", None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                return None
            raise
        except BaseException:
            connection.close()
            raise

    @asynccontextmanager
    async def _locked_index(self, target: McpOAuthTarget) -> typing.AsyncIterator[_CredentialIndex]:
        """有界等待目标锁，取消和异常路径均等待 IO 收束并关闭连接。"""
        address = self._address(target)
        deadline = anyio.current_time() + self._lock_timeout
        connection: sqlite3.Connection | None = None
        try:
            while connection is None:
                connection = await _run_io(
                    lambda: self._try_lock(address), on_cancel=_close_undelivered_lock,
                )
                if connection is None:
                    remaining = deadline - anyio.current_time()
                    if remaining <= 0:
                        raise McpOAuthStorageError("storage_busy")
                    await anyio.sleep(min(0.025, remaining))
            index = _CredentialIndex(self._root / f"{address}.db", address, target, self._vault)
            await _run_io(index.initialize)
            yield index
        finally:
            if connection is not None:
                with anyio.CancelScope(shield=True):
                    await _run_io(connection.close)

    @asynccontextmanager
    async def transaction(self, target: McpOAuthTarget) -> typing.AsyncIterator[McpCredentialTransaction]:
        """为登录、刷新和退出提供同一锁与版本规则。"""
        async with self._locked_index(target) as index:
            transaction = _Transaction(index, await _run_io(index.read))
            try:
                yield transaction
            finally:
                transaction.active = False

    async def read(self, target: McpOAuthTarget) -> McpOAuthCredentialRecord:
        """恢复最新持久记录，不将损坏或后端故障当作未登录。"""
        async with self.transaction(target) as transaction:
            return transaction.record

    async def delete(self, target: McpOAuthTarget) -> McpOAuthLogoutResult:
        """无需解码机密即可退出，允许清除载荷已损坏的凭据。"""
        async with self._locked_index(target) as index:
            return await _run_io(index.delete)

    async def view(self, target: McpOAuthTarget) -> McpOAuthCredentialView:
        """投影注册、存储、过期或不可用状态，不暴露 SDK 或机密对象。"""
        try:
            record = await self.read(target)
        except McpOAuthStorageError as error:
            return McpOAuthCredentialView(target, "unavailable", error=error.code)
        snapshot = record.snapshot
        if snapshot is None:
            return McpOAuthCredentialView(target, "missing")
        token = snapshot.token
        if token is None:
            return McpOAuthCredentialView(target, "registered")
        expired = token.expires_at is not None and token.expires_at <= self._clock()
        return McpOAuthCredentialView(target, "expired" if expired else "stored", token.expires_at)


if __name__ == '__main__':
    pass
