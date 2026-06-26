# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing

CID_RE = re.compile(r"^cid_([0-9a-z]+)_[0-9a-f]{8}$")
SID_RE = re.compile(r"^sid_([0-9a-z]+)_[0-9a-z]+_[0-9a-f]{6}$")


def valid_session_ids(cid: typing.Any, sid: typing.Any) -> bool:
    """校验本地 history 恢复使用的 cid/sid 格式。"""
    cid_text = str(cid or "").strip()
    sid_text = str(sid or "").strip()

    cid_match = CID_RE.fullmatch(cid_text)
    sid_match = SID_RE.fullmatch(sid_text)

    if cid_match is None or sid_match is None:
        return False

    return cid_match.group(1) == sid_match.group(1)


if __name__ == '__main__':
    pass
