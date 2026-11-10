import asyncio
import csv
import os
import subprocess
import sys

import pytest

from infrastructure.platform.process_capture import ProcessCapture


@pytest.mark.anyio
@pytest.mark.skipif(os.name != "nt", reason="Windows nested process regression")
async def test_cancel_capture_reaps_nested_process(tmp_path) -> None:
    pid_file = tmp_path / "child.pid"
    script = (
        "import subprocess,sys,time,pathlib; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid)); "
        "time.sleep(60)"
    )
    task = asyncio.create_task(ProcessCapture.run_shell(
        script, shell=[sys.executable, "-c"], timeout_sec=30,
    ))
    child_pid = None
    try:
        async with asyncio.timeout(10):
            while not pid_file.exists():
                await asyncio.sleep(0.05)
        child_pid = int(pid_file.read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {child_pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, check=True,
        )
        rows = list(csv.reader(result.stdout.splitlines()))
        assert not any(len(row) > 1 and row[1] == str(child_pid) for row in rows)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if child_pid is not None:
            subprocess.run(["taskkill", "/PID", str(child_pid), "/T", "/F"], capture_output=True)
