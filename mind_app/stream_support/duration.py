# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math


def format_elapsed(elapsed_sec: float) -> str:
    elapsed = max(0.0, float(elapsed_sec or 0.0))

    minute = 60
    hour = 60 * minute
    day = 24 * hour
    month = 30 * day
    year = 365 * day

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
        hours, rem = divmod(total_seconds, hour)
        minutes, seconds = divmod(rem, minute)
        return f"{hours}h {minutes}m {seconds}s"

    if elapsed < month:
        total_seconds = int(math.floor(elapsed))
        days, rem = divmod(total_seconds, day)
        hours, rem = divmod(rem, hour)
        minutes, _ = divmod(rem, minute)
        return f"{days}d {hours}h {minutes}m"

    if elapsed < year:
        total_seconds = int(math.floor(elapsed))
        months, rem = divmod(total_seconds, month)
        days, rem = divmod(rem, day)
        hours, _ = divmod(rem, hour)
        return f"{months}mo {days}d {hours}h"

    total_seconds = int(math.floor(elapsed))
    years, rem = divmod(total_seconds, year)
    months, rem = divmod(rem, month)
    days, _ = divmod(rem, day)
    return f"{years}y {months}mo {days}d"


if __name__ == "__main__":
    pass
