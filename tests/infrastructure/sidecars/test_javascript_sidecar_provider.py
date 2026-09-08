from pathlib import Path

import pytest

from infrastructure.sidecars.javascript.provider import JavaScriptSidecarProvider


@pytest.mark.anyio
async def test_provider_checks_runtime_without_creating_session(
    tmp_path: Path,
) -> None:
    """可用性检查不得提前创建 Session 或启动持久 Kernel。"""
    provider = JavaScriptSidecarProvider(tmp_path)
    try:
        await provider.ensure_available()
        assert provider._sessions == {}
    finally:
        await provider.close()


@pytest.mark.anyio
async def test_provider_close_is_idempotent(tmp_path: Path) -> None:
    """Provider 总关闭可以被应用生命周期重复调用。"""
    provider = JavaScriptSidecarProvider(tmp_path)

    await provider.close()
    await provider.close()
