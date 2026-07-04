# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import sys
import locale
import typing
from mind_nova import const


def decode_process_output(data: bytes) -> str:
    """使用候选编码解码进程输出。"""
    if not data:
        return ""

    for encoding in process_output_encodings():
        try:
            return data.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue

    return data.decode(const.CHARSET, errors="replace")


def process_output_encodings() -> list[str]:
    """返回进程输出的候选解码顺序。"""
    candidates: list[typing.Any] = [
        "utf-8-sig",
        const.CHARSET,
        sys.stdout.encoding,
        sys.stderr.encoding,
        locale.getpreferredencoding(False)
    ]

    if os.name == "nt":
        candidates.extend(["mbcs", "oem", "cp936", "gbk"])

    encodings: list[str] = []
    seen: set[str] = set()

    for item in candidates:
        encoding = str(item or "").strip()
        if not encoding:
            continue
        key = encoding.lower().replace("_", "-")
        if key in seen:
            continue
        seen.add(key)
        encodings.append(encoding)

    return encodings


if __name__ == '__main__':
    pass

