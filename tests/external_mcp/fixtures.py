"""生成独立 MCP 验收配置，并管理测试自身创建的远端 fixture 进程。"""

import asyncio
import contextlib
import sys
import tomllib
import typing

import tomlkit

from collections.abc import AsyncIterator
from dataclasses import (
    dataclass,
    field,
)
from pathlib import Path
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

FixtureTransport = typing.Literal["stdio", "streamable_http", "sse"]
FixtureMode = typing.Literal[
    "ready", "empty", "no-tools", "discovery-failure", "startup-failure",
    "handshake-timeout", "disconnect", "close-stall",
]
FixtureEvent = typing.Literal[
    "process.started", "process.closed", "listening", "session.opened",
    "session.closed", "initialized", "tools.listed", "tool.started",
    "tool.completed", "fault.injected",
]


class FixtureFact(BaseModel):
    """校验单个 fixture 进程写出的事实，实例身份不会由 PID 推断。"""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    event: FixtureEvent
    instance_id: UUID
    pid: int = Field(gt=0)
    created_ns: int = Field(gt=0)
    sequence: int = Field(gt=0)
    transport: FixtureTransport
    session_id: str | None = None
    tool: str | None = None
    port: int | None = Field(default=None, gt=0, le=65535)


class FixtureReply(BaseModel):
    """校验只读工具的实际返回值，供连接隔离和调用去重验收使用。"""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    instance_id: UUID
    pid: int = Field(gt=0)
    session_id: str
    call_count: int = Field(gt=0)
    value: str


@dataclass(frozen=True, slots=True)
class FixtureSpec:
    """描述独立 fixture 的启动参数，文件均落在调用方指定的验收目录。"""

    directory: Path
    name: str
    transport: FixtureTransport = "stdio"
    mode: FixtureMode = "ready"
    repository: Path = field(kw_only=True)

    @property
    def facts_path(self) -> Path:
        """返回本服务追加写入的事实文件。"""
        return self.directory / f"{self.name}.jsonl"

    @property
    def release_path(self) -> Path:
        """返回可控阻塞工具使用的释放信号文件。"""
        return self.directory / f"{self.name}.release"

    def arguments(self) -> tuple[str, ...]:
        """构造跨平台 Python 子进程参数，不经 Shell 解释。"""
        return (
            "-m", "tests.external_mcp.fixture_server", "--name", self.name,
            "--transport", self.transport, "--mode", self.mode,
            "--facts", str(self.facts_path), "--release", str(self.release_path),
        )


def read_facts(path: Path) -> tuple[FixtureFact, ...]:
    """校验完整的 JSONL 记录，正在追加的最后半行留待下一次读取。"""
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ()
    return tuple(
        FixtureFact.model_validate_json(line)
        for line in content.splitlines(keepends=True)
        if line.endswith("\n")
    )


async def wait_for_fact(
    path: Path,
    event: FixtureEvent,
    *,
    after: int = 0,
    timeout: float = 10.0,
) -> FixtureFact:
    """在固定时限内等待新事实，不用固定长等待判断服务就绪。"""
    async with asyncio.timeout(timeout):
        while True:
            for fact in read_facts(path)[after:]:
                if fact.event == event:
                    return fact
            await asyncio.sleep(0.025)


@dataclass(frozen=True, slots=True)
class RemoteFixture:
    """持有 fixture 远端地址和进程句柄，由创建它的上下文负责关闭。"""

    spec: FixtureSpec
    url: str
    process: asyncio.subprocess.Process


@contextlib.asynccontextmanager
async def remote_fixture(spec: FixtureSpec) -> AsyncIterator[RemoteFixture]:
    """启动本机 HTTP/SSE fixture，并只回收本上下文创建的进程。"""
    if spec.transport == "stdio":
        raise ValueError("remote fixture requires an HTTP or SSE transport")
    spec.directory.mkdir(parents=True, exist_ok=True)
    offset = len(read_facts(spec.facts_path))
    log_path = spec.directory / f"{spec.name}.stderr.log"
    with log_path.open("ab") as stderr:
        process = await asyncio.create_subprocess_exec(
            sys.executable, *spec.arguments(),
            cwd=spec.repository,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=stderr,
        )
        try:
            fact = await wait_for_fact(spec.facts_path, "listening", after=offset)
            if fact.port is None:
                raise ValueError("listening fixture did not publish a port")
            suffix = "/sse" if spec.transport == "sse" else "/mcp/"
            yield RemoteFixture(spec, f"http://127.0.0.1:{fact.port}{suffix}", process)
        finally:
            await close_fixture_process(process)


async def close_fixture_process(process: asyncio.subprocess.Process) -> None:
    """限时回收创建方持有的 fixture 进程，允许进程已自行退出。"""
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(process.wait(), timeout=5.0)
    except TimeoutError:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.wait()


def write_config(directory: Path, remotes: tuple[RemoteFixture, ...], *, repository: Path) -> Path:
    """使用 TOML 序列化器创建专用服务表，不读取或修改用户配置。"""
    directory.mkdir(parents=True, exist_ok=True)
    document = tomlkit.document()
    servers = tomlkit.table()
    entries: tuple[tuple[str, str, FixtureMode, bool], ...] = (
        ("A", "a", "ready", True), ("B", "b", "ready", True),
        ("D", "disabled", "ready", False), ("E", "empty", "empty", True),
        ("Filtered", "filtered", "ready", True),
        ("NoTools", "no-tools", "no-tools", True),
        ("F", "failure", "startup-failure", False),
        ("DiscoveryFailure", "discovery-failure", "discovery-failure", False),
        ("Slow", "slow", "handshake-timeout", False),
        ("Disconnect", "disconnect", "disconnect", False),
        ("CloseStall", "close-stall", "close-stall", False),
        ("Docs API", "alias-one", "ready", False),
        ("Docs/API", "alias-two", "ready", False),
        ("all", "named-all", "ready", False),
    )
    for key, name, mode, enabled in entries:
        spec = FixtureSpec(directory, name, mode=mode, repository=repository)
        item = tomlkit.table()
        item["command"] = sys.executable
        item["args"] = list(spec.arguments())
        item["cwd"] = str(repository)
        item["enabled"] = enabled
        item["startup_timeout_sec"] = 2.0
        item["tool_timeout_sec"] = 30.0
        if key == "Filtered":
            item["allow"] = []
        servers[key] = item
    for remote in remotes:
        servers[remote.spec.name] = {"url": remote.url, "enabled": True}
    document["mcp_servers"] = servers
    path = directory / "mcp-fixture.toml"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(tomlkit.dumps(document))
    return path


def client_arguments(path: Path, *, repository: Path) -> tuple[str, ...]:
    """通过既有配置覆盖替换整张 MCP 表，不合并日常服务或经 Shell 转义。"""
    servers = tomllib.loads(path.read_text(encoding="utf-8")).get("mcp_servers")
    if not isinstance(servers, dict):
        raise ValueError("fixture configuration requires a server table")
    inline = tomlkit.inline_table()
    inline.update(servers)
    return (
        sys.executable, str(repository / "mind.py"),
        "-c", "mcp_servers=" + inline.as_string(),
    )
