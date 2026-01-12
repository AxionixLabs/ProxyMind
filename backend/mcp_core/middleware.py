#  __  __ _     _     _ _
# |  \/  (_) __| | __| | | _____      ____ _ _ __ ___
# | |\/| | |/ _` |/ _` | |/ _ \ \ /\ / / _` | '__/ _ \
# | |  | | | (_| | (_| | |  __/\ V  V / (_| | | |  __/
# |_|  |_|_|\__,_|\__,_|_|\___| \_/\_/ \__,_|_|  \___|
#

import time
import uuid
import typing
import asyncio
import functools
from datetime import (
    datetime, timezone
)
from mcp.types import (
    CallToolResult, TextContent
)


def as_mcp_result(payload: dict[str, typing.Any] | None) -> CallToolResult:
    ok   = bool(payload.get("ok", True))
    code = payload.get("code", "OK")
    msg  = payload.get("message", "")
    tool = payload.get("tool", "tool")

    summary = f"{tool} :: {code} :: {msg}"

    return CallToolResult(
        content=[TextContent(type="text", text=summary)], structuredContent=payload, isError=(not ok)
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def done(
    *,
    tool: str,
    args: dict[str, typing.Any],
    data: typing.Any = None,
    message: str = "OK",
    trace_id: str,
    duration_ms: int,
    **meta
) -> dict[str, typing.Any]:

    return {
        "ok"          : True,
        "code"        : "OK",
        "message"     : message,
        "tool"        : tool,
        "args"        : args,
        "trace_id"    : trace_id,
        "ts"          : _now_iso(),
        "duration_ms" : duration_ms,
        "retryable"   : False,
        "severity"    : "info",
        "data"        : data,
        "meta"        : meta or {},
    }


def fail(
    *,
    tool: str,
    args: dict[str, typing.Any],
    code: str,
    message: str,
    retryable: bool,
    trace_id: str,
    duration_ms: int,
    severity: str = "error",
    **meta
) -> dict[str, typing.Any]:

    return {
        "ok"          : False,
        "code"        : code,
        "message"     : message,
        "tool"        : tool,
        "args"        : args,
        "trace_id"    : trace_id,
        "ts"          : _now_iso(),
        "duration_ms" : duration_ms,
        "retryable"   : retryable,
        "severity"    : severity,
        "data"        : None,
        "meta"        : meta or {},
    }


def exception_middleware(tool_name: str):

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> CallToolResult:
            t0, trace_id = time.perf_counter(), uuid.uuid4().hex
            try:
                snapshot: dict[str, typing.Any] = {**kwargs}
                if args: snapshot["_pos"] = [repr(a) for a in args[:3]]
            except Exception as e:
                snapshot = {"_snapshot": f"failed {e}"}

            try:
                data = await func(*args, **kwargs)

                payload = done(
                    tool=tool_name,
                    args=snapshot,
                    data=data,
                    message="OK",
                    trace_id=trace_id,
                    duration_ms=int((time.perf_counter() - t0) * 1000)
                )
                return as_mcp_result(payload)

            except asyncio.CancelledError:
                payload = fail(
                    tool=tool_name,
                    args=snapshot,
                    code="CANCELLED",
                    message="Tool execution cancelled",
                    retryable=False,
                    trace_id=trace_id,
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                    severity="warn"
                )
                return as_mcp_result(payload)

            except Exception as e:
                payload = fail(
                    tool=tool_name,
                    args=snapshot,
                    code="TOOL_CRASH",
                    message=str(e),
                    retryable=False,
                    trace_id=trace_id,
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                    severity="error"
                )
                return as_mcp_result(payload)

        return wrapper

    return decorator


if __name__ == '__main__':
    pass
