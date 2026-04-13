# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import ast
import gzip
import json
import time
import uuid
import zlib
import base64
import string
import typing
import secrets
import operator
from loguru import logger
from datetime import (
    datetime, timedelta, timezone
)
from urllib.parse import (
    urlencode, parse_qs, quote
)
from collections.abc import (
    Callable, Mapping
)
from backend.mcp_hub.hub_nexus.domain.extract import ExtractService
from backend.utilities.trace import clip_text


class TemplateEvaluation(object):

    EXPR_RE = re.compile(r"\{\{\s*(.*?)\s*}}")

    _BIN_OPS: Mapping[type[ast.operator], Callable[[typing.Any, typing.Any], typing.Any]] = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod
    }

    _CMP_OPS: Mapping[type[ast.cmpop], Callable[[typing.Any, typing.Any], bool]] = {
        ast.Eq: operator.eq,
        ast.NotEq: operator.ne,
        ast.Gt: operator.gt,
        ast.GtE: operator.ge,
        ast.Lt: operator.lt,
        ast.LtE: operator.le,
        ast.In: lambda a, b: a in b,
        ast.NotIn: lambda a, b: a not in b
    }

    _BOOL_OPS: Mapping[type[ast.boolop], Callable[[list[typing.Any]], bool]] = {
        ast.And: all,
        ast.Or: any
    }

    _UNARY_OPS: Mapping[type[ast.unaryop], Callable[[typing.Any], typing.Any]] = {
        ast.UAdd: operator.pos,
        ast.USub: operator.neg,
        ast.Not: operator.not_
    }

    @staticmethod
    def _helper_pick(source: typing.Any, path: str, default: typing.Any = None) -> typing.Any:
        """复用 ExtractService 的增强路径语法取值，失败时返回默认值。"""
        ok_pick, value = ExtractService.safe_pick(source, path)
        return value if ok_pick else default

    @staticmethod
    def _helper_coalesce(*values: typing.Any) -> typing.Any:
        """返回第一个非空值。"""
        for value in values:
            if value not in (None, "", [], {}, ()):
                return value
        return None

    @staticmethod
    def _helper_now_s() -> int:
        """返回当前秒级时间戳。"""
        return int(time.time())

    @staticmethod
    def _helper_now_ms() -> int:
        """返回当前毫秒级时间戳。"""
        return int(time.time() * 1000)

    @staticmethod
    def _helper_uuid4() -> str:
        """返回 uuid4 文本。"""
        return str(uuid.uuid4())

    @staticmethod
    def _helper_nonce(length: int = 16, alphabet: str | None = None) -> str:
        """返回指定长度的随机文本。"""
        size = max(1, int(length))
        letters = alphabet if isinstance(alphabet, str) and alphabet else (string.ascii_letters + string.digits)
        return "".join(secrets.choice(letters) for _ in range(size))

    @staticmethod
    def _helper_b64encode(value: typing.Any, encoding: str = "utf-8") -> str:
        """把文本或二进制编码为 base64 文本。"""
        if isinstance(value, bytes):
            source = value
        elif isinstance(value, bytearray):
            source = bytes(value)
        else:
            source = str("" if value is None else value).encode(encoding)
        return base64.b64encode(source).decode("ascii")

    @staticmethod
    def _helper_b64decode(value: typing.Any, encoding: str = "utf-8", as_text: bool = True) -> typing.Any:
        """把 base64 文本解码为文本或字节。"""
        decoded = base64.b64decode(str(value or "").strip(), validate=True)
        return decoded.decode(encoding) if as_text else decoded

    @staticmethod
    def _helper_json_dumps(
        value: typing.Any,
        ensure_ascii: bool = False,
        sort_keys: bool = False
    ) -> str:
        """把对象稳定序列化为 JSON 文本。"""
        return json.dumps(value, ensure_ascii=ensure_ascii, sort_keys=sort_keys, separators=(",", ":"))

    @staticmethod
    def _helper_json_loads(value: typing.Any) -> typing.Any:
        """把 JSON 文本反序列化为对象。"""
        return json.loads(str(value or ""))

    @staticmethod
    def _helper_urlencode(
        value: dict[str, typing.Any],
        doseq: bool = True,
        safe: str = "",
        plus_for_space: bool = True
    ) -> str:
        """把 dict 编码为 query string。"""
        if not isinstance(value, dict):
            raise ValueError("urlencode expects dict")
        quote_via = None if plus_for_space else quote
        if quote_via is None:
            return urlencode(value, doseq=bool(doseq), safe=str(safe or ""))
        return urlencode(value, doseq=bool(doseq), safe=str(safe or ""), quote_via=quote_via)

    @staticmethod
    def _helper_urldecode(value: typing.Any, keep_blank_values: bool = True) -> dict[str, typing.Any]:
        """把 query string 解码为 dict。"""
        parsed = parse_qs(str(value or ""), keep_blank_values=bool(keep_blank_values))
        return {
            key: values[0] if isinstance(values, list) and len(values) == 1 else values
            for key, values in parsed.items()
        }

    @staticmethod
    def _helper_dict_merge(*items: typing.Any) -> dict[str, typing.Any]:
        """按顺序合并多个 dict，后者覆盖前者。"""
        merged: dict[str, typing.Any] = {}
        for one in items:
            if one is None:
                continue
            if not isinstance(one, dict):
                raise ValueError("dict_merge expects dict arguments")
            merged.update(one)
        return merged

    @staticmethod
    def _helper_sort_keys(value: dict[str, typing.Any]) -> dict[str, typing.Any]:
        """返回按 key 排序的新 dict。"""
        if not isinstance(value, dict):
            raise ValueError("sort_keys expects dict")
        return {key: value[key] for key in sorted(value.keys(), key=lambda x: str(x))}

    @staticmethod
    def _helper_canonical_query(value: dict[str, typing.Any]) -> str:
        """把 dict 转成稳定排序的 query string。"""
        if not isinstance(value, dict):
            raise ValueError("canonical_query expects dict")
        pairs: list[tuple[str, str]] = []
        for key, raw in value.items():
            key_text = "" if key is None else str(key)
            if isinstance(raw, list):
                for one in raw:
                    pairs.append((key_text, "" if one is None else str(one)))
            else:
                pairs.append((key_text, "" if raw is None else str(raw)))
        pairs.sort(key=lambda item: (item[0], item[1]))
        return "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in pairs)

    @staticmethod
    def _helper_hex_encode(value: typing.Any, encoding: str = "utf-8") -> str:
        """把文本或二进制编码为 hex 文本。"""
        if isinstance(value, bytes):
            raw = value
        elif isinstance(value, bytearray):
            raw = bytes(value)
        else:
            raw = str("" if value is None else value).encode(encoding)
        return raw.hex()

    @staticmethod
    def _helper_hex_decode(value: typing.Any, encoding: str = "utf-8", as_text: bool = True) -> typing.Any:
        """把 hex 文本解码为文本或字节。"""
        decoded = bytes.fromhex(str(value or "").strip())
        return decoded.decode(encoding) if as_text else decoded

    @staticmethod
    def _decode_binary(value: typing.Any, input_format: str, encoding: str) -> bytes:
        """按指定输入格式把值转成二进制。"""
        fmt = str(input_format or "text").strip().lower()
        if fmt == "bytes":
            if isinstance(value, bytes):
                return value
            if isinstance(value, bytearray):
                return bytes(value)
            return str("" if value is None else value).encode(encoding)
        if fmt == "text":
            return str("" if value is None else value).encode(encoding)
        if fmt == "base64":
            return base64.b64decode(str(value or "").strip(), validate=True)
        if fmt == "hex":
            return bytes.fromhex(str(value or "").strip())
        raise ValueError(f"unsupported input_format: {input_format}")

    @staticmethod
    def _encode_binary(value: bytes, out_mode: str, encoding: str) -> typing.Any:
        """按指定输出格式把二进制编码出去。"""
        fmt = str(out_mode or "base64").strip().lower()
        if fmt == "bytes":
            return value
        if fmt == "text":
            return value.decode(encoding)
        if fmt == "base64":
            return base64.b64encode(value).decode("ascii")
        if fmt == "hex":
            return value.hex()
        raise ValueError(f"unsupported out_mode: {out_mode}")

    @staticmethod
    def _helper_gzip_encode(
        value: typing.Any,
        input_format: str = "text",
        out_mode: str = "base64",
        encoding: str = "utf-8",
        compress_level: int = 9
    ) -> typing.Any:
        """把输入压缩为 gzip。"""
        source = TemplateEvaluation._decode_binary(value, input_format, encoding)
        compressed = gzip.compress(source, compresslevel=int(compress_level))
        return TemplateEvaluation._encode_binary(compressed, out_mode, encoding)

    @staticmethod
    def _helper_gzip_decode(
        value: typing.Any,
        input_format: str = "base64",
        out_mode: str = "text",
        encoding: str = "utf-8"
    ) -> typing.Any:
        """把 gzip 内容解压。"""
        source = TemplateEvaluation._decode_binary(value, input_format, encoding)
        return TemplateEvaluation._encode_binary(gzip.decompress(source), out_mode, encoding)

    @staticmethod
    def _helper_zlib_encode(
        value: typing.Any,
        input_format: str = "text",
        out_mode: str = "base64",
        encoding: str = "utf-8",
        compress_level: int = 9
    ) -> typing.Any:
        """把输入压缩为 zlib。"""
        source = TemplateEvaluation._decode_binary(value, input_format, encoding)
        compressed = zlib.compress(source, level=int(compress_level))
        return TemplateEvaluation._encode_binary(compressed, out_mode, encoding)

    @staticmethod
    def _helper_zlib_decode(
        value: typing.Any,
        input_format: str = "base64",
        out_mode: str = "text",
        encoding: str = "utf-8",
        wbits: int = zlib.MAX_WBITS
    ) -> typing.Any:
        """把 zlib 内容解压。"""
        source = TemplateEvaluation._decode_binary(value, input_format, encoding)
        return TemplateEvaluation._encode_binary(zlib.decompress(source, wbits=int(wbits)), out_mode, encoding)

    @staticmethod
    def _resolve_datetime(timestamp: int | float | None, unit: str, utc: bool) -> datetime:
        """把秒或毫秒时间戳转换为 datetime。"""
        zone = timezone.utc if utc else None
        if timestamp is None:
            return datetime.now(tz=zone)
        base = float(timestamp)
        if unit == "ms":
            base = base / 1000.0
        elif unit != "s":
            raise ValueError(f"unsupported unit: {unit}")
        return datetime.fromtimestamp(base, tz=zone)

    @staticmethod
    def _helper_now_iso(fmt: str = "%Y-%m-%dT%H:%M:%SZ", utc: bool = True) -> str:
        """返回当前时间的格式化文本。"""
        return TemplateEvaluation._resolve_datetime(None, "s", utc).strftime(fmt)

    @staticmethod
    def _helper_format_ts(
        timestamp: int | float,
        unit: str = "s",
        fmt: str = "%Y-%m-%dT%H:%M:%SZ",
        utc: bool = True
    ) -> str:
        """格式化给定时间戳。"""
        return TemplateEvaluation._resolve_datetime(timestamp, str(unit or "s").strip().lower(), utc).strftime(fmt)

    @staticmethod
    def _helper_offset_ts(
        timestamp: int | float | None = None,
        unit: str = "s",
        offset_seconds: int = 0,
        offset_minutes: int = 0,
        offset_hours: int = 0
    ) -> int:
        """对给定时间戳做偏移并返回同单位整数时间戳。"""
        unit_norm = str(unit or "s").strip().lower()
        dt = TemplateEvaluation._resolve_datetime(timestamp, unit_norm, True) + timedelta(
            seconds=int(offset_seconds),
            minutes=int(offset_minutes),
            hours=int(offset_hours),
        )
        if unit_norm == "ms":
            return int(dt.timestamp() * 1000)
        return int(dt.timestamp())

    _HELPERS: Mapping[str, Callable[..., typing.Any]] = {
        "pick"            : _helper_pick,
        "coalesce"        : _helper_coalesce,
        "now_s"           : _helper_now_s,
        "now_ms"          : _helper_now_ms,
        "uuid4"           : _helper_uuid4,
        "nonce"           : _helper_nonce,
        "b64encode"       : _helper_b64encode,
        "b64decode"       : _helper_b64decode,
        "json_dumps"      : _helper_json_dumps,
        "json_loads"      : _helper_json_loads,
        "urlencode"       : _helper_urlencode,
        "urldecode"       : _helper_urldecode,
        "dict_merge"      : _helper_dict_merge,
        "sort_keys"       : _helper_sort_keys,
        "canonical_query" : _helper_canonical_query,
        "hex_encode"      : _helper_hex_encode,
        "hex_decode"      : _helper_hex_decode,
        "gzip_encode"     : _helper_gzip_encode,
        "gzip_decode"     : _helper_gzip_decode,
        "zlib_encode"     : _helper_zlib_encode,
        "zlib_decode"     : _helper_zlib_decode,
        "now_iso"         : _helper_now_iso,
        "format_ts"       : _helper_format_ts,
        "offset_ts"       : _helper_offset_ts
    }

    @staticmethod
    def _attr_or_key(obj: typing.Any, name: str) -> typing.Any:
        """优先按字典键读取，其次按对象属性读取模板表达式中的成员。"""
        if name.startswith("__"):
            raise ValueError(f"unsafe attribute: {name}")

        if isinstance(obj, dict):
            if name in obj:
                return obj[name]
            raise KeyError(name)

        try:
            return getattr(obj, name)
        except AttributeError as e:
            raise KeyError(name) from e

    @staticmethod
    def safe_eval_expr(expr: str, ctx: dict[str, typing.Any]) -> typing.Any:
        """在受限 AST 范围内安全求值模板表达式。"""
        if "__" in expr:
            raise ValueError("dunder not allowed in template")

        try:
            node = ast.parse(expr, mode="eval")
        except SyntaxError as e:
            raise ValueError(f"invalid template expr: {expr!r}: {e}") from e

        def walk(n: ast.AST) -> typing.Any:
            if isinstance(n, ast.Constant):
                return n.value

            if isinstance(n, ast.Name):
                if n.id in ctx:
                    return ctx[n.id]
                raise KeyError(n.id)

            if isinstance(n, ast.Attribute):
                return TemplateEvaluation._attr_or_key(walk(n.value), n.attr)

            if isinstance(n, ast.Subscript):
                return walk(n.value)[walk(n.slice)]

            if isinstance(n, ast.List):
                return [walk(x) for x in n.elts]

            if isinstance(n, ast.Tuple):
                return tuple(walk(x) for x in n.elts)

            if isinstance(n, ast.Dict):
                pairs: dict[typing.Any, typing.Any] = {}
                for key_node, value_node in zip(n.keys, n.values, strict=True):
                    if key_node is None:
                        raise ValueError("dict unpacking not allowed in template")
                    pairs[walk(key_node)] = walk(value_node)
                return pairs

            if isinstance(n, ast.BinOp):
                fn = TemplateEvaluation._BIN_OPS.get(type(n.op))
                if fn is None:
                    raise ValueError(f"unsupported binop: {type(n.op).__name__}")
                return fn(walk(n.left), walk(n.right))

            if isinstance(n, ast.UnaryOp):
                fn = TemplateEvaluation._UNARY_OPS.get(type(n.op))
                if fn is None:
                    raise ValueError(f"unsupported unaryop: {type(n.op).__name__}")
                return fn(walk(n.operand))

            if isinstance(n, ast.BoolOp):
                op_type = type(n.op)
                if op_type not in TemplateEvaluation._BOOL_OPS:
                    raise ValueError(f"unsupported boolop: {op_type.__name__}")
                vals = [walk(v) for v in n.values]
                return all(vals) if op_type is ast.And else any(vals)

            if isinstance(n, ast.Compare):
                left = walk(n.left)
                for op, comp in zip(n.ops, n.comparators, strict=True):
                    fn = TemplateEvaluation._CMP_OPS.get(type(op))
                    if fn is None:
                        raise ValueError(f"unsupported cmpop: {type(op).__name__}")
                    right = walk(comp)
                    if not fn(left, right):
                        return False
                    left = right
                return True

            if isinstance(n, ast.Call):
                if not isinstance(n.func, ast.Name):
                    raise ValueError("only helper call allowed in template")

                helper = TemplateEvaluation._HELPERS.get(n.func.id)
                if helper is None:
                    raise ValueError(f"unsupported helper: {n.func.id}")

                args = [walk(arg) for arg in n.args]

                kwargs: dict[str, typing.Any] = {}
                for kw in n.keywords:
                    key = kw.arg
                    if key is None:
                        raise ValueError("kwargs unpacking not allowed in template")
                    kwargs[key] = walk(kw.value)

                return helper(*args, **kwargs)

            if isinstance(n, ast.Lambda):
                raise ValueError("lambda not allowed in template")

            if isinstance(n, (ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp)):
                raise ValueError("comprehension not allowed in template")

            raise ValueError(f"unsupported expr node: {type(n).__name__}")

        return walk(node.body)


class TemplateService(object):

    @staticmethod
    def _ctx_keys_view(ctx: dict[str, typing.Any]) -> str:
        """输出上下文 key 摘要，便于定位未命中的模板变量。"""
        keys = [str(key) for key in sorted(ctx.keys(), key=lambda item: str(item))]
        return clip_text(str(keys), limit=220)

    @staticmethod
    def _child_path(parent: str, segment: typing.Any) -> str:
        """拼接嵌套渲染路径，便于日志直接定位字段位置。"""
        if isinstance(segment, int):
            return f"{parent}[{segment}]"
        text = str(segment)
        if text.isidentifier():
            return f"{parent}.{text}"
        return f"{parent}[{text!r}]"

    @staticmethod
    def _rendered_view(value: typing.Any) -> str:
        """把渲染值压成适合日志展示的短文本。"""
        if value is None:
            return "None"
        if value == "":
            return "<empty>"
        return clip_text(value, limit=160)

    @staticmethod
    def _log_expr_error(path: str, expr: str, ctx: dict[str, typing.Any], error: Exception) -> None:
        """记录模板表达式未命中或求值失败。"""
        if isinstance(error, KeyError):
            logger.warning(
                f"template render miss path={path} expr={clip_text(expr, limit=180)} "
                f"missing={clip_text(error.args[0] if error.args else error, limit=80)} "
                f"ctx_keys={TemplateService._ctx_keys_view(ctx)}"
            )
            return
        logger.warning(
            f"template render error path={path} expr={clip_text(expr, limit=180)} "
            f"error={clip_text(f'{type(error).__name__}: {error}', limit=220)}"
        )

    @staticmethod
    def render(value: typing.Any, ctx: dict[str, typing.Any], path: str = "template") -> typing.Any:
        """递归渲染字符串、列表与字典中的模板表达式。"""
        if isinstance(value, str):
            stripped = value.strip()
            match = TemplateEvaluation.EXPR_RE.fullmatch(stripped)
            if match:
                expr = match.group(1)
                try:
                    rendered = TemplateEvaluation.safe_eval_expr(expr, ctx)
                except (KeyError, ValueError, TypeError, IndexError) as e:
                    TemplateService._log_expr_error(path, expr, ctx, e)
                    raise
                if rendered in (None, ""):
                    logger.debug(
                        f"template render empty path={path} mode=fullmatch "
                        f"expr={clip_text(expr, limit=180)} result={TemplateService._rendered_view(rendered)}"
                    )
                return rendered

            def repl(found: re.Match[str]) -> str:
                expr = found.group(1)
                try:
                    rendered = TemplateEvaluation.safe_eval_expr(expr, ctx)
                except (KeyError, ValueError, TypeError, IndexError) as e:
                    TemplateService._log_expr_error(path, expr, ctx, e)
                    raise
                if rendered in (None, ""):
                    logger.debug(
                        f"template render empty path={path} mode=placeholder "
                        f"expr={clip_text(expr, limit=180)} result={TemplateService._rendered_view(rendered)}"
                    )
                return "" if rendered is None else str(rendered)

            return TemplateEvaluation.EXPR_RE.sub(repl, value)

        if isinstance(value, list):
            return [
                TemplateService.render(item, ctx, path=TemplateService._child_path(path, index))
                for index, item in enumerate(value)
            ]

        if isinstance(value, dict):
            return {
                k: TemplateService.render(v, ctx, path=TemplateService._child_path(path, k))
                for k, v in value.items()
            }

        return value


if __name__ == '__main__':
    pass
