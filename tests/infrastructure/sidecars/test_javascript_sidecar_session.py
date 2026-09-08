from pathlib import Path

import pytest

from infrastructure.sidecars.javascript.bundle import JavaScriptBundle
from infrastructure.sidecars.javascript.session import JavaScriptSidecarSession
@pytest.mark.anyio
async def test_session_starts_lazily_and_preserves_cell_state(
    tmp_path: Path,
    repository_root: Path,
) -> None:
    """Session 在首次执行时启动 Kernel，并在后续 Cell 复用上下文。"""
    session = JavaScriptSidecarSession(
        cwd=tmp_path,
        session_id="session-test",
        access_mode="workspace-write",
        bundle=JavaScriptBundle.at(repository_root / "sidecars" / "js_repl"),
    )

    async def reject_tool(
        name: str,
        arguments: dict[str, object],
        call_id: str,
    ) -> dict[str, object]:
        raise AssertionError((name, arguments, call_id))

    try:
        assert session._process.process is None
        await session.execute(
            "const answer = 41;",
            timeout_ms=5000,
            call_tool=reject_tool,
        )
        result = await session.execute(
            "console.log(answer + 1);",
            timeout_ms=5000,
            call_tool=reject_tool,
        )
    finally:
        await session.close()

    assert result.output == "42"
    assert session._process.process is None
