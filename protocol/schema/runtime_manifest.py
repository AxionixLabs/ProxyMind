# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

RuntimeService = typing.Literal["helix", "cua"]
RuntimeStation = typing.Literal["win32", "darwin"]


class RuntimePackage(BaseModel):
    """校验服务端声明的归档身份和完整性字段。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    filename: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*\.zip$")
    size: int = Field(gt=0)
    hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    updated_at: str
    url: str


class RuntimeManifest(BaseModel):
    """保存经过边界校验的运行组件清单。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    host: str
    service: RuntimeService
    channel: typing.Literal["stable"]
    station: RuntimeStation
    arch: str
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    build: str
    published_at: str
    notes: str
    package: RuntimePackage


class RuntimeManifestResponse(BaseModel):
    """校验清单端点的成功信封。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    ok: typing.Literal[True]
    data: RuntimeManifest


if __name__ == '__main__':
    pass
