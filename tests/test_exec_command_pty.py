import asyncio
import os
import shlex
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import jsonschema
import pytest

from agent.application.tools.coding_schemas import EXEC_COMMAND_INPUT_SCHEMA
from agent.application.tools.coding_schemas import WRITE_STDIN_INPUT_SCHEMA
from agent.ports import CapabilityError
from agent.ports import InteractiveProcessSpec
from agent.ports import TerminalSize
from infrastructure.config.paths import resolve_application_layout
from infrastructure.platform.pty import LocalInteractiveProcessCapability
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


def test_exec_command_schema_exposes_opt_in_pty_and_resize() -> None:
    properties = EXEC_COMMAND_INPUT_SCHEMA["properties"]
    assert properties["tty"]["default"] is False
    assert properties["terminal_rows"]["default"] == 24
    assert properties["terminal_columns"]["default"] == 80

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


@pytest.mark.anyio
async def test_exec_command_pty_supports_input_resize_and_cursor(tmp_path) -> None:
    script = tmp_path / "interactive.py"
    script.write_text(
        "import shutil\n"
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
        assert started["data"]["stderr"] == ""
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
    root = Path(__file__).resolve().parents[1]
    layout = resolve_application_layout(
        entry_file=root / "mind.py",
        argv0="mind.py",
        platform=sys.platform,
    )
    platform_directory = "windows" if os.name == "nt" else "macos"
    executable_name = "mind_sandbox_server.exe" if os.name == "nt" else "mind_sandbox_server"
    sidecar = root / "schematic" / "sandbox" / platform_directory / "bin" / executable_name
    if not sidecar.is_file():
        pytest.skip(f"sandbox sidecar is unavailable: {sidecar}")

    script = tmp_path / "sandbox_interactive.py"
    script.write_text(
        "value = input('SANDBOX>')\n"
        "print('VALUE=' + value, flush=True)\n",
        encoding="utf-8",
    )
    command, shell = _python_command(script)
    coding = create_workspace_coding(
        root=tmp_path,
        application_layout=layout,
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
