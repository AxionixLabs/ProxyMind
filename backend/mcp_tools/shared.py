# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field

MatrixArg = typing.Annotated[
    typing.Optional[dict[str, dict[str, typing.Any]]],
    Field(description="多设备覆盖参数映射。键通常是设备标识，值是该设备专属参数。"),
]

PackageArg = typing.Annotated[
    str,
    Field(description="目标应用包名。"),
]


if __name__ == '__main__':
    pass
