# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import sys
import time
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
from mind_core.design import Design
from mind_nova import (
    craft, request
)


class Upgrade(object):

    @staticmethod
    def overwrite(src_dir: Path, dst_dir: Path) -> None:
        dst_dir.mkdir(parents=True, exist_ok=True)

        for root, dirs, files in os.walk(src_dir):
            rel = Path(root).relative_to(src_dir)
            target_root = dst_dir / rel
            target_root.mkdir(parents=True, exist_ok=True)

            for d in dirs:
                (target_root / d).mkdir(parents=True, exist_ok=True)

            for f in files:
                src_file = Path(root) / f
                dst_file = target_root / f

                if dst_file.exists():
                    if dst_file.is_dir():
                        shutil.rmtree(dst_file, ignore_errors=True)
                    else:
                        dst_file.unlink(missing_ok=True)

                shutil.copy2(src_file, dst_file)

                if not sys.platform.startswith("win") and os.access(src_file, os.X_OK):
                    try:
                        os.chmod(dst_file, os.stat(src_file).st_mode)
                    except OSError:
                        pass

    async def install_app(self, remote: dict[str, typing.Any], install_dir: str) -> dict[str, typing.Any]:
        """
        下载 -> 校验 -> 解压到临时目录 -> 覆盖安装 -> 删除压缩包
        """
        pkg = (remote.get("package") or {})
        url = str(pkg.get("url") or "").strip()
        sha256_expect = str(
            pkg.get("sha256") or pkg.get("hash") or ""
        ).strip().lower() or None
        version = str(remote.get("version") or "").strip() or "unknown"

        if not url:
            raise MindError("Install failed: missing package.url")

        target_dir = Path(install_dir).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

        filename = Path(urlparse(url).path or "").name.strip() or f"runtime_{version}.zip"
        archive_path = (target_dir / filename).resolve()

        done    = 0
        hasher  = hashlib.sha256()
        started = time.perf_counter()

        remove_archive: bool = True
        timeout: float       = 120.0
        chunk_size: int      = 1024 * 256

        state: dict[str, typing.Any] = {
            "stage"    : "warming",
            "filename" : filename,
            "phase"    : 0.0,
            "done"     : 0,
            "speed"    : 0.0
        }
        stop_event = asyncio.Event()
        anim_task = asyncio.create_task(
            Design.download_animation(state, stop_event)
        )

        try:
            state["stage"] = "downloading"

            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()

                    total = int(resp.headers.get("content-length") or 0)
                    with open(archive_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=chunk_size):
                            if not chunk: continue

                            f.write(chunk)
                            size = len(chunk)
                            done += size

                            if sha256_expect:
                                hasher.update(chunk)

                            elapsed = max(0.001, time.perf_counter() - started)
                            state["done"] = done
                            state["speed"] = done / elapsed
                            if total > 0:
                                state["phase"] = min(1.0, done / total)

            state["stage"] = "verifying"
            state["phase"] = 1.0
            state["speed"] = 0.0

            if sha256_expect:
                actual = hasher.hexdigest().lower()
                if actual != sha256_expect:
                    stop_event.set()
                    archive_path.unlink(missing_ok=True)
                    raise MindError(
                        f"Install failed: sha256 mismatch expect={sha256_expect} actual={actual}"
                    )

            with tempfile.TemporaryDirectory(prefix="mind_runtime_") as tmp_dir:
                tmp_path = Path(tmp_dir).resolve()
                extract_dir = tmp_path / "extract"
                extract_dir.mkdir(parents=True, exist_ok=True)

                state["stage"] = "extracting"

                if archive_path.suffix.lower() == ".zip":
                    with zipfile.ZipFile(archive_path, "r") as zf:
                        zf.extractall(extract_dir)
                else:
                    stop_event.set()
                    raise MindError(f"Install failed: unsupported archive type: {archive_path.suffix}")

                children = [p for p in extract_dir.iterdir() if p.exists()]

                state["stage"] = "installing"

                if len(children) == 1 and children[0].is_dir():
                    src_root = children[0]
                    final_target = target_dir / src_root.name

                    if final_target.exists():
                        shutil.rmtree(final_target, ignore_errors=True)

                    shutil.copytree(src_root, final_target)

                else:
                    src_root = extract_dir
                    final_target = target_dir
                    self.overwrite(src_root, final_target)

            state["stage"] = "cleaning"

            if remove_archive:
                archive_path.unlink(missing_ok=True)

            elapsed = max(0.001, time.perf_counter() - started)

            state["stage"] = "done"
            state["speed"] = 0.0

            return {
                "ok"               : True,
                "version"          : version,
                "install_dir"      : str(target_dir),
                "archive"          : str(archive_path),
                "removed_archive"  : bool(remove_archive),
                "downloaded_bytes" : done,
                "elapsed_sec"      : round(elapsed, 3)
            }

        except MindError:
            stop_event.set()
            archive_path.unlink(missing_ok=True)
            raise

        except Exception as e:
            stop_event.set()
            archive_path.unlink(missing_ok=True)
            raise MindError(f"Install failed: {type(e).__name__}: {e}") from e

        finally:
            stop_event.set()
            await anim_task

    async def upgrade_app(self, install_dir: typing.Union[str, Path]) -> None:
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
            raise MindError(f"❌ Install failed after {max_retries} retries: remote={remote}")

        await self.install_app(remote, str(install_dir))


if __name__ == '__main__':
    pass
