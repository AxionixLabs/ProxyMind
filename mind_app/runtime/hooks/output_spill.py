# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import shutil
import typing
import hashlib
import asyncio
import tempfile
import contextlib
from dataclasses import dataclass
from pathlib import Path
from mind_nova import const

DEFAULT_OUTPUT_THRESHOLD_BYTES = 256 * 1024
OUTPUT_PREVIEW_BYTES           = 8 * 1024


@dataclass(frozen=True, slots=True)
class HookOutputSpill:
    """描述已经写入临时文件的 Hook 输出。"""
    channel: str
    path: str
    size_bytes: int
    head: str
    tail: str

    def metadata(self) -> dict[str, typing.Any]:
        """返回可写入 Hook 执行记录的结构化信息。"""
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "head": self.head,
            "tail": self.tail,
        }

    def summary(self) -> str:
        """返回适合作为上下文或诊断信息的有界摘要。"""
        parts = [
            f"Hook {self.channel} output spilled to {self.path}",
            f"size_bytes: {self.size_bytes}",
        ]
        if self.head:
            parts.extend(("head:", self.head))
        if self.tail and self.tail != self.head:
            parts.extend(("tail:", self.tail))

        return "\n".join(parts)


@dataclass(frozen=True, slots=True)
class CapturedHookOutput:
    """保存内存输出或 spill 文件描述。"""
    data: bytes = b""
    spill: HookOutputSpill | None = None

    def text(self) -> str:
        """返回完整内存文本或 spill 摘要。"""
        if self.spill is not None:
            return self.spill.summary()
        return self.data.decode(const.CHARSET, errors="replace").strip()


class HookOutputSpillStore:
    """按会话管理 Hook 大输出临时文件。"""

    def __init__(
        self,
        *,
        threshold_bytes: int = DEFAULT_OUTPUT_THRESHOLD_BYTES,
        root: Path | None = None
    ) -> None:
        threshold = int(threshold_bytes)
        if threshold <= 0:
            raise ValueError("hook output threshold must be greater than 0")
        self.threshold_bytes = threshold

        self._root      = root
        self._owns_root = root is None

        self._session_files: dict[str, set[Path]] = {}

    async def capture(
        self,
        stream: asyncio.StreamReader | None,
        *,
        session_id: str,
        channel: str
    ) -> CapturedHookOutput:
        """流式读取输出，超过阈值后写入会话临时文件。"""
        if stream is None:
            return CapturedHookOutput()

        buffered = bytearray()
        head     = bytearray()
        tail     = bytearray()

        size_bytes = 0

        spill_path: Path | None            = None
        spill_file: typing.BinaryIO | None = None

        try:
            while chunk := await stream.read(65536):
                size_bytes += len(chunk)
                self._update_preview(head, tail, chunk)

                if spill_file is None:
                    buffered.extend(chunk)
                    if len(buffered) > self.threshold_bytes:
                        spill_path, spill_file = self._open_spill(
                            session_id=session_id,
                            channel=channel,
                        )
                        spill_file.write(buffered)
                        buffered.clear()
                else:
                    spill_file.write(chunk)
        finally:
            if spill_file is not None:
                spill_file.close()

        if spill_path is None:
            return CapturedHookOutput(data=bytes(buffered))

        return CapturedHookOutput(spill=HookOutputSpill(
            channel=channel,
            path=str(spill_path),
            size_bytes=size_bytes,
            head=head.decode(const.CHARSET, errors="replace").strip(),
            tail=tail.decode(const.CHARSET, errors="replace").strip(),
        ))

    async def spill_text(
        self,
        text: str,
        *,
        session_id: str,
        channel: str
    ) -> HookOutputSpill:
        """把指定文本写入会话临时文件并返回恢复信息。"""
        data = str(text or "").encode(const.CHARSET)

        spill_path, spill_file = self._open_spill(
            session_id=session_id,
            channel=channel,
        )
        try:
            spill_file.write(data)
        finally:
            spill_file.close()

        head = data[:OUTPUT_PREVIEW_BYTES]
        tail = data[-OUTPUT_PREVIEW_BYTES:]

        return HookOutputSpill(
            channel=channel,
            path=str(spill_path),
            size_bytes=len(data),
            head=head.decode(const.CHARSET, errors="replace").strip(),
            tail=tail.decode(const.CHARSET, errors="replace").strip(),
        )

    async def cleanup_session(self, session_id: str) -> None:
        """删除指定会话产生的全部 spill 文件。"""
        paths   = self._session_files.pop(str(session_id or ""), set())
        parents = {path.parent for path in paths}

        for path in paths:
            with contextlib.suppress(OSError):
                path.unlink()

        for parent in parents:
            with contextlib.suppress(OSError):
                parent.rmdir()

    async def close(self) -> None:
        """删除当前存储持有的全部 spill 文件。"""
        root = self._root
        for session_id in tuple(self._session_files):
            await self.cleanup_session(session_id)
        if root is not None and self._owns_root:
            with contextlib.suppress(OSError):
                shutil.rmtree(root)
        self._root = None

    def _open_spill(
        self,
        *,
        session_id: str,
        channel: str
    ) -> tuple[Path, typing.BinaryIO]:
        """创建并登记一个输出临时文件。"""
        root        = self._ensure_root()
        session_key = str(session_id or "session")
        digest      = hashlib.sha256(session_key.encode(const.CHARSET)).hexdigest()[:16]

        session_dir = root / digest
        session_dir.mkdir(parents=True, exist_ok=True)

        descriptor, raw_path = tempfile.mkstemp(
            prefix=f"{channel}-",
            suffix=".log",
            dir=session_dir,
        )
        path = Path(raw_path)
        self._session_files.setdefault(str(session_id or ""), set()).add(path)
        return path, os.fdopen(descriptor, "wb")

    def _ensure_root(self) -> Path:
        """返回已经创建的 spill 根目录。"""
        if self._root is None:
            self._root = Path(tempfile.mkdtemp(prefix="hook-output-"))
        else:
            self._root.mkdir(parents=True, exist_ok=True)
        return self._root

    @staticmethod
    def _update_preview(
        head: bytearray,
        tail: bytearray,
        chunk: bytes
    ) -> None:
        """更新输出头尾预览。"""
        if len(head) < OUTPUT_PREVIEW_BYTES:
            remaining = OUTPUT_PREVIEW_BYTES - len(head)
            head.extend(chunk[:remaining])

        tail.extend(chunk)
        overflow = len(tail) - OUTPUT_PREVIEW_BYTES
        if overflow > 0:
            del tail[:overflow]


if __name__ == '__main__':
    pass
