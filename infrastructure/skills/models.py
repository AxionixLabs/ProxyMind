# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from pathlib import Path
from dataclasses import dataclass


@dataclass(frozen=True)
class SkillSpec:
    """本地可发现 skill 的轻量描述。"""

    name: str
    description: str
    source: str
    root: Path
    entry: Path

    @staticmethod
    def scope_for_source(source: str) -> str:
        """把本地来源映射为服务端技能作用域。"""
        return {
            "project": "repo",
            "user": "user",
            "bundled": "global"
        }.get(source, source)

    def payload(self) -> dict[str, str]:
        """返回可放入模型请求的轻量载荷。"""
        return {
            "name": self.name,
            "description": self.description,
            "path": str(self.entry),
            "scope": self.scope_for_source(self.source)
        }


if __name__ == '__main__':
    pass
