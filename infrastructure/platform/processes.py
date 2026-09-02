# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import signal
import typing
import asyncio
import subprocess

TERMINATE_GRACE_SEC = 0.5
FINAL_WAIT_SEC = 1.0
TASKKILL_TIMEOUT_SEC = 2.0


def subprocess_process_group_kwargs() -> dict[str, typing.Any]:
    """返回建立独立进程组或会话所需的启动参数。"""
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return {"creationflags": flags} if flags else {}
    return {"start_new_session": True}


async def close_process_stdin(
    process: asyncio.subprocess.Process,
    *,
    timeout_sec: float = TERMINATE_GRACE_SEC
) -> None:
    """关闭进程标准输入管道并进行有界等待。"""
    stdin_pipe = getattr(process, "stdin", None)
    if stdin_pipe is None or stdin_pipe.is_closing():
        return None

    stdin_pipe.close()

    wait_closed = getattr(stdin_pipe, "wait_closed", None)
    if not callable(wait_closed):
        return None

    try:
        await asyncio.wait_for(
            wait_closed(),
            timeout=max(0.0, float(timeout_sec)),
        )
    except (BrokenPipeError, ConnectionResetError, RuntimeError, ValueError):
        return None
    except asyncio.TimeoutError:
        return None


async def wait_for_process(
    process: asyncio.subprocess.Process,
    timeout_ms: int
) -> None:
    """等待进程退出或达到等待时间。"""
    if timeout_ms <= 0 or process.returncode is not None:
        return None
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout_ms / 1000)
    except asyncio.TimeoutError:
        return None


async def interrupt_process_tree(
    process: asyncio.subprocess.Process
) -> bool:
    """向独立进程组发送中断信号。"""
    if process.returncode is not None:
        return True

    if os.name == "nt":
        break_signal = getattr(signal, "CTRL_BREAK_EVENT", None)
        if break_signal is None:
            return False
        try:
            process.send_signal(break_signal)
            return True
        except (ProcessLookupError, RuntimeError, ValueError, OSError):
            return False

    return _signal_posix_process_group(process, signal.SIGINT)


async def terminate_process_tree(
    process: asyncio.subprocess.Process,
    *,
    force: bool,
    grace_sec: float = TERMINATE_GRACE_SEC,
    final_wait_sec: float = FINAL_WAIT_SEC
) -> None:
    """终止独立进程组，并在宽限期后升级为强制终止。"""
    if process.returncode is not None:
        return None

    grace_sec = max(0.0, float(grace_sec))
    final_wait_sec = max(0.0, float(final_wait_sec))

    await close_process_stdin(process, timeout_sec=grace_sec)

    if os.name == "nt":
        await _terminate_windows_process_tree(
            process,
            force=force,
            grace_sec=grace_sec,
            final_wait_sec=final_wait_sec,
        )
        return None

    signal_number = signal.SIGKILL if force else signal.SIGTERM

    if not _signal_posix_process_group(process, signal_number):
        _signal_top_process(process, force=force)

    if not force:
        await wait_for_process(process, int(grace_sec * 1000))
        if process.returncode is None:
            if not _signal_posix_process_group(process, signal.SIGKILL):
                _signal_top_process(process, force=True)

    await wait_for_process(process, int(final_wait_sec * 1000))


async def _terminate_windows_process_tree(
    process: asyncio.subprocess.Process,
    *,
    force: bool,
    grace_sec: float,
    final_wait_sec: float
) -> None:
    """在 Windows 上按中断、等待和强杀顺序终止进程树。"""
    descendant_pids = _windows_descendant_process_ids(process.pid)

    if not force:
        _send_windows_ctrl_break(process)
        await wait_for_process(process, int(grace_sec * 1000))
        if process.returncode is not None and not descendant_pids:
            return None

    killed = await _taskkill_process_tree(process.pid, force=True)
    if not killed:
        current_descendants = _windows_descendant_process_ids(process.pid)
        descendant_pids.extend(
            pid for pid in current_descendants if pid not in descendant_pids
        )
        _force_terminate_windows_processes(descendant_pids)
        _signal_top_process(process, force=True)

    await wait_for_process(process, int(final_wait_sec * 1000))


def _send_windows_ctrl_break(process: asyncio.subprocess.Process) -> None:
    """向 Windows 新进程组发送 CTRL_BREAK。"""
    break_signal = getattr(signal, "CTRL_BREAK_EVENT", None)
    if break_signal is None:
        return None
    try:
        process.send_signal(break_signal)
    except (ProcessLookupError, RuntimeError, ValueError, OSError):
        return None


async def _taskkill_process_tree(pid: int, *, force: bool) -> bool:
    """调用系统工具终止 Windows 进程树。"""
    args = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        args.append("/F")

    task: asyncio.subprocess.Process | None = None
    try:
        task = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(task.wait(), timeout=TASKKILL_TIMEOUT_SEC)
    except (OSError, RuntimeError, ValueError):
        return False
    except asyncio.TimeoutError:
        if task is not None and task.returncode is None:
            try:
                task.kill()
            except (ProcessLookupError, RuntimeError, ValueError):
                pass
            await asyncio.gather(task.wait(), return_exceptions=True)
        return False

    return task.returncode == 0


def _windows_descendant_process_ids(root_pid: int) -> list[int]:
    """通过系统进程快照返回指定进程的全部后代。"""
    if os.name != "nt":
        return []

    import ctypes
    from ctypes import wintypes

    class _ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        create_snapshot = kernel32.CreateToolhelp32Snapshot
        process_first = kernel32.Process32FirstW
        process_next = kernel32.Process32NextW
        close_handle = kernel32.CloseHandle

    except (AttributeError, OSError):
        return []

    create_snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    create_snapshot.restype = wintypes.HANDLE
    process_first.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    process_first.restype = wintypes.BOOL
    process_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    process_next.restype = wintypes.BOOL
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    snapshot = create_snapshot(0x00000002, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        return []

    parent_by_pid: dict[int, int] = {}

    entry = _ProcessEntry()
    entry.dwSize = ctypes.sizeof(_ProcessEntry)

    try:
        has_entry = bool(process_first(snapshot, ctypes.byref(entry)))
        while has_entry:
            parent_by_pid[int(entry.th32ProcessID)] = int(
                entry.th32ParentProcessID
            )
            has_entry = bool(process_next(snapshot, ctypes.byref(entry)))
    finally:
        close_handle(snapshot)

    descendants: list[int] = []

    known_pids = {root_pid}
    while True:
        discovered = [
            pid
            for pid, parent_pid in parent_by_pid.items()
            if pid not in known_pids and parent_pid in known_pids
        ]
        if not discovered:
            break
        descendants.extend(discovered)
        known_pids.update(discovered)

    return descendants


def _force_terminate_windows_processes(process_ids: list[int]) -> None:
    """按后代优先顺序强制终止 Windows 进程。"""
    for pid in reversed(process_ids):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError, ValueError):
            continue


def _signal_posix_process_group(
    process: asyncio.subprocess.Process,
    signal_number: int
) -> bool:
    """向 POSIX 进程组发送信号。"""
    try:
        os.killpg(process.pid, signal_number)
        return True
    except (ProcessLookupError, PermissionError, RuntimeError, ValueError, OSError):
        return False


def _signal_top_process(
    process: asyncio.subprocess.Process,
    *,
    force: bool
) -> None:
    """向顶层进程发送兜底终止信号。"""
    try:
        if force:
            process.kill()
        else:
            process.terminate()
    except (ProcessLookupError, RuntimeError, ValueError):
        return None


if __name__ == '__main__':
    pass
