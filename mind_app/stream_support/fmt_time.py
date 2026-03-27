# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math


ELAPSED_WIDTHS = {
    "sub10s" : 4,
    "secs"   : 4,
    "mins"   : 7,
    "hours"  : 11,
    "days"   : 11,
    "months" : 12,
    "years"  : 12
}


def max_elapsed_display_width() -> int:
    return max(int(width) for width in ELAPSED_WIDTHS.values())


def elapsed_format_key(elapsed_sec: float) -> str:
    elapsed = max(0.0, float(elapsed_sec or 0.0))

    if elapsed < 10:
        return "sub10s"
    if elapsed < 60:
        return "secs"
    if elapsed < 3600:
        return "mins"
    if elapsed < 86400:
        return "hours"
    if elapsed < 2592000:
        return "days"
    if elapsed < 31536000:
        return "months"
    return "years"


def elapsed_display_width(key: str) -> int:
    return int(ELAPSED_WIDTHS.get(str(key or ""), 12))


def pad_elapsed_label(label: str, *, key: str, width: int | None = None) -> str:
    target_width = elapsed_display_width(key) if width is None else max(1, int(width))
    return str(label or "").ljust(target_width)


def format_elapsed(elapsed_sec: float) -> str:
    elapsed = max(0.0, float(elapsed_sec or 0.0))

    minute = 60
    hour   = 60 * minute
    day    = 24 * hour
    month  = 30 * day
    year   = 365 * day

    if elapsed < minute:
        if elapsed < 10:
            return f"{elapsed:.1f}s"
        return f"{int(elapsed)}s"

    if elapsed < hour:
        total_seconds = int(math.floor(elapsed))

        minutes, seconds = divmod(total_seconds, minute)
        return f"{minutes}m {seconds}s"

    if elapsed < day:
        total_seconds = int(math.floor(elapsed))

        hours, rem       = divmod(total_seconds, hour)
        minutes, seconds = divmod(rem, minute)
        return f"{hours}h {minutes}m {seconds}s"

    if elapsed < month:
        total_seconds = int(math.floor(elapsed))

        days, rem  = divmod(total_seconds, day)
        hours, rem = divmod(rem, hour)
        minutes, _ = divmod(rem, minute)
        return f"{days}d {hours}h {minutes}m"

    if elapsed < year:
        total_seconds = int(math.floor(elapsed))

        months, rem = divmod(total_seconds, month)
        days, rem   = divmod(rem, day)
        hours, _    = divmod(rem, hour)
        return f"{months}mo {days}d {hours}h"

    total_seconds = int(math.floor(elapsed))

    years, rem  = divmod(total_seconds, year)
    months, rem = divmod(rem, month)
    days, _     = divmod(rem, day)
    return f"{years}y {months}mo {days}d"


if __name__ == "__main__":
    pass
