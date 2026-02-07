#  _____         _      __  __ _     _     _ _
# |_   _|_ _ ___| | __ |  \/  (_) __| | __| | | _____      ____ _ _ __ ___
#   | |/ _` / __| |/ / | |\/| | |/ _` |/ _` | |/ _ \ \ /\ / / _` | '__/ _ \
#   | | (_| \__ \   <  | |  | | | (_| | (_| | |  __/\ V  V / (_| | | |  __/
#   |_|\__,_|___/_|\_\ |_|  |_|_|\__,_|\__,_|_|\___| \_/\_/ \__,_|_|  \___|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
import functools
from loguru import logger
from backend.utilities.instance import Ins


def task_middleware(tool_name: str):
    
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> typing.Any:
            targets   = kwargs.pop("targets", None)      # None | list[str]
            overrides = kwargs.pop("overrides", None)  # None | dict[str, dict]

            old_ctx: dict   = Ins.CTX.get()
            new_ctx: dict   = dict(old_ctx)
            new_ctx["tool"] = tool_name

            if targets is not None and not isinstance(targets, list):
                raise ValueError("targets must be list[str]")

            new_ctx.pop("targets", None)
            new_ctx.pop("overrides", None)

            if targets:
                new_ctx["targets"] = [str(x).strip() for x in targets if str(x).strip()]
                if isinstance(overrides, dict):
                    new_ctx["overrides"] = {str(k): v for k, v in overrides.items() if isinstance(v, dict)}

            token = Ins.CTX.set(new_ctx)
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.error(f"[ERROR] {tool_name}: {type(e).__name__}: {e}")
                raise
            finally:
                Ins.CTX.reset(token)

        return wrapper
    return decorator


if __name__ == '__main__':
    pass
