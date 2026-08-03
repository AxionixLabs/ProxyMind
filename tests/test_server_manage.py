# -*- coding: utf-8 -*-

from types import SimpleNamespace

import pytest

from engine import manage
from engine.manage import ServerManage


@pytest.mark.anyio
async def test_spawn_uses_configured_working_directory(
    monkeypatch,
    tmp_path,
) -> None:
    calls = []

    async def create_subprocess(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(pid=1234)

    monkeypatch.setattr(
        manage.asyncio,
        "create_subprocess_exec",
        create_subprocess,
    )

    server = ServerManage(
        ["python", "-m", "backend.helix"],
        cwd=tmp_path,
    )
    try:
        await server.spawn()
    finally:
        await server.close()

    assert calls[0][0] == ("python", "-m", "backend.helix")
    assert calls[0][1]["cwd"] == str(tmp_path)
