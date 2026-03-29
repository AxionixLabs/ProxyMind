# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from fastapi import Request
from backend.utilities.runtime import Idle


async def touch_middleware(request: Request, call_next: typing.Callable) -> typing.Any:
    idle: Idle = request.app.state.idle

    await idle.touch()
    return await call_next(request)


if __name__ == '__main__':
    pass
