# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

COMPACT_RULE_PADDING         = 3
COMPACT_RULE_MIN_INNER_WIDTH = 36
COMPACT_RULE_TERMINAL_MARGIN = 4
COMPACT_RULE_MAX_INNER_WIDTH = 82


def compact_rule_min_width(*, padding: int = COMPACT_RULE_PADDING) -> int:
    """返回紧凑规则线的最小宽度。"""
    return COMPACT_RULE_MIN_INNER_WIDTH + int(padding) * 2 + 2


def compact_rule_max_width(*, padding: int = COMPACT_RULE_PADDING) -> int:
    """返回紧凑规则线的最大宽度。"""
    return COMPACT_RULE_MAX_INNER_WIDTH + int(padding)


def compact_rule_width(
    natural_width: int,
    *,
    terminal_width: int | None = None,
    padding: int = COMPACT_RULE_PADDING
) -> int:
    """按统一的紧凑规则线策略计算显示宽度。"""
    minimum = compact_rule_min_width(padding=padding)
    natural = max(0, int(natural_width or 0))

    if terminal_width is None:
        return max(natural, minimum)

    available = max(
        minimum,
        min(
            int(terminal_width) - COMPACT_RULE_TERMINAL_MARGIN,
            compact_rule_max_width(padding=padding)
        )
    )
    return min(max(natural, minimum), available)


def full_rule_width(
    *,
    terminal_width: int | None,
    natural_width: int,
    margin: int = COMPACT_RULE_TERMINAL_MARGIN
) -> int:
    """按终端可用宽度计算完整规则线宽度。"""
    natural = max(0, int(natural_width or 0))
    if terminal_width is None:
        return max(natural, compact_rule_max_width())
    return max(natural, int(terminal_width) - int(margin))


if __name__ == '__main__':
    pass
