# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import hashlib
from collections.abc import Mapping


def derive_local_session_id(
    source: str,
    coordinates: Mapping[str, object],
) -> str:
    """从线上会话坐标派生不泄露原始标识的稳定本地 Session 身份。"""
    normalized_source = str(source or "").strip().lower()
    cid = str(coordinates.get("cid") or "").strip()
    sid = str(coordinates.get("sid") or "").strip()
    if not normalized_source or not cid or not sid:
        raise ValueError("local session identity requires source, cid and sid")
    digest = hashlib.sha256(
        f"{normalized_source}\0{cid}\0{sid}".encode("utf-8")
    ).hexdigest()[:32]
    return f"{normalized_source}_session_{digest}"


if __name__ == '__main__':
    pass
