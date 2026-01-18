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

                payload = {
                    "ok"          : True,
                    "code"        : "OK",
                    "message"     : "OK",
                    "tool"        : tool_name,
                    "args"        : snapshot,
                    "trace_id"    : trace_id,
                    "ts"          : now_iso(),
                    "duration_ms" : int((time.perf_counter() - t0) * 1000),
                    "retryable"   : False,
                    "severity"    : "info",
                    "data"        : data,
                    "meta"        : {}
                }
                logger.info(payload)

                return data

            except asyncio.CancelledError as e:
                payload = {
                    "ok"          : False,
                    "code"        : "CANCELLED",
                    "message"     : "Tool execution cancelled",
                    "tool"        : tool_name,
                    "args"        : snapshot,
                    "trace_id"    : trace_id,
                    "ts"          : now_iso(),
                    "duration_ms" : int((time.perf_counter() - t0) * 1000),
                    "retryable"   : False,
                    "severity"    : "warn",
                    "data"        : None,
                    "meta"        : {}
                }
                logger.warning(payload)
                raise e

            except Exception as e:
                payload = {
                    "ok"          : False,
                    "code"        : "TOOL CRASH",
                    "message"     : str(e),
                    "tool"        : tool_name,
                    "args"        : snapshot,
                    "trace_id"    : trace_id,
                    "ts"          : now_iso(),
                    "duration_ms" : int((time.perf_counter() - t0) * 1000),
                    "retryable"   : False,
                    "severity"    : "error",
                    "data"        : None,
                    "meta"        : {}
                }
                logger.error(payload)
                raise e

        return wrapper

    return decorator


if __name__ == '__main__':
    pass
