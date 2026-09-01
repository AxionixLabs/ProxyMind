# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import asyncio
import tempfile
from pathlib import Path
from collections.abc import Iterable
from agent.ports import (
    CapabilityError,
    FilesystemCapability,
)


class LocalFilesystemCapability:
    """提供限定工作区根目录的异步文件能力。"""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        """解析并固定不可变工作区根目录。"""
        resolved = Path(root).expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError(f"filesystem root is not a directory: {resolved}")
        self.root = resolved

    async def read_text(self, path: str) -> str:
        """读取根目录内的 UTF-8 文本文件。"""
        target = self._resolve_file(path)
        if await asyncio.to_thread(target.is_dir):
            raise CapabilityError(
                "filesystem_not_file",
                f"path is a directory: {path}",
            )
        try:
            return await asyncio.to_thread(target.read_text, encoding="utf-8")
        except FileNotFoundError as error:
            raise CapabilityError(
                "filesystem_not_found",
                f"file does not exist: {path}",
            ) from error
        except IsADirectoryError as error:
            raise CapabilityError(
                "filesystem_not_file",
                f"path is a directory: {path}",
            ) from error
        except OSError as error:
            raise CapabilityError(
                "filesystem_read_failed",
                str(error).strip() or "filesystem read failed",
                details={"exception_type": type(error).__name__},
            ) from error

    async def write_text(self, path: str, content: str) -> None:
        """以原子替换方式写入根目录内的 UTF-8 文本文件。"""
        target = self._resolve_file(path)
        if not isinstance(content, str):
            raise CapabilityError(
                "filesystem_content_invalid",
                "filesystem content must be a string",
            )
        try:
            await asyncio.to_thread(_atomic_write, target, content)
        except OSError as error:
            raise CapabilityError(
                "filesystem_write_failed",
                str(error).strip() or "filesystem write failed",
                retryable=True,
                details={"exception_type": type(error).__name__},
            ) from error

    async def exists(self, path: str) -> bool:
        """判断根目录内的路径是否存在。"""
        target = self._resolve_path(path)
        return await asyncio.to_thread(target.exists)

    async def list_files(self, path: str = ".") -> tuple[str, ...]:
        """列出根目录内指定目录的直接文件项。"""
        target = self._resolve_path(path)
        if not await asyncio.to_thread(target.exists):
            raise CapabilityError(
                "filesystem_not_found",
                f"directory does not exist: {path}",
            )
        if not await asyncio.to_thread(target.is_dir):
            raise CapabilityError(
                "filesystem_not_directory",
                f"path is not a directory: {path}",
            )
        try:
            entries = await asyncio.to_thread(lambda: tuple(target.iterdir()))
        except FileNotFoundError as error:
            raise CapabilityError(
                "filesystem_not_found",
                f"directory does not exist: {path}",
            ) from error
        except OSError as error:
            raise CapabilityError(
                "filesystem_list_failed",
                str(error).strip() or "filesystem listing failed",
                details={"exception_type": type(error).__name__},
            ) from error
        return tuple(
            sorted(
                str(entry.relative_to(self.root)).replace(os.sep, "/")
                for entry in entries
                if entry.is_file()
            )
        )

    async def aclose(self) -> None:
        """文件能力不持有连接，关闭操作保持幂等。"""
        return None

    def _resolve_file(self, path: str) -> Path:
        """解析并确认目标最终位于工作区根目录内。"""
        target = self._resolve_path(path)
        if target == self.root:
            raise CapabilityError(
                "filesystem_not_file",
                "filesystem path points to the root directory",
            )
        return target

    def _resolve_path(self, path: str) -> Path:
        """解析相对路径并拒绝绝对路径及目录穿越。"""
        if not isinstance(path, str) or not path.strip():
            raise CapabilityError(
                "filesystem_path_invalid",
                "filesystem path must be non-empty",
            )
        raw = path.strip()
        candidate = Path(raw)
        if candidate.is_absolute():
            raise CapabilityError(
                "filesystem_path_invalid",
                "filesystem path must be relative to the root",
            )
        target = (self.root / candidate).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as error:
            raise CapabilityError(
                "filesystem_path_forbidden",
                "filesystem path escapes the configured root",
            ) from error
        return target


class InMemoryFilesystemCapability:
    """提供无磁盘副作用的根目录文件能力替身。"""

    def __init__(self, files: Iterable[tuple[str, str]] = ()) -> None:
        """绑定一组规范化相对路径和 UTF-8 文本内容。"""
        self._files: dict[str, str] = {}
        self._closed = False
        for path, content in files:
            normalized = self._normalize(path)
            if not isinstance(content, str):
                raise TypeError("filesystem content must be a string")
            self._files[normalized] = content

    async def read_text(self, path: str) -> str:
        """读取内存中的 UTF-8 文本文件。"""
        self._require_open()
        normalized = self._normalize(path)
        if normalized not in self._files:
            raise CapabilityError(
                "filesystem_not_found",
                f"file does not exist: {path}",
            )
        return self._files[normalized]

    async def write_text(self, path: str, content: str) -> None:
        """原子替换内存中的 UTF-8 文本文件。"""
        self._require_open()
        normalized = self._normalize(path)
        if not isinstance(content, str):
            raise CapabilityError(
                "filesystem_content_invalid",
                "filesystem content must be a string",
            )
        self._files[normalized] = content

    async def exists(self, path: str) -> bool:
        """判断内存根目录内的路径是否存在。"""
        self._require_open()
        normalized = self._normalize(path, allow_root=True)
        if normalized == ".":
            return True
        prefix = f"{normalized}/"
        return normalized in self._files or any(
            item.startswith(prefix) for item in self._files
        )

    async def list_files(self, path: str = ".") -> tuple[str, ...]:
        """列出内存根目录内指定目录的直接文件项。"""
        self._require_open()
        normalized = self._normalize(path, allow_root=True)
        if normalized != "." and normalized in self._files:
            raise CapabilityError(
                "filesystem_not_directory",
                f"path is not a directory: {path}",
            )
        prefix = "" if normalized == "." else f"{normalized}/"
        directories = {
            item[len(prefix):].split("/", 1)[0]
            for item in self._files
            if item.startswith(prefix)
        }
        if normalized != "." and not any(
            item == normalized or item.startswith(prefix)
            for item in self._files
        ):
            raise CapabilityError(
                "filesystem_not_found",
                f"directory does not exist: {path}",
            )
        return tuple(
            sorted(
                f"{prefix}{name}"
                for name in directories
                if f"{prefix}{name}" in self._files
            )
        )

    async def aclose(self) -> None:
        """幂等关闭内存文件能力。"""
        self._closed = True

    def _require_open(self) -> None:
        """拒绝在关闭后继续访问内存文件。"""
        if self._closed:
            raise CapabilityError("filesystem_closed", "filesystem capability is closed")

    @staticmethod
    def _normalize(path: str, *, allow_root: bool = False) -> str:
        """规范化虚拟路径并拒绝绝对路径和目录穿越。"""
        if not isinstance(path, str) or not path.strip():
            raise CapabilityError(
                "filesystem_path_invalid",
                "filesystem path must be non-empty",
            )
        raw = path.strip().replace("\\", "/")
        if raw.startswith("/") or ":" in raw.split("/", 1)[0]:
            raise CapabilityError(
                "filesystem_path_invalid",
                "filesystem path must be relative to the root",
            )
        parts = [part for part in raw.split("/") if part not in {"", "."}]
        if any(part == ".." for part in parts):
            raise CapabilityError(
                "filesystem_path_forbidden",
                "filesystem path escapes the configured root",
            )
        normalized = "/".join(parts)
        if not normalized and not allow_root:
            raise CapabilityError(
                "filesystem_not_file",
                "filesystem path points to the root directory",
            )
        return normalized or "."


def _atomic_write(target: Path, content: str) -> None:
    """在同一目录中写临时文件并原子替换目标。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        dir=str(target.parent),
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


if not isinstance(LocalFilesystemCapability(Path.cwd()), FilesystemCapability):
    raise TypeError("LocalFilesystemCapability must implement FilesystemCapability")
if not isinstance(InMemoryFilesystemCapability(), FilesystemCapability):
    raise TypeError("InMemoryFilesystemCapability must implement FilesystemCapability")


if __name__ == '__main__':
    pass
