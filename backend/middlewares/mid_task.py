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


def task_middleware(tool_name: str):
    """Task middleware"""
    
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> typing.Any:
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.error(f"[ERROR] {tool_name}: {type(e).__name__}: {e}")
                raise
                
        return wrapper
    return decorator


if __name__ == '__main__':
    pass
