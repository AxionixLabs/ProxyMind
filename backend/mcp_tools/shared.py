# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field

SerialArg = typing.Annotated[
    typing.Optional[str],
    Field(description="目标设备 serial。多设备连接时必须提供；单设备连接时可省略。"),
]
PackageArg = typing.Annotated[
    str,
    Field(description="目标应用包名。"),
]


if __name__ == '__main__':
    pass
