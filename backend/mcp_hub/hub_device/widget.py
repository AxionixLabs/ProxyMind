# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import typing


class Widget(object):

    def __init__(self, attr: dict):
        self.id    = attr.get("resource-id", "")
        self.desc  = attr.get("content-desc", "").strip()
        self.text  = attr.get("text", "").strip()
        self.clazz = attr.get("class", "")
        self.bbox  = self.parse_bounds(attr.get("bounds"))

        self.checkable  = self.truthy(attr.get("checkable"))
        self.checked    = self.truthy(attr.get("checked"))
        self.clickable  = self.truthy(attr.get("clickable"))
        self.enabled    = self.truthy(attr.get("enabled"))
        self.focusable  = self.truthy(attr.get("focusable"))
        self.scrollable = self.truthy(attr.get("scrollable"))
        self.selected   = self.truthy(attr.get("selected"))

    @property
    def center(self) -> typing.Optional[list[int]]:
        """根据 bbox 计算控件中心点坐标 [cx,cy]；bbox 无效则返回 None。"""
        if not self.bbox or len(self.bbox) != 4:
            return None

        x1, y1, x2, y2 = self.bbox
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2

        return [cx, cy]

    @property
    def semantic(self) -> str:
        """生成语义化摘要字符串：flags + 关键属性（便于日志/LLM/检索）。"""
        flags = [
            (self.clickable, "click"), (self.focusable, "focus"), (self.scrollable, "scroll")
        ]
        label = ",".join([v for k, v in flags if k])
        return (
            f"[{label}] id={self.id};desc={self.desc};text={self.text};class={self.clazz};bbox={self.bbox}"
        )

    def to_node(self) -> dict[str, typing.Any]:
        """导出统一节点结构。"""
        return {
            "id"     : self.id,
            "desc"   : self.desc,
            "text"   : self.text,
            "class"  : self.clazz,
            "center" : self.center,
            "bbox"   : self.bbox
        }

    @staticmethod
    def truthy(char: typing.Optional[str]) -> bool:
        """将 XML 属性值（'true'/'false'/None）安全转换为 bool。"""
        return (str(char).lower() if char else "") == "true"

    @staticmethod
    def parse_bounds(bounds: typing.Optional[str]) -> typing.Optional[list[int]]:
        """解析 Android bounds 字符串："[x1,y1][x2,y2]" -> [x1,y1,x2,y2]。"""
        if not bounds: return None
        pattern = re.compile(r"\[(\d+),(\d+)]\[(\d+),(\d+)]")

        if not (m := pattern.match(bounds)):
            return None
        x1, y1, x2, y2 = map(int, m.groups())

        return [x1, y1, x2, y2]


if __name__ == '__main__':
    pass
