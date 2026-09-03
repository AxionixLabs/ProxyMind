# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import subprocess
from pathlib import Path

from protocol.transport import config

MACOS_QUARANTINE_ATTRIBUTE = "com.apple.quarantine"
MACOS_XATTR_EXECUTABLE = "/usr/bin/xattr"


def _run_xattr(arguments: tuple[str, ...]) -> str:
    """执行 macOS xattr 并返回标准输出。"""
    completed = subprocess.run(
        [MACOS_XATTR_EXECUTABLE, *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
        encoding=config.CHARSET,
        errors="replace",
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "xattr failed"
        raise OSError(detail)
    return completed.stdout


def macos_path_has_quarantine(path: Path) -> bool:
    """检查路径是否携带 macOS 下载隔离属性。"""
    attributes = _run_xattr((str(path),))
    return MACOS_QUARANTINE_ATTRIBUTE in attributes.splitlines()


def remove_macos_quarantine_tree(root: Path) -> None:
    """从受控构建目录及其后代移除 macOS 下载隔离属性。"""
    _run_xattr(("-dr", MACOS_QUARANTINE_ATTRIBUTE, str(root)))


if __name__ == '__main__':
    pass
