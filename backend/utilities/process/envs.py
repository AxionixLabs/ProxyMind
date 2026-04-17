# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os


def spawn_env() -> dict[str, str]:
    """为外部 Python 工具提供更稳的 UTF-8/纯文本子进程环境。"""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["TERM"] = "dumb"
    env["NO_COLOR"] = "1"
    return env


if __name__ == '__main__':
    pass
