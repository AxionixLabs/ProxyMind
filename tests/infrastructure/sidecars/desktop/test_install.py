import asyncio
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import threading
import zipfile
from pathlib import Path
from unittest.mock import (
    AsyncMock,
    patch,
)

import httpx
import pytest

from infrastructure.errors import AppError
from infrastructure.sidecars.desktop.install import DesktopRuntimeInstaller
from infrastructure.update.runtime import Upgrade
from protocol.schema.runtime_manifest import RuntimeManifest


@pytest.fixture
def archive_bytes() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("desktop-sidecar.exe", "desktop-sidecar.dll", "desktop-sidecar.deps.json", "coreclr.dll",
                     "hostfxr.dll", "hostpolicy.dll", "System.Private.CoreLib.dll", "PresentationFramework.dll",
                     "zh-Hans/resources.dll"):
            archive.writestr(name, b"self-contained fixture")
        archive.writestr("desktop-sidecar.runtimeconfig.json", json.dumps({"runtimeOptions": {"includedFrameworks": [
            {"name": "Microsoft.NETCore.App"}, {"name": "Microsoft.WindowsDesktop.App"},
        ]}}))
    return output.getvalue()


@pytest.fixture
def manifest(archive_bytes: bytes) -> RuntimeManifest:
    return RuntimeManifest.model_validate({
        "host": "Mind", "service": "cua", "station": "win32", "arch": "x64", "channel": "stable",
        "version": "1.0.0", "build": "fixture", "published_at": "2026-11-06", "notes": "fixture",
        "package": {"filename": "cua-win-x64.zip", "size": len(archive_bytes),
                    "hash": hashlib.sha256(archive_bytes).hexdigest(), "updated_at": "2026-11-06",
                    "url": "https://downloads.test/runtime.zip?signature=private"},
    })


@pytest.fixture
def manifest_fetch(manifest: RuntimeManifest):
    with patch("infrastructure.sidecars.desktop.install.fetch_manifest", new_callable=AsyncMock,
               return_value=manifest) as fetch:
        yield fetch


@pytest.fixture
def download(archive_bytes: bytes):
    requests = []

    async def respond(request):
        requests.append(request)
        return httpx.Response(200, content=archive_bytes)

    client = httpx.AsyncClient
    with patch("infrastructure.update.runtime.httpx.AsyncClient",
               side_effect=lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs)):
        yield requests


@pytest.mark.anyio
async def test_first_install_cache_offline_and_no_signed_url_on_disk(tmp_path, manifest_fetch, download):
    progress = AsyncMock()
    installer = DesktopRuntimeInstaller(tmp_path, progress=progress)
    executable = await installer.ensure()
    assert executable.read_bytes() == b"self-contained fixture"
    assert (executable.parent / "zh-Hans/resources.dll").is_file()
    assert len(download) == 1
    assert not list(tmp_path.rglob("*.zip")) and not list(tmp_path.rglob(".install-*"))
    assert "signature" not in (executable.parent / ".installed.json").read_text()
    assert "url" not in (tmp_path / "win-x64/current.json").read_text()
    assert await installer.ensure() == executable
    assert manifest_fetch.await_count == 1
    assert await DesktopRuntimeInstaller(tmp_path).ensure() == executable
    manifest_fetch.return_value = None
    assert await DesktopRuntimeInstaller(tmp_path).ensure() == executable
    assert len(download) == 1
    progress.start.assert_awaited_once()
    progress.stop.assert_awaited_once()
    assert progress.start.call_args.args[0]["stage"] == "done"


@pytest.mark.anyio
async def test_parallel_installers_publish_once(tmp_path, manifest_fetch, download):
    paths = await asyncio.gather(*(DesktopRuntimeInstaller(tmp_path).ensure() for _ in range(5)))
    assert len(set(paths)) == 1 and len(download) == 1
    assert not list(tmp_path.rglob(".install-*"))


@pytest.mark.anyio
async def test_new_version_keeps_active_factory_pinned_and_failed_upgrade_keeps_current(tmp_path, manifest_fetch, download):
    first = DesktopRuntimeInstaller(tmp_path)
    original = await first.ensure()
    remote = manifest_fetch.return_value
    manifest_fetch.return_value = remote.model_copy(update={"version": "2.0.0"})
    upgraded = await DesktopRuntimeInstaller(tmp_path).ensure()
    assert original != upgraded and original.is_file() and upgraded.is_file()
    assert await first.ensure() == original
    current = (tmp_path / "win-x64/current.json").read_bytes()
    manifest_fetch.return_value = remote.model_copy(update={"version": "3.0.0",
        "package": remote.package.model_copy(update={"hash": "0" * 64})})
    with pytest.raises(ValueError, match="SHA-256"):
        await DesktopRuntimeInstaller(tmp_path).ensure()
    assert (tmp_path / "win-x64/current.json").read_bytes() == current
    assert await first.ensure() == original


@pytest.mark.anyio
@pytest.mark.parametrize("corruption", ["missing", "changed", "extra", "receipt"])
async def test_damaged_cache_never_executes_or_silently_downgrades(tmp_path, manifest_fetch, download, corruption):
    executable = await DesktopRuntimeInstaller(tmp_path).ensure()
    if corruption == "missing":
        (executable.parent / "coreclr.dll").unlink()
    elif corruption == "changed":
        executable.write_bytes(b"changed")
    elif corruption == "extra":
        (executable.parent / "unexpected.dll").write_bytes(b"changed")
    else:
        (executable.parent / ".installed.json").write_text("{}")
    with pytest.raises(ValueError, match="damaged"):
        await DesktopRuntimeInstaller(tmp_path).ensure()
    manifest_fetch.return_value = None
    with pytest.raises(ValueError, match="offline"):
        await DesktopRuntimeInstaller(tmp_path).ensure()
    assert len(download) == 1


@pytest.mark.anyio
async def test_crash_staging_is_recovered_under_lock(tmp_path, manifest_fetch, download):
    abandoned = tmp_path / "win-x64/.install-abandoned"
    abandoned.mkdir(parents=True)
    (abandoned / "runtime.zip").write_bytes(b"partial")
    (abandoned.parent / ".record-abandoned").write_text("partial")
    await DesktopRuntimeInstaller(tmp_path).ensure()
    assert not abandoned.exists() and not list(abandoned.parent.glob(".record-*"))


@pytest.mark.anyio
async def test_offline_without_cache_fails_and_releases_lock(tmp_path, manifest_fetch, download):
    remote = manifest_fetch.return_value
    manifest_fetch.return_value = None
    with pytest.raises(OSError):
        await DesktopRuntimeInstaller(tmp_path).ensure()
    manifest_fetch.return_value = remote
    assert (await DesktopRuntimeInstaller(tmp_path).ensure()).is_file()


@pytest.mark.anyio
async def test_current_record_write_failure_recovers_without_redownload(tmp_path, manifest_fetch, download):
    replace = os.replace

    def fail_current(source, target):
        if Path(target).name == "current.json":
            raise OSError("disk full")
        return replace(source, target)

    with patch("infrastructure.sidecars.desktop.install.os.replace", side_effect=fail_current):
        with pytest.raises(OSError, match="disk full"):
            await DesktopRuntimeInstaller(tmp_path).ensure()
    assert not (tmp_path / "win-x64/current.json").exists()
    assert not list(tmp_path.rglob(".record-*"))
    assert (await DesktopRuntimeInstaller(tmp_path).ensure()).is_file()
    assert len(download) == 1


@pytest.mark.anyio
async def test_permission_denied_does_not_spin_until_timeout(tmp_path, manifest_fetch):
    with patch("infrastructure.sidecars.desktop.install.FileLease", side_effect=PermissionError("denied")) as lease:
        with pytest.raises(PermissionError):
            await DesktopRuntimeInstaller(tmp_path).ensure()
        lease.assert_called_once()


@pytest.mark.anyio
@pytest.mark.parametrize("url", ["http://downloads.test/package.zip", "https://user:password@downloads.test/package.zip", "file:///tmp/package.zip"])
async def test_unsafe_download_url_never_requests_package(tmp_path, manifest_fetch, download, url):
    remote = manifest_fetch.return_value
    manifest_fetch.return_value = remote.model_copy(update={"package": remote.package.model_copy(update={"url": url})})
    with pytest.raises(ValueError, match="HTTPS"):
        await DesktopRuntimeInstaller(tmp_path).ensure()
    assert not download


@pytest.mark.anyio
@pytest.mark.parametrize("excess", [False, True])
async def test_chunked_response_is_bounded_by_manifest_without_content_length(
    tmp_path, manifest_fetch, archive_bytes, excess,
):
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield archive_bytes + b"extra" if excess else archive_bytes[:-1]

    client = httpx.AsyncClient
    with patch("infrastructure.update.runtime.httpx.AsyncClient", side_effect=lambda **kwargs: client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Body())), **kwargs,
    )):
        with pytest.raises((AppError, ValueError)):
            await DesktopRuntimeInstaller(tmp_path).ensure()
    assert not list(tmp_path.rglob(".install-*"))
    assert not (tmp_path / "win-x64/current.json").exists()


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["hash", "short", "excess", "http403", "http503", "broken_zip", "missing_runtime"])
async def test_failed_download_never_publishes(tmp_path, manifest_fetch, manifest, archive_bytes, failure):
    body = archive_bytes
    status = 200
    if failure == "hash":
        manifest_fetch.return_value = manifest.model_copy(update={"package": manifest.package.model_copy(update={"hash": "0" * 64})})
    elif failure == "short":
        body = body[:-1]
    elif failure == "excess":
        body += b"unexpected"
    elif failure.startswith("http"):
        status = int(failure[4:])
    else:
        if failure == "broken_zip":
            body = b"not a zip"
        else:
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr("desktop-sidecar.exe", b"missing all libraries")
            body = output.getvalue()
        manifest_fetch.return_value = manifest.model_copy(update={"package": manifest.package.model_copy(update={
            "hash": hashlib.sha256(body).hexdigest(), "size": len(body),
        })})
    client = httpx.AsyncClient
    progress = AsyncMock()
    with patch("infrastructure.update.runtime.httpx.AsyncClient", side_effect=lambda **kwargs: client(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, content=body)), **kwargs,
    )):
        with pytest.raises((ValueError, AppError, zipfile.BadZipFile)):
            await DesktopRuntimeInstaller(tmp_path, progress=progress).ensure()
    assert not (tmp_path / "win-x64/current.json").exists()
    assert not list(tmp_path.rglob("desktop-sidecar.exe")) and not list(tmp_path.rglob(".install-*"))
    assert progress.start.call_args.args[0]["stage"] == "failed"
    progress.stop.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["cancel", "timeout"])
async def test_download_cancel_and_deadline_cleanup(tmp_path, manifest_fetch, mode):
    started = asyncio.Event()

    async def interrupted(**kwargs):
        kwargs["archive_path"].write_bytes(b"partial")
        started.set()
        await asyncio.Event().wait()

    progress = AsyncMock()
    with patch.object(Upgrade, "download_archive", side_effect=interrupted):
        task = asyncio.create_task(DesktopRuntimeInstaller(tmp_path, progress=progress,
                                                          timeout=0.05 if mode == "timeout" else 5).ensure())
        await started.wait()
        if mode == "cancel":
            task.cancel()
        with pytest.raises(asyncio.CancelledError if mode == "cancel" else TimeoutError):
            await task
    assert not list(tmp_path.rglob(".install-*"))
    assert not (tmp_path / "win-x64/current.json").exists()
    progress.stop.assert_awaited_once()


@pytest.mark.anyio
async def test_cancel_waits_for_extraction_then_cleans_without_publish(tmp_path, manifest_fetch, download):
    started, release = threading.Event(), threading.Event()
    prepare = DesktopRuntimeInstaller._prepare

    def delayed(*args):
        started.set()
        assert release.wait(3)
        prepare(*args)

    with patch.object(DesktopRuntimeInstaller, "_prepare", side_effect=delayed):
        task = asyncio.create_task(DesktopRuntimeInstaller(tmp_path).ensure())
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and list(tmp_path.rglob(".install-*"))
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not list(tmp_path.rglob(".install-*")) and not (tmp_path / "win-x64/current.json").exists()
    assert (await DesktopRuntimeInstaller(tmp_path).ensure()).is_file()


@pytest.mark.anyio
async def test_os_lock_survives_other_waiter_cancel_and_releases_on_process_exit(
    tmp_path, repository_root, manifest_fetch, download,
):
    root = tmp_path / "win-x64"
    root.mkdir()
    code = ("import sys; from pathlib import Path; from infrastructure.platform.file_lock import FileLease; "
            "lease=FileLease(Path(sys.argv[1]), exclusive=True); print('locked',flush=True); sys.stdin.readline()")
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-c", code, str(root / "install.lock"), cwd=repository_root,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    try:
        assert (await asyncio.wait_for(child.stdout.readline(), 10)).strip() == b"locked"
        task = asyncio.create_task(DesktopRuntimeInstaller(tmp_path).ensure())
        await asyncio.sleep(0.2)
        assert not task.done() and not download
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        child.kill()
        await child.wait()
    assert (await DesktopRuntimeInstaller(tmp_path).ensure()).is_file()


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/escape", "C:escape", "a\\..\\b", "a/../b",
                                  "a//b", "a/./b", "bad:stream", "NUL", "dir/COM1.txt", "trailing.", "space "])
def test_zip_path_escape_and_windows_ambiguity_rejected(tmp_path, name):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(name, "bad")
    with pytest.raises(AppError, match="unsafe"):
        Upgrade.safe_extract_zip(archive, tmp_path / "unpacked")
    assert not (tmp_path / "unpacked").exists()


@pytest.mark.parametrize("failure", ["symlink", "duplicate", "bytes", "files"])
def test_zip_link_duplicate_and_expansion_limits(tmp_path, failure):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as output:
        if failure == "symlink":
            entry = zipfile.ZipInfo("link")
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            output.writestr(entry, "../outside")
        else:
            output.writestr("FILE", "12345")
            output.writestr("file" if failure == "duplicate" else "other", "12345")
    with pytest.raises(AppError):
        Upgrade.safe_extract_zip(archive, tmp_path / "unpacked", max_bytes=1 if failure == "bytes" else 100,
                                 max_files=1 if failure == "files" else 10)
    assert not (tmp_path / "unpacked").exists()
