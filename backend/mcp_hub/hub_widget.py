#  _   _       _      __        ___     _            _
# | | | |_   _| |__   \ \      / (_) __| | __ _  ___| |_
# | |_| | | | | '_ \   \ \ /\ / /| |/ _` |/ _` |/ _ \ __|
# |  _  | |_| | |_) |   \ V  V / | | (_| | (_| |  __/ |_
# |_| |_|\__,_|_.__/     \_/\_/  |_|\__,_|\__, |\___|\__|
#                                         |___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import typing


class Widget(object):

    def __init__(self, attr: dict) -> None:
        self.id    = attr.get("resource-id", "")
        self.desc  = attr.get("content-desc", "")
        self.text  = attr.get("text", "")
        self.clazz = attr.get("class", "")
        self.bbox  = self.parse_bounds(attr.get("bounds"))

        self.package = attr.get("package", "")

        self.clickable  = self.truthy(attr.get("clickable"))
        self.focusable  = self.truthy(attr.get("focusable"))
        self.scrollable = self.truthy(attr.get("scrollable"))
        self.enabled    = self.truthy(attr.get("enabled"))
        self.checked    = self.truthy(attr.get("checked"))
        self.selected   = self.truthy(attr.get("selected"))

    @property
    def center(self) -> typing.Optional[list[int]]:
        if not self.bbox or len(self.bbox) != 4:
            return None

        x1, y1, x2, y2 = self.bbox
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2

        return [cx, cy]

    @staticmethod
    def truthy(char: typing.Optional[str]) -> bool:
        return (str(char).lower() if char else "") == "true"

    @property
    def credible(self) -> bool:
        return bool(self.id.strip() or self.desc.strip() or self.text.strip())

    @property
    def semantic(self) -> typing.Optional[str]:
        flag_s = ",".join(
            [name for ok, name in [(self.clickable, "click"),(self.focusable, "focus")] if ok]
        )
        return (
            f"[{flag_s}] id={self.id};desc={self.desc};text={self.text};class={self.clazz};bbox={self.bbox}"
        )

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
