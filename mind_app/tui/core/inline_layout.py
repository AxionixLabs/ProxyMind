# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

class BottomAnchorState(object):
    """保存底部区域的占位、增长基线和释放状态。"""

    __slots__ = (
        "footprint_height",
        "completion_visible",
        "stable_line_baseline",
        "live_height_baseline",
        "consumed_height",
        "release_active",
    )

    footprint_height: int
    completion_visible: bool
    stable_line_baseline: int
    live_height_baseline: int
    consumed_height: int
    release_active: bool

    def __init__(self) -> None:
        self.footprint_height = 0
        self.completion_visible = False
        self.stable_line_baseline = 0
        self.live_height_baseline = 0
        self.consumed_height = 0
        self.release_active = False

    def frame_key(self) -> tuple[int | bool, ...]:
        """返回影响底部释放空间的状态键。"""
        return (
            self.footprint_height,
            self.completion_visible,
            self.stable_line_baseline,
            self.live_height_baseline,
            self.consumed_height,
            self.release_active,
        )

    def begin(
        self,
        *,
        footprint_height: int,
        completion_visible: bool,
        stable_line_baseline: int,
        live_height_baseline: int,
        release_active: bool = False,
    ) -> None:
        """建立新的底部占位和正文增长基线。"""
        self.footprint_height = max(0, footprint_height)
        self.completion_visible = completion_visible
        self.stable_line_baseline = max(0, stable_line_baseline)
        self.live_height_baseline = max(0, live_height_baseline)
        self.consumed_height = 0
        self.release_active = release_active

    def preserve_footprint(self, minimum_height: int) -> None:
        """保留不小于指定值的底部占位高度。"""
        self.footprint_height = max(
            self.footprint_height,
            max(0, minimum_height),
        )

    def consume(self, height: int) -> None:
        """按正文增长量单调消费底部占位高度。"""
        self.consumed_height = min(
            self.footprint_height,
            max(self.consumed_height, max(0, height)),
        )

    def begin_release(
        self,
        *,
        stable_line_baseline: int,
        live_height_baseline: int,
    ) -> None:
        """从临时区域关闭时的正文位置开始消费底部占位。"""
        self.completion_visible = False
        self.stable_line_baseline = max(0, stable_line_baseline)
        self.live_height_baseline = max(0, live_height_baseline)
        self.consumed_height = 0
        self.release_active = True

    def clamp(self, maximum_height: int) -> None:
        """把占位及已消费高度限制在终端可用范围内。"""
        maximum_height = max(0, maximum_height)
        self.footprint_height = min(
            self.footprint_height,
            maximum_height,
        )
        self.consumed_height = min(
            self.consumed_height,
            self.footprint_height,
        )

    def release_height(self, occupied_height: int) -> int:
        """返回当前仍需保留在输入区下方的高度。"""
        return max(
            0,
            self.footprint_height
            - self.consumed_height
            - max(0, occupied_height),
        )

    def clear(self) -> None:
        """清除底部占位和正文增长基线。"""
        self.footprint_height = 0
        self.completion_visible = False
        self.stable_line_baseline = 0
        self.live_height_baseline = 0
        self.consumed_height = 0
        self.release_active = False


class InlineLayoutState(object):
    """维护内联画布高度及输入区收束状态。"""

    __slots__ = (
        "canvas_height_floor",
        "input_growth_baseline",
        "bottom_anchor",
        "input_anchor",
        "input_canvas_saturated",
    )

    canvas_height_floor: int
    input_growth_baseline: int | None
    bottom_anchor: BottomAnchorState
    input_anchor: BottomAnchorState
    input_canvas_saturated: bool

    def __init__(
        self,
        canvas_height_floor: int = 0,
        input_growth_baseline: int | None = None,
        bottom_anchor: BottomAnchorState | None = None,
        input_anchor: BottomAnchorState | None = None,
        input_canvas_saturated: bool = False,
    ) -> None:
        self.canvas_height_floor = canvas_height_floor
        self.input_growth_baseline = input_growth_baseline
        self.bottom_anchor = (
            bottom_anchor if bottom_anchor is not None else BottomAnchorState()
        )
        self.input_anchor = (
            input_anchor if input_anchor is not None else BottomAnchorState()
        )
        self.input_canvas_saturated = input_canvas_saturated

    @property
    def input_growth_active(self) -> bool:
        """返回是否已记录多行输入增长前的画布高度。"""
        return self.input_growth_baseline is not None

    def frame_key(self) -> tuple[int | bool | None, ...]:
        """返回影响执行周期收束画面的状态键。"""
        return (
            self.canvas_height_floor,
            self.input_growth_baseline,
            self.input_canvas_saturated,
            *self.bottom_anchor.frame_key(),
            *self.input_anchor.frame_key(),
        )

    def observe_input_layout(
        self,
        *,
        input_height: int,
        footprint_height: int,
        stable_line_baseline: int,
        live_height_baseline: int,
    ) -> None:
        """记录多行输入增长前的画布和输入区峰值。"""
        if input_height > 1 and self.input_growth_baseline is None:
            self.input_growth_baseline = self.canvas_height_floor
            if self._input_anchor_reusable():
                self.input_anchor.preserve_footprint(footprint_height)
            else:
                self.input_canvas_saturated = False
                self.input_anchor.begin(
                    footprint_height=footprint_height,
                    completion_visible=False,
                    stable_line_baseline=stable_line_baseline,
                    live_height_baseline=live_height_baseline,
                    release_active=True,
                )
        elif self.input_growth_baseline is not None:
            self.input_anchor.preserve_footprint(footprint_height)

    def _input_anchor_reusable(self) -> bool:
        """判断上一次输入释放空间是否仍可供后续增长复用。"""
        return bool(
            self.input_canvas_saturated
            and self.input_anchor.release_active
            and self.input_anchor.consumed_height
            < self.input_anchor.footprint_height
        )

    def settle_input(self, *, natural_height: int, input_empty: bool) -> None:
        """按输入状态从底部向上收束画布高度。"""
        baseline = self.input_growth_baseline
        if baseline is None:
            return None

        if input_empty:
            self.bottom_anchor.clear()
            self.input_growth_baseline = None
            maximum_height = natural_height
        else:
            maximum_height = max(baseline, natural_height)

        self.cap_canvas_height(maximum_height)

    def cap_canvas_height(self, maximum_height: int) -> None:
        """降低画布高度下限但不主动增加高度。"""
        self.canvas_height_floor = min(
            self.canvas_height_floor,
            max(0, int(maximum_height)),
        )

    def fit_canvas_height(
        self,
        *,
        natural_height: int,
        available_height: int,
    ) -> int:
        """按自然高度、既有下限和可用高度确定画布高度。"""
        available_height = max(1, int(available_height))
        canvas_height = min(
            available_height,
            max(
                self.canvas_height_floor,
                max(1, int(natural_height)),
            ),
        )
        self.canvas_height_floor = canvas_height
        if (
            self.input_growth_active
            and canvas_height >= available_height
        ):
            self.input_canvas_saturated = True
        self.input_anchor.clamp(available_height)
        return canvas_height

    def preserve_canvas_height(
        self,
        minimum_height: int,
        *,
        available_height: int,
    ) -> None:
        """在可用范围内保留不低于指定值的画布高度。"""
        available_height = max(1, int(available_height))
        self.canvas_height_floor = min(
            available_height,
            max(
                self.canvas_height_floor,
                min(
                    available_height,
                    max(0, int(minimum_height)),
                ),
            ),
        )

    def bottom_release_height(self, occupied_height: int) -> int:
        """返回各类底部锚点仍需保留的最大释放高度。"""
        input_release_height = (
            self.input_anchor.release_height(occupied_height)
            if self.input_canvas_saturated
            else 0
        )
        return max(
            self.bottom_anchor.release_height(occupied_height),
            input_release_height,
        )

    def reset(self) -> None:
        """清除画布高度、输入增长基线和底部锚点。"""
        self.canvas_height_floor = 0
        self.bottom_anchor.clear()
        self.cancel_input_growth()

    def cancel_input_growth(self) -> None:
        """取消输入增长周期及其底部释放空间。"""
        self.input_growth_baseline = None
        self.input_canvas_saturated = False
        self.input_anchor.clear()

    def release_empty_input(self) -> None:
        """在输入为空时清除输入增长基线。"""
        self.input_growth_baseline = None
