# -*- coding: utf-8 -*-

import asyncio
import threading

import pytest

from engine import upgrade as upgrade_module
from engine.upgrade import Upgrade


@pytest.mark.anyio
async def test_install_cancellation_waits_for_threaded_phase(
    monkeypatch,
    tmp_path,
) -> None:
    work_dir = tmp_path / "work"
    install_dir = tmp_path / "install"
    phase_started = threading.Event()
    release_phase = threading.Event()

    async def download_archive(**kwargs):
        kwargs["archive_path"].write_bytes(b"archive")
        return 7, None

    def extract_runtime(**kwargs):
        phase_started.set()
        assert release_phase.wait(timeout=2.0)
        runtime_root = kwargs["extract_dir"] / "helix.dist"
        runtime_root.mkdir(parents=True)
        return runtime_root

    upgrade = Upgrade()
    monkeypatch.setattr(upgrade, "download_archive", download_archive)
    monkeypatch.setattr(upgrade, "extract_runtime", extract_runtime)

    def make_work_dir(**_kwargs) -> str:
        work_dir.mkdir()
        return str(work_dir)

    monkeypatch.setattr(
        upgrade_module.tempfile,
        "mkdtemp",
        make_work_dir,
    )

    task = asyncio.create_task(upgrade.install_app(
        {
            "version": "test",
            "package": {"url": "https://example.test/runtime.zip"},
        },
        str(install_dir),
    ))

    assert await asyncio.to_thread(phase_started.wait, 1.0)
    task.cancel()
    await asyncio.sleep(0)

    assert not task.done()
    assert work_dir.exists()

    release_phase.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not work_dir.exists()
