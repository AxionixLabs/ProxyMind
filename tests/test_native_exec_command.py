# -*- coding: utf-8 -*-

import asyncio
import gc
import time
import uuid
from pathlib import Path

from mind_app.client_tools.coding.native import coding_tools
from mind_app.client_tools.types import ClientTool, ClientToolRuntime
from mind_app.native_coding import NativeCoding
from mind_app.native_coding.exec.exec_command import ExecCommandTools, ExecSession


def approved_execution(**overrides: object) -> dict[str, object]:
    """生成允许本地执行的测试授权元数据。"""
    data: dict[str, object] = {
        "grantId": "test-grant",
        "state": "approved",
        "target": "local",
        "risk": "test",
        "category": "test",
        "reasons": [],
        "policyVersion": "test-v1",
        "expiresAt": time.time() + 60,
    }
    data.update(overrides)
    return data


def write_execution(
    *,
    session_id: object,
    stdin: str,
    wait_ms: int = 1000,
    max_output_chars: int = 12000,
    control: str = "none",
) -> dict[str, object]:
    """生成非空会话写入使用的 canonical 授权。"""
    return approved_execution(
        grantId=f"write-{uuid.uuid4().hex}",
        canonicalArguments={
            "session_id": str(session_id or ""),
            "stdin": stdin,
            "wait_ms": wait_ms,
            "max_output_chars": max_output_chars,
            "control": control,
        },
    )


def run_async(value: object) -> object:
    """同步测试中运行异步工具调用。"""
    async def runner() -> object:
        try:
            return await value
        finally:
            await asyncio.sleep(0)
            gc.collect()
            await asyncio.sleep(0)

    return asyncio.run(runner())


def write_script(root: Path, name: str, content: str) -> str:
    """写入测试脚本并返回可由 shell 执行的命令。"""
    script = root / name
    script.write_text(content, encoding="utf-8")
    return f"python -u {script.name}"


def tool_by_name(tools: list[ClientTool], name: str) -> ClientTool:
    """按名称取得客户端工具。"""
    for tool in tools:
        if tool.name == name:
            return tool
    raise AssertionError(f"tool not found: {name}")


async def terminate_session(
    coding: NativeCoding,
    session_id: object,
    *,
    cid: str = "",
    sid: str = "",
) -> None:
    """尽力终止测试创建的会话。"""
    exec_session_id = str(session_id or "").strip()
    if not exec_session_id:
        return
    await coding.write_stdin(
        session_id=exec_session_id,
        control="terminate",
        wait_ms=1000,
        cid=cid,
        sid=sid,
    )
    await asyncio.sleep(0.05)


def test_exec_command_requires_execution_metadata(tmp_path: Path) -> None:
    """缺少 execution 元数据时拒绝启动会话。"""
    result = run_async(
        NativeCoding(root=tmp_path).exec_command(
            command="python --version",
            cwd=".",
            yield_time_ms=0,
            timeout_sec=5,
            execution=None,
        )
    )

    assert result["ok"] is False
    assert result["data"]["tool"] == "exec_command"
    assert result["data"]["error"] == "execution_policy_blocked"
    assert result["data"]["risk_signals"] == ["missing_execution_metadata"]


def test_exec_command_rejects_empty_command(tmp_path: Path) -> None:
    """空命令返回稳定失败原因。"""
    result = run_async(
        NativeCoding(root=tmp_path).exec_command(
            command="",
            execution=approved_execution(),
        )
    )

    assert result["ok"] is False
    assert result["data"]["reason"] == "command_empty"


def test_exec_command_rejects_missing_cwd(tmp_path: Path) -> None:
    """不存在的工作目录不会启动会话。"""
    result = run_async(
        NativeCoding(root=tmp_path).exec_command(
            command="python --version",
            cwd="missing",
            yield_time_ms=0,
            timeout_sec=5,
            execution=approved_execution(),
        )
    )

    assert result["ok"] is False
    assert result["data"]["tool"] == "exec_command"
    assert result["data"]["reason"] == "cwd_not_directory"
    assert result["data"]["cwd"] == "missing"


def test_exec_command_returns_cloud_sandbox_handoff(tmp_path: Path) -> None:
    """云沙箱目标返回移交结果，不启动本地会话。"""
    result = run_async(
        NativeCoding(root=tmp_path).exec_command(
            command="python --version",
            cwd=".",
            yield_time_ms=0,
            timeout_sec=5,
            execution=approved_execution(target="cloud_sandbox"),
        )
    )

    assert result["ok"] is False
    assert result["data"]["tool"] == "exec_command"
    assert result["data"]["execution_target"] == "cloud_sandbox"
    assert result["data"]["requires_cloud_sandbox"] is True
    assert result["data"]["grant_id"] == "test-grant"


def test_exec_command_reports_timeout_during_initial_wait(tmp_path: Path) -> None:
    """首轮等待超过 timeout 时终止会话并报告超时。"""
    command = write_script(
        tmp_path,
        "slow_start.py",
        "\n".join(
            [
                "import time",
                "print('slow-ready', flush=True)",
                "time.sleep(30)",
                "",
            ]
        ),
    )

    result = run_async(
        NativeCoding(root=tmp_path).exec_command(
            command=command,
            cwd=".",
            yield_time_ms=1500,
            timeout_sec=1,
            idle_timeout_sec=20,
            execution=approved_execution(),
        )
    )

    assert result["ok"] is False
    assert result["data"]["tool"] == "exec_command"
    assert result["data"]["status"] == "exited"
    assert result["data"]["timed_out"] is True
    assert result["data"]["reason"] == "command_timed_out"
    assert "slow-ready" in result["data"]["stdout"]


def test_exec_command_truncates_large_initial_output(tmp_path: Path) -> None:
    """初始输出超过限制时返回截断标记。"""
    command = write_script(
        tmp_path,
        "large_output.py",
        "\n".join(
            [
                "import time",
                "print('x' * 3000, flush=True)",
                "time.sleep(30)",
                "",
            ]
        ),
    )

    async def scenario() -> None:
        coding = NativeCoding(root=tmp_path)
        result = await coding.exec_command(
            command=command,
            cwd=".",
            yield_time_ms=1000,
            max_output_chars=1024,
            timeout_sec=20,
            idle_timeout_sec=20,
            execution=approved_execution(),
        )
        session_id = result["data"].get("session_id")

        try:
            assert result["ok"] is True
            assert result["data"]["status"] == "running"
            assert result["data"]["stdout_truncated"] is True
            assert result["data"]["truncated"] is True
            assert "...[truncated " in result["data"]["stdout"]
        finally:
            await terminate_session(coding, session_id)

    run_async(scenario())


def test_exec_command_starts_session_and_write_stdin_completes(tmp_path: Path) -> None:
    """会话能返回首批输出、接收 stdin，并在退出时汇总文件审计。"""
    command = write_script(
        tmp_path,
        "interactive.py",
        "\n".join(
            [
                "import sys",
                "from pathlib import Path",
                "print('ready', flush=True)",
                "for raw in sys.stdin:",
                "    line = raw.rstrip('\\n')",
                "    if line == 'write':",
                "        Path('from_exec.txt').write_text('created', encoding='utf-8')",
                "        print('wrote-file', flush=True)",
                "    elif line == 'quit':",
                "        print('bye', flush=True)",
                "        break",
                "    else:",
                "        print(f'echo:{line}', flush=True)",
                "print('done', flush=True)",
                "",
            ]
        ),
    )

    async def scenario() -> None:
        coding = NativeCoding(root=tmp_path)
        cid = "cid-main"
        owner_sid = "sid-main"
        start = await coding.exec_command(
            command=command,
            cwd=".",
            yield_time_ms=500,
            timeout_sec=10,
            idle_timeout_sec=10,
            execution=approved_execution(),
            cid=cid,
            sid=owner_sid,
        )
        session_id = start["data"].get("session_id")

        try:
            assert start["ok"] is True
            assert start["data"]["tool"] == "exec_command"
            assert start["data"]["status"] == "running"
            assert str(session_id).startswith("exec_")
            assert "ready" in start["data"]["stdout"]
            assert coding.last_shell_result == start["data"]

            echo = await coding.write_stdin(
                session_id=session_id,
                stdin="hello\n",
                wait_ms=500,
                execution=write_execution(
                    session_id=session_id, stdin="hello\n", wait_ms=500
                ),
                cid=cid,
                sid=owner_sid,
                call_id="call-hello",
            )
            assert echo["ok"] is True
            assert echo["data"]["tool"] == "write_stdin"
            assert echo["data"]["status"] == "running"
            assert echo["data"]["stdin_written"] == len("hello\n")
            assert "echo:hello" in echo["data"]["stdout"]

            wrote = await coding.write_stdin(
                session_id=session_id,
                stdin="write\n",
                wait_ms=500,
                execution=write_execution(
                    session_id=session_id, stdin="write\n", wait_ms=500
                ),
                cid=cid,
                sid=owner_sid,
                call_id="call-write",
            )
            assert wrote["ok"] is True
            assert "wrote-file" in wrote["data"]["stdout"]
            assert (tmp_path / "from_exec.txt").read_text(encoding="utf-8") == "created"

            finished = await coding.write_stdin(
                session_id=session_id,
                stdin="quit\n",
                wait_ms=1000,
                execution=write_execution(
                    session_id=session_id, stdin="quit\n", wait_ms=1000
                ),
                cid=cid,
                sid=owner_sid,
                call_id="call-quit",
            )
            assert finished["ok"] is True
            assert finished["data"]["status"] == "exited"
            assert finished["data"]["exit_code"] == 0
            assert "bye" in finished["data"]["stdout"]
            assert "done" in finished["data"]["stdout"]
            assert finished["data"]["shell_write_detected"] is False
            assert finished["data"]["shell_file_changes"]["changed"] is False
            assert coding.last_shell_result == finished["data"]
            assert coding.validation_history[-1] == finished["data"]

            missing = await coding.write_stdin(
                session_id=session_id,
                stdin="after-exit\n",
                wait_ms=0,
            )
            assert missing["ok"] is False
            assert missing["data"]["reason"] == "exec_session_not_found"
        finally:
            await terminate_session(coding, session_id, cid=cid, sid=owner_sid)

    run_async(scenario())


def test_write_stdin_can_poll_incremental_output(tmp_path: Path) -> None:
    """stdin 为空时 write_stdin 只轮询增量输出。"""
    command = write_script(
        tmp_path,
        "delayed.py",
        "\n".join(
            [
                "import time",
                "print('first', flush=True)",
                "time.sleep(0.3)",
                "print('second', flush=True)",
                "",
            ]
        ),
    )

    async def scenario() -> None:
        coding = NativeCoding(root=tmp_path)
        start = await coding.exec_command(
            command=command,
            cwd=".",
            yield_time_ms=50,
            timeout_sec=5,
            execution=approved_execution(),
        )
        session_id = start["data"].get("session_id")

        try:
            assert start["ok"] is True
            assert start["data"]["status"] == "running"

            polled = await coding.write_stdin(
                session_id=session_id,
                stdin="",
                wait_ms=1000,
            )
            assert polled["ok"] is True
            assert polled["data"]["status"] == "exited"
            assert polled["data"]["stdin_written"] == 0
            assert "second" in polled["data"]["stdout"]
        finally:
            await terminate_session(coding, session_id)

    run_async(scenario())


def test_exec_session_output_snapshot_does_not_drain_output(tmp_path: Path) -> None:
    """只读快照查看输出时不消费 write_stdin 的增量缓冲。"""
    command = write_script(
        tmp_path,
        "snapshot_tail.py",
        "\n".join(
            [
                "import time",
                "print('first', flush=True)",
                "time.sleep(0.3)",
                "print('second', flush=True)",
                "time.sleep(2.0)",
                "",
            ]
        ),
    )

    async def scenario() -> None:
        coding = NativeCoding(root=tmp_path)
        start = await coding.exec_command(
            command=command,
            cwd=".",
            yield_time_ms=50,
            timeout_sec=5,
            execution=approved_execution(),
        )
        session_id = start["data"].get("session_id")

        try:
            assert start["ok"] is True
            assert start["data"]["status"] == "running"

            snapshot: dict[str, object] = {}
            for _ in range(20):
                snapshot = await coding.exec_session_output_snapshot(
                    session_id=str(session_id),
                    max_output_chars=12000,
                )
                display_text = "\n".join(
                    str(line) for line in snapshot.get("output_lines") or []
                )
                if "second" in display_text:
                    break
                await asyncio.sleep(0.1)

            assert snapshot["ok"] is True
            display_text = "\n".join(str(line) for line in snapshot["output_lines"])
            assert "first" in display_text
            assert "second" in display_text

            polled = await coding.write_stdin(
                session_id=session_id,
                stdin="",
                wait_ms=0,
            )
            assert polled["ok"] is True
            assert "second" in polled["data"]["stdout"]

            after_poll = await coding.exec_session_output_snapshot(
                session_id=str(session_id),
                max_output_chars=12000,
            )
            after_poll_text = "\n".join(str(line) for line in after_poll["output_lines"])
            assert "first" in after_poll_text
            assert "second" in after_poll_text
        finally:
            await terminate_session(coding, session_id)

    run_async(scenario())


def test_exec_command_records_output_lines_in_receive_order(tmp_path: Path) -> None:
    """exec_command 按接收顺序保留 stdout/stderr 混合输出。"""
    command = write_script(
        tmp_path,
        "ordered_exec_output.py",
        "\n".join(
            [
                "import sys",
                "sys.stderr.write('stderr-first\\n')",
                "sys.stderr.flush()",
                "sys.stdout.write('stdout-second\\n')",
                "sys.stdout.flush()",
                "",
            ]
        ),
    )

    async def scenario() -> None:
        coding = NativeCoding(root=tmp_path)
        result = await coding.exec_command(
            command=command,
            cwd=".",
            yield_time_ms=1000,
            timeout_sec=5,
            execution=approved_execution(),
        )

        assert result["ok"] is True
        assert result["data"]["status"] == "exited"
        assert result["data"]["stdout"].strip() == "stdout-second"
        assert result["data"]["stderr"].strip() == "stderr-first"
        assert result["data"]["output_lines"] == ["stderr-first", "stdout-second"]

    run_async(scenario())


def test_running_exec_sessions_snapshot_updates_after_terminate(tmp_path: Path) -> None:
    """running exec 快照随会话启动和终止更新。"""
    command = write_script(
        tmp_path,
        "snapshot_wait.py",
        "\n".join(
            [
                "import time",
                "print('snapshot-ready', flush=True)",
                "time.sleep(30)",
                "",
            ]
        ),
    )

    async def scenario() -> None:
        coding = NativeCoding(root=tmp_path)
        start = await coding.exec_command(
            command=command,
            cwd=".",
            yield_time_ms=500,
            timeout_sec=20,
            idle_timeout_sec=20,
            execution=approved_execution(),
        )
        session_id = start["data"].get("session_id")

        try:
            snapshot = await coding.running_exec_sessions()
            assert snapshot["count"] == 1
            assert snapshot["items"][0]["session_id"] == session_id
            assert snapshot["items"][0]["command"] == command

            stopped = await coding.write_stdin(
                session_id=session_id,
                control="terminate",
                wait_ms=1000,
            )
            assert stopped["ok"] is True

            empty = await coding.running_exec_sessions()
            assert empty == {"count": 0, "items": []}
        finally:
            await terminate_session(coding, session_id)

    run_async(scenario())


def test_finalize_cancels_reader_after_drain_timeout() -> None:
    """进程已退出但 reader 不结束时，会在有限时间内取消 reader。"""

    class ExitedProcess:
        returncode = 0
        pid = 12345

    async def never_finishes() -> None:
        await asyncio.Event().wait()

    async def scenario() -> None:
        original_timeout = ExecCommandTools.IO_DRAIN_TIMEOUT_SEC
        ExecCommandTools.IO_DRAIN_TIMEOUT_SEC = 0.05
        try:
            session = ExecSession(
                session_id="exec_test",
                owner_cid="cid",
                owner_sid="sid",
                command="test",
                cwd=".",
                process=ExitedProcess(),
                runtime={},
                started_at=time.time(),
                expires_at=time.time() + 10,
                idle_timeout_sec=10,
                audit_mode="off",
                audit_before=None,
            )
            reader = asyncio.create_task(never_finishes())
            session.stdout_task = reader

            started = time.perf_counter()
            await ExecCommandTools._finalize_if_exited(session)
            elapsed = time.perf_counter() - started

            assert session.finalized is True
            assert reader.cancelled() is True
            assert elapsed < 1
        finally:
            ExecCommandTools.IO_DRAIN_TIMEOUT_SEC = original_timeout

    run_async(scenario())


def test_terminate_kills_process_tree_with_inherited_output_pipe(tmp_path: Path) -> None:
    """terminate 会处理仍持有输出管道的子进程。"""
    write_script(
        tmp_path,
        "pipe_holder.py",
        "\n".join(
            [
                "import time",
                "print('child-ready', flush=True)",
                "time.sleep(30)",
                "",
            ]
        ),
    )
    command = write_script(
        tmp_path,
        "spawn_holder.py",
        "\n".join(
            [
                "import subprocess",
                "import sys",
                "import time",
                "child = subprocess.Popen([sys.executable, '-u', 'pipe_holder.py'])",
                "print(f'parent-ready:{child.pid}', flush=True)",
                "time.sleep(30)",
                "",
            ]
        ),
    )

    async def scenario() -> None:
        coding = NativeCoding(root=tmp_path)
        start = await coding.exec_command(
            command=command,
            cwd=".",
            yield_time_ms=800,
            timeout_sec=20,
            idle_timeout_sec=20,
            execution=approved_execution(),
        )
        session_id = start["data"].get("session_id")

        try:
            assert start["ok"] is True
            assert start["data"]["status"] == "running"
            assert "parent-ready" in start["data"]["stdout"]

            started = time.perf_counter()
            stopped = await asyncio.wait_for(
                coding.write_stdin(
                    session_id=session_id,
                    control="terminate",
                    wait_ms=1000,
                ),
                timeout=6,
            )
            elapsed = time.perf_counter() - started

            assert stopped["ok"] is True
            assert stopped["data"]["status"] == "exited"
            assert stopped["data"]["control"] == "terminate"
            assert elapsed < 6
        finally:
            try:
                await asyncio.wait_for(
                    coding.write_stdin(
                        session_id=session_id,
                        control="kill",
                        wait_ms=0,
                    ),
                    timeout=6,
                )
            except (asyncio.TimeoutError, AssertionError):
                pass

    run_async(scenario())


def test_write_stdin_reports_closed_stdin(tmp_path: Path) -> None:
    """stdin 已关闭但进程仍运行时，写入返回 stdin closed。"""
    command = write_script(
        tmp_path,
        "ignore_stdin.py",
        "\n".join(
            [
                "import time",
                "print('ignoring', flush=True)",
                "time.sleep(30)",
                "",
            ]
        ),
    )

    async def scenario() -> None:
        coding = NativeCoding(root=tmp_path)
        cid = "cid-closed"
        owner_sid = "sid-closed"
        start = await coding.exec_command(
            command=command,
            cwd=".",
            yield_time_ms=500,
            timeout_sec=20,
            idle_timeout_sec=20,
            execution=approved_execution(),
            cid=cid,
            sid=owner_sid,
        )
        session_id = start["data"].get("session_id")

        try:
            assert start["ok"] is True
            assert start["data"]["status"] == "running"

            closed = await coding.write_stdin(
                session_id=session_id,
                control="eof",
                wait_ms=0,
                cid=cid,
                sid=owner_sid,
            )
            assert closed["ok"] is True
            assert closed["data"]["status"] == "running"

            result = await coding.write_stdin(
                session_id=session_id,
                stdin="after-eof\n",
                wait_ms=0,
                execution=write_execution(
                    session_id=session_id, stdin="after-eof\n", wait_ms=0
                ),
                cid=cid,
                sid=owner_sid,
                call_id="call-after-eof",
            )

            assert result["ok"] is False
            assert result["data"]["reason"] == "exec_stdin_closed"
            assert result["data"]["session_id"] == session_id
        finally:
            await terminate_session(coding, session_id, cid=cid, sid=owner_sid)

    run_async(scenario())


def test_write_stdin_kill_control_exits_session(tmp_path: Path) -> None:
    """kill 控制会强制结束仍在运行的会话。"""
    command = write_script(
        tmp_path,
        "kill_wait.py",
        "\n".join(
            [
                "import time",
                "print('kill-ready', flush=True)",
                "time.sleep(30)",
                "",
            ]
        ),
    )

    async def scenario() -> None:
        coding = NativeCoding(root=tmp_path)
        start = await coding.exec_command(
            command=command,
            cwd=".",
            yield_time_ms=500,
            timeout_sec=20,
            idle_timeout_sec=20,
            execution=approved_execution(),
        )
        session_id = start["data"].get("session_id")

        stopped = await coding.write_stdin(
            session_id=session_id,
            control="kill",
            wait_ms=1000,
        )

        assert stopped["ok"] is True
        assert stopped["data"]["status"] == "exited"
        assert stopped["data"]["control"] == "kill"
        assert stopped["data"]["session_id"] == session_id

    run_async(scenario())


def test_write_stdin_idle_cleanup_removes_session(tmp_path: Path) -> None:
    """空闲超时会在下一次访问时清理会话。"""
    command = write_script(
        tmp_path,
        "idle_wait.py",
        "\n".join(
            [
                "import time",
                "print('idle-ready', flush=True)",
                "time.sleep(30)",
                "",
            ]
        ),
    )

    async def scenario() -> None:
        coding = NativeCoding(root=tmp_path)
        start = await coding.exec_command(
            command=command,
            cwd=".",
            yield_time_ms=500,
            timeout_sec=20,
            idle_timeout_sec=1,
            execution=approved_execution(),
        )
        session_id = start["data"].get("session_id")

        assert start["ok"] is True
        assert start["data"]["status"] == "running"

        await asyncio.sleep(1.2)

        result = await coding.write_stdin(
            session_id=session_id,
            stdin="after-idle\n",
            wait_ms=0,
        )

        assert result["ok"] is False
        assert result["data"]["reason"] == "exec_session_not_found"
        assert result["data"]["session_id"] == session_id

    run_async(scenario())


def test_write_stdin_rejects_poll_and_control_from_other_owner(tmp_path: Path) -> None:
    """轮询和控制动作不能访问其他服务端会话创建的进程。"""
    command = write_script(
        tmp_path,
        "owner_wait.py",
        "import time\nprint('owner-ready', flush=True)\ntime.sleep(30)\n",
    )

    async def scenario() -> None:
        coding = NativeCoding(root=tmp_path)
        start = await coding.exec_command(
            command=command,
            yield_time_ms=500,
            timeout_sec=20,
            execution=approved_execution(),
            cid="cid-owner",
            sid="sid-owner",
        )
        session_id = start["data"].get("session_id")

        try:
            poll = await coding.write_stdin(
                session_id=session_id,
                cid="cid-other",
                sid="sid-owner",
                wait_ms=0,
            )
            assert poll["ok"] is False
            assert poll["data"]["reason"] == "exec_session_owner_mismatch"

            control = await coding.write_stdin(
                session_id=session_id,
                cid="cid-owner",
                sid="sid-other",
                control="terminate",
                wait_ms=0,
            )
            assert control["ok"] is False
            assert control["data"]["reason"] == "exec_session_owner_mismatch"
            assert start["data"]["status"] == "running"
        finally:
            await terminate_session(
                coding, session_id, cid="cid-owner", sid="sid-owner"
            )

    run_async(scenario())


def test_nonempty_stdin_requires_canonical_grant_and_rejects_replay(tmp_path: Path) -> None:
    """非空 stdin 在写入前校验 canonical、grant 和本地防重放状态。"""
    command = write_script(
        tmp_path,
        "grant_wait.py",
        "import sys\nprint('grant-ready', flush=True)\nfor line in sys.stdin:\n print(line, flush=True)\n",
    )

    async def scenario() -> None:
        coding = NativeCoding(root=tmp_path)
        start = await coding.exec_command(
            command=command,
            yield_time_ms=500,
            timeout_sec=20,
            execution=approved_execution(),
            cid="cid-grant",
            sid="sid-grant",
        )
        session_id = start["data"].get("session_id")

        try:
            missing = await coding.write_stdin(
                session_id=session_id,
                stdin="missing\n",
                wait_ms=0,
                cid="cid-grant",
                sid="sid-grant",
                call_id="call-missing",
            )
            assert missing["data"]["reason"] == "execution_metadata_required"

            mismatch_execution = write_execution(
                session_id=session_id, stdin="authorized\n", wait_ms=0
            )
            mismatch = await coding.write_stdin(
                session_id=session_id,
                stdin="tampered\n",
                wait_ms=0,
                execution=mismatch_execution,
                cid="cid-grant",
                sid="sid-grant",
                call_id="call-mismatch",
            )
            assert mismatch["data"]["reason"] == "execution_canonical_arguments_mismatch"

            execution = write_execution(
                session_id=session_id, stdin="accepted\n", wait_ms=200
            )
            accepted = await coding.write_stdin(
                session_id=session_id,
                stdin="accepted\n",
                wait_ms=200,
                execution=execution,
                cid="cid-grant",
                sid="sid-grant",
                call_id="call-accepted",
            )
            assert accepted["ok"] is True

            replay = await coding.write_stdin(
                session_id=session_id,
                stdin="accepted\n",
                wait_ms=200,
                execution=execution,
                cid="cid-grant",
                sid="sid-grant",
                call_id="call-replay",
            )
            assert replay["data"]["reason"] == "execution_grant_reused"
        finally:
            await terminate_session(
                coding, session_id, cid="cid-grant", sid="sid-grant"
            )

    run_async(scenario())


def test_write_stdin_reports_missing_session_id(tmp_path: Path) -> None:
    """空 session_id 返回稳定失败原因。"""
    result = run_async(
        NativeCoding(root=tmp_path).write_stdin(
            session_id="",
            stdin="ignored\n",
        )
    )

    assert result["ok"] is False
    assert result["data"]["reason"] == "session_id_empty"
    assert result["data"]["tool"] == "write_stdin"


def test_write_stdin_reports_unknown_session(tmp_path: Path) -> None:
    """不存在的 session_id 返回稳定失败原因。"""
    result = run_async(
        NativeCoding(root=tmp_path).write_stdin(
            session_id="exec_missing",
            stdin="ignored\n",
        )
    )

    assert result["ok"] is False
    assert result["data"]["reason"] == "exec_session_not_found"
    assert result["data"]["session_id"] == "exec_missing"


def test_write_stdin_rejects_invalid_control(tmp_path: Path) -> None:
    """非法控制动作不会写入或影响会话。"""
    command = write_script(
        tmp_path,
        "wait.py",
        "\n".join(
            [
                "import time",
                "print('waiting', flush=True)",
                "time.sleep(5)",
                "",
            ]
        ),
    )

    async def scenario() -> None:
        coding = NativeCoding(root=tmp_path)
        start = await coding.exec_command(
            command=command,
            cwd=".",
            yield_time_ms=500,
            timeout_sec=10,
            execution=approved_execution(),
        )
        session_id = start["data"].get("session_id")

        try:
            invalid = await coding.write_stdin(
                session_id=session_id,
                control="pause",
                wait_ms=0,
            )
            assert invalid["ok"] is False
            assert invalid["data"]["reason"] == "exec_control_invalid"
            assert invalid["data"]["session_id"] == session_id
        finally:
            await terminate_session(coding, session_id)

    run_async(scenario())


def test_write_stdin_eof_closes_stdin_and_exits(tmp_path: Path) -> None:
    """EOF 控制会关闭 stdin 并允许读取剩余输出。"""
    command = write_script(
        tmp_path,
        "read_all.py",
        "\n".join(
            [
                "import sys",
                "print('start', flush=True)",
                "payload = sys.stdin.read()",
                "print(f'len:{len(payload)}', flush=True)",
                "",
            ]
        ),
    )

    async def scenario() -> None:
        coding = NativeCoding(root=tmp_path)
        start = await coding.exec_command(
            command=command,
            cwd=".",
            yield_time_ms=500,
            timeout_sec=10,
            execution=approved_execution(),
        )
        session_id = start["data"].get("session_id")

        try:
            assert start["ok"] is True
            assert start["data"]["status"] == "running"
            assert "start" in start["data"]["stdout"]

            closed = await coding.write_stdin(
                session_id=session_id,
                control="eof",
                wait_ms=1000,
            )
            assert closed["ok"] is True
            assert closed["data"]["control"] == "eof"
            assert closed["data"]["stdin_written"] == 0
            assert closed["data"]["status"] == "exited"
            assert "len:0" in closed["data"]["stdout"]
        finally:
            await terminate_session(coding, session_id)

    run_async(scenario())


def test_exec_command_and_write_stdin_client_tool_handlers(tmp_path: Path) -> None:
    """客户端内置工具只执行可信 runtime 中的 canonical 参数。"""
    command = write_script(
        tmp_path,
        "tool_entry.py",
        "\n".join(
            [
                "import sys",
                "print('tool-ready', flush=True)",
                "line = sys.stdin.readline().rstrip('\\n')",
                "print(f'tool-got:{line}', flush=True)",
                "",
            ]
        ),
    )

    async def scenario() -> None:
        coding = NativeCoding(root=tmp_path)
        tools = coding_tools(coding)
        assert [tool.name for tool in tools] == [
            "shell_command",
            "exec_command",
            "write_stdin",
            "apply_patch",
        ]
        exec_tool = tool_by_name(tools, "exec_command")
        stdin_tool = tool_by_name(tools, "write_stdin")
        cid = "cid-handler"
        owner_sid = "sid-handler"
        runtime = ClientToolRuntime(
            session=None,
            execution=approved_execution(
                grantId="exec-handler",
                canonicalArguments={
                    "command": command,
                    "cwd": ".",
                    "yield_time_ms": 500,
                    "max_output_chars": 24000,
                    "timeout_sec": 10,
                    "idle_timeout_sec": 300,
                },
            ),
            cid=cid,
            sid=owner_sid,
            call_id="call-exec",
        )
        session_id: object = ""

        assert exec_tool.input_schema["required"] == ["command"]
        assert stdin_tool.input_schema["required"] == ["session_id"]
        assert all("execution" not in tool.input_schema["properties"] for tool in tools)

        try:
            start = await exec_tool.handler(
                {
                    "command": "model-command-is-not-executed",
                },
                runtime,
            )
            start_structured = start.structuredContent or {}
            session_id = start_structured["data"].get("session_id")

            assert start.isError is False
            assert start_structured["ok"] is True
            assert start_structured["tool"] == "exec_command"
            assert start_structured["args"]["command"] == command
            assert start_structured["args"]["yield_time_ms"] == 500
            assert "execution" not in start_structured["args"]
            assert start_structured["data"]["status"] == "running"
            assert "tool-ready" in start_structured["data"]["stdout"]

            runtime.execution = write_execution(
                session_id=session_id,
                stdin="handler\n",
                wait_ms=2000,
            )
            runtime.call_id = "call-stdin"
            finished = await stdin_tool.handler(
                {
                    "session_id": session_id,
                    "stdin": "model-input-is-not-written\n",
                    "wait_ms": 2000,
                },
                runtime,
            )
            finished_structured = finished.structuredContent or {}

            assert finished.isError is False
            assert finished_structured["ok"] is True
            assert finished_structured["tool"] == "write_stdin"
            assert finished_structured["args"]["session_id"] == session_id
            assert finished_structured["args"]["stdin"] == "handler\n"
            assert finished_structured["data"]["status"] == "exited"
            assert "tool-got:handler" in finished_structured["data"]["stdout"]
        finally:
            await terminate_session(coding, session_id, cid=cid, sid=owner_sid)

    run_async(scenario())


def test_exec_command_handler_rejects_model_execution(tmp_path: Path) -> None:
    """模型 arguments 中出现 execution 时拒绝启动进程。"""
    command = write_script(tmp_path, "forbidden.py", "print('not-run')\n")
    tool = tool_by_name(coding_tools(NativeCoding(root=tmp_path)), "exec_command")
    runtime = ClientToolRuntime(
        session=None,
        execution=approved_execution(
            grantId="exec-forbidden",
            canonicalArguments={
                "command": command,
                "cwd": ".",
                "yield_time_ms": 1000,
                "max_output_chars": 24000,
                "timeout_sec": 10,
                "idle_timeout_sec": 300,
            },
        ),
        cid="cid-forbidden",
        sid="sid-forbidden",
        call_id="call-forbidden",
    )

    result = run_async(
        tool.handler({"command": command, "execution": {"grantId": "forged"}}, runtime)
    )
    structured = result.structuredContent or {}

    assert result.isError is True
    assert structured["data"]["reason"] == "model_execution_forbidden"
