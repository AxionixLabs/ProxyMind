#     _                      _   _
#    / \   ___ ___  ___ _ __| |_(_) ___  _ __
#   / _ \ / __/ __|/ _ \ '__| __| |/ _ \| '_ \
#  / ___ \\__ \__ \  __/ |  | |_| | (_) | | | |
# /_/   \_\___/___/\___|_|   \__|_|\___/|_| |_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import typing


class AssertionService(object):

    @staticmethod
    def compare(actual: typing.Any, op: str, expected: typing.Any = None) -> bool:
        """按断言操作符比较实际值与期望值。"""
        match str(op or "").strip().lower():
            case "eq":
                return actual == expected
            case "ne":
                return actual != expected
            case "gt":
                return actual > expected
            case "ge":
                return actual >= expected
            case "lt":
                return actual < expected
            case "le":
                return actual <= expected
            case "contains":
                return str(expected) in str(actual)
            case "in":
                return actual in expected
            case "exists":
                return True
            case "empty":
                return actual in (None, "", [], {}, ())
            case "not_empty":
                return actual not in (None, "", [], {}, ())
            case "regex":
                return re.search(str(expected), str(actual or "")) is not None
            case _:
                raise ValueError(f"unsupported op: {op}")


if __name__ == '__main__':
    pass
