#  _____                _       __  __ _     _     _ _
# |_   _|__  _   _  ___| |__   |  \/  (_) __| | __| | | _____      ____ _ _ __ ___
#   | |/ _ \| | | |/ __| '_ \  | |\/| | |/ _` |/ _` | |/ _ \ \ /\ / / _` | '__/ _ \
#   | | (_) | |_| | (__| | | | | |  | | | (_| | (_| | |  __/\ V  V / (_| | | |  __/
#   |_|\___/ \__,_|\___|_| |_| |_|  |_|_|\__,_|\__,_|_|\___| \_/\_/ \__,_|_|  \___|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from fastapi import Request
from backend.utilities.pipeline import Idle


def touch_middleware(idle: Idle) -> typing.Callable:
    async def func(request: Request, call_next: typing.Callable) -> typing.Any:
        await idle.touch()
        return await call_next(request)
    return func


if __name__ == '__main__':
    pass
