import asyncio
import os
import shlex
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import jsonschema
import pytest

from agent.application.tools.coding_schemas import EXEC_COMMAND_INPUT_SCHEMA
from agent.application.tools.coding_schemas import WRITE_STDIN_INPUT_SCHEMA
from agent.application.views.builders.tools import build_native_tool_result_view
from agent.ports import CapabilityError
from agent.ports import InteractiveProcessSpec
from agent.ports import OutputSurfaceContext
from agent.ports import TerminalSize
from frontends.tui.adapters.session import create_tui_output_session
from frontends.tui.core.runtime import TuiRuntime
from infrastructure.config.paths import ApplicationLayout
from infrastructure.config.paths import resolve_application_layout
from infrastructure.platform.process_sessions import ProcessSession
from infrastructure.platform.process_sessions import ProcessSessionManager
from infrastructure.platform.process_sessions import ProcessSessionSpec
from infrastructure.platform.pty import LocalInteractiveProcessCapability
from infrastructure.workspace.commands.process import ProcessCommandExecutor
from mind import create_workspace_coding


pytestmark = pytest.mark.pty_acceptance


class _ResizeFailureHandle:
    """为公开工具失败路径提供可控的交互句柄。"""

    def __init__(self) -> None:
        self.session_id = "interactive_process_resize_failure"
        self.pid: int | None = 1234
        self.returncode: int | None = None
        self._exited = asyncio.Event()

    async def read_output(self) -> AsyncIterator[bytes]:
        await self._exited.wait()
        if False:
            yield b""

    async def write(self, data: str, *, eof: bool = False) -> None:
        del data, eof

    async def interrupt(self) -> None:
        return None

    async def resize(self, size: TerminalSize) -> None:
        del size
        raise CapabilityError(
            "interactive_process_resize_failed",
            "synthetic resize failure",
        )

    async def wait(self) -> int:
        await self._exited.wait()
        return int(self.returncode if self.returncode is not None else -1)

    async def terminate(self, *, force: bool = False) -> None:
        self.returncode = -9 if force else -15
        self._exited.set()

    async def aclose(self) -> None:
        if self.returncode is None:
            await self.terminate(force=True)


class _ResizeFailureCapability:
    """返回 resize 失败句柄并提供幂等关闭。"""

    def __init__(self) -> None:
        self.handle: _ResizeFailureHandle | None = None

    async def spawn(self, spec: InteractiveProcessSpec) -> _ResizeFailureHandle:
        del spec
        self.handle = _ResizeFailureHandle()
        return self.handle

    async def aclose(self) -> None:
        if self.handle is not None:
            await self.handle.aclose()


def _python_command(script: Path) -> tuple[str, str]:
    argv = [sys.executable, "-u", str(script)]
    if os.name == "nt":
        return subprocess.list2cmdline(argv), os.environ.get("COMSPEC", "cmd.exe")
    return shlex.join(argv), os.environ.get("SHELL", "/bin/sh")


def _sandbox_layout_or_skip() -> ApplicationLayout:
    """返回当前平台的真实 sandbox 布局，产物缺失时跳过。"""
    root = Path(__file__).resolve().parents[1]
    layout = resolve_application_layout(
        entry_file=root / "mind.py",
        argv0="mind.py",
        platform=sys.platform,
    )
    platform_directory = "windows" if os.name == "nt" else "macos"
    executable_name = (
        "mind_sandbox_server.exe"
        if os.name == "nt"
        else "mind_sandbox_server"
    )
    sidecar = (
        root
        / "schematic"
        / "sandbox"
        / platform_directory
        / "bin"
        / executable_name
    )
    if not sidecar.is_file():
        pytest.skip(f"sandbox sidecar is unavailable: {sidecar}")
    return layout


async def _wait_until_session_finalized(
    session: ProcessSession,
    *,
    timeout: float = 5.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not session.finalized:
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("timed out waiting for process session finalization")
        await asyncio.sleep(0.02)


def test_exec_command_schema_exposes_opt_in_pty_and_resize() -> None:
    properties = EXEC_COMMAND_INPUT_SCHEMA["properties"]
    assert properties["tty"]["default"] is False
    assert properties["terminal_rows"]["default"] == 24
    assert properties["terminal_columns"]["default"] == 80
    assert properties["yield_time_ms"]["default"] == 10_000
    assert properties["yield_time_ms"]["minimum"] == 0
    assert properties["yield_time_ms"]["maximum"] == 30_000

    write_properties = WRITE_STDIN_INPUT_SCHEMA["properties"]
    assert write_properties["wait_ms"]["default"] == 250
    assert write_properties["wait_ms"]["minimum"] == 0
    assert write_properties["wait_ms"]["maximum"] == 300_000

    jsonschema.validate(
        {
            "session_id": "exec_test",
            "control": "resize",
            "terminal_rows": 31,
            "terminal_columns": 101,
        },
        WRITE_STDIN_INPUT_SCHEMA,
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"session_id": "exec_test", "control": "resize"},
            WRITE_STDIN_INPUT_SCHEMA,
        )


def test_process_command_wait_bounds_match_codex() -> None:
    assert ProcessCommandExecutor._resolve_exec_yield_time(
        None,
        windows=True,
    ) == 10_000
    assert ProcessCommandExecutor._resolve_exec_yield_time(
        0,
        windows=True,
    ) == 10_000
    assert ProcessCommandExecutor._resolve_exec_yield_time(
        0,
        windows=False,
    ) == 250
    assert ProcessCommandExecutor._resolve_exec_yield_time(
        60_000,
        windows=False,
    ) == 30_000

    assert ProcessCommandExecutor._resolve_write_wait_time(
        0,
        input_text="input",
        control="none",
    ) == 250
    assert ProcessCommandExecutor._resolve_write_wait_time(
        60_000,
        input_text="input",
        control="none",
    ) == 30_000
    assert ProcessCommandExecutor._resolve_write_wait_time(
        0,
        input_text="",
        control="none",
    ) == 5_000
    assert ProcessCommandExecutor._resolve_write_wait_time(
        500_000,
        input_text="",
        control="none",
    ) == 300_000
    assert ProcessCommandExecutor._resolve_write_wait_time(
        0,
        input_text="",
        control="interrupt",
    ) == 250


def test_process_session_event_chunks_preserve_utf8_boundaries() -> None:
    payload = b"x" * 8191 + "🙂".encode("utf-8") + b"tail"

    chunks = ProcessSessionManager._bounded_event_chunks(payload)

    assert b"".join(chunks) == payload
    assert all(len(chunk) <= 8192 for chunk in chunks)
    assert "".join(chunk.decode("utf-8") for chunk in chunks) == (
        "x" * 8191 + "🙂tail"
    )


@pytest.mark.anyio
async def test_exec_command_pty_supports_input_resize_and_cursor(tmp_path) -> None:
    script = tmp_path / "interactive.py"
    script.write_text(
        "import sys\n"
        "import shutil\n"
        "print(f'ISATTY={sys.stdin.isatty()}:'"
        "      f'{sys.stdout.isatty()}:{sys.stderr.isatty()}', flush=True)\n"
        "value = input('READY>')\n"
        "print('VALUE=' + value, flush=True)\n"
        "input('RESIZE>')\n"
        "size = shutil.get_terminal_size()\n"
        "print(f'SIZE={size.lines}x{size.columns}', flush=True)\n",
        encoding="utf-8",
    )
    command, shell = _python_command(script)
    capability = LocalInteractiveProcessCapability()
    coding = create_workspace_coding(
        root=tmp_path,
        application_layout=None,
        interactive_process_capability=capability,
        network_access="enabled",
    )
    try:
        started = await coding.exec_command(
            command=command,
            shell=shell,
            tty=True,
            terminal_rows=24,
            terminal_columns=80,
            yield_time_ms=100,
            timeout_sec=30,
            cid="cid_test",
            sid="sid_test",
        )
        assert started["ok"] is True
        assert started["data"]["status"] == "running"
        assert started["data"]["pty"] is True
        assert started["data"]["pty_fallback"] is False
        assert started["data"]["execution_backend"] == "native-pty"
        assert started["data"]["yield_time_ms"] == (
            10_000 if os.name == "nt" else 250
        )
        assert started["data"]["stderr"] == ""
        assert "ISATTY=True:True:True" in started["data"]["output"]
        session_id = started["data"]["session_id"]
        running = await coding.running_exec_sessions()
        assert running["items"][0]["pty"] is True
        assert running["items"][0]["terminal_rows"] == 24
        assert running["items"][0]["terminal_columns"] == 80

        interacted = await coding.write_stdin(
            session_id=session_id,
            stdin="hello\r",
            wait_ms=1000,
            cid="cid_test",
            sid="sid_test",
        )
        assert interacted["ok"] is True
        assert interacted["data"]["status"] == "running"
        assert interacted["data"]["pty"] is True
        assert "VALUE=hello" in interacted["data"]["output"]

        resized = await coding.write_stdin(
            session_id=session_id,
            control="resize",
            terminal_rows=31,
            terminal_columns=101,
            wait_ms=0,
            cid="cid_test",
            sid="sid_test",
        )
        assert resized["ok"] is True
        assert resized["data"]["terminal_rows"] == 31
        assert resized["data"]["terminal_columns"] == 101

        completed = await coding.write_stdin(
            session_id=session_id,
            stdin="\r",
            wait_ms=3000,
            cid="cid_test",
            sid="sid_test",
        )
        assert completed["ok"] is True
        assert completed["data"]["status"] == "exited"
        assert completed["data"]["exit_code"] == 0
        assert "SIZE=31x101" in completed["data"]["output"]
        output_before_resize = "".join(
            result["data"]["output"]
            for result in (started, interacted)
        )
        assert output_before_resize.count("VALUE=hello") == 1
        assert completed["data"]["output"].count("SIZE=31x101") == 1
    finally:
        await coding.close()
        await capability.aclose()


@pytest.mark.anyio
async def test_exec_command_fast_pty_exit_does_not_become_background(tmp_path) -> None:
    script = tmp_path / "fast_exit.py"
    script.write_text(
        "print('FAST-EXIT', flush=True)\n",
        encoding="utf-8",
    )
    command, shell = _python_command(script)
    capability = LocalInteractiveProcessCapability()
    coding = create_workspace_coding(
        root=tmp_path,
        application_layout=None,
        interactive_process_capability=capability,
        network_access="enabled",
    )
    try:
        result = await coding.exec_command(
            command=command,
            shell=shell,
            tty=True,
            timeout_sec=30,
            cid="cid_test",
            sid="sid_test",
        )
        running = await coding.running_exec_sessions()
    finally:
        await coding.close()
        await capability.aclose()

    assert result["ok"] is True
    assert result["data"]["status"] == "exited"
    assert result["data"]["yield_time_ms"] == 10_000
    assert result["data"]["output"].count("FAST-EXIT") == 1
    assert not running["items"]


@pytest.mark.anyio
async def test_exec_command_pty_never_falls_back_to_pipe(tmp_path) -> None:
    command, shell = _python_command(tmp_path / "missing.py")
    coding = create_workspace_coding(
        root=tmp_path,
        application_layout=None,
        network_access="enabled",
    )
    try:
        result = await coding.exec_command(
            command=command,
            shell=shell,
            tty=True,
            sandbox_mode="danger-full-access",
        )
    finally:
        await coding.close()

    assert result["ok"] is False
    assert result["data"]["reason"] == "interactive_process_unavailable"
    assert result["data"]["execution_backend"] == "native-pty"


@pytest.mark.anyio
async def test_exec_command_pty_delivers_terminal_eof(tmp_path) -> None:
    script = tmp_path / "terminal_eof.py"
    script.write_text(
        "import sys\n"
        "print('EOF-READY', flush=True)\n"
        "sys.stdin.read()\n"
        "print('EOF-RECEIVED', flush=True)\n",
        encoding="utf-8",
    )
    command, shell = _python_command(script)
    capability = LocalInteractiveProcessCapability()
    coding = create_workspace_coding(
        root=tmp_path,
        application_layout=None,
        interactive_process_capability=capability,
        network_access="enabled",
    )
    try:
        started = await coding.exec_command(
            command=command,
            shell=shell,
            tty=True,
            yield_time_ms=100,
            timeout_sec=30,
            cid="cid_test",
            sid="sid_test",
        )
        completed = await coding.write_stdin(
            session_id=started["data"]["session_id"],
            control="eof",
            wait_ms=3000,
            cid="cid_test",
            sid="sid_test",
        )
    finally:
        await coding.close()
        await capability.aclose()

    assert completed["ok"] is True
    assert completed["data"]["status"] == "exited"
    assert "EOF-RECEIVED" in completed["data"]["output"]


@pytest.mark.anyio
async def test_exec_command_pty_projects_background_lifecycle_to_tui(
    tmp_path,
) -> None:
    script = tmp_path / "terminal_projection.py"
    script.write_text(
        "value = input('TUI-READY>')\n"
        "print('TUI-VALUE=' + value, flush=True)\n",
        encoding="utf-8",
    )
    command, shell = _python_command(script)
    capability = LocalInteractiveProcessCapability()
    coding = create_workspace_coding(
        root=tmp_path,
        application_layout=None,
        interactive_process_capability=capability,
        network_access="enabled",
    )
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    context = OutputSurfaceContext(
        surface_id="surface_pty_projection",
        cid="cid_test",
        sid="sid_test",
        turn_id="turn_test",
        agent_id="root",
    )
    output_session = create_tui_output_session(
        "",
        context=context,
        runtime=runtime,
        animate=False,
    )
    await output_session.open()
    presentation = output_session.presentation
    try:
        started = await coding.exec_command(
            command=command,
            shell=shell,
            tty=True,
            yield_time_ms=100,
            timeout_sec=30,
            cid="cid_test",
            sid="sid_test",
        )
        session_id = started["data"]["session_id"]
        await presentation.emit(build_native_tool_result_view(
            "exec_command",
            {"command": command, "tty": True},
            ok=True,
            data=started["data"],
            call_id="exec-1",
        ))

        waiting = await coding.write_stdin(
            session_id=session_id,
            wait_ms=0,
            cid="cid_test",
            sid="sid_test",
        )
        await presentation.emit(build_native_tool_result_view(
            "write_stdin",
            {"session_id": session_id, "stdin": ""},
            ok=True,
            data=waiting["data"],
            call_id="wait-1",
        ))
        activity = "".join(
            text for _style, text in runtime.screen.activity_block.fragments
        )
        assert activity.startswith("• Waiting for background terminal")

        completed = await coding.write_stdin(
            session_id=session_id,
            stdin="projected\r",
            wait_ms=3000,
            cid="cid_test",
            sid="sid_test",
        )
        await presentation.emit(build_native_tool_result_view(
            "write_stdin",
            {"session_id": session_id, "stdin": "projected\r"},
            ok=True,
            data=completed["data"],
            call_id="input-1",
        ))
    finally:
        await output_session.close()
        runtime.set_execution_active(False)
        await coding.close()
        await capability.aclose()

    text = "".join(
        value for _style, value in runtime.document.fragments(width=80)
    )
    assert completed["data"]["status"] == "exited"
    assert "TUI-VALUE=projected" in completed["data"]["output"]
    assert text.count("Waited for background terminal") == 1
    assert text.count("Interacted with background terminal") == 1


@pytest.mark.anyio
async def test_exec_command_pty_reports_resize_capability_failure(tmp_path) -> None:
    capability = _ResizeFailureCapability()
    coding = create_workspace_coding(
        root=tmp_path,
        application_layout=None,
        interactive_process_capability=capability,
        network_access="enabled",
    )
    try:
        started = await coding.exec_command(
            command="ignored by fake capability",
            tty=True,
            yield_time_ms=0,
            timeout_sec=30,
            cid="cid_test",
            sid="sid_test",
        )
        result = await coding.write_stdin(
            session_id=started["data"]["session_id"],
            control="resize",
            terminal_rows=31,
            terminal_columns=101,
            wait_ms=0,
            cid="cid_test",
            sid="sid_test",
        )
    finally:
        await coding.close()
        await capability.aclose()

    assert result["ok"] is False
    assert result["data"]["reason"] == "interactive_process_resize_failed"


@pytest.mark.anyio
async def test_sandboxed_exec_command_uses_sidecar_pty(tmp_path) -> None:
    script = tmp_path / "sandbox_interactive.py"
    script.write_text(
        "import sys\n"
        "print(f'ISATTY={sys.stdin.isatty()}:'"
        "      f'{sys.stdout.isatty()}:{sys.stderr.isatty()}', flush=True)\n"
        "value = input('SANDBOX>')\n"
        "print('VALUE=' + value, flush=True)\n",
        encoding="utf-8",
    )
    command, shell = _python_command(script)
    coding = create_workspace_coding(
        root=tmp_path,
        application_layout=_sandbox_layout_or_skip(),
        network_access="enabled",
    )
    try:
        started = await coding.exec_command(
            command=command,
            shell=shell,
            tty=True,
            yield_time_ms=100,
            timeout_sec=30,
            sandbox_mode="workspace-write",
            cid="cid_test",
            sid="sid_test",
        )
        assert started["ok"] is True
        assert started["data"]["status"] == "running"
        assert started["data"]["pty"] is True
        assert started["data"]["pty_fallback"] is False
        assert "ISATTY=True:True:True" in started["data"]["output"]
        assert started["data"]["stderr"] == ""
        session_id = started["data"]["session_id"]

        resized = await coding.write_stdin(
            session_id=session_id,
            control="resize",
            terminal_rows=31,
            terminal_columns=101,
            wait_ms=0,
            cid="cid_test",
            sid="sid_test",
        )
        assert resized["ok"] is False
        assert resized["data"]["reason"] == "exec_resize_unavailable"

        completed = await coding.write_stdin(
            session_id=session_id,
            stdin="sandbox-value\r",
            wait_ms=3000,
            cid="cid_test",
            sid="sid_test",
        )
        assert completed["ok"] is True
        assert completed["data"]["status"] == "exited"
        assert completed["data"]["pty"] is True
        assert "VALUE=sandbox-value" in completed["data"]["output"]
    finally:
        await coding.close()


@pytest.mark.anyio
async def test_sandboxed_pty_delivers_terminal_eof(tmp_path) -> None:
    script = tmp_path / "sandbox_eof.py"
    script.write_text(
        "import sys\n"
        "print('SANDBOX-EOF-READY', flush=True)\n"
        "sys.stdin.read()\n"
        "print('SANDBOX-EOF-RECEIVED', flush=True)\n",
        encoding="utf-8",
    )
    command, shell = _python_command(script)
    coding = create_workspace_coding(
        root=tmp_path,
        application_layout=_sandbox_layout_or_skip(),
        network_access="enabled",
    )
    try:
        started = await coding.exec_command(
            command=command,
            shell=shell,
            tty=True,
            timeout_sec=30,
            sandbox_mode="workspace-write",
            cid="cid_test",
            sid="sid_test",
        )
        completed = await coding.write_stdin(
            session_id=started["data"]["session_id"],
            control="eof",
            wait_ms=3000,
            cid="cid_test",
            sid="sid_test",
        )
    finally:
        await coding.close()

    assert started["ok"] is True
    assert started["data"]["status"] == "running"
    assert completed["ok"] is True
    assert completed["data"]["status"] == "exited"
    assert "SANDBOX-EOF-RECEIVED" in completed["data"]["output"]


@pytest.mark.anyio
async def test_sandboxed_pty_interrupt_keeps_process_interactive(tmp_path) -> None:
    script = tmp_path / "sandbox_interrupt.py"
    if os.name == "nt":
        script.write_text(
            "import ctypes\n"
            "import sys\n"
            "from ctypes import wintypes\n"
            "kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)\n"
            "stream = kernel32.GetStdHandle(-10)\n"
            "mode = wintypes.DWORD()\n"
            "assert kernel32.GetConsoleMode(stream, ctypes.byref(mode))\n"
            "assert kernel32.SetConsoleMode(stream, mode.value & ~7)\n"
            "print('SANDBOX-WAITING', flush=True)\n"
            "value = sys.stdin.buffer.read(1)\n"
            "print(f'SANDBOX-BYTE={value[0]}', flush=True)\n"
            "print('SANDBOX-AFTER', flush=True)\n",
            encoding="utf-8",
        )
        interrupted = "SANDBOX-BYTE=3"
    else:
        script.write_text(
            "import time\n"
            "print('SANDBOX-WAITING', flush=True)\n"
            "try:\n"
            "    while True:\n"
            "        time.sleep(1)\n"
            "except KeyboardInterrupt:\n"
            "    print('SANDBOX-INTERRUPTED', flush=True)\n"
            "print('SANDBOX-AFTER', flush=True)\n",
            encoding="utf-8",
        )
        interrupted = "SANDBOX-INTERRUPTED"
    command, shell = _python_command(script)
    coding = create_workspace_coding(
        root=tmp_path,
        application_layout=_sandbox_layout_or_skip(),
        network_access="enabled",
    )
    try:
        started = await coding.exec_command(
            command=command,
            shell=shell,
            tty=True,
            timeout_sec=30,
            sandbox_mode="workspace-write",
            cid="cid_test",
            sid="sid_test",
        )
        completed = await coding.write_stdin(
            session_id=started["data"]["session_id"],
            control="interrupt",
            wait_ms=3000,
            cid="cid_test",
            sid="sid_test",
        )
    finally:
        await coding.close()

    assert started["ok"] is True
    assert started["data"]["status"] == "running"
    assert completed["ok"] is True
    assert completed["data"]["status"] == "exited"
    assert interrupted in completed["data"]["output"]
    assert "SANDBOX-AFTER" in completed["data"]["output"]


@pytest.mark.anyio
async def test_exec_command_pty_interrupt_keeps_process_interactive(tmp_path) -> None:
    script = tmp_path / "interrupt.py"
    if os.name == "nt":
        script.write_text(
            "import ctypes\n"
            "import sys\n"
            "from ctypes import wintypes\n"
            "kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)\n"
            "stream = kernel32.GetStdHandle(-10)\n"
            "mode = wintypes.DWORD()\n"
            "assert kernel32.GetConsoleMode(stream, ctypes.byref(mode))\n"
            "assert kernel32.SetConsoleMode(stream, mode.value & ~7)\n"
            "print('WAITING', flush=True)\n"
            "value = sys.stdin.buffer.read(1)\n"
            "print(f'BYTE={value[0]}', flush=True)\n"
            "print('AFTER', flush=True)\n",
            encoding="utf-8",
        )
        interrupted = "BYTE=3"
    else:
        script.write_text(
            "import time\n"
            "print('WAITING', flush=True)\n"
            "try:\n"
            "    while True:\n"
            "        time.sleep(1)\n"
            "except KeyboardInterrupt:\n"
            "    print('INTERRUPTED', flush=True)\n"
            "print('AFTER', flush=True)\n",
            encoding="utf-8",
        )
        interrupted = "INTERRUPTED"
    command, shell = _python_command(script)
    capability = LocalInteractiveProcessCapability()
    coding = create_workspace_coding(
        root=tmp_path,
        application_layout=None,
        interactive_process_capability=capability,
        network_access="enabled",
    )
    try:
        started = await coding.exec_command(
            command=command,
            shell=shell,
            tty=True,
            yield_time_ms=100,
            timeout_sec=30,
            cid="cid_test",
            sid="sid_test",
        )
        assert started["data"]["status"] == "running"

        interrupted_result = await coding.write_stdin(
            session_id=started["data"]["session_id"],
            control="interrupt",
            wait_ms=3000,
            cid="cid_test",
            sid="sid_test",
        )
        assert interrupted_result["ok"] is True
        assert interrupted_result["data"]["status"] == "exited"
        assert interrupted in interrupted_result["data"]["output"]
        assert "AFTER" in interrupted_result["data"]["output"]
    finally:
        await coding.close()
        await capability.aclose()


@pytest.mark.anyio
async def test_exec_command_pty_bounds_output_and_resets_old_cursor(tmp_path) -> None:
    script = tmp_path / "large_output.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.write('HEAD-SENTINEL\\n')\n"
        "for index in range(24000):\n"
        "    sys.stdout.write(f'{index:05d}:' + ('x' * 90) + '\\n')\n"
        "sys.stdout.write('TAIL-SENTINEL\\n')\n"
        "sys.stdout.flush()\n"
        "input()\n",
        encoding="utf-8",
    )
    command, shell = _python_command(script)
    capability = LocalInteractiveProcessCapability()
    coding = create_workspace_coding(
        root=tmp_path,
        application_layout=None,
        interactive_process_capability=capability,
        network_access="enabled",
    )
    try:
        started = await coding.exec_command(
            command=command,
            shell=shell,
            tty=True,
            yield_time_ms=10000,
            max_output_chars=120000,
            timeout_sec=30,
            cid="cid_test",
            sid="sid_test",
        )
        session_id = started["data"]["session_id"]

        assert started["data"]["status"] == "running"
        assert started["data"]["stdout_dropped"] > 0
        assert started["data"]["output_truncated"] is True
        assert "HEAD-SENTINEL" in started["data"]["output"]
        assert "TAIL-SENTINEL" in started["data"]["output"]

        update = await coding.wait_exec_session_update(
            session_id,
            revision=0,
            timeout_sec=1.0,
        )
        assert update["changed"] is True
        assert update["delta_reset"] is True
        revisions = [item["revision"] for item in update["delta"]]
        assert revisions == sorted(revisions)
        assert len(revisions) <= 256
        assert all(
            len(item["text"].encode("utf-8")) <= 8192
            for item in update["delta"]
        )

        completed = await coding.write_stdin(
            session_id=session_id,
            stdin="\r",
            wait_ms=3000,
            cid="cid_test",
            sid="sid_test",
        )
        assert completed["data"]["status"] == "exited"
    finally:
        await coding.close()
        await capability.aclose()


@pytest.mark.anyio
async def test_cancelled_initial_pty_call_keeps_registered_session(tmp_path) -> None:
    script = tmp_path / "cancelled_call.py"
    script.write_text(
        "value = input('CANCEL-READY>')\n"
        "print('CANCEL-VALUE=' + value, flush=True)\n",
        encoding="utf-8",
    )
    command, shell = _python_command(script)
    capability = LocalInteractiveProcessCapability()
    coding = create_workspace_coding(
        root=tmp_path,
        application_layout=None,
        interactive_process_capability=capability,
        network_access="enabled",
    )
    initial = asyncio.create_task(coding.exec_command(
        command=command,
        shell=shell,
        tty=True,
        yield_time_ms=30000,
        timeout_sec=30,
        cid="cid_test",
        sid="sid_test",
    ))
    try:
        session_id = ""
        for _index in range(100):
            running = await coding.running_exec_sessions()
            if running["items"]:
                session_id = running["items"][0]["session_id"]
                break
            await asyncio.sleep(0.01)
        assert session_id

        initial.cancel()
        with pytest.raises(asyncio.CancelledError):
            await initial

        completed = await coding.write_stdin(
            session_id=session_id,
            stdin="still-running\r",
            wait_ms=3000,
            cid="cid_test",
            sid="sid_test",
        )
        assert completed["ok"] is True
        assert completed["data"]["status"] == "exited"
        assert "CANCEL-VALUE=still-running" in completed["data"]["output"]
    finally:
        if not initial.done():
            initial.cancel()
            await asyncio.gather(initial, return_exceptions=True)
        await coding.close()
        await capability.aclose()


@pytest.mark.anyio
async def test_process_session_admission_is_bounded_and_prunes_finalized() -> None:
    manager = ProcessSessionManager()
    manager.MAX_SESSIONS = 2
    manager.sessions["live"] = SimpleNamespace(
        session_id="live",
        finalized=False,
        started_at=2.0,
    )

    await manager._reserve_session_slot()
    with pytest.raises(CapabilityError) as error:
        await manager._reserve_session_slot()
    assert error.value.code == "process_session_limit_reached"
    await manager._release_session_slot()

    manager.sessions["finished"] = SimpleNamespace(
        session_id="finished",
        finalized=True,
        started_at=1.0,
    )
    await manager._reserve_session_slot()
    assert "finished" not in manager.sessions
    assert "live" in manager.sessions
    await manager._release_session_slot()


@pytest.mark.anyio
async def test_process_session_reaper_enforces_timeout_without_api_polling() -> None:
    capability = LocalInteractiveProcessCapability()
    manager = ProcessSessionManager(
        interactive_process_capability=capability,
    )
    session = await manager.start(ProcessSessionSpec(
        command="long running PTY",
        args=(sys.executable, "-c", "import time; time.sleep(30)"),
        cwd=str(Path.cwd()),
        display_cwd=".",
        runtime={},
        origin="tool",
        timeout_sec=1,
        idle_timeout_sec=10,
        env=dict(os.environ),
        tty=True,
    ))
    reaper = manager._reaper_task
    try:
        await _wait_until_session_finalized(session)
        assert session.finalized is True
        assert session.termination_reason == "expired"
        assert session.session_id not in manager.sessions
        assert not capability._handles
    finally:
        await manager.close()
        await capability.aclose()

    assert reaper is not None
    assert reaper.done()


@pytest.mark.anyio
async def test_exec_command_reports_timeout_after_autonomous_reaping(tmp_path) -> None:
    script = tmp_path / "timeout.py"
    script.write_text(
        "import time\ntime.sleep(30)\n",
        encoding="utf-8",
    )
    command, shell = _python_command(script)
    capability = LocalInteractiveProcessCapability()
    coding = create_workspace_coding(
        root=tmp_path,
        application_layout=None,
        interactive_process_capability=capability,
        network_access="enabled",
    )
    try:
        result = await coding.exec_command(
            command=command,
            shell=shell,
            tty=True,
            yield_time_ms=3000,
            timeout_sec=1,
            idle_timeout_sec=10,
            cid="cid_test",
            sid="sid_test",
        )
    finally:
        await coding.close()
        await capability.aclose()

    assert result["ok"] is False
    assert result["data"]["status"] == "exited"
    assert result["data"]["timed_out"] is True
    assert result["data"]["reason"] == "command_timed_out"


@pytest.mark.anyio
@pytest.mark.parametrize("interaction_kind", ["input", "resize"])
async def test_process_session_reaper_extends_idle_deadline_after_interaction(
    interaction_kind: str,
) -> None:
    capability = LocalInteractiveProcessCapability()
    manager = ProcessSessionManager(
        interactive_process_capability=capability,
    )
    session = await manager.start(ProcessSessionSpec(
        command="idle PTY",
        args=(sys.executable, "-c", "import time; time.sleep(30)"),
        cwd=str(Path.cwd()),
        display_cwd=".",
        runtime={},
        origin="tool",
        timeout_sec=10,
        idle_timeout_sec=1,
        env=dict(os.environ),
        tty=True,
    ))
    try:
        await asyncio.sleep(0.7)
        if interaction_kind == "input":
            result = await manager.apply(session, input_text="x")
        else:
            result = await manager.apply(
                session,
                control="resize",
                terminal_size=TerminalSize(rows=31, columns=101),
            )
        assert result is None
        await asyncio.sleep(0.7)
        assert session.process.returncode is None
        await _wait_until_session_finalized(session)
    finally:
        await manager.close()
        await capability.aclose()


@pytest.mark.anyio
async def test_process_session_output_extends_idle_deadline() -> None:
    capability = LocalInteractiveProcessCapability()
    manager = ProcessSessionManager(
        interactive_process_capability=capability,
    )
    session = await manager.start(ProcessSessionSpec(
        command="active output PTY",
        args=(
            sys.executable,
            "-c",
            (
                "import time\n"
                "for index in range(5):\n"
                " print(f'PULSE={index}', flush=True)\n"
                " time.sleep(0.4)\n"
                "time.sleep(30)\n"
            ),
        ),
        cwd=str(Path.cwd()),
        display_cwd=".",
        runtime={},
        origin="tool",
        timeout_sec=10,
        idle_timeout_sec=1,
        env=dict(os.environ),
        tty=True,
    ))
    try:
        await asyncio.sleep(1.3)
        assert session.process.returncode is None
        assert session.output_revision >= 4
        await _wait_until_session_finalized(session)
    finally:
        await manager.close()
        await capability.aclose()


@pytest.mark.anyio
async def test_process_session_deadlines_ignore_wall_clock_jumps() -> None:
    capability = LocalInteractiveProcessCapability()
    manager = ProcessSessionManager(
        interactive_process_capability=capability,
    )
    session = await manager.start(ProcessSessionSpec(
        command="monotonic PTY",
        args=(sys.executable, "-c", "import time; time.sleep(30)"),
        cwd=str(Path.cwd()),
        display_cwd=".",
        runtime={},
        origin="tool",
        timeout_sec=10,
        idle_timeout_sec=10,
        env=dict(os.environ),
        tty=True,
    ))
    try:
        with patch(
            "infrastructure.platform.process_sessions.time.time",
            return_value=session.started_at + 3600,
        ):
            await manager.cleanup()
        assert session.process.returncode is None
    finally:
        await manager.close()
        await capability.aclose()


@pytest.mark.anyio
async def test_process_session_manager_rejects_start_after_close() -> None:
    manager = ProcessSessionManager()
    await manager.close()

    with pytest.raises(CapabilityError) as error:
        await manager.start(ProcessSessionSpec(
            command="closed manager",
            args=(sys.executable, "-c", "pass"),
            cwd=str(Path.cwd()),
            display_cwd=".",
            runtime={},
            origin="tool",
            timeout_sec=10,
            idle_timeout_sec=10,
            env=dict(os.environ),
        ))

    assert error.value.code == "process_session_manager_closed"
