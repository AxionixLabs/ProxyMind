# -*- coding: utf-8 -*-
# Notes: ==== Mind(TM) ====

import os
import typing
from collections.abc import (
    Callable,
    Mapping,
)
from dataclasses import dataclass


class ImageReadError(ValueError):
    """描述图片读取适配器返回的稳定失败分类。"""

    def __init__(
        self,
        code: str,
        path: str,
        details: Mapping[str, int] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.path = path
        self.details = dict(details or {})


@dataclass(frozen=True, slots=True)
class ImageAsset:
    """保存已经读取并可作为模型附件使用的图片快照。"""

    path: str
    filename: str
    mime_type: str
    size: int
    data_url: str


class ImageReaderPort(typing.Protocol):
    """定义绑定工作区的异步图片读取能力。"""

    async def read(self, path: str) -> ImageAsset:
        """读取路径并返回不可变图片快照。"""
        ...


ImageReaderFactory: typing.TypeAlias = Callable[
    [str | os.PathLike[str]],
    ImageReaderPort,
]

__all__ = (
    "ImageAsset",
    "ImageReadError",
    "ImageReaderFactory",
    "ImageReaderPort",
)
