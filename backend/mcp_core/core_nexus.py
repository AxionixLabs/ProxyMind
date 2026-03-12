#   ____                 _   _
#  / ___|___  _ __ ___  | \ | | _____  ___   _ ___
# | |   / _ \| '__/ _ \ |  \| |/ _ \ \/ / | | / __|
# | |__| (_) | | |  __/ | |\  |  __/>  <| |_| \__ \
#  \____\___/|_|  \___| |_| \_|\___/_/\_\\__,_|___/
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
import asyncio
import binascii
import operator
import websockets
from pathlib import Path
from loguru import logger
from dataclasses import (
    dataclass, field
)
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

    _BIN_OPS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
    }

    _CMP_OPS = {
        ast.Eq: operator.eq,
        ast.NotEq: operator.ne,
        ast.Gt: operator.gt,
        ast.GtE: operator.ge,
        ast.Lt: operator.lt,
        ast.LtE: operator.le,
        ast.In: lambda a, b: a in b,
        ast.NotIn: lambda a, b: a not in b,
    }

    _BOOL_OPS = {
        ast.And: all,
        ast.Or: any,
    }

    _UNARY_OPS = {
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
        """
        受限表达式求值：
        - 支持 name / constant / binop / boolop / compare / unaryop
        - 支持 dict/list/tuple 下标
        - 支持 dict 的点路径访问（user.id）
        - 禁止 call / import / lambda / 推导式等
        """
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
                # py3.9+: slice 直接是 expr
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
                if op_type not in Evaluation._BIN_OPS:
                    raise ValueError(f"unsupported binop: {op_type.__name__}")
                return Evaluation._BIN_OPS[op_type](walk(n.left), walk(n.right))

            if isinstance(n, ast.UnaryOp):
                op_type = type(n.op)
                if op_type not in Evaluation._UNARY_OPS:
                    raise ValueError(f"unsupported unaryop: {op_type.__name__}")
                return Evaluation._UNARY_OPS[op_type](walk(n.operand))

            if isinstance(n, ast.BoolOp):
                op_type = type(n.op)
                if op_type not in Evaluation._BOOL_OPS:
                    raise ValueError(f"unsupported boolop: {op_type.__name__}")
                vals = [walk(v) for v in n.values]
                if op_type is ast.And:
                    return all(vals)
                return any(vals)

            if isinstance(n, ast.Compare):
                left = walk(n.left)
                for op, comp in zip(n.ops, n.comparators):
                    op_type = type(op)
                    if op_type not in Evaluation._CMP_OPS:
                        raise ValueError(f"unsupported cmpop: {op_type.__name__}")
                    right = walk(comp)
                    if not Evaluation._CMP_OPS[op_type](left, right):
                        return False
                    left = right
                return True

            # 明确禁止
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
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/webp": ".webp",
            "image/gif": ".gif",
            "image/bmp": ".bmp",
            "video/mp4": ".mp4",
            "video/webm": ".webm",
            "video/quicktime": ".mov",
            "video/x-matroska": ".mkv",
            "video/ogg": ".ogv",
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
        is_b64 = bool(m.group(2))
        raw = m.group(3)

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
                    "source": "url",
                    "url": s,
                    "mime_type": None,
                    "data": None,
                    "kind": None,
                }

            if parsed := Tools.parse_data_url(s):
                mime_type, data = parsed
                return {
                    "source": "data_url",
                    "url": None,
                    "mime_type": mime_type,
                    "data": data,
                    "kind": Tools.detect_media_kind(mime_type),
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
            for key in ("url", "src", "href"):
                if isinstance(value.get(key), str):
                    ref = Tools.detect_media_ref(value[key])
                    if ref:
                        return ref | {"source": "json_path"}

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
        timeout: float = 30.0,
    ) -> tuple[dict[str, typing.Any], dict[str, typing.Any]]:
        source = str(ref.get("source") or "unknown")
        url = ref.get("url")
        mime_type = ref.get("mime_type")
        data = ref.get("data")
        kind = ref.get("kind") or Tools.detect_media_kind(mime_type or "")

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
        timeout: float = 30.0,
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
                            "source": "response_body",
                            "url": None,
                            "mime_type": str(content_type or "").split(";")[0].strip(),
                            "data": bytes(source),
                            "kind": kind,
                        }
                        media_info, attachment = await Tools.materialize_media_ref(
                            ref=ref,
                            save_dir=save_dir,
                            tool=tool,
                            default_name=kind,
                            timeout=timeout,
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
                            "size"      : len(source),
                        })
                return media_list, attachments, logs

            # B. JSON / dict / list / SSE / WS 路径提取
            target = source

            if source_kind in {"sse_events", "ws_messages"}:
                if media_index is not None and isinstance(target, list):
                    target = target[int(media_index)]
                elif isinstance(target, list) and target:
                    target = target[0]

            if media_path:
                ok_pick, value = Tools.safe_pick(target, media_path)
                if not ok_pick:
                    logs.append(f"media_path[{media_path}] -> {value}")
                    return media_list, attachments, logs
                target = value

            ref = Tools.detect_media_ref(target)
            if not ref:
                return media_list, attachments, logs

            if save_response:
                media_info, attachment = await Tools.materialize_media_ref(
                    ref=ref,
                    save_dir=save_dir,
                    tool=tool,
                    default_name="media",
                    timeout=timeout,
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


class Nexus(object):
    """Nexus class."""

    agent_id: str = "nexus"

    def __init__(self):
        self.runs: dict[str, RunRecord] = {}

    @staticmethod
    async def request(
        *,
        method: str,
        url: str,
        base_url: typing.Optional[str] = None,
        headers: typing.Optional[dict[str, str]] = None,
        params: typing.Optional[dict[str, typing.Any]] = None,
        json_body: typing.Optional[dict[str, typing.Any]] = None,
        body_text: typing.Optional[str] = None,
        form: typing.Optional[dict[str, typing.Any]] = None,
        files: typing.Optional[list[dict[str, typing.Any]]] = None,
        timeout: float = 30.0,
        retries: int = 0,
        follow_redirects: bool = True,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        save_response: bool = False,
        save_dir: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:

        method  = (method or "GET").upper()
        url     = Tools.url_join(base_url, url)
        headers = dict(headers or {})

        t0 = time.perf_counter()
        last_err: typing.Optional[str] = None

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=follow_redirects) as client:
            for _ in range(max(0, int(retries)) + 1):
                try:
                    files_payload = Tools.files_payload(files)

                    resp = await client.request(
                        method,
                        url,
                        headers=headers,
                        params=params,
                        json=json_body,
                        content=body_text,
                        data=form,
                        files=files_payload
                    )
                    elapsed_ms = Tools.ms_since(t0)

                    resp_ct = str(resp.headers.get("content-type") or "")
                    body_bytes = resp.content

                    try:
                        body_json = resp.json()
                    except (TypeError, ValueError, json.JSONDecodeError):
                        body_json = None

                    try:
                        body_text_view = resp.text
                    except (TypeError, ValueError, AttributeError):
                        body_text_view = None

                    # 如果 body 本身就是 image/video，则不保留 body_text/body_json
                    if Tools.detect_media_kind(resp_ct):
                        body_json = None
                        body_text_view = None

                    media_list, attachments, media_logs = await Tools.collect_media(
                        source_kind="http_body",
                        source=body_bytes,
                        content_type=resp_ct,
                        save_response=save_response,
                        save_dir=save_dir,
                        tool="http_media",
                        timeout=timeout
                    )

                    ok = 200 <= int(resp.status_code) < 400

                    pack =  {
                        "text"        : f"{method} {url} -> {resp.status_code} ({elapsed_ms}ms)",
                        "attachments" : attachments,
                        "data": {
                            "ok": ok,
                            "request": {
                                "method"           : method,
                                "url"              : url,
                                "headers"          : headers,
                                "params"           : params,
                                "json"             : json_body,
                                "body_text"        : body_text,
                                "timeout"          : timeout,
                                "retries"          : retries,
                                "follow_redirects" : follow_redirects,
                                "form"             : form,
                                "files": [
                                    {
                                        "field"        : x.get("field"),
                                        "filename"     : x.get("filename"),
                                        "content_type" : x.get("content_type"),
                                        "path"         : x.get("path")
                                    }
                                    for x in (files or []) if isinstance(x, dict)
                                ]
                            },
                            "response": {
                                "status"         : resp.status_code,
                                "headers"        : dict(resp.headers),
                                "elapsed_ms"     : elapsed_ms,
                                "body_text"      : body_text_view,
                                "body_json"      : body_json,
                                "content_type"   : resp_ct,
                                "content_length" : len(body_bytes),
                                "media"          : media_list
                            }
                        },
                        "logs": media_logs[:]
                    }

                    checked = Tools.apply_extract_assert(
                        pack["data"], extract=extract, asserts=asserts
                    )

                    pack["data"]["extract"] = checked["extract"]
                    pack["data"]["asserts"] = checked["asserts"]
                    pack["data"]["assert_summary"] = checked["summary"]
                    pack["data"]["assert_ok"] = bool(checked["ok"])
                    pack["data"]["ok"] = bool(pack["data"]["ok"]) and bool(checked["ok"])
                    pack["logs"].extend(checked["logs"])

                    if extract or asserts:
                        pack["text"] += (
                            f" extract={len(checked['extract'])}"
                            f" fail={checked['summary']['fail']}"
                        )

                    return pack

                except (httpx.TimeoutException, httpx.RequestError, OSError) as e:
                    last_err = f"{type(e).__name__}: {e}"

        elapsed_ms = Tools.ms_since(t0)

        pack = {
            "text"        : f"{method} {url} -> ERROR ({elapsed_ms}ms) {last_err}",
            "attachments" : [],
            "data": {
                "ok": False,
                "request": {
                    "method"           : method,
                    "url"              : url,
                    "headers"          : headers,
                    "params"           : params,
                    "json"             : json_body,
                    "body_text"        : body_text,
                    "timeout"          : timeout,
                    "retries"          : retries,
                    "follow_redirects" : follow_redirects,
                    "form"             : form,
                    "files": [
                        {
                            "field"        : x.get("field"),
                            "filename"     : x.get("filename"),
                            "content_type" : x.get("content_type"),
                            "path"         : x.get("path")
                        }
                        for x in (files or []) if isinstance(x, dict)
                    ]
                },
                "response": {
                    "status"         : None,
                    "headers"        : {},
                    "elapsed_ms"     : elapsed_ms,
                    "body_text"      : None,
                    "body_json"      : None,
                    "content_type"   : None,
                    "content_length" : 0,
                    "media"          : []
                },
                "error": last_err
            },
            "logs": []
        }

        checked = Tools.apply_extract_assert(
            pack["data"], extract=extract, asserts=asserts
        )

        pack["data"]["extract"] = checked["extract"]
        pack["data"]["asserts"] = checked["asserts"]
        pack["data"]["assert_summary"] = checked["summary"]
        pack["data"]["assert_ok"] = bool(checked["ok"])
        pack["data"]["ok"] = bool(pack["data"]["ok"]) and bool(checked["ok"])
        pack["logs"].extend(checked["logs"])

        if extract or asserts:
            pack["text"] += (
                f" extract={len(checked['extract'])}"
                f" fail={checked['summary']['fail']}"
            )

        return pack

    @staticmethod
    async def sse(
        *,
        method: str,
        url: str,
        base_url: typing.Optional[str] = None,
        headers: typing.Optional[dict[str, str]] = None,
        params: typing.Optional[dict[str, typing.Any]] = None,
        json_body: typing.Optional[dict[str, typing.Any]] = None,
        body_text: typing.Optional[str] = None,
        form: typing.Optional[dict[str, typing.Any]] = None,
        files: typing.Optional[list[dict[str, typing.Any]]] = None,
        timeout: float = 60.0,
        retries: int = 0,
        follow_redirects: bool = True,
        max_events: typing.Optional[int] = None,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        media_index: typing.Optional[int] = None,
        media_path: typing.Optional[str] = None,
        save_response: bool = False,
        save_dir: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:

        method  = (method or "GET").upper()
        url     = Tools.url_join(base_url, url)
        headers = dict(headers or {})

        t0 = time.perf_counter()
        last_err: typing.Optional[str] = None

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=follow_redirects) as client:
            for _ in range(max(0, int(retries)) + 1):
                try:
                    files_payload = Tools.files_payload(files)

                    events: list[dict[str, typing.Any]] = []
                    status: typing.Optional[int] = None

                    async with client.stream(
                        method,
                        url,
                        headers=headers,
                        params=params,
                        json=json_body,
                        content=body_text,
                        data=form,
                        files=files_payload
                    ) as resp:

                        status     = resp.status_code
                        elapsed_ms = Tools.ms_since(t0)

                        if status != 200:
                            pack = {
                                "text"        : f"SSE {method} {url} -> {status} ({elapsed_ms}ms)",
                                "attachments" : [],
                                "data": {
                                    "ok": False,
                                    "request": {
                                        "method"           : method,
                                        "url"              : url,
                                        "headers"          : headers,
                                        "params"           : params,
                                        "json"             : json_body,
                                        "body_text"        : body_text,
                                        "timeout"          : timeout,
                                        "retries"          : retries,
                                        "follow_redirects" : follow_redirects,
                                        "form"             : form,
                                        "files": [
                                            {
                                                "field"        : x.get("field"),
                                                "filename"     : x.get("filename"),
                                                "content_type" : x.get("content_type"),
                                                "path"         : x.get("path")
                                            }
                                            for x in (files or []) if isinstance(x, dict)
                                        ]
                                    },
                                    "response": {
                                        "status"         : status,
                                        "headers"        : dict(resp.headers),
                                        "elapsed_ms"     : elapsed_ms,
                                        "events"         : events,
                                        "content_type"   : str(resp.headers.get("content-type") or ""),
                                        "content_length" : None,
                                        "body_text"      : None,
                                        "body_json"      : None,
                                        "media"          : []
                                    }
                                },
                                "logs": []
                            }

                            checked = Tools.apply_extract_assert(
                                pack["data"], extract=extract, asserts=asserts
                            )

                            pack["data"]["extract"] = checked["extract"]
                            pack["data"]["asserts"] = checked["asserts"]
                            pack["data"]["assert_summary"] = checked["summary"]
                            pack["data"]["assert_ok"] = bool(checked["ok"])
                            pack["data"]["ok"] = bool(pack["data"]["ok"]) and bool(checked["ok"])
                            pack["logs"].extend(checked["logs"])

                            if extract or asserts:
                                pack["text"] += (
                                    f" extract={len(checked['extract'])}"
                                    f" fail={checked['summary']['fail']}"
                                )

                            return pack

                        buf = ""
                        async for chunk in resp.aiter_text():
                            buf += chunk
                            buf = buf.replace("\r\n", "\n")

                            while "\n\n" in buf:
                                raw, buf = buf.split("\n\n", 1)
                                ev = Tools.sse_block(raw)
                                if not ev: continue

                                events.append({
                                    "event" : ev.event,
                                    "id"    : ev.id,
                                    "data"  : ev.data
                                })

                                if max_events and 0 < int(max_events) <= len(events):
                                    elapsed_ms = Tools.ms_since(t0)
                                    
                                    media_list, attachments, media_logs = await Tools.collect_media(
                                        source_kind="sse_events",
                                        source=events,
                                        media_index=media_index,
                                        media_path=media_path,
                                        save_response=save_response,
                                        save_dir=save_dir,
                                        tool="sse_media",
                                        timeout=timeout
                                    )
                                    
                                    pack = {
                                        "text"        : f"SSE {method} {url} events={len(events)} ({elapsed_ms}ms)",
                                        "attachments" : [],
                                        "data": {
                                            "ok": True,
                                            "request": {
                                                "method"           : method,
                                                "url"              : url,
                                                "headers"          : headers,
                                                "params"           : params,
                                                "json"             : json_body,
                                                "body_text"        : body_text,
                                                "timeout"          : timeout,
                                                "retries"          : retries,
                                                "follow_redirects" : follow_redirects,
                                                "form"             : form,
                                                "files": [
                                                    {
                                                        "field"        : x.get("field"),
                                                        "filename"     : x.get("filename"),
                                                        "content_type" : x.get("content_type"),
                                                        "path"         : x.get("path")
                                                    }
                                                    for x in (files or []) if isinstance(x, dict)
                                                ]
                                            },
                                            "response": {
                                                "status"         : status,
                                                "headers"        : dict(resp.headers),
                                                "elapsed_ms"     : elapsed_ms,
                                                "events"         : events,
                                                "content_type"   : str(resp.headers.get("content-type") or ""),
                                                "content_length" : None,
                                                "body_text"      : None,
                                                "body_json"      : None,
                                                "media"          : media_list
                                            }
                                        },
                                        "logs": media_logs[:]
                                    }

                                    checked = Tools.apply_extract_assert(
                                        pack["data"], extract=extract, asserts=asserts
                                    )

                                    pack["data"]["extract"] = checked["extract"]
                                    pack["data"]["asserts"] = checked["asserts"]
                                    pack["data"]["assert_summary"] = checked["summary"]
                                    pack["data"]["assert_ok"] = bool(checked["ok"])
                                    pack["data"]["ok"] = bool(pack["data"]["ok"]) and bool(checked["ok"])
                                    pack["logs"].extend(checked["logs"])

                                    if extract or asserts:
                                        pack["text"] += (
                                            f" extract={len(checked['extract'])}"
                                            f" fail={checked['summary']['fail']}"
                                        )

                                    return pack

                        if tail := Tools.sse_block(buf):
                            events.append({"event": tail.event, "id": tail.id, "data": tail.data})

                    elapsed_ms = Tools.ms_since(t0)
                    ok = (status == 200 and len(events) > 0)

                    media_list, attachments, media_logs = await Tools.collect_media(
                        source_kind="sse_events",
                        source=events,
                        media_index=media_index,
                        media_path=media_path,
                        save_response=save_response,
                        save_dir=save_dir,
                        tool="sse_media",
                        timeout=timeout
                    )

                    pack = {
                        "text"        : f"SSE {method} {url} events={len(events)} ({elapsed_ms}ms)",
                        "attachments" : attachments,
                        "data": {
                            "ok": ok,
                            "request": {
                                "method"           : method,
                                "url"              : url,
                                "headers"          : headers,
                                "params"           : params,
                                "json"             : json_body,
                                "body_text"        : body_text,
                                "timeout"          : timeout,
                                "retries"          : retries,
                                "follow_redirects" : follow_redirects,
                                "form"             : form,
                                "files": [
                                    {
                                        "field"        : x.get("field"),
                                        "filename"     : x.get("filename"),
                                        "content_type" : x.get("content_type"),
                                        "path"         : x.get("path")
                                    }
                                    for x in (files or []) if isinstance(x, dict)
                                ]
                            },
                            "response": {
                                "status"         : status,
                                "headers"        : dict(resp.headers),
                                "elapsed_ms"     : elapsed_ms,
                                "events"         : events,
                                "content_type"   : str(resp.headers.get("content-type") or ""),
                                "content_length" : None,
                                "body_text"      : None,
                                "body_json"      : None,
                                "media"          : media_list
                            }
                        },
                        "logs": media_logs[:]
                    }

                    checked = Tools.apply_extract_assert(
                        pack["data"], extract=extract, asserts=asserts
                    )

                    pack["data"]["extract"] = checked["extract"]
                    pack["data"]["asserts"] = checked["asserts"]
                    pack["data"]["assert_summary"] = checked["summary"]
                    pack["data"]["assert_ok"] = bool(checked["ok"])
                    pack["data"]["ok"] = bool(pack["data"]["ok"]) and bool(checked["ok"])
                    pack["logs"].extend(checked["logs"])

                    if extract or asserts:
                        pack["text"] += (
                            f" extract={len(checked['extract'])}"
                            f" fail={checked['summary']['fail']}"
                        )

                    return pack

                except (httpx.TimeoutException, httpx.RequestError, OSError) as e:
                    last_err = f"{type(e).__name__}: {e}"

        elapsed_ms = Tools.ms_since(t0)
        pack = {
            "text"        : f"SSE {method} {url} -> ERROR ({elapsed_ms}ms) {last_err}",
            "attachments" : [],
            "data": {
                "ok": False,
                "request": {
                    "method"           : method,
                    "url"              : url,
                    "headers"          : headers,
                    "params"           : params,
                    "json"             : json_body,
                    "body_text"        : body_text,
                    "timeout"          : timeout,
                    "retries"          : retries,
                    "follow_redirects" : follow_redirects,
                    "form"             : form,
                    "files": [
                        {
                            "field"        : x.get("field"),
                            "filename"     : x.get("filename"),
                            "content_type" : x.get("content_type"),
                            "path"         : x.get("path")
                        }
                        for x in (files or []) if isinstance(x, dict)
                    ]
                },
                "response": {
                    "status"         : None,
                    "headers"        : {},
                    "elapsed_ms"     : elapsed_ms,
                    "events"         : [],
                    "content_type"   : None,
                    "content_length" : None,
                    "body_text"      : None,
                    "body_json"      : None,
                    "media"          : []
                },
                "error": last_err
            },
            "logs": []
        }

        checked = Tools.apply_extract_assert(
            pack["data"], extract=extract, asserts=asserts
        )

        pack["data"]["extract"] = checked["extract"]
        pack["data"]["asserts"] = checked["asserts"]
        pack["data"]["assert_summary"] = checked["summary"]
        pack["data"]["assert_ok"] = bool(checked["ok"])
        pack["data"]["ok"] = bool(pack["data"]["ok"]) and bool(checked["ok"])
        pack["logs"].extend(checked["logs"])

        if extract or asserts:
            pack["text"] += (
                f" extract={len(checked['extract'])}"
                f" fail={checked['summary']['fail']}"
            )

        return pack

    @staticmethod
    async def ws(
        *,
        url: str,
        headers: typing.Optional[dict[str, str]] = None,
        sends: typing.Optional[list[str]] = None,
        timeout: float = 60.0,
        max_messages: int = 10,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        media_index: typing.Optional[int] = None,
        media_path: typing.Optional[str] = None,
        save_response: bool = False,
        save_dir: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:

        t0 = time.perf_counter()

        headers = headers or {}
        sends = sends or []
        recv: list[str] = []
        last_err: typing.Optional[str] = None

        try:
            async with websockets.connect(url, additional_headers=headers, open_timeout=timeout) as ws:
                for s in sends:
                    await ws.send(s)

                for _ in range(int(max_messages)):
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    except websockets.exceptions.ConnectionClosedOK:
                        break
                    recv.append(msg if isinstance(msg, str) else msg.decode(const.CHARSET, const.IGNORE))

        except (
            websockets.exceptions.ConnectionClosedError,
            websockets.exceptions.WebSocketException,
            OSError,
            asyncio.TimeoutError
        ) as e:
            last_err = f"{type(e).__name__}: {e}"

        elapsed_ms = Tools.ms_since(t0)
        ok = (last_err is None) or bool(recv)

        msg_target: list[typing.Any] = []
        for x in recv:
            try:
                msg_target.append(json.loads(x))
            except (TypeError, ValueError, json.JSONDecodeError):
                msg_target.append(x)

        media_list, attachments, media_logs = await Tools.collect_media(
            source_kind="ws_messages",
            source=msg_target,
            media_index=media_index,
            media_path=media_path,
            save_response=save_response,
            save_dir=save_dir,
            tool="ws_media",
            timeout=timeout
        )

        pack =  {
            "text"        : f"WS {url} msgs={len(recv)} ({elapsed_ms}ms)",
            "attachments" : attachments,
            "data": {
                "ok": ok,
                "request": {
                    "url"          : url,
                    "headers"      : headers,
                    "sends"        : sends,
                    "timeout"      : timeout,
                    "max_messages" : max_messages
                },
                "response": {
                    "elapsed_ms"     : elapsed_ms,
                    "messages"       : recv,
                    "error"          : (None if ok else last_err),
                    "status"         : None,
                    "headers"        : {},
                    "content_type"   : None,
                    "content_length" : None,
                    "body_text"      : None,
                    "body_json"      : None,
                    "media"          : media_list
                }
            },
            "logs": media_logs[:]
        }

        checked = Tools.apply_extract_assert(
            pack["data"], extract=extract, asserts=asserts
        )

        pack["data"]["extract"] = checked["extract"]
        pack["data"]["asserts"] = checked["asserts"]
        pack["data"]["assert_summary"] = checked["summary"]
        pack["data"]["assert_ok"] = bool(checked["ok"])
        pack["data"]["ok"] = bool(pack["data"]["ok"]) and bool(checked["ok"])
        pack["logs"].extend(checked["logs"])

        if extract or asserts:
            pack["text"] += (
                f" extract={len(checked['extract'])}"
                f" fail={checked['summary']['fail']}"
            )

        return pack

    @staticmethod
    async def graphql(
        *,
        url: str,
        query: str,
        variables: typing.Optional[dict[str, typing.Any]] = None,
        operation_name: typing.Optional[str] = None,
        base_url: typing.Optional[str] = None,
        headers: typing.Optional[dict[str, str]] = None,
        params: typing.Optional[dict[str, typing.Any]] = None,
        timeout: float = 30.0,
        retries: int = 0,
        follow_redirects: bool = True,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        media_path: typing.Optional[str] = None,
        save_response: bool = False,
        save_dir: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:

        hd = dict(headers or {})
        hd.setdefault("Content-Type", "application/json")

        payload = {
            "query"     : query,
            "variables" : variables or {}
        }
        if operation_name:
            payload["operationName"] = operation_name

        pack = await Nexus.request(
            method="POST",
            url=url,
            base_url=base_url,
            headers=hd,
            params=params,
            json_body=payload,
            timeout=timeout,
            retries=retries,
            follow_redirects=follow_redirects,
            extract=extract,
            asserts=asserts,
            save_response=save_response,
            save_dir=save_dir
        )

        data      = pack.get("data") or {}
        resp      = data.get("response") or {}
        body_json = resp.get("body_json") if isinstance(resp, dict) else None

        attachments = list(pack.get("attachments") or [])
        logs = list(pack.get("logs") or [])

        media_list, media_attachments, media_logs = await Tools.collect_media(
            source_kind="json_body",
            source=body_json,
            media_path=media_path,
            save_response=save_response,
            save_dir=save_dir,
            tool="graphql_media",
            timeout=timeout,
        )

        attachments.extend(media_attachments)
        logs.extend(media_logs)

        existing_media = resp.get("media") if isinstance(resp.get("media"), list) else []
        resp["media"] = [*existing_media, *media_list]

        data["response"]    = resp
        pack["data"]        = data
        pack["attachments"] = attachments
        pack["logs"]        = logs

        gql_errors = None
        gql_ok     = bool(data.get("ok", False))

        if isinstance(body_json, dict):
            gql_errors = body_json.get("errors")
            if gql_errors:
                gql_ok = False

        data["graphql"] = {
            "query"          : query,
            "variables"      : variables or {},
            "operation_name" : operation_name,
            "errors"         : gql_errors
        }
        data["ok"] = gql_ok

        pack["data"] = data

        if gql_ok:
            pack["text"] = (
                f"GQL POST {Tools.url_join(base_url, url)} "
                f"-> {resp.get('status')} ({resp.get('elapsed_ms')}ms)"
            )
        else:
            pack["text"] = (
                f"GQL POST {Tools.url_join(base_url, url)} "
                f"-> FAIL ({resp.get('elapsed_ms')}ms)"
            )

        if extract or asserts:
            pack["text"] += (
                f" extract={len(data.get('extract') or {})}"
                f" fail={(data.get('assert_summary') or {}).get('fail', 0)}"
            )

        return pack

    # workflow: ==== MCP Tool ====
    async def nexus_http(
        self,
        payload: dict[str, typing.Any],
        concurrency: int = 1
    ) -> dict[str, typing.Any]:
        return await self.task_sequence(payload, concurrency, kind="http")

    # workflow: ==== MCP Tool ====
    async def nexus_sse(
        self,
        payload: dict[str, typing.Any],
        concurrency: int = 1
    ) -> dict[str, typing.Any]:
        return await self.task_sequence(payload, concurrency, kind="sse")

    # workflow: ==== MCP Tool ====
    async def nexus_ws(
        self,
        payload: dict[str, typing.Any],
        concurrency: int = 1
    ) -> dict[str, typing.Any]:
        return await self.task_sequence(payload, concurrency, kind="ws")

    # workflow: ==== MCP Tool ====
    async def nexus_graphql(
        self,
        payload: dict[str, typing.Any],
        concurrency: int = 1
    ) -> dict[str, typing.Any]:
        return await self.task_sequence(payload, concurrency, kind="graphql")

    async def task_sequence(
        self,
        payload: dict[str, typing.Any],
        concurrency: int,
        *,
        kind: typing.Literal["http", "sse", "ws", "graphql"]
    ) -> dict[str, typing.Any]:
        if not isinstance(payload, dict):
            return {
                "text"        : f"kind={kind} invalid payload",
                "attachments" : [],
                "data": {
                    "ok"      : False,
                    "kind"    : kind,
                    "payload" : payload
                },
                "logs": []
            }

        started_ms = Tools.ms_now()
        mission_id = f"nexus_{started_ms}"

        # 1) vars -> ctx
        ctx: dict[str, typing.Any] = {}
        if isinstance(payload.get("vars"), dict):
            ctx.update(payload["vars"])

        # 2) env / options 也走模板
        env   = payload.get("env") if isinstance(payload.get("env"), dict) else {}
        env_r = Tools.template(env, ctx) if env else {}

        base_url     = str(env_r.get("base_url") or "")
        base_headers = dict(env_r.get("headers") or {})
        base_timeout = float(env_r.get("timeout", 30.0))

        options   = payload.get("options") if isinstance(payload.get("options"), dict) else {}
        options_r = Tools.template(options, ctx) if options else {}
        fail_fast = bool(options_r.get("fail_fast", True))

        items = payload.get("items")
        if isinstance(items, list) and items:
            raw_items = [x for x in items if isinstance(x, dict)]
        else:
            raw_items = [{
                "name": payload.get("name"),
                "request": {
                    k: v for k, v in dict(payload).items()
                    if k not in {"name", "extract", "asserts", "items", "env", "vars", "options"}
                },
                "extract": payload.get("extract"),
                "asserts": payload.get("asserts")
            }]

        sem = asyncio.Semaphore(max(1, int(concurrency)))

        async def mission_once(i: int, item: dict[str, typing.Any]) -> tuple[int, StepResult]:
            async with sem:
                name = str(item.get("name") or f"{kind}_{i + 1:03d}")

                req   = item.get("request")
                req   = req if isinstance(req, dict) else {}
                req_r = Tools.template(req, ctx)

                item_extract = item.get("extract") if isinstance(item.get("extract"), dict) else None
                item_asserts = item.get("asserts") if isinstance(item.get("asserts"), list) else None

                extract_r = Tools.template(item_extract, ctx) if item_extract else None
                asserts_r = Tools.template(item_asserts, ctx) if item_asserts else None

                t0 = time.perf_counter()

                if kind == "http":
                    pack = await self.request(
                        method=str(req_r.get("method", "GET")),
                        url=str(req_r.get("url", "")),
                        base_url=str(req_r.get("base_url") or base_url) or None,
                        headers={**base_headers, **dict(req_r.get("headers") or {})},
                        params=req_r.get("params"),
                        json_body=req_r.get("json") or req_r.get("json_body"),
                        body_text=req_r.get("body") or req_r.get("body_text"),
                        form=req_r.get("form") if isinstance(req_r.get("form"), dict) else None,
                        files=req_r.get("files") if isinstance(req_r.get("files"), list) else None,
                        timeout=float(req_r.get("timeout", base_timeout)),
                        retries=int(req_r.get("retries", 0)),
                        follow_redirects=bool(req_r.get("follow_redirects", True)),
                        extract=extract_r,
                        asserts=asserts_r,
                        save_response=bool(req_r.get("save_response", False)),
                        save_dir=(str(req_r.get("save_dir")) if req_r.get("save_dir") else None)
                    )
                    data = pack.get("data") or {}
                    elapsed_ms = Tools.ms_since(t0)
                    return i, StepResult(
                        name=name,
                        type="http",
                        ok=bool(data.get("ok")),
                        elapsed_ms=elapsed_ms,
                        detail={
                            "request"        : data.get("request"),
                            "response"       : data.get("response"),
                            "extract"        : data.get("extract"),
                            "asserts"        : data.get("asserts"),
                            "assert_summary" : data.get("assert_summary"),
                            "assert_ok"      : data.get("assert_ok"),
                            "attachments"    : pack.get("attachments")
                        }
                    )

                if kind == "sse":
                    pack = await self.sse(
                        method=str(req_r.get("method", "GET")),
                        url=str(req_r.get("url", "")),
                        base_url=str(req_r.get("base_url") or base_url) or None,
                        headers={**base_headers, **dict(req_r.get("headers") or {})},
                        params=req_r.get("params"),
                        json_body=req_r.get("json") or req_r.get("json_body"),
                        body_text=req_r.get("body") or req_r.get("body_text"),
                        form=req_r.get("form") if isinstance(req_r.get("form"), dict) else None,
                        files=req_r.get("files") if isinstance(req_r.get("files"), list) else None,
                        timeout=float(req_r.get("timeout", base_timeout)),
                        retries=int(req_r.get("retries", 0)),
                        follow_redirects=bool(req_r.get("follow_redirects", True)),
                        max_events=(None if req_r.get("max_events", None) is None else int(req_r.get("max_events"))),
                        extract=extract_r,
                        asserts=asserts_r,
                        media_index=(None if req_r.get("media_index") is None else int(req_r.get("media_index"))),
                        media_path=(str(req_r.get("media_path")) if req_r.get("media_path") else None),
                        save_response=bool(req_r.get("save_response", False)),
                        save_dir=(str(req_r.get("save_dir")) if req_r.get("save_dir") else None)
                    )
                    data = pack.get("data") or {}
                    elapsed_ms = Tools.ms_since(t0)
                    return i, StepResult(
                        name=name,
                        type="sse",
                        ok=bool(data.get("ok")),
                        elapsed_ms=elapsed_ms,
                        detail={
                            "request"        : data.get("request") or {},
                            "response"       : data.get("response") or {},
                            "extract"        : data.get("extract"),
                            "asserts"        : data.get("asserts"),
                            "assert_summary" : data.get("assert_summary"),
                            "assert_ok"      : data.get("assert_ok"),
                            "attachments"    : pack.get("attachments")
                        }
                    )

                if kind == "graphql":
                    pack = await self.graphql(
                        url=str(req_r.get("url", "")),
                        query=str(req_r.get("query", "")),
                        variables=req_r.get("variables") if isinstance(req_r.get("variables"), dict) else {},
                        operation_name=req_r.get("operation_name") or req_r.get("operationName"),
                        base_url=str(req_r.get("base_url") or base_url) or None,
                        headers={**base_headers, **dict(req_r.get("headers") or {})},
                        params=req_r.get("params"),
                        timeout=float(req_r.get("timeout", base_timeout)),
                        retries=int(req_r.get("retries", 0)),
                        follow_redirects=bool(req_r.get("follow_redirects", True)),
                        extract=extract_r,
                        asserts=asserts_r,
                        media_path=(str(req_r.get("media_path")) if req_r.get("media_path") else None),
                        save_response=bool(req_r.get("save_response", False)),
                        save_dir=(str(req_r.get("save_dir")) if req_r.get("save_dir") else None)
                    )
                    data = pack.get("data") or {}
                    elapsed_ms = Tools.ms_since(t0)
                    return i, StepResult(
                        name=name,
                        type="graphql",
                        ok=bool(data.get("ok")),
                        elapsed_ms=elapsed_ms,
                        detail={
                            "request"        : data.get("request"),
                            "response"       : data.get("response"),
                            "extract"        : data.get("extract"),
                            "asserts"        : data.get("asserts"),
                            "assert_summary" : data.get("assert_summary"),
                            "assert_ok"      : data.get("assert_ok"),
                            "attachments"    : pack.get("attachments")
                        }
                    )

                raw_sends = req_r.get("sends")
                if isinstance(raw_sends, list):
                    sends_v = [str(x) for x in raw_sends]
                elif raw_sends is None:
                    sends_v = []
                else:
                    sends_v = [str(raw_sends)]

                pack = await self.ws(
                    url=str(req_r.get("url", "")),
                    headers={**base_headers, **dict(req_r.get("headers") or {})},
                    sends=sends_v,
                    timeout=float(req_r.get("timeout", base_timeout)),
                    max_messages=int(req_r.get("max_messages", 10)),
                    extract=extract_r,
                    asserts=asserts_r,
                    media_index=(None if req_r.get("media_index") is None else int(req_r.get("media_index"))),
                    media_path=(str(req_r.get("media_path")) if req_r.get("media_path") else None),
                    save_response=bool(req_r.get("save_response", False)),
                    save_dir=(str(req_r.get("save_dir")) if req_r.get("save_dir") else None)
                )
                data = pack.get("data") or {}
                elapsed_ms = Tools.ms_since(t0)
                return i, StepResult(
                    name=name,
                    type="ws",
                    ok=bool(data.get("ok")),
                    elapsed_ms=elapsed_ms,
                    detail={
                        "request"        : data.get("request") or {},
                        "response"       : data.get("response") or {},
                        "extract"        : data.get("extract"),
                        "asserts"        : data.get("asserts"),
                        "assert_summary" : data.get("assert_summary"),
                        "assert_ok"      : data.get("assert_ok"),
                        "attachments"    : pack.get("attachments")
                    }
                )

        tasks = [
            asyncio.create_task(mission_once(i, it)) for i, it in enumerate(raw_items)
        ]

        if fail_fast:
            pending = set(tasks)
            done_ordered: list[tuple[int, StepResult]] = []
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for t in done:
                    idx, sr = await t
                    done_ordered.append((idx, sr))
                    if not sr.ok:
                        for p in pending:
                            p.cancel()
                        pending = set()
                        break
            done_ordered.sort(key=lambda x: x[0])
            step_results = [sr for _, sr in done_ordered]
        else:
            out = await asyncio.gather(*tasks, return_exceptions=False)
            out.sort(key=lambda x: x[0])
            step_results = [sr for _, sr in out]

        ok_run = all(s.ok for s in step_results) if step_results else False
        finished_ms = Tools.ms_now()

        rec = RunRecord(
            mission_id=mission_id,
            ok=ok_run,
            started_ms=started_ms,
            finished_ms=finished_ms,
            payload=payload,
            final_ctx=ctx,
            steps=step_results
        )
        self.runs[mission_id] = rec

        text = f"kind={kind} total={len(step_results)} mission_id={mission_id}"
        all_attachments: list[dict[str, typing.Any]] = []
        for s in step_results:
            all_attachments.extend((s.detail or {}).get("attachments") or [])

        return {
            "text"        : text,
            "attachments" : all_attachments,
            "data": {
                "ok"         : ok_run,
                "mission_id" : mission_id,
                "kind"       : kind,
                "summary": {
                    "total"   : len(step_results),
                    "pass"    : sum(1 for s in step_results if s.ok),
                    "fail"    : sum(1 for s in step_results if not s.ok),
                    "cost_ms" : finished_ms - started_ms
                },
                "steps"     : [Tools.step_dict(s) for s in step_results],
                "final_ctx" : ctx,
                "payload"   : payload,
                "evidence"  : {"steps": [Tools.step_dict(s) for s in step_results]}
            },
            "logs": []
        }


if __name__ == '__main__':
    pass
