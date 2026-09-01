# -*- coding: utf-8 -*-

import hashlib
import typing


def derive_stable_id(prefix: str, *parts: typing.Any) -> str:
    """根据规范化前缀和稳定组成部分派生确定性标识。"""
    normalized_prefix = str(prefix or "id").strip("_-") or "id"
    encoded = "\x1f".join(str(part or "") for part in parts).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:40]
    return f"{normalized_prefix}_{digest}"
