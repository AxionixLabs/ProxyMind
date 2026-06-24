# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import time
import stat
import httpx
import shutil
import typing
import asyncio
import hashlib
import zipfile
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from engine.tinker import MindError
from engine import signals
from mind_nova import (
    craft, request
)

UpgradeProgressStarter = typing.Callable[
    [], typing.Coroutine[typing.Any, typing.Any, None]
]


class UpgradeProgress(typing.Protocol):

    async def start(self, state: dict[str, typing.Any]) -> None:
        """启动升级进度展示。"""
        ...

    async def stop(self) -> None:
        """停止升级进度展示。"""
        ...


class UpgradeProgressController(object):
    """管理升级进度展示的启动状态。"""

    def __init__(
        self,
        progress: UpgradeProgress | None,
        state: dict[str, typing.Any]
    ) -> None:
        """绑定进度展示对象和共享状态。"""
        self.progress = progress
        self.state    = state
        self.started  = False

    async def start(self) -> None:
        """按需启动进度展示。"""
        if self.progress is None or self.started:
            return None

        await self.progress.start(self.state)
        self.started = True

    async def stop(self) -> None:
        """按需停止已启动的进度展示。"""
        if self.progress is None or not self.started:
            return None

        await self.progress.stop()


class Upgrade(object):

    @staticmethod
    def mark_final(
        state: dict[str, typing.Any],
        label: str,
        *,
        icon: str,
        style: str,
        stage: str | None = None
    ) -> None:
        """写入下载流程的最终展示状态。"""
        if stage is not None:
            state["stage"] = stage

        state["speed"]       = 0.0
        state["final_icon"]  = icon
        state["final_label"] = label
        state["final_style"] = style

    @staticmethod
    def cancel_download(
        state: dict[str, typing.Any],
        archive_path: Path,
        *,
        stage: str | None = None,
        reset_progress: bool = False
    ) -> None:
        """结束下载动画并移除未完成的归档文件。"""
        if stage is not None:
            state["stage"] = stage
        if reset_progress:
            state["phase"] = 0.0
            state["done"]  = 0

        archive_path.unlink(missing_ok=True)

    @staticmethod
    def set_stage(
        state: dict[str, typing.Any],
        stage: str,
        *,
        phase: float | None = None,
        speed: float | None = None
    ) -> None:
        """更新下载流程的阶段状态。"""
        state["stage"] = stage
        if phase is not None:
            state["phase"] = phase
        if speed is not None:
            state["speed"] = speed

    @staticmethod
    def download_state(filename: str) -> dict[str, typing.Any]:
        """创建下载动画使用的初始状态。"""
        return {
            "stage"    : "warming",
            "filename" : filename,
            "phase"    : 0.0,
            "done"     : 0,
            "speed"    : 0.0
        }

    @staticmethod
    def download_error(exc: Exception) -> MindError:
        """将下载阶段异常转换为统一的安装错误。"""
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            if code in {401, 403}:
                return MindError(f"Install failed: download unauthorized status={code}")
            if code in {502, 503, 504}:
                return MindError(f"Install failed: download service unavailable status={code}")
            return MindError(f"Install failed: download http status={code}")

        if isinstance(exc, httpx.TimeoutException):
            return MindError(f"Install failed: download timeout: {type(exc).__name__}")

        if isinstance(exc, httpx.ConnectError):
            return MindError("Install failed: download connection failed")

        if isinstance(exc, httpx.HTTPError):
            return MindError(f"Install failed: download request failed: {type(exc).__name__}")

        return MindError(f"Install failed: download failed: {type(exc).__name__}: {exc}")

    @staticmethod
    def backend_runtime_name() -> str:
        """返回当前平台的后端运行时目录名。"""
        if sys.platform.startswith("win"):
            return "helix.dist"
        if sys.platform == "darwin":
            return "helix.app"
        raise MindError(f"Install failed: unsupported platform: {sys.platform}")

    @staticmethod
    def safe_extract_zip(archive_path: Path, extract_dir: Path) -> None:
        """校验 zip 成员路径后解压到指定目录。"""
        extract_root = extract_dir.resolve()

        with zipfile.ZipFile(archive_path, "r") as zf:
            for info in zf.infolist():
                raw_name = info.filename
                member = Path(raw_name)

                if member.is_absolute() or ".." in member.parts:
                    raise MindError(f"Install failed: unsafe archive path: {raw_name}")

                target = (extract_root / member).resolve()
                if target == extract_root:
                    continue
                if not target.is_relative_to(extract_root):
                    raise MindError(f"Install failed: unsafe archive path: {raw_name}")

            zf.extractall(extract_root)

    @staticmethod
    def replace_tree(src_dir: Path, dst_dir: Path) -> None:
        """使用临时目录和备份目录替换目标目录。"""
        parent = dst_dir.parent
        parent.mkdir(parents=True, exist_ok=True)

        tmp_dir = parent / f".{dst_dir.name}.tmp"
        bak_dir = parent / f".{dst_dir.name}.bak"

        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        if bak_dir.exists():
            if dst_dir.exists():
                shutil.rmtree(bak_dir, ignore_errors=True)
            else:
                try:
                    bak_dir.rename(dst_dir)
                except Exception as e:
                    raise MindError(
                        f"Install failed: restore stale backup failed: {type(e).__name__}: {e}"
                    ) from e

        try:
            shutil.copytree(src_dir, tmp_dir)

            if dst_dir.exists():
                dst_dir.rename(bak_dir)

            tmp_dir.rename(dst_dir)

            if bak_dir.exists():
                shutil.rmtree(bak_dir, ignore_errors=True)

        except Exception as e:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)

            if not dst_dir.exists() and bak_dir.exists():
                try:
                    bak_dir.rename(dst_dir)
                except Exception as restore_error:
                    raise MindError(
                        f"Install failed: replace runtime failed: {type(e).__name__}: {e}; "
                        f"restore failed: {type(restore_error).__name__}: {restore_error}"
                    ) from restore_error

            raise MindError(f"Install failed: replace runtime failed: {type(e).__name__}: {e}") from e

    @classmethod
    def backend_runtime_exec(cls, runtime_root: Path) -> Path:
        """返回后端运行时目录内的可执行文件路径。"""
        if sys.platform.startswith("win"):
            return runtime_root / "helix.exe"
        if sys.platform == "darwin":
            return runtime_root / "Contents" / "MacOS" / "helix"

        raise MindError(f"Install failed: unsupported platform: {sys.platform}")

    @classmethod
    def resolve_runtime_root(cls, extract_dir: Path) -> Path:
        """从解压目录中定位并校验后端运行时根目录。"""
        runtime_name = cls.backend_runtime_name()
        direct       = extract_dir / runtime_name

        if direct.is_dir():
            runtime_root = direct
        else:
            children = [p for p in extract_dir.iterdir() if p.exists()]
            dirs     = [p for p in children if p.is_dir()]

            if len(children) == 1 and len(dirs) == 1:
                nested = dirs[0] / runtime_name
                if nested.is_dir():
                    runtime_root = nested
                elif dirs[0].name == runtime_name:
                    runtime_root = dirs[0]
                else:
                    raise MindError("Install failed: invalid backend package layout")
            else:
                raise MindError("Install failed: invalid backend package layout")

        runtime_exec = cls.backend_runtime_exec(runtime_root)
        if not runtime_exec.is_file():
            raise MindError(f"Install failed: backend executable missing: {runtime_exec.name}")

        if sys.platform == "darwin":
            try:
                runtime_exec.chmod(runtime_exec.stat().st_mode | stat.S_IXUSR)
            except OSError as e:
                raise MindError(f"Install failed: chmod backend executable failed: {e}") from e

        return runtime_root

    def verify_archive(
        self,
        *,
        state: dict[str, typing.Any],
        sha256_actual: str | None,
        sha256_expect: str | None
    ) -> None:
        """校验下载归档的哈希值。"""
        self.set_stage(state, "verifying", phase=1.0, speed=0.0)

        if not sha256_expect:
            return

        if sha256_actual != sha256_expect:
            raise MindError(
                f"Install failed: sha256 mismatch expect={sha256_expect} actual={sha256_actual}"
            )

    def extract_runtime(
        self,
        *,
        archive_path: Path,
        extract_dir: Path,
        state: dict[str, typing.Any]
    ) -> Path:
        """解压归档并返回已校验的运行时根目录。"""
        extract_dir.mkdir(parents=True, exist_ok=True)
        self.set_stage(state, "extracting")

        if archive_path.suffix.lower() != ".zip":
            raise MindError(f"Install failed: unsupported archive type: {archive_path.suffix}")

        self.safe_extract_zip(archive_path, extract_dir)
        return self.resolve_runtime_root(extract_dir)

    def install_runtime(
        self,
        *,
        runtime_root: Path,
        target_dir: Path,
        state: dict[str, typing.Any]
    ) -> None:
        """安装已校验的后端运行时。"""
        self.set_stage(state, "installing")
        final_target = target_dir / self.backend_runtime_name()
        self.replace_tree(runtime_root, final_target)
        self.set_stage(state, "cleaning")

    async def download_archive(
        self,
        *,
        url: str,
        filename: str,
        archive_path: Path,
        state: dict[str, typing.Any],
        started: float,
        sha256_enabled: bool,
        timeout: float,
        chunk_size: int,
        start_progress: UpgradeProgressStarter | None = None
    ) -> tuple[int, str | None]:
        """下载远端归档文件并更新进度状态。"""
        done   = 0
        hasher = hashlib.sha256() if sha256_enabled else None

        self.set_stage(state, "connecting")
        if start_progress is not None:
            await start_progress()

        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                async with client.stream("GET", url) as resp:
                    if resp.status_code == 404:
                        raise MindError(
                            f"No backend upgrade package was found: {filename}"
                        )

                    resp.raise_for_status()

                    total = int(resp.headers.get("content-length") or 0)
                    state["total"] = total
                    self.set_stage(state, "downloading")

                    with open(archive_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=chunk_size):
                            if not chunk: continue

                            f.write(chunk)
                            size = len(chunk)
                            done += size

                            if hasher is not None:
                                hasher.update(chunk)

                            elapsed = max(0.001, time.perf_counter() - started)

                            state["done"] = done
                            state["speed"] = done / elapsed

                            if total > 0:
                                state["phase"] = min(1.0, done / total)

                    if 0 < total != done:
                        raise MindError(
                            f"Install failed: incomplete download expect={total} actual={done}"
                        )

        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except MindError:
            raise
        except Exception as e:
            if signals.task_interrupt_active():
                raise asyncio.CancelledError from e
            raise self.download_error(e) from e

        return done, hasher.hexdigest().lower() if hasher is not None else None

    async def install_app(
        self,
        remote: dict[str, typing.Any],
        install_dir: str,
        *,
        progress: UpgradeProgress | None = None
    ) -> dict[str, typing.Any]:
        """下载、校验并安装当前平台的后端运行时。"""
        pkg = (remote.get("package") or {})
        url = str(pkg.get("url") or "").strip()

        sha256_expect = str(
            pkg.get("sha256") or pkg.get("hash") or ""
        ).strip().lower() or None

        version = str(remote.get("version") or "").strip() or "unknown"

        if not url:
            raise MindError("No backend upgrade package is available for this platform.")

        target_dir = Path(install_dir).expanduser().resolve()
        filename   = Path(urlparse(url).path or "").name.strip() or f"runtime_{version}.zip"

        archive_path: Path | None = None
        tmp_path: Path | None     = None

        started = time.perf_counter()

        timeout: float  = 120.0
        chunk_size: int = 1024 * 256

        state = self.download_state(filename)

        progress_controller = UpgradeProgressController(progress, state)

        try:
            target_dir.mkdir(parents=True, exist_ok=True)

            tmp_path     = Path(tempfile.mkdtemp(prefix="mind_runtime_")).resolve()
            archive_path = (tmp_path / filename).resolve()

            done, sha256_actual = await self.download_archive(
                url=url,
                filename=filename,
                archive_path=archive_path,
                state=state,
                started=started,
                sha256_enabled=bool(sha256_expect),
                timeout=timeout,
                chunk_size=chunk_size,
                start_progress=progress_controller.start
            )

            self.verify_archive(
                state=state,
                sha256_actual=sha256_actual,
                sha256_expect=sha256_expect
            )
            await asyncio.sleep(0)

            runtime_root = await asyncio.to_thread(
                self.extract_runtime,
                archive_path=archive_path,
                extract_dir=tmp_path / "extract",
                state=state
            )
            await asyncio.sleep(0)

            await asyncio.to_thread(
                self.install_runtime,
                runtime_root=runtime_root,
                target_dir=target_dir,
                state=state
            )

            if tmp_path.exists():
                self.set_stage(state, "cleaning")
                await asyncio.sleep(0)
                await asyncio.to_thread(shutil.rmtree, tmp_path, ignore_errors=True)
                tmp_path = None

            elapsed = max(0.001, time.perf_counter() - started)

            self.mark_final(
                state, "complete", icon="✓", style="bold #87FFAF", stage="done"
            )

            return {
                "ok"               : True,
                "version"          : version,
                "install_dir"      : str(target_dir),
                "archive"          : str(archive_path or ""),
                "removed_archive"  : True,
                "downloaded_bytes" : done,
                "elapsed_sec"      : round(elapsed, 3)
            }

        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            if archive_path is not None:
                self.cancel_download(
                    state, archive_path, stage="cancelled"
                )
            else:
                state["stage"] = "cancelled"
            raise

        except MindError:
            if archive_path is not None:
                self.cancel_download(
                    state, archive_path, stage="failed"
                )
            else:
                state["stage"] = "failed"
            raise

        except Exception as e:
            if archive_path is not None:
                self.cancel_download(
                    state, archive_path, stage="failed"
                )
            else:
                state["stage"] = "failed"
            raise MindError(f"Install failed: {type(e).__name__}: {e}") from e

        finally:
            if tmp_path is not None and tmp_path.exists():
                await asyncio.to_thread(shutil.rmtree, tmp_path, ignore_errors=True)
            await progress_controller.stop()

    async def upgrade_app(
        self,
        install_dir: typing.Union[str, Path],
        *,
        progress: UpgradeProgress | None = None
    ) -> None:
        """获取远端清单并执行后端运行时升级。"""
        if not await craft.port_listen(port := 3333):
            await craft.kill_port(port)

        remote: typing.Optional[dict] = None

        max_retries: int = 3
        for i in range(max_retries):
            if remote := await request.fetch_manifest():
                break
            if i < 2:
                await asyncio.sleep(1.0)

        if not remote:
            raise MindError(
                f"No backend upgrade manifest is available after {max_retries} retries."
            )

        await self.install_app(remote, str(install_dir), progress=progress)


if __name__ == '__main__':
    pass
