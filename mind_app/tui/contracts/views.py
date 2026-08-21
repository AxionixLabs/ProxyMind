# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import dataclass
from enum import Enum


class ViewCompletion(str, Enum):
    """描述交互视图的终止语义。"""
    ACCEPTED = "accepted"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ViewIdentity(object):
    """描述一个底部选择视图的稳定身份和会话代数。"""
    view_id: str | None
    generation: int
    session_id: int | None


if __name__ == '__main__':
    pass
