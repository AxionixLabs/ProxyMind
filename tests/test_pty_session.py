import ctypes
import hashlib
import os
import queue
import re
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import pytest

from tests.support.pty import PtyKey
from tests.support.pty import PtySession
from tests.support.pty import TerminalSize
from tests.support.pty import spawn_pty


pytestmark = pytest.mark.pty_acceptance


def _spawn_python(
    source: str,
    *,
    size: TerminalSize = TerminalSize(),
) -> PtySession:
    return spawn_pty(
        [sys.executable, "-u", "-c", source],
        cwd=Path.cwd(),
        env=os.environ,
        size=size,
    )


def _process_exists(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return exit_code.value == 259
    finally:
        kernel32.CloseHandle(handle)


def _raw_input_setup_source() -> str:
    if os.name == "nt":
        return (
            "import ctypes; from ctypes import wintypes; "
            "kernel32=ctypes.WinDLL('kernel32',use_last_error=True); "
            "handle=kernel32.GetStdHandle(-10); mode=wintypes.DWORD(); "
            "assert kernel32.GetConsoleMode(handle,ctypes.byref(mode)); "
            "assert kernel32.SetConsoleMode(handle,mode.value & ~7); "
        )
    return "import tty; tty.setraw(sys.stdin.fileno()); "


def test_pty_session_writes_utf8_and_drains_output() -> None:
    """验证会话持续排空输出并保留 UTF-8 内容。"""
    line_count = 4096
    line_width = 79
    source = (
        "import sys; "
        "print('READY-世界', flush=True); "
        f"sys.stdout.write(('x' * {line_width} + '\\n') * {line_count}); "
        "sys.stdout.write('TAIL-SENTINEL\\n'); sys.stdout.flush()"
    )

    with _spawn_python(source) as session:
        session.wait_for_output("READY-世界")
        assert session.wait_for_exit() == 0
        assert len(session.output()) > 8192
        assert "TAIL-SENTINEL" in session.output_text()


def test_pty_session_delivers_enter_and_text() -> None:
    """验证文本和 Enter 通过真实终端行规程送达子进程。"""
    source = "value = input('READY>'); print('VALUE=' + value, flush=True)"

    with _spawn_python(source) as session:
        session.wait_for_output("READY>")
        session.write_text("输入-emoji-🙂")
        session.send_key(PtyKey.ENTER)
        session.wait_for_output("VALUE=输入-emoji-🙂")
        assert session.wait_for_exit() == 0


def test_pty_session_normalizes_newlines_backspace_and_eof() -> None:
    """验证换行、退格与输入关闭遵循统一终端契约。"""
    source = (
        "import sys; values=[input('READY>') for _ in range(3)]; "
        "print('VALUES='+'|'.join(values),flush=True); "
        "print('EOF-READY',flush=True); value=sys.stdin.buffer.read(); "
        "print(f'EOF={len(value)}',flush=True)"
    )

    with _spawn_python(source) as session:
        session.wait_for_output("READY>")
        session.write_text("one\n")
        session.write_text("two\r")
        session.write_text("\n")
        session.write_text("abcX")
        session.write(b"\x08")
        session.send_key(PtyKey.ENTER)
        session.wait_for_output("VALUES=one|two|abc")
        session.wait_for_output("EOF-READY")
        session.close_input()
        session.close_input()
        with pytest.raises(RuntimeError, match="input is closed"):
            session.write_text("late")
        session.wait_for_output("EOF=0")
        assert session.wait_for_exit() == 0


def test_pty_session_handles_large_input_and_output_concurrently() -> None:
    """验证双向高水位流量不会互相阻塞。"""
    payload = b"z" * (256 * 1024)
    digest = hashlib.sha256(payload).hexdigest()
    source = (
        "import hashlib,sys,threading; "
        + _raw_input_setup_source()
        + "\ndef emit():\n"
        " for index in range(2048):\n"
        "  print(f'OUT-{index:04d}-'+'y'*64,flush=True)\n"
        "thread=threading.Thread(target=emit); thread.start(); data=bytearray()\n"
        f"while len(data)<{len(payload)}:\n"
        f" data.extend(sys.stdin.buffer.read({len(payload)}-len(data)))\n"
        "thread.join(); print('DIGEST='+hashlib.sha256(data).hexdigest(),flush=True); "
        "print('IO-TAIL',flush=True)"
    )

    with _spawn_python(source) as session:
        session.write(payload)
        session.wait_for_output(f"DIGEST={digest}", timeout=10.0)
        session.wait_for_output("IO-TAIL", timeout=10.0)
        assert session.wait_for_exit(timeout=10.0) == 0


def test_pty_session_ctrl_c_interrupts_and_process_continues() -> None:
    """验证 Ctrl-C 中断当前等待后子进程仍可继续执行。"""
    if os.name == "nt":
        source = (
            "import ctypes,sys; from ctypes import wintypes; "
            "kernel32=ctypes.WinDLL('kernel32',use_last_error=True); "
            "handle=kernel32.GetStdHandle(-10); mode=wintypes.DWORD(); "
            "assert kernel32.GetConsoleMode(handle,ctypes.byref(mode)); "
            "assert kernel32.SetConsoleMode(handle,mode.value & ~7); "
            "print('WAITING',flush=True); value=sys.stdin.buffer.read(1); "
            "print(f'BYTE={value[0]}',flush=True); print('AFTER',flush=True)"
        )
        expected = "BYTE=3"
    else:
        source = (
            "import time; print('WAITING', flush=True); "
            "\ntry:\n while True: time.sleep(1)"
            "\nexcept KeyboardInterrupt:\n print('INTERRUPTED', flush=True)"
            "\nprint('AFTER', flush=True)"
        )
        expected = "INTERRUPTED"

    with _spawn_python(source) as session:
        session.wait_for_output("WAITING")
        session.send_key(PtyKey.CTRL_C)
        session.wait_for_output(expected)
        session.wait_for_output("AFTER")
        assert session.wait_for_exit() == 0


def test_pty_session_resize_is_visible_to_child() -> None:
    """验证 resize 能被 PTY 中的子进程读取。"""
    source = (
        "import shutil; print('READY', flush=True); input(); "
        "size = shutil.get_terminal_size(); "
        "print(f'SIZE={size.lines}x{size.columns}', flush=True)"
    )

    with _spawn_python(source, size=TerminalSize(rows=24, columns=80)) as session:
        session.wait_for_output("READY")
        session.resize(TerminalSize(rows=31, columns=101))
        session.send_key(PtyKey.ENTER)
        session.wait_for_output("SIZE=31x101")
        assert session.wait_for_exit() == 0


def test_pty_session_close_terminates_descendant_process() -> None:
    """验证关闭 PTY 会同时终止根进程和后台后代。"""
    source = (
        "import subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(120)']); "
        "print(f'CHILD={child.pid}', flush=True); time.sleep(120)"
    )
    session = _spawn_python(source)
    output = session.wait_for_output("CHILD=").decode("utf-8", errors="replace")
    match = re.search(r"CHILD=(\d+)", output)
    assert match is not None
    child_pid = int(match.group(1))
    assert _process_exists(child_pid)

    session.close()

    deadline = time.monotonic() + 2.0
    while _process_exists(child_pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not _process_exists(child_pid)


def test_root_exit_with_pty_descendant_converges() -> None:
    """验证根进程先退出时持有终端的后代不会阻塞排空。"""
    source = (
        "import subprocess,sys; "
        "child=subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(120)']); "
        "print(f'ORPHAN={child.pid}',flush=True)"
    )

    with _spawn_python(source) as session:
        output = session.wait_for_output("ORPHAN=").decode(
            "utf-8",
            errors="replace",
        )
        match = re.search(r"ORPHAN=(\d+)", output)
        assert match is not None
        child_pid = int(match.group(1))
        assert session.wait_for_exit(timeout=3.0) == 0
        assert not _process_exists(child_pid)


def test_concurrent_terminate_and_repeated_close_are_idempotent() -> None:
    """验证并发终止、回收和重复关闭只收敛一次。"""
    session = _spawn_python("import time; print('READY',flush=True); time.sleep(120)")
    session.wait_for_output("READY")
    results: queue.Queue[int] = queue.Queue()

    def terminate() -> None:
        results.put(session.terminate())

    workers = [threading.Thread(target=terminate) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=3.0)
        assert not worker.is_alive()
    assert results.qsize() == 2
    session.close()
    session.close()


def test_force_kill_reaps_process() -> None:
    """验证显式强杀等待根进程并关闭终端输出。"""
    session = _spawn_python("import time; print('READY',flush=True); time.sleep(120)")
    session.wait_for_output("READY")
    session.kill()
    assert session.output_closed
    session.close()
