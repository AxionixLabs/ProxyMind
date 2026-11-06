# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import errno
import hashlib
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from infrastructure.errors import AppError
from infrastructure.platform.file_lock import FileLease
from infrastructure.sidecars.assets import (
    MACOS_DESKTOP_EXECUTABLE,
    desktop_runtime_files,
)
from infrastructure.update.runtime import (
    Upgrade,
    UpgradeProgress,
    run_threaded_phase,
)
from observability import observe
from protocol.client.manifest import fetch_manifest
from protocol.schema.json_value import JsonValue
from protocol.schema.runtime_manifest import RuntimeManifest


class _InstalledFile(BaseModel):
    """记录安装时验证过的单个文件完整性。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    size: int = Field(ge=0)
    hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class _ReleaseIdentity(BaseModel):
    """记录不含签名地址的本地版本身份。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @property
    def directory(self) -> str:
        """生成不可跨目录的版本路径。"""
        return f"{self.version}-{self.hash[:16]}"


class _InstalledRelease(_ReleaseIdentity):
    """记录已完整安装的文件清单，供缓存复用前检查。"""

    files: dict[str, _InstalledFile]


def _digest(path: Path) -> _InstalledFile:
    """流式计算文件大小与摘要，不将运行库读入内存。"""
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return _InstalledFile(size=path.stat().st_size, hash=digest)


def _write_atomic(path: Path, content: str) -> None:
    """在同一卷落盘后原子替换小型安装记录。"""
    descriptor, temporary = tempfile.mkstemp(prefix=".record-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class DesktopRuntimeInstaller:
    """拥有进程内版本选择与用户缓存安装，不启动原生会话。

    组合根注入缓存根和展示端口；首次使用查询版本，后续会话固定已校验路径。
    OS 文件锁串行化安装，临时目录只在持锁期间使用，版本发布后不覆盖。
    """

    def __init__(
        self, cache_root: Path, *, progress: UpgradeProgress | None = None, timeout: float = 900.0,
        platform: str = "win32",
    ) -> None:
        """保存缓存位置与安装总时限，不创建目录或访问网络。"""
        if platform not in {"win32", "darwin"}:
            raise ValueError(f"Unsupported CUA runtime platform: {platform}")
        self._platform = platform
        self._root = cache_root.expanduser().resolve() / ("macos-arm64" if platform == "darwin" else "win-x64")
        self._progress = progress
        self._timeout = timeout
        self._resolved: Path | None = None
        self._lock = asyncio.Lock()

    async def ensure(self) -> Path:
        """完成版本选择、缓存校验或安装，取消不会启动半成品。"""
        async with self._lock:
            if self._resolved is not None:
                return self._resolved
            state: dict[str, JsonValue] = {"stage": "warming", "done": 0, "phase": 0.0, "name": "CUA"}
            try:
                if self._progress is not None:
                    await self._progress.start(state)
                async with asyncio.timeout(self._timeout):
                    self._resolved = await self._resolve(state)
                state["stage"] = "done" if state["done"] else "ready"
                return self._resolved
            except asyncio.CancelledError:
                state["stage"] = "cancelled"
                raise
            except TimeoutError as error:
                state["stage"] = "failed"
                raise TimeoutError(f"CUA installation exceeded {self._timeout:g} seconds; check the network and retry") from error
            except (OSError, ValueError, AppError, zipfile.BadZipFile):
                state["stage"] = "failed"
                raise
            finally:
                if self._progress is not None:
                    await self._progress.stop()

    async def _resolve(self, state: dict[str, JsonValue]) -> Path:
        """查询明确版本，仅网络不可用时使用最后一次成功的本地版本。"""
        remote = await fetch_manifest("cua", station="darwin" if self._platform == "darwin" else "win32",
                                      arch="arm64" if self._platform == "darwin" else "x64")
        self._root.mkdir(parents=True, exist_ok=True)
        lease = await self._acquire_lease()
        try:
            await run_threaded_phase(self._clean_interrupted_install)
            if remote is None:
                identity = _ReleaseIdentity.model_validate_json((self._root / "current.json").read_bytes())
                directory = self._root / identity.directory
                if not await run_threaded_phase(self._valid_cache, directory, identity, self._platform):
                    raise ValueError("CUA is offline and no intact installed runtime is available")
                observe("desktop.runtime.offline_cache", version=identity.version)
            else:
                identity = _ReleaseIdentity(version=remote.version, hash=remote.package.hash)
                directory = self._root / identity.directory
                if directory.exists():
                    if not await run_threaded_phase(self._valid_cache, directory, identity, self._platform):
                        raise ValueError(
                            f"CUA cache is damaged: {directory}. Close desktop sessions and remove this version before retrying."
                        )
                    observe("desktop.runtime.cache_hit", version=identity.version)
                else:
                    await self._install(remote, identity, directory, state)
                _write_atomic(self._root / "current.json", identity.model_dump_json())
            executable = directory / (MACOS_DESKTOP_EXECUTABLE if self._platform == "darwin" else "desktop-sidecar.exe")
            observe("desktop.runtime.ready", version=identity.version, executable=str(executable))
            return executable
        finally:
            lease.close()

    async def _acquire_lease(self) -> FileLease:
        """等待跨进程安装锁，取消和进程退出由系统释放资源。"""
        while True:
            try:
                return FileLease(self._root / "install.lock", exclusive=True)
            except OSError as error:
                windows_conflict = getattr(error, "winerror", None) == 33
                posix_conflict = os.name != "nt" and error.errno in {errno.EAGAIN, errno.EACCES}
                if not windows_conflict and not posix_conflict:
                    raise
                await asyncio.sleep(0.1)

    def _clean_interrupted_install(self) -> None:
        """持锁清理上次进程退出留下的私有暂存目录，不触及发布版本。"""
        for path in self._root.glob(".install-*"):
            if path.is_dir() and not path.is_symlink() and path.resolve().is_relative_to(self._root.resolve()):
                shutil.rmtree(path)
        for path in self._root.glob(".record-*"):
            if path.is_file() and not path.is_symlink():
                path.unlink()

    @staticmethod
    def _valid_cache(directory: Path, identity: _ReleaseIdentity, platform: str = "win32") -> bool:
        """核对完整文件集合和内容，缓存不接受缺失文件或静默变更。"""
        try:
            if directory.is_symlink():
                return False
            receipt = _InstalledRelease.model_validate_json((directory / ".installed.json").read_bytes())
            if receipt.version != identity.version or receipt.hash != identity.hash:
                return False
            files = desktop_runtime_files(directory, platform=platform)
            if platform == "darwin":
                _verify_macos_signature(directory)
            actual = {path.relative_to(directory).as_posix(): _digest(path) for path in files}
            return actual == receipt.files
        except (OSError, ValueError):
            return False

    async def _install(
        self, remote: RuntimeManifest, identity: _ReleaseIdentity, directory: Path, state: dict[str, JsonValue],
    ) -> None:
        """下载并校验自包含 ZIP，在同卷暂存完成后一次重命名发布。"""
        package = remote.package
        url = urlsplit(package.url)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("CUA package requires an HTTPS download URL without credentials")
        if package.size > 256 * 1024**2:
            raise ValueError("CUA package exceeds download size limit")
        with tempfile.TemporaryDirectory(prefix=".install-", dir=self._root) as temporary:
            work = Path(temporary)
            archive = work / "runtime.zip"
            runtime = work / "runtime"
            started = time.perf_counter()
            done, digest = await Upgrade().download_archive(
                url=package.url, archive_path=archive, state=state, started=started,
                sha256_enabled=True, timeout=30.0, chunk_size=256 * 1024, expected_size=package.size,
            )
            if done != package.size or digest != package.hash:
                raise ValueError("CUA package size or SHA-256 differs from manifest")
            state["stage"] = "extracting"
            await run_threaded_phase(self._prepare, archive, runtime, identity, self._platform)
            state["stage"] = "installing"
            runtime.rename(directory)
            observe("desktop.runtime.installed", version=identity.version, bytes=done)

    @staticmethod
    def _prepare(archive: Path, runtime: Path, identity: _ReleaseIdentity, platform: str = "win32") -> None:
        """校验归档路径、运行库和逐文件摘要，在线程完成后才允许发布。"""
        Upgrade.safe_extract_zip(archive, runtime, max_bytes=512 * 1024**2, max_files=5000)
        if platform == "darwin":
            # ZIP 解压器不恢复 Unix mode；只赋予固定 Mach-O 入口执行位。
            (runtime / MACOS_DESKTOP_EXECUTABLE).chmod(0o755)
        files = desktop_runtime_files(runtime, platform=platform)
        if platform == "darwin":
            _verify_macos_signature(runtime)
        receipt = _InstalledRelease(
            version=identity.version, hash=identity.hash,
            files={path.relative_to(runtime).as_posix(): _digest(path) for path in files},
        )
        _write_atomic(runtime / ".installed.json", receipt.model_dump_json())


def _verify_macos_signature(runtime: Path) -> None:
    """发布与缓存复用前校验 bundle 完整签名，不修改隔离属性或绕过 Gatekeeper。"""
    try:
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(runtime / "DesktopSidecar.app")],
                       check=True, capture_output=True, timeout=15)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise ValueError("CUA macOS bundle signature verification failed") from error


if __name__ == '__main__':
    pass
