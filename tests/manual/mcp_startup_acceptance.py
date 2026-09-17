# -*- coding: utf-8 -*-

"""从真实终端用本机 MCP 子进程验收可选启动、目录复用与关闭边界。"""

import argparse
import asyncio
import contextlib
import json
import platform
import sys
import time
import typing
from pathlib import Path

from mcp.shared.exceptions import McpError
from pydantic import JsonValue

from agent.ports.mcp_runtime import (
    McpRuntimeContext,
    McpServicesBusy,
)
from infrastructure.config.session import ConfigSession
from infrastructure.config.store import ConfigStore
from infrastructure.mcp.external_runtime import ExternalMcpRuntime
from tests.external_mcp.fixtures import (
    FixtureSpec,
    client_arguments,
    close_fixture_process,
    read_facts,
    remote_fixture,
    wait_for_fact,
)


async def start_activity(snapshot: typing.Callable[[], dict[str, JsonValue]]) -> None:
    """保留正式 runtime 的活动入口，不保存服务地址或协议正文。"""
    print("Source MCP startup requested.", flush=True)


async def stop_activity(kind: str | None = None, *, settle: bool = True) -> None:
    """报告等待边界返回，后台连接事实另从 runtime 读取。"""
    print("Source MCP startup wait returned.", flush=True)


async def cleanup(operation: typing.Awaitable[None]) -> None:
    """在终端取消时仍收束本验收创建的资源。"""
    task = asyncio.ensure_future(operation)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


def create_runtime(root: Path, repository: Path, remotes: dict[str, str], budget: float) -> ExternalMcpRuntime:
    """创建独立有效配置，必需服务使用 stdio，三个可选服务使用真实 HTTP。"""
    spec = FixtureSpec(root, "required", repository=repository)
    servers: dict[str, JsonValue] = {
        "required": {
            "command": sys.executable, "args": list(spec.arguments()), "cwd": str(repository),
            "required": True, "startup_timeout_sec": 15,
        },
        **{name: {"url": url, "startup_timeout_sec": 15, "optional_startup_wait_sec": budget} for name, url in remotes.items()},
    }
    store = ConfigStore(root / "config.toml")
    store.update({("mcp_servers",): servers})
    return ExternalMcpRuntime(McpRuntimeContext(
        ConfigSession(store, workspace=root), start_activity, stop_activity, cleanup,
    ))


async def settled(runtime: ExternalMcpRuntime) -> None:
    """等待真实连接终态，超时不会被记录为通过。"""
    async with asyncio.timeout(15):
        while any(item.state == "starting" for item in runtime.service_snapshots):
            await asyncio.sleep(0.025)


async def release_after(specs: tuple[FixtureSpec, ...], delay: float) -> None:
    """释放受控慢目录，让两种等待策略承受相同的真实延迟。"""
    await asyncio.sleep(delay)
    for spec in specs:
        spec.release_path.write_text("ready", encoding="utf-8")


async def run_acceptance(directory: Path, repository: Path) -> dict[str, JsonValue]:
    """比较冷启动预算，随后验证缓存调用、定义变化和退休时的真实资源关闭。"""
    delayed = tuple(FixtureSpec(directory, name, transport="streamable_http", mode="delayed-discovery", repository=repository) for name in ("slow-one", "slow-two"))
    failed = FixtureSpec(directory, "failed", transport="streamable_http", mode="http-protocol-error", repository=repository)
    results: dict[str, JsonValue] = {}
    async with contextlib.AsyncExitStack() as stack:
        remotes = [await stack.enter_async_context(remote_fixture(spec)) for spec in (*delayed, failed)]
        urls = {remote.spec.name: remote.url for remote in remotes}
        elapsed: dict[str, float] = {}
        for label, budget in (("full-wait", 0), ("bounded-wait", 0.1)):
            for spec in delayed:
                spec.release_path.unlink(missing_ok=True)
            runtime = create_runtime(directory / label, repository, urls, budget)
            release = asyncio.create_task(release_after(delayed, 3))
            try:
                started = time.monotonic()
                await runtime.start()
                elapsed[label] = time.monotonic() - started
                with runtime.use_tools() as initial:
                    assert initial is not None
                    names = tuple(initial.tools)
                    if budget:
                        assert elapsed[label] < 2 and len(names) == 2
                        await asyncio.gather(runtime.start(), runtime.start())
                    await release
                    await settled(runtime)
                    assert tuple(initial.tools) == names
                with runtime.use_tools() as current:
                    assert current is not None and len(current.tools) == 6
                    for name in ("required", "slow-one", "slow-two"):
                        assert not (await current.call_tool(f"mcp__{name}__ping")).isError
                assert [item.state for item in runtime.service_snapshots].count("failed") == 1
                if budget:
                    await cached_calls(runtime, delayed, results)
                    for spec in delayed:
                        spec.release_path.unlink(missing_ok=True)
                    await runtime.stop_services()
                    offsets = [len(read_facts(spec.facts_path)) for spec in delayed]
                    await runtime.start()
                    for index, spec in enumerate(delayed):
                        await wait_for_fact(spec.facts_path, "fault.injected", after=offsets[index])
                    runtime.retire()
                    for spec in delayed:
                        spec.release_path.write_text("ready", encoding="utf-8")
                await runtime.stop()
                assert runtime.group is None
            finally:
                release.cancel()
                await asyncio.gather(release, return_exceptions=True)
                await runtime.stop()
            print(f"{label}: {elapsed[label]:.3f}s, passed", flush=True)
        assert elapsed["full-wait"] >= 3
        assert elapsed["bounded-wait"] < elapsed["full-wait"] / 2
        results.update(elapsed_sec=elapsed, initial_frozen_scope_unchanged=True, required_ready=True, optional_failure_isolated=True, retired_resources_closed=True)
    facts = [fact for path in directory.rglob("*.jsonl") for fact in read_facts(path)]
    results["fixture_pids"] = sorted({fact.pid for fact in facts if fact.event == "process.started"})
    results["sessions_opened"] = sum(fact.event == "session.opened" for fact in facts)
    results["sessions_closed"] = sum(fact.event == "session.closed" for fact in facts)
    assert results["sessions_opened"] == results["sessions_closed"]
    return results


async def cached_calls(runtime: ExternalMcpRuntime, delayed: tuple[FixtureSpec, ...], results: dict[str, JsonValue]) -> None:
    """重用相同配置的旧目录，执行时等待并检查实时定义，禁止缓存覆盖审批。"""
    await runtime.stop_services()
    offsets = [len(read_facts(spec.facts_path)) for spec in delayed]
    for spec in delayed:
        spec.release_path.unlink(missing_ok=True)
    await runtime.start()
    with runtime.use_tools() as cached:
        assert cached is not None and len(cached.tools) == 6
        for spec in delayed:
            assert cached.tools[f"mcp__{spec.name}__ping"].meta["approval_mode"] == "prompt"
        try:
            await runtime.stop_services()
        except McpServicesBusy:
            pass
        else:
            raise AssertionError("Cached tool scope did not hold its connections")
        for index, spec in enumerate(delayed):
            await wait_for_fact(spec.facts_path, "fault.injected", after=offsets[index])
            call = asyncio.create_task(cached.call_tool(f"mcp__{spec.name}__ping"))
            try:
                await asyncio.sleep(0)
                assert not call.done()
                spec.release_path.write_text("changed" if index else "ready", encoding="utf-8")
                if index:
                    try:
                        await call
                    except McpError:
                        pass
                    else:
                        raise AssertionError("Changed live catalog was accepted")
                    assert not any(fact.event == "tool.started" for fact in read_facts(spec.facts_path)[offsets[index]:])
                else:
                    assert not (await call).isError
            finally:
                call.cancel()
                await asyncio.gather(call, return_exceptions=True)
    results.update(cached_call_waited=True, cached_approval_conservative=True, changed_catalog_blocked_before_call=True)
    print("Cached catalog and frozen invocation checks: passed", flush=True)


async def run_client(directory: Path, repository: Path) -> dict[str, JsonValue]:
    """在继承的真实终端启动源码客户端，慢目录由验收方释放文件。"""
    slow = FixtureSpec(directory, "slow", mode="delayed-discovery", repository=repository)
    required = FixtureSpec(directory, "required", repository=repository)
    failed = FixtureSpec(directory, "failed", transport="streamable_http", mode="http-protocol-error", repository=repository)
    async with remote_fixture(failed) as remote:
        store = ConfigStore(directory / "config.toml")
        store.update({("mcp_servers",): {
            "required": {"command": sys.executable, "args": list(required.arguments()), "cwd": str(repository), "required": True},
            "slow": {"command": sys.executable, "args": list(slow.arguments()), "cwd": str(repository), "startup_timeout_sec": 180, "optional_startup_wait_sec": 0.1},
            "failed": {"url": remote.url, "optional_startup_wait_sec": 0.1},
        }})
        process = await asyncio.create_subprocess_exec(*client_arguments(directory / "config.toml", repository=repository), cwd=directory)
        try:
            await process.wait()
        finally:
            await close_fixture_process(process)
        assert process.returncode == 0
    facts = [fact for path in directory.glob("*.jsonl") for fact in read_facts(path)]
    return {
        "client_exit_code": process.returncode,
        "fixture_pids": sorted({fact.pid for fact in facts if fact.event == "process.started"}),
        "sessions_opened": sum(fact.event == "session.opened" for fact in facts),
        "sessions_closed": sum(fact.event == "session.closed" for fact in facts),
    }


def main() -> None:
    """要求新验收目录，保存脱敏事实并在失败时返回非零退出码。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--client", action="store_true")
    arguments = parser.parse_args()
    directory = arguments.directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    report: dict[str, JsonValue] = {"platform": platform.platform(), "entry_kind": "source-client-tty" if arguments.client else "source-runtime-tty-real-mcp", "terminal": sys.stdin.isatty(), "passed": False}
    try:
        operation = run_client if arguments.client else run_acceptance
        report.update(asyncio.run(operation(directory, arguments.repository.resolve())))
        report["passed"] = True
    finally:
        with (directory / "report.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)


if __name__ == "__main__":
    main()
