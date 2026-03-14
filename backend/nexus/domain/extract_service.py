#  _____      _                  _     ____                  _
# | ____|_  _| |_ _ __ __ _  ___| |_  / ___|  ___ _ ____   _(_) ___ ___
# |  _| \ \/ / __| '__/ _` |/ __| __| \___ \ / _ \ '__\ \ / / |/ __/ _ \
# | |___ >  <| |_| | | (_| | (__| |_   ___) |  __/ |   \ V /| | (_|  __/
# |_____/_/\_\\__|_|  \__,_|\___|\__| |____/ \___|_|    \_/ |_|\___\___|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import json
import typing


class ExtractService(object):
    """承载 extract 路径解析、过滤与后处理能力。"""

    _FILTER_SEGMENT_RE = re.compile(r"^\[(.+?)=(.*)](\*)?$")

    @staticmethod
    def _parse_filter_expected(raw: str) -> typing.Any:
        """把过滤条件里的字面量转成基础 Python 值。"""
        text = str(raw)
        lowered = text.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        if lowered == "null":
            return None
        try:
            return int(text)
        except ValueError:
            pass
        try:
            return float(text)
        except ValueError:
            pass
        return text

    @staticmethod
    def split_path_and_ops(path: str) -> tuple[str, list[str]]:
        """把提取路径拆成主路径与后处理操作。"""
        raw = str(path or "").strip()
        if not raw:
            return "", []
        parts = [part.strip() for part in raw.split("|")]
        return parts[0], [part for part in parts[1:] if part]

    @staticmethod
    def _pick_path(data: typing.Any, path: str) -> typing.Any:
        """按点路径从嵌套对象中提取值，支持 * / first / last / 过滤段。"""
        if not path:
            return data

        segments = [segment for segment in str(path).split(".") if segment != ""]

        def walk(current: typing.Any, remaining: list[str]) -> typing.Any:
            if not remaining:
                return current

            segment = remaining[0]
            tail = remaining[1:]

            if segment == "":
                return walk(current, tail)

            if segment == "*":
                if not isinstance(current, list):
                    raise KeyError(segment)
                if not tail:
                    return current
                return [walk(item, tail) for item in current]

            match = ExtractService._FILTER_SEGMENT_RE.match(segment)
            if match:
                if not isinstance(current, list):
                    raise KeyError(segment)

                filter_path = str(match.group(1)).strip()
                expected = ExtractService._parse_filter_expected(match.group(2).strip())
                collect_all = bool(match.group(3))

                matched_items: list[typing.Any] = []
                for item in current:
                    ok_pick, actual = ExtractService.safe_pick(item, filter_path)
                    if ok_pick and actual == expected:
                        if collect_all:
                            matched_items.append(item)
                        else:
                            return walk(item, tail)

                if collect_all:
                    if not matched_items:
                        raise KeyError(segment)
                    if not tail:
                        return matched_items
                    return [walk(item, tail) for item in matched_items]

                raise KeyError(segment)

            if isinstance(current, dict):
                if segment not in current:
                    raise KeyError(segment)
                return walk(current[segment], tail)

            if isinstance(current, list):
                if segment == "first":
                    return walk(current[0], tail)
                if segment == "last":
                    return walk(current[-1], tail)
                return walk(current[int(segment)], tail)

            raise KeyError(segment)

        return walk(data, segments)

    @staticmethod
    def apply_ops(value: typing.Any, ops: list[str]) -> typing.Any:
        """对提取结果执行最小后处理：default / len / join / json / pick / regex。"""
        current = value

        for op in ops:
            op_name, _, op_arg = op.partition(":")
            op_name = op_name.strip().lower()
            op_arg = op_arg.strip()

            if op_name == "default":
                if current is None:
                    current = op_arg
                continue

            if op_name == "len":
                current = len(current) if current is not None else 0
                continue

            if op_name == "join":
                sep = op_arg
                if not isinstance(current, list):
                    raise ValueError("join requires list value")
                current = sep.join("" if item is None else str(item) for item in current)
                continue

            if op_name == "json":
                current = json.loads(str(current or ""))
                continue

            if op_name == "pick":
                if not op_arg:
                    raise ValueError("pick requires path")
                current = ExtractService._pick_path(current, op_arg)
                continue

            if op_name == "regex":
                pattern = op_arg
                if not pattern:
                    raise ValueError("regex requires pattern")
                match = re.search(pattern, "" if current is None else str(current))
                if not match:
                    raise ValueError("regex no match")
                if match.groups():
                    current = match.group(1)
                else:
                    current = match.group(0)
                continue

            if op_name == "regex_group":
                if not op_arg:
                    raise ValueError("regex_group requires index")
                if not isinstance(current, re.Match):
                    raise ValueError("regex_group requires previous regex match object")
                current = current.group(int(op_arg))
                continue

            raise ValueError(f"unsupported extract op: {op_name}")

        return current

    @staticmethod
    def pick(data: typing.Any, path: str) -> typing.Any:
        """按增强路径语法提取值。"""
        main_path, ops = ExtractService.split_path_and_ops(path)
        try:
            current = ExtractService._pick_path(data, main_path)
        except Exception as e:
            default_op = next((op for op in ops if op.strip().lower().startswith("default:")), None)
            if default_op is None:
                raise e
            current = None
        return ExtractService.apply_ops(current, ops)

    @staticmethod
    def safe_pick(data: typing.Any, path: str) -> tuple[bool, typing.Any]:
        """安全提取路径值，失败时返回错误信息而不是抛异常。"""
        try:
            return True, ExtractService.pick(data, path)
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"


if __name__ == '__main__':
    pass
