# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import typing
import functools
from loguru import logger
from backend.utilities.trace import summarize_args


def task_middleware(tool_name: str):
    """Task middleware"""

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> typing.Any:
            t0 = time.perf_counter()
            payload = {
                "kwargs": kwargs,
                "positional_count": len(args)
            }
            logger.debug(f"tool begin tool={tool_name} payload={summarize_args(payload)}")
            try:
                result = await func(*args, **kwargs)
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                ok = None
                if isinstance(result, dict):
                    data = result.get("data")
                    if isinstance(data, dict) and "ok" in data:
                        ok = bool(data.get("ok"))
                    elif "ok" in result:
                        ok = bool(result.get("ok"))

                logger.debug(f"tool end tool={tool_name} ok={ok} elapsed_ms={elapsed_ms}")
                return result
            except Exception as e:
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                logger.error(
                    f"tool error tool={tool_name} elapsed_ms={elapsed_ms} "
                    f"payload={summarize_args(payload)} {type(e).__name__}: {e}"
                )
                raise

        return wrapper
    return decorator


if __name__ == '__main__':
    pass
