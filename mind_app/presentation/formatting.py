# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math


def format_elapsed(elapsed_sec: float) -> str:
    """把经过时间格式化为紧凑标签。"""
    elapsed = max(0.0, float(elapsed_sec or 0.0))
    minute = 60
    hour = 60 * minute
    day = 24 * hour
    month = 30 * day
    year = 365 * day

    if elapsed < minute:
        return f"{elapsed:.1f}s" if elapsed < 10 else f"{int(elapsed)}s"
    total_seconds = int(math.floor(elapsed))
    if elapsed < hour:
        minutes, seconds = divmod(total_seconds, minute)
        return f"{minutes}m {seconds}s"
    if elapsed < day:
        hours, remainder = divmod(total_seconds, hour)
        minutes, seconds = divmod(remainder, minute)
        return f"{hours}h {minutes}m {seconds}s"
    if elapsed < month:
        days, remainder = divmod(total_seconds, day)
        hours, remainder = divmod(remainder, hour)
        minutes, _ = divmod(remainder, minute)
        return f"{days}d {hours}h {minutes}m"
    if elapsed < year:
        months, remainder = divmod(total_seconds, month)
        days, remainder = divmod(remainder, day)
        hours, _ = divmod(remainder, hour)
        return f"{months}mo {days}d {hours}h"
    years, remainder = divmod(total_seconds, year)
    months, remainder = divmod(remainder, month)
    days, _ = divmod(remainder, day)
    return f"{years}y {months}mo {days}d"


if __name__ == '__main__':
    pass
