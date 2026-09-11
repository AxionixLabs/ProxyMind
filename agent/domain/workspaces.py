# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import ntpath
import posixpath


def workspace_path_key(path: str) -> str:
    """比较已解析目录的路径身份，不读取文件系统或进程环境。"""
    text = path.strip()
    if not text:
        return ""
    windows = text.replace("/", "\\")
    if windows.casefold().startswith("\\\\?\\unc\\"):
        windows = "\\\\" + windows[8:]
    elif windows.startswith("\\\\?\\"):
        windows = windows[4:]
    drive, _tail = ntpath.splitdrive(windows)
    if drive:
        return ntpath.normpath(windows).replace("\\", "/").casefold()
    return posixpath.normpath(text)


if __name__ == '__main__':
    pass
