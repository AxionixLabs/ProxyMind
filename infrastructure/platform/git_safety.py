# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os

SAFE_BARE_REPOSITORY_CONFIG = "safe.bareRepository=explicit"
EXECUTABLE_FILTER_CONFIG_PATTERN = r"^filter\..*\.(clean|process)$"


def disabled_git_hooks_config() -> str:
    """返回当前平台禁用 Git hooks 的临时配置。"""
    return "core.hooksPath=NUL" if os.name == "nt" else "core.hooksPath=/dev/null"


if __name__ == '__main__':
    pass
