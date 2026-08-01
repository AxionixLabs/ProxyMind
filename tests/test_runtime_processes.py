# -*- coding: utf-8 -*-

import os
import sys
import errno
import signal
import asyncio
import contextlib
from unittest.mock import AsyncMock

import pytest

import mind_app.runtime.processes as runtime_processes
from mind_app.runtime.processes import (
    subprocess_process_group_kwargs,
    terminate_process_tree
)


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except SystemError as error:
        cause = error.__cause__
        if (
            os.name == "nt"
            and isinstance(cause, OSError)
            and getattr(cause, "winerror", None) == 87
        ):
            return False
        raise
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as error:
        if os.name == "nt" and getattr(error, "winerror", None) == 87:
            return False
        return error.errno != errno.ESRCH
    return True


async def _wait_until_process_exits(pid: int, timeout_sec: float = 3.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_sec
    while asyncio.get_running_loop().time() < deadline:
        if not _process_exists(pid):
            return True
        await asyncio.sleep(0.05)
    return not _process_exists(pid)


@pytest.mark.anyio
@pytest.mark.parametrize("force", [False, True])
async def test_terminate_process_tree_stops_descendant(
    force: bool,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    child_code = (
        "import os, signal, time\n"
        "if os.name == 'nt':\n"
        "    signal.signal(signal.SIGBREAK, signal.SIG_IGN)\n"
        "print('ready', flush=True)\n"
        "time.sleep(60)\n"
    )
    parent_code = (
        "import subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}], "
        "stdout=subprocess.PIPE, text=True)\n"
        "child.stdout.readline()\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        parent_code,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **subprocess_process_group_kwargs(),
    )
    child_pid: int | None = None

    try:
        assert process.stdout is not None
        child_pid = int(
            (await asyncio.wait_for(process.stdout.readline(), timeout=5)).strip()
        )
        assert _process_exists(child_pid)

        if os.name == "nt":
            monkeypatch.setattr(
                runtime_processes,
                "_taskkill_process_tree",
                AsyncMock(return_value=False),
            )

        await terminate_process_tree(process, force=force)

        assert process.returncode is not None
        assert await _wait_until_process_exits(child_pid)
    finally:
        if process.returncode is None:
            await terminate_process_tree(process, force=True)
        if child_pid is not None and _process_exists(child_pid):
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.kill(child_pid, signal.SIGTERM)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(process.communicate(), timeout=2)
