# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field

ScrollDirectionArg = typing.Annotated[
    typing.Literal["up", "down", "left", "right"],
    Field(description="内容移动方向，不是手指滑动方向。")
]
EdgeArg = typing.Annotated[
    typing.Literal["top", "bottom"],
    Field(description="要滚动到的页面边界。")
]
LocatorByArg = typing.Annotated[
    typing.Literal["id", "desc", "text", "bbox", "xpath"],
    Field(description="定位方式。")
]
LocatorValueArg = typing.Annotated[
    typing.Union[str, list],
    Field(description="定位值；字符串定位直接传文本，`bbox` 通常传坐标列表。")
]
MatchModeArg = typing.Annotated[
    typing.Literal["eq", "contains", "regex"],
    Field(description="字符串类定位的匹配方式。")
]
IgnoreCaseArg = typing.Annotated[
    bool,
    Field(description="字符串类定位时是否忽略大小写。")
]
CoordArg = typing.Annotated[
    int,
    Field(description="绝对屏幕坐标值。")
]
DurationArg = typing.Annotated[
    int,
    Field(description="手势持续时间，单位毫秒。")
]
TimeoutArg = typing.Annotated[
    float,
    Field(description="超时时间，单位秒。")
]
MaxSwipesArg = typing.Annotated[
    int,
    Field(description="最多尝试的滑动次数。")
]
ShouldClickArg = typing.Annotated[
    bool,
    Field(description="命中目标后是否继续点击。")
]
InputTextArg = typing.Annotated[
    str,
    Field(description="要注入到当前输入焦点的文本内容。")
]
WidgetViewArg = typing.Annotated[
    typing.Literal["interactive", "credible", "all"],
    Field(description="Widget 返回范围，可选可交互、可信标识或全部控件。")
]
LocatorArg = typing.Annotated[
    str,
    Field(description="一条完整 locator 表达式，用于愈合诊断。")
]
WaitStateArg = typing.Annotated[
    typing.Literal["exists", "gone"],
    Field(description="等待目标状态，是出现还是消失。")
]


if __name__ == '__main__':
    pass
