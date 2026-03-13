#  _   _ _   _ _       _   _
# | | | | |_(_) |___  | \ | | _____  ___   _ ___
# | | | | __| | / __| |  \| |/ _ \ \/ / | | / __|
# | |_| | |_| | \__ \ | |\  |  __/>  <| |_| \__ \
#  \___/ \__|_|_|___/ |_| \_|\___/_/\_\\__,_|___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import ast
import json
import time
import uuid
import httpx
import base64
import typing
import binascii
import operator
from pathlib import Path
from dataclasses import (
    dataclass, field
)
from collections.abc import (
    Callable, Mapping
)
from loguru import logger
from backend.utilities import const


@dataclass
class SseEvent:
    event: typing.Optional[str] = None
    data: str = ""
    id: typing.Optional[str] = None


@dataclass
class StepResult:
    name: str
    type: str
    ok: bool
    elapsed_ms: int
    detail: dict[str, typing.Any] = field(default_factory=dict)


@dataclass
class RunRecord:
    mission_id: str
    ok: bool
    started_ms: int
    finished_ms: int
    payload: dict[str, typing.Any]
    final_ctx: dict[str, typing.Any]
    steps: list[StepResult]


class Evaluation(object):
    """Evaluation class."""

    EXPR_RE = re.compile(r"\{\{\s*(.*?)\s*}}")

    _BIN_OPS: Mapping[type[ast.operator], Callable[[typing.Any, typing.Any], typing.Any]] = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
    }

    _CMP_OPS: Mapping[type[ast.cmpop], Callable[[typing.Any, typing.Any], bool]] = {
        ast.Eq: operator.eq,
        ast.NotEq: operator.ne,
        ast.Gt: operator.gt,
        ast.GtE: operator.ge,
        ast.Lt: operator.lt,
        ast.LtE: operator.le,
        ast.In: lambda a, b: a in b,
        ast.NotIn: lambda a, b: a not in b,
    }

    _BOOL_OPS: Mapping[type[ast.boolop], Callable[[list[typing.Any]], bool]] = {
        ast.And: all,
        ast.Or: any,
    }

    _UNARY_OPS: Mapping[type[ast.unaryop], Callable[[typing.Any], typing.Any]] = {
        ast.UAdd: operator.pos,
        ast.USub: operator.neg,
        ast.Not: operator.not_,
    }

    @staticmethod
    def _attr_or_key(obj: typing.Any, name: str) -> typing.Any:
        if name.startswith("__"):
            raise ValueError(f"unsafe attribute: {name}")

        if isinstance(obj, dict):
            if name in obj:
                return obj[name]
            raise KeyError(name)

        if hasattr(obj, name):
            return getattr(obj, name)

        raise KeyError(name)

    @staticmethod
    def safe_eval_expr(expr: str, ctx: dict[str, typing.Any]) -> typing.Any:
        try:
            node = ast.parse(expr, mode="eval")
        except SyntaxError as e:
            raise ValueError(f"invalid template expr: {expr!r}: {e}") from e

        def walk(n: ast.AST) -> typing.Any:
            if isinstance(n, ast.Expression):
                return walk(n.body)

            if isinstance(n, ast.Constant):
                return n.value

            if isinstance(n, ast.Name):
                if n.id in ctx:
                    return ctx[n.id]
                raise KeyError(n.id)

            if isinstance(n, ast.Attribute):
                base = walk(n.value)
                return Evaluation._attr_or_key(base, n.attr)

            if isinstance(n, ast.Subscript):
                base = walk(n.value)
                key = walk(n.slice)
                return base[key]

            if isinstance(n, ast.List):
                return [walk(x) for x in n.elts]

            if isinstance(n, ast.Tuple):
                return tuple(walk(x) for x in n.elts)

            if isinstance(n, ast.Dict):
                return {walk(k): walk(v) for k, v in zip(n.keys, n.values)}

            if isinstance(n, ast.BinOp):
                op_type = type(n.op)
                fn = Evaluation._BIN_OPS.get(op_type)
                if fn is None:
                    raise ValueError(f"unsupported binop: {op_type.__name__}")
                return fn(walk(n.left), walk(n.right))

            if isinstance(n, ast.UnaryOp):
                op_type = type(n.op)
                fn = Evaluation._UNARY_OPS.get(op_type)
                if fn is None:
                    raise ValueError(f"unsupported unaryop: {op_type.__name__}")
                return fn(walk(n.operand))

            if isinstance(n, ast.BoolOp):
                op_type = type(n.op)
                if op_type not in Evaluation._BOOL_OPS:
                    raise ValueError(f"unsupported boolop: {op_type.__name__}")
                vals = [walk(v) for v in n.values]
                return all(vals) if op_type is ast.And else any(vals)

            if isinstance(n, ast.Compare):
                left = walk(n.left)
                for op, comp in zip(n.ops, n.comparators):
                    op_type = type(op)
                    fn = Evaluation._CMP_OPS.get(op_type)
                    if fn is None:
                        raise ValueError(f"unsupported cmpop: {op_type.__name__}")
                    right = walk(comp)
                    if not fn(left, right):
                        return False
                    left = right
                return True

            if isinstance(n, ast.Call):
                raise ValueError("function call not allowed in template")
            if isinstance(n, ast.Lambda):
                raise ValueError("lambda not allowed in template")
            if isinstance(n, (ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp)):
                raise ValueError("comprehension not allowed in template")
            if "__" in expr:
                raise ValueError("dunder not allowed in template")

            raise ValueError(f"unsupported expr node: {type(n).__name__}")

        return walk(node)


class Tools(object):

    @staticmethod
    def ms_now() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def ms_since(t0: float) -> int:
        return int((time.perf_counter() - t0) * 1000)

    @staticmethod
    def url_join(base_url: typing.Optional[str], url: str) -> str:
        if not base_url:
            return url
        return base_url.rstrip("/") + "/" + url.lstrip("/")

    @staticmethod
    def template(x: typing.Any, ctx: dict[str, typing.Any]) -> typing.Any:
        if isinstance(x, str):
            s = x.strip()

            # 情况1：整个字符串就是一个表达式 -> 保留原始类型
            m = Evaluation.EXPR_RE.fullmatch(s)
            if m:
                _expr = m.group(1)
                return Evaluation.safe_eval_expr(_expr, ctx)

            # 情况2：字符串内部有若干表达式 -> 替换成字符串
            def repl(match: re.Match[str]) -> str:
                expr = match.group(1)
                val = Evaluation.safe_eval_expr(expr, ctx)
                return "" if val is None else str(val)

            return Evaluation.EXPR_RE.sub(repl, x)

        if isinstance(x, list):
            return [Tools.template(i, ctx) for i in x]

        if isinstance(x, dict):
            return {k: Tools.template(v, ctx) for k, v in x.items()}

        return x

    @staticmethod
    def sse_block(block: str) -> typing.Optional[SseEvent]:
        if not (raw := block.strip("\r\n")).strip():
            return None

        ev = SseEvent(event=None, data="", id=None)
        data_lines: list[str] = []

        for ln in raw.splitlines():
            if ln.startswith(":") or ":" not in ln:
                continue
            k, v = ln.split(":", 1)
            key = k.strip()
            val = v.lstrip()
            if key == "event":
                ev.event = val
            elif key == "data":
                data_lines.append(val)
            elif key == "id":
                ev.id = val

        ev.data = "\n".join(data_lines)
        return ev

    @staticmethod
    def merge_step_extract(pack_data: typing.Any, ctx: dict[str, typing.Any], allow_ctx_merge: bool) -> None:
        if not allow_ctx_merge:
            return None
        if not isinstance(pack_data, dict):
            return None

        step_extract = pack_data.get("extract")
        if isinstance(step_extract, dict) and step_extract:
            ctx.update(step_extract)

    @staticmethod
    def step_dict(step: StepResult) -> dict[str, typing.Any]:
        return {
            "name"       : step.name,
            "type"       : step.type,
            "ok"         : step.ok,
            "elapsed_ms" : step.elapsed_ms,
            "detail"     : step.detail
        }

    @staticmethod
    def pick(data: typing.Any, path: str) -> typing.Any:
        """按 a.b.0.c 路径取值"""
        if not path: return data

        cur = data
        for seg in str(path).split("."):
            if seg == "": continue
            if isinstance(cur, dict):
                if seg not in cur:
                    raise KeyError(seg)
                cur = cur[seg]
                continue

            if isinstance(cur, list):
                idx = int(seg)
                cur = cur[idx]
                continue

            raise KeyError(seg)

        return cur

    @staticmethod
    def safe_pick(data: typing.Any, path: str) -> tuple[bool, typing.Any]:
        try:
            return True, Tools.pick(data, path)
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    @staticmethod
    def compare(actual: typing.Any, op: str, expected: typing.Any = None) -> bool:
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

    @staticmethod
    def apply_extract_assert(
        source: dict[str, typing.Any],
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None
    ) -> dict[str, typing.Any]:
        """
        对 source 做字段提取和断言校验。

        source:
          一般就是 pack["data"]

        extract:
          {"alias": "response.body_json.code"}

        asserts:
          [{"path":"response.status","op":"eq","value":200}]
        """
        extract = extract or {}
        asserts = asserts or []

        logs: list[str] = []
        extracted: dict[str, typing.Any] = {}

        for alias, path in extract.items():
            ok_pick, value = Tools.safe_pick(source, str(path))
            if ok_pick:
                extracted[alias] = value
            else:
                extracted[alias] = None
                logs.append(f"extract[{alias}] {path} -> {value}")

        results: list[dict[str, typing.Any]] = []
        fail_count = 0

        for rule in asserts:
            if not isinstance(rule, dict):
                continue

            path = str(rule.get("path") or "").strip()
            op = str(rule.get("op") or "eq").strip().lower()
            expected = rule.get("value")

            ok_pick, actual = Tools.safe_pick(source, path)

            if op == "exists":
                passed = bool(ok_pick)
                result = {
                    "path"     : path,
                    "op"       : op,
                    "expected" : None,
                    "actual"   : (actual if ok_pick else None),
                    "ok"       : passed,
                    "error"    : (None if ok_pick else actual)
                }
            else:
                if not ok_pick:
                    passed = False
                    result = {
                        "path"     : path,
                        "op"       : op,
                        "expected" : expected,
                        "actual"   : None,
                        "ok"       : False,
                        "error"    : actual
                    }
                else:
                    try:
                        passed = Tools.compare(actual, op, expected)
                        result = {
                            "path"     : path,
                            "op"       : op,
                            "expected" : expected,
                            "actual"   : actual,
                            "ok"       : passed,
                            "error"    : None
                        }
                    except Exception as e:
                        passed = False
                        result = {
                            "path"     : path,
                            "op"       : op,
                            "expected" : expected,
                            "actual"   : actual,
                            "ok"       : False,
                            "error"    : f"{type(e).__name__}: {e}"
                        }

            if not passed:
                fail_count += 1

            results.append(result)

        total = len(results)
        return {
            "ok"      : (fail_count == 0),
            "extract" : extracted,
            "asserts" : results,
            "summary": {
                "total" : total,
                "pass"  : total - fail_count,
                "fail"  : fail_count
            },
            "logs": logs
        }

    @staticmethod
    def files_payload(
        items: typing.Optional[list[dict[str, typing.Any]]]
    ) -> typing.Optional[list[tuple[str, typing.Any]]]:
        if not items: return None
        payload: list[tuple[str, typing.Any]] = []

        for it in items:
            if not isinstance(it, dict):
                continue

            f = str(it.get("field") or "file")
            filename = str(it.get("filename") or "upload.bin")
            content_type = str(it.get("content_type") or "application/octet-stream")

            if it.get("path"):
                # 处理文件路径
                p = Path(str(it["path"])).expanduser()
                try:
                    # 确保文件路径存在，并尝试读取
                    with p.open("rb") as file:
                        file_content = file.read()  # 读取文件内容
                        payload.append((f, (filename or p.name, file_content, content_type)))
                except Exception as e:
                    logger.error(f"Error reading file {p}: {e}")
                    continue

            elif it.get("text") is not None:
                # 处理文本数据
                data = str(it.get("text") or "").encode(const.CHARSET)
                payload.append((f, (filename, data, content_type)))

            elif it.get("bytes") is not None:
                # 处理字节数据
                raw = it.get("bytes")
                if isinstance(raw, bytes):
                    payload.append((f, (filename, raw, content_type)))
                elif isinstance(raw, str):
                    payload.append((f, (filename, raw.encode(const.CHARSET), content_type)))

        return payload or None

    @staticmethod
    def detect_media_kind(content_type: str) -> typing.Optional[str]:
        ct = str(content_type or "").split(";")[0].strip().lower()
        if ct.startswith("image/"):
            return "image"
        if ct.startswith("video/"):
            return "video"
        return None

    @staticmethod
    def media_suffix(content_type: str, fallback_kind: str | None = None) -> str:
        ct = str(content_type or "").split(";")[0].strip().lower()

        mapping = {
            "image/png"        : ".png",
            "image/jpeg"       : ".jpg",
            "image/jpg"        : ".jpg",
            "image/webp"       : ".webp",
            "image/gif"        : ".gif",
            "image/bmp"        : ".bmp",
            "video/mp4"        : ".mp4",
            "video/webm"       : ".webm",
            "video/quicktime"  : ".mov",
            "video/x-matroska" : ".mkv",
            "video/ogg"        : ".ogv"
        }
        if ct in mapping:
            return mapping[ct]

        if fallback_kind == "image":
            return ".png"
        if fallback_kind == "video":
            return ".mp4"
        return ".bin"

    @staticmethod
    def mk_out_dir(output_dir: str, tool: str) -> Path:
        base_dir = Path(output_dir or ".").expanduser().resolve()
        base_dir.mkdir(parents=True, exist_ok=True)

        tag = f"{time.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
        out_dir = base_dir / "nexus" / tool / tag
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir

    @staticmethod
    def parse_data_url(value: str) -> tuple[str, bytes] | None:
        if not isinstance(value, str):
            return None
        if not value.startswith("data:"):
            return None

        m = re.match(r"^data:([^;,]+)?(;base64)?,(.*)$", value, re.I | re.S)
        if not m:
            return None

        mime_type = (m.group(1) or "application/octet-stream").strip().lower()
        is_b64    = bool(m.group(2))
        raw       = m.group(3)

        try:
            if is_b64:
                data = base64.b64decode(raw, validate=False)
            else:
                data = raw.encode(const.CHARSET)
            return mime_type, data
        except (binascii.Error, ValueError, TypeError, UnicodeEncodeError, LookupError):
            return None

    @staticmethod
    def parse_base64_blob(value: str) -> tuple[str | None, bytes] | None:
        if not isinstance(value, str):
            return None

        s = value.strip()
        if not s:
            return None

        # 避免把普通短文本误判成 base64
        if not re.fullmatch(r"[A-Za-z0-9+/=\s_-]+", s):
            return None

        # 排除 URL / data_url
        if s.startswith(("http://", "https://", "data:")):
            return None

        try:
            data = base64.b64decode(s, validate=False)
        except (binascii.Error, ValueError):
            return None

        if not data:
            return None

        return None, data

    @staticmethod
    def guess_mime_from_bytes(data: bytes, fallback_kind: str | None = None) -> str | None:
        if not data:
            return None

        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
            return "image/gif"
        if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
            return "image/webp"
        if len(data) >= 12 and data[4:8] == b"ftyp":
            return "video/mp4"

        return "image/png" if fallback_kind == "image" else None

    @staticmethod
    def detect_media_ref(value: typing.Any) -> dict[str, typing.Any] | None:
        if value is None:
            return None

        # 1) 直接字符串
        if isinstance(value, str):
            s = value.strip()

            if s.startswith(("http://", "https://")):
                return {
                    "source"    : "url",
                    "url"       : s,
                    "mime_type" : None,
                    "data"      : None,
                    "kind"      : None
                }

            if parsed := Tools.parse_data_url(s):
                mime_type, data = parsed
                return {
                    "source"    : "data_url",
                    "url"       : None,
                    "mime_type" : mime_type,
                    "data"      : data,
                    "kind"      : Tools.detect_media_kind(mime_type)
                }

            if parsed := Tools.parse_base64_blob(s):
                mime_type, data = parsed
                mime_type = mime_type or Tools.guess_mime_from_bytes(data)
                return {
                    "source"    : "base64",
                    "url"       : None,
                    "mime_type" : mime_type,
                    "data"      : data,
                    "kind"      : Tools.detect_media_kind(mime_type or "")
                }

            return None

        # 2) dict 结构
        if isinstance(value, dict):
            for key in ("url", "src", "href", "image_url", "video_url"):
                if isinstance(value.get(key), str):
                    ref = Tools.detect_media_ref(value[key])
                    if ref:
                        return {
                            **ref,
                            "source"    : "json_path",
                            "mime_type" : ref.get("mime_type") or value.get("mime_type") or value.get("content_type"),
                            "kind"      : ref.get("kind") or value.get("kind")
                        }

            for key in ("data_url", "dataUrl"):
                if isinstance(value.get(key), str):
                    ref = Tools.detect_media_ref(value[key])
                    if ref:
                        return ref | {"source": "json_path"}

            for key in ("base64", "content", "data"):
                if isinstance(value.get(key), str):
                    ref = Tools.detect_media_ref(value[key])
                    if ref:
                        return ref | {"source": "json_path"}

        return None

    @staticmethod
    async def materialize_media_ref(
        *,
        ref: dict[str, typing.Any],
        save_dir: str,
        tool: str,
        default_name: str = "media",
        timeout: float = 30.0
    ) -> tuple[dict[str, typing.Any], dict[str, typing.Any]]:

        source    = str(ref.get("source") or "unknown")
        url       = ref.get("url")
        mime_type = ref.get("mime_type")
        data      = ref.get("data")
        kind      = ref.get("kind") or Tools.detect_media_kind(mime_type or "")

        if url:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(str(url))
                resp.raise_for_status()
                data = resp.content
                mime_type = str(resp.headers.get("content-type") or mime_type or "").split(";")[0].strip()
                kind = kind or Tools.detect_media_kind(mime_type)

        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ValueError("empty media data")

        if not mime_type:
            mime_type = Tools.guess_mime_from_bytes(bytes(data), fallback_kind=kind)

        kind = kind or Tools.detect_media_kind(mime_type or "")
        if kind not in {"image", "video"}:
            raise ValueError(f"unsupported media kind: mime={mime_type!r}")

        out_dir = Tools.mk_out_dir(save_dir, tool)
        ext = Tools.media_suffix(mime_type or "", fallback_kind=kind)
        filename = f"{default_name}{ext}"
        out_file = out_dir / filename
        out_file.write_bytes(bytes(data))

        media_info = {
            "kind"      : kind,
            "source"    : source,
            "path"      : str(out_file),
            "filename"  : filename,
            "mime_type" : mime_type,
            "size"      : len(data)
        }

        attachment = {
            "kind"      : kind,
            "path"      : str(out_file),
            "filename"  : filename,
            "mime_type" : mime_type,
            "size"      : len(data),
            "source"    : source
        }
        return media_info, attachment

    @staticmethod
    async def collect_media(
        *,
        source_kind: str,
        source: typing.Any,
        content_type: str | None = None,
        media_path: str | None = None,
        media_index: int | None = None,
        save_response: bool = False,
        save_dir: str | None = None,
        tool: str = "media",
        timeout: float = 30.0
    ) -> tuple[list[dict[str, typing.Any]], list[dict[str, typing.Any]], list[str]]:

        media_list: list[dict[str, typing.Any]] = []
        attachments: list[dict[str, typing.Any]] = []
        logs: list[str] = []

        try:
            # A. HTTP body 本身就是媒体
            if source_kind == "http_body":
                kind = Tools.detect_media_kind(content_type or "")
                if kind and isinstance(source, (bytes, bytearray)):
                    if save_response:
                        ref = {
                            "source"    : "response_body",
                            "url"       : None,
                            "mime_type" : str(content_type or "").split(";")[0].strip(),
                            "data"      : bytes(source),
                            "kind"      : kind
                        }
                        media_info, attachment = await Tools.materialize_media_ref(
                            ref=ref,
                            save_dir=save_dir,
                            tool=tool,
                            default_name=kind,
                            timeout=timeout
                        )
                        media_list.append(media_info)
                        attachments.append(attachment)
                    else:
                        media_list.append({
                            "kind"      : kind,
                            "source"    : "response_body",
                            "path"      : None,
                            "filename"  : None,
                            "mime_type" : str(content_type or "").split(";")[0].strip(),
                            "size"      : len(source)
                        })
                return media_list, attachments, logs

            # B. JSON / dict / list / SSE / WS 路径提取
            target = source

            if source_kind in {"sse_events", "ws_messages"}:
                if media_index is not None and isinstance(target, list):
                    idx = int(media_index)
                    if idx < 0 or idx >= len(target):
                        logs.append(f"media_index[{idx}] out of range")
                        return media_list, attachments, logs
                    target = target[idx]
                elif isinstance(target, list):
                    if not target:
                        return media_list, attachments, logs
                    target = target[0]

                # 对 SSE / WS 常见包装结构做一层展开
                # 1) dict.data 是 JSON 字符串 -> 自动 json.loads
                # 2) dict.data 是 URL / data_url 字符串 -> 直接作为 target
                if isinstance(target, dict) and "data" in target:
                    raw_data = target.get("data")

                    if isinstance(raw_data, str):
                        raw_data_s = raw_data.strip()

                        # 优先尝试 JSON 解析
                        if raw_data_s.startswith("{") or raw_data_s.startswith("["):
                            try:
                                target = json.loads(raw_data_s)
                            except (TypeError, ValueError, json.JSONDecodeError):
                                target = raw_data
                        else:
                            target = raw_data

                    elif raw_data is not None:
                        target = raw_data

            if media_path:
                ok_pick, value = Tools.safe_pick(target, media_path)
                if not ok_pick:
                    logs.append(f"media_path[{media_path}] -> {value}")
                    return media_list, attachments, logs
                target = value

            ref = Tools.detect_media_ref(target)
            if not ref:
                logs.append(f"detect_media_ref fail: type={type(target).__name__}")
                return media_list, attachments, logs

            if save_response:
                media_info, attachment = await Tools.materialize_media_ref(
                    ref=ref,
                    save_dir=save_dir,
                    tool=tool,
                    default_name="media",
                    timeout=timeout
                )
                media_list.append(media_info)
                attachments.append(attachment)
            else:
                media_list.append({
                    "kind"      : ref.get("kind"),
                    "source"    : ref.get("source"),
                    "path"      : None,
                    "filename"  : None,
                    "mime_type" : ref.get("mime_type"),
                    "size"      : (len(ref["data"]) if isinstance(ref.get("data"), (bytes, bytearray)) else None)
                })

        except Exception as e:
            logs.append(f"collect_media[{source_kind}] -> {type(e).__name__}: {e}")

        return media_list, attachments, logs


class Build(object):

    @staticmethod
    def build_pack(
        *,
        text: str,
        ok: bool,
        request: dict[str, typing.Any],
        response: dict[str, typing.Any],
        attachments: typing.Optional[list[dict[str, typing.Any]]] = None,
        logs: typing.Optional[list[str]] = None,
        error: typing.Optional[str] = None,
        extra_data: typing.Optional[dict[str, typing.Any]] = None
    ) -> dict[str, typing.Any]:

        data: dict[str, typing.Any] = {
            "ok": ok, "request": request, "response": response
        }
        if error is not None:
            data["error"] = error
        if extra_data:
            data.update(extra_data)

        return {
            "text"        : text,
            "attachments" : attachments or [],
            "data"        : data,
            "logs"        : logs or []
        }

    @staticmethod
    def finalize_pack(
        pack: dict[str, typing.Any],
        *,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None
    ) -> dict[str, typing.Any]:

        checked = Tools.apply_extract_assert(
            pack.get("data") or {},
            extract=extract,
            asserts=asserts
        )

        data = pack.setdefault("data", {})
        logs = pack.setdefault("logs", [])

        data["extract"] = checked["extract"]
        data["asserts"] = checked["asserts"]
        data["assert_summary"] = checked["summary"]
        data["assert_ok"] = bool(checked["ok"])
        data["ok"] = bool(data.get("ok")) and bool(checked["ok"])

        logs.extend(checked["logs"])

        if extract or asserts:
            pack["text"] = str(pack.get("text") or "") + (
                f" extract={len(checked['extract'])}"
                f" fail={checked['summary']['fail']}"
            )

        return pack

    @staticmethod
    def files_meta(
        files: typing.Optional[list[dict[str, typing.Any]]]
    ) -> list[dict[str, typing.Any]]:
        return [
            {
                "field"        : x.get("field"),
                "filename"     : x.get("filename"),
                "content_type" : x.get("content_type"),
                "path"         : x.get("path")
            }
            for x in (files or []) if isinstance(x, dict)
        ]

    @staticmethod
    def build_gql_extra(
        *,
        query: str,
        variables: dict[str, typing.Any],
        operation_name: typing.Optional[str],
        errors: typing.Any
    ) -> dict[str, typing.Any]:
        return {
            "graphql": {
                "query"          : query,
                "variables"      : variables,
                "operation_name" : operation_name,
                "errors"         : errors
            }
        }

    @staticmethod
    def build_request_http_like(
        *,
        method: str,
        url: str,
        headers: typing.Optional[dict[str, str]] = None,
        params: typing.Optional[dict[str, typing.Any]] = None,
        json_body: typing.Optional[dict[str, typing.Any]] = None,
        body_text: typing.Optional[str] = None,
        timeout: float = 30.0,
        retries: int = 0,
        follow_redirects: bool = True,
        form: typing.Optional[dict[str, typing.Any]] = None,
        files: typing.Optional[list[dict[str, typing.Any]]] = None
    ) -> dict[str, typing.Any]:
        return {
            "method"           : method,
            "url"              : url,
            "headers"          : dict(headers or {}),
            "params"           : params,
            "json"             : json_body,
            "body_text"        : body_text,
            "timeout"          : timeout,
            "retries"          : retries,
            "follow_redirects" : follow_redirects,
            "form"             : form,
            "files"            : Build.files_meta(files)
        }

    @staticmethod
    def build_request_ws(
        *,
        url: str,
        headers: typing.Optional[dict[str, str]] = None,
        sends: typing.Optional[list[str]] = None,
        timeout: float = 60.0,
        max_messages: int = 10
    ) -> dict[str, typing.Any]:
        return {
            "url"          : url,
            "headers"      : dict(headers or {}),
            "sends"        : sends or [],
            "timeout"      : timeout,
            "max_messages" : max_messages
        }

    @staticmethod
    def build_response_http_like(
        *,
        status: typing.Optional[int],
        headers: typing.Optional[dict[str, typing.Any]],
        elapsed_ms: int,
        body_text: typing.Optional[str],
        body_json: typing.Any,
        content_type: typing.Optional[str],
        content_length: typing.Optional[int],
        media: typing.Optional[list[dict[str, typing.Any]]] = None
    ) -> dict[str, typing.Any]:
        return {
            "status"         : status,
            "headers"        : dict(headers or {}),
            "elapsed_ms"     : elapsed_ms,
            "body_text"      : body_text,
            "body_json"      : body_json,
            "content_type"   : content_type,
            "content_length" : content_length,
            "media"          : media or []
        }

    @staticmethod
    def build_response_sse(
        *,
        status: typing.Optional[int],
        headers: typing.Optional[dict[str, typing.Any]],
        elapsed_ms: int,
        events: typing.Optional[list[dict[str, typing.Any]]] = None,
        content_type: typing.Optional[str] = None,
        content_length: typing.Optional[int] = None,
        media: typing.Optional[list[dict[str, typing.Any]]] = None
    ) -> dict[str, typing.Any]:
        return {
            "status"         : status,
            "headers"        : dict(headers or {}),
            "elapsed_ms"     : elapsed_ms,
            "events"         : events or [],
            "content_type"   : content_type,
            "content_length" : content_length,
            "body_text"      : None,
            "body_json"      : None,
            "media"          : media or []
        }

    @staticmethod
    def build_response_ws(
        *,
        elapsed_ms: int,
        messages: typing.Optional[list[str]] = None,
        error: typing.Optional[str] = None,
        media: typing.Optional[list[dict[str, typing.Any]]] = None
    ) -> dict[str, typing.Any]:
        return {
            "status"         : None,
            "headers"        : {},
            "elapsed_ms"     : elapsed_ms,
            "messages"       : messages or [],
            "error"          : error,
            "content_type"   : None,
            "content_length" : None,
            "body_text"      : None,
            "body_json"      : None,
            "media"          : media or []
        }


if __name__ == '__main__':
    pass
