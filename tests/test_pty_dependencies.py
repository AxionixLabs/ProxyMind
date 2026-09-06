import os
import sys

import pytest
import pyte


pytestmark = pytest.mark.pty_acceptance


def test_vt_parser_preserves_text_and_color() -> None:
    """验证验收依赖能够投影基础 VT 文本和颜色。"""
    screen = pyte.Screen(20, 2)
    stream = pyte.Stream(screen)

    stream.feed("\x1b[31mready\x1b[0m")

    assert screen.display[0].strip() == "ready"
    assert screen.buffer[0][0].fg == "red"
    assert screen.buffer[0][5].fg == "default"


@pytest.mark.skipif(os.name == "nt", reason="POSIX PTY only")
def test_posix_pty_runs_python_with_utf8() -> None:
    """验证 POSIX runner 能在真实 PTY 中执行 UTF-8 子进程。"""
    import pexpect

    child = pexpect.spawn(
        sys.executable,
        ["-u", "-c", "print('PTY-READY-世界', flush=True)"],
        encoding="utf-8",
        timeout=5,
        dimensions=(24, 80),
    )
    try:
        child.expect_exact("PTY-READY-世界")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows ConPTY only")
def test_windows_conpty_runs_python_with_utf8() -> None:
    """验证 Windows runner 能在真实 ConPTY 中执行 UTF-8 子进程。"""
    import winpty

    command = [
        sys.executable,
        "-u",
        "-c",
        "print('PTY-READY-\\u4e16\\u754c', flush=True)",
    ]
    process = winpty.PtyProcess.spawn(command, dimensions=(24, 80))
    chunks: list[str] = []
    try:
        while True:
            try:
                chunks.append(process.read(4096))
            except EOFError:
                break
        process.wait()
        assert "PTY-READY-世界" in "".join(chunks)
        assert process.exitstatus == 0
    finally:
        if process.isalive():
            process.sendintr()
            process.wait()
