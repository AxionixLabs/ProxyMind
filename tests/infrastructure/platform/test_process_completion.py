import asyncio
import sys

import pytest

from infrastructure.platform.process_sessions import (
    ProcessSessionManager,
    ProcessSessionSpec,
)


@pytest.mark.anyio
@pytest.mark.parametrize("exit_code", (0, 3))
async def test_completion_retains_drained_output_after_session_removal(
    tmp_path,
    exit_code: int,
) -> None:
    manager = ProcessSessionManager()
    try:
        session = await manager.start(ProcessSessionSpec(
            command="interactive output",
            args=(
                sys.executable, "-u", "-c",
                "import sys; print('first', flush=True); sys.stdin.readline(); "
                f"print('last', flush=True); sys.exit({exit_code})",
            ),
            cwd=str(tmp_path),
            display_cwd=str(tmp_path),
            runtime={},
            origin="tool",
            timeout_sec=10,
            idle_timeout_sec=10,
            owner_cid="cid",
            owner_sid="sid",
            owner_turn_id="turn",
            call_id="original-exec",
        ))
        async with asyncio.timeout(5):
            while True:
                revision = manager.change_revision
                snapshots = await manager.execution_snapshots()
                if snapshots[0].output_lines:
                    break
                await manager.wait_for_change(revision=revision, timeout_sec=1)
        assert not snapshots[0].completed
        drained = await manager.drain(session)
        assert b"first" in drained[0]
        await manager.apply(session, input_text="\n")

        async with asyncio.timeout(5):
            while True:
                revision = manager.change_revision
                snapshots = await manager.execution_snapshots()
                if snapshots[0].completed:
                    break
                await manager.wait_for_change(revision=revision, timeout_sec=1)

        completed = snapshots[0]
        assert completed.exit_code == exit_code
        assert completed.output_lines == ("first", "last")
        assert (completed.cid, completed.sid, completed.turn_id, completed.call_id) == (
            "cid", "sid", "turn", "original-exec",
        )
        await manager.finalize_if_exited(session)
        manager.remove(session.session_id)
        assert await manager.execution_snapshots() == (completed,)
    finally:
        await manager.close()
