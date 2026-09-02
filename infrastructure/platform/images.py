# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import base64
import os
from pathlib import Path

from agent.ports.media import (
    ImageAsset,
    ImageReadError,
)

__all__ = (
    "FileImageReader",
    "MAX_IMAGE_BYTES",
)


MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _image_mime_type(content: bytes) -> str:
    """根据文件头识别受支持的图片类型。"""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    return ""


class FileImageReader:
    """从绑定工作区读取有界图片，并隔离阻塞文件操作。"""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root).resolve()

    async def read(self, path: str) -> ImageAsset:
        """在线程中读取图片，避免阻塞 Harness 事件循环。"""
        return await asyncio.to_thread(self._read, path)

    def _read(self, raw_path: str) -> ImageAsset:
        """读取、校验并编码单个图片文件。"""
        try:
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                candidate = self._root / candidate
            image_path = candidate.resolve()
        except (OSError, RuntimeError, ValueError) as error:
            raise ImageReadError("path_invalid", raw_path) from error

        if not image_path.is_file():
            raise ImageReadError("path_not_file", raw_path)

        try:
            size = image_path.stat().st_size
        except OSError as error:
            raise ImageReadError("path_unreadable", raw_path) from error

        if size > MAX_IMAGE_BYTES:
            raise ImageReadError(
                "image_too_large",
                raw_path,
                {"size": size, "max_bytes": MAX_IMAGE_BYTES},
            )

        try:
            content = image_path.read_bytes()
        except OSError as error:
            raise ImageReadError("path_unreadable", raw_path) from error

        mime_type = _image_mime_type(content)
        if not mime_type:
            raise ImageReadError(
                "unsupported_image",
                raw_path,
                {"size": size},
            )

        try:
            display_path = image_path.relative_to(self._root).as_posix()
        except ValueError:
            display_path = str(image_path)
        return ImageAsset(
            path=display_path,
            filename=image_path.name,
            mime_type=mime_type,
            size=size,
            data_url=(
                f"data:{mime_type};base64,"
                f"{base64.b64encode(content).decode('ascii')}"
            ),
        )


if __name__ == '__main__':
    pass
