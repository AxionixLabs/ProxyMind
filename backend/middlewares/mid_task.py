#  _____         _      __  __ _     _     _ _
# |_   _|_ _ ___| | __ |  \/  (_) __| | __| | | _____      ____ _ _ __ ___
#   | |/ _` / __| |/ / | |\/| | |/ _` |/ _` | |/ _ \ \ /\ / / _` | '__/ _ \
#   | | (_| \__ \   <  | |  | | | (_| | (_| | |  __/\ V  V / (_| | | |  __/
#   |_|\__,_|___/_|\_\ |_|  |_|_|\__,_|\__,_|_|\___| \_/\_/ \__,_|_|  \___|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import uuid
import typing
import asyncio
import functools
from loguru import logger
from datetime import (
    datetime, timezone
)

now_iso: typing.Callable[
    [], str
] = lambda: datetime.now(timezone.utc).isoformat()


def task_middleware(tool_name: str):

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> typing.Any:
            t0, trace_id = time.perf_counter(), uuid.uuid4().hex

            try:
                snapshot: dict[str, typing.Any] = {**kwargs}
                if args:
                    snapshot["_pos"] = [repr(a) for a in args[:3]]
            except Exception as e:
                snapshot = {"_snapshot": f"failed {e}"}

            try:
                data = await func(*args, **kwargs)

                logger.info({
                    "ok"     : 1,
                    "tool"   : tool_name,
                    "trace"  : trace_id,
                    "dur_ms" : int((time.perf_counter() - t0) * 1000),
                    "args"   : snapshot,
                })
                return data

            except asyncio.CancelledError:
                logger.warning({
                    "ok"     : 0,
                    "tool"   : tool_name,
                    "trace"  : trace_id,
                    "code"   : f"CANCELLED",
                    "dur_ms" : int((time.perf_counter() - t0) * 1000),
                    "args"   : snapshot,
                })
                raise

            except Exception as e:
                logger.error({
                    "ok"     : 0,
                    "tool"   : tool_name,
                    "trace"  : trace_id,
                    "code"   : f"CRASH",
                    "err"    : f"{type(e).__name__}: {e}",
                    "dur_ms" : int((time.perf_counter() - t0) * 1000),
                    "args"   : snapshot
                })
                raise

        return wrapper

    return decorator


if __name__ == '__main__':
    pass
