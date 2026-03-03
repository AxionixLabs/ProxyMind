#   ____                 _   _
#  / ___|___  _ __ ___  | \ | | _____  ___   _ ___
# | |   / _ \| '__/ _ \ |  \| |/ _ \ \/ / | | / __|
# | |__| (_) | | |  __/ | |\  |  __/>  <| |_| \__ \
#  \____\___/|_|  \___| |_| \_|\___/_/\_\\__,_|___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import json
import time
import httpx
import typing
import asyncio
import websockets
from dataclasses import dataclass, field
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
    failures: list[dict[str, typing.Any]] = field(default_factory=list)
    extracted: dict[str, typing.Any] = field(default_factory=dict)
    detail: dict[str, typing.Any] = field(default_factory=dict)


@dataclass
class RunRecord:
    run_id: str
    ok: bool
    started_ms: int
    finished_ms: int
    payload: dict[str, typing.Any]
    final_ctx: dict[str, typing.Any]
    steps: list[StepResult]


def ms_now() -> int:
    return int(time.time() * 1000)


def ms_since(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def url_join(base_url: typing.Optional[str], url: str) -> str:
    if not base_url: return url
    return base_url.rstrip("/") + "/" + url.lstrip("/")


def tmpl(x: typing.Any, ctx: dict[str, typing.Any]) -> typing.Any:
    """极简模板：只替换字符串里的 {{k}}"""
    if isinstance(x, str):
        s = x
        for k, v in ctx.items():
            s = s.replace("{{" + k + "}}", str(v))
        return s
    if isinstance(x, list):
        return [tmpl(i, ctx) for i in x]
    if isinstance(x, dict):
        return {k: tmpl(v, ctx) for k, v in x.items()}
    return x


def json_select(body: typing.Any, selector: str) -> typing.Any:
    """
    极简 selector:
      - $.a.b.c  (dict only)
    """
    if not isinstance(selector, str) or not selector.startswith("$."):
        return None
    cur = body
    for part in selector[2:].split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def try_json(s: str) -> typing.Optional[typing.Any]:
    try:
        return json.loads(s)
    except ValueError:
        return None


def deep_contains(hay: typing.Any, needle: typing.Any) -> bool:
    """
    深度“子集包含”：
    - dict: needle 的每个 key 必须在 hay 且值深度匹配
    - list: needle 的每个元素必须在 hay 中能匹配到一个元素（不要求顺序）
    - str/number/bool/None: 直接相等
    """
    if needle is None:
        return hay is None

    if isinstance(needle, dict):
        if not isinstance(hay, dict):
            return False
        for k, nv in needle.items():
            if k not in hay:
                return False
            if not deep_contains(hay[k], nv):
                return False
        return True

    if isinstance(needle, list):
        if not isinstance(hay, list):
            return False
        for nv in needle:
            if not any(deep_contains(hv, nv) for hv in hay):
                return False
        return True

    return hay == needle


def match_contains(value: typing.Any, spec: dict[str, typing.Any]) -> bool:
    """
    spec 支持：
      - {"contains": <any>}                        # 默认策略：str包含 / list包含 / dict深度子集 / 标量相等
      - {"contains_regex": "pat", "flags":"i"}     # 正则（i=ignorecase）
      - {"contains_any": [spec|scalar|dict]}       # 任意一个满足
      - {"contains_all": [spec|scalar|dict]}       # 全部满足
      - {"contains_path": "$.k", "contains": ...}  # value 是 list[dict] 时，从每个元素取 path 再匹配
      - {"contains_key": "k"}                      # value 是 dict 时包含 key
      - {"ignore_case": true}                      # str contains 时忽略大小写
    """
    ignore_case = bool(spec.get("ignore_case", False))

    if "contains_regex" in spec:
        pat = str(spec["contains_regex"])
        flags = re.I if ("flags" in spec and "i" in str(spec["flags"]).lower()) else 0
        if isinstance(value, str):
            return re.search(pat, value, flags) is not None
        return False

    if "contains_any" in spec:
        arr = spec.get("contains_any") or []
        return any(match_contains(value, x if isinstance(x, dict) else {"contains": x}) for x in arr)

    if "contains_all" in spec:
        arr = spec.get("contains_all") or []
        return all(match_contains(value, x if isinstance(x, dict) else {"contains": x}) for x in arr)

    if "contains_path" in spec:
        path = str(spec["contains_path"])
        if not isinstance(value, list):
            return False

        def pick(v: typing.Any) -> typing.Any:
            return json_select(v, path) if isinstance(v, dict) else None

        picked = [pick(it) for it in value]
        needle = spec.get("contains")
        return any(
            deep_contains(p, needle) if isinstance(needle, (dict, list)) else (p == needle)
            for p in picked
        )

    if "contains_key" in spec:
        if not isinstance(value, dict):
            return False
        return str(spec["contains_key"]) in value

    needle = spec.get("contains")

    if isinstance(value, str):
        if needle is None:
            return False
        sub = str(needle)
        return sub.lower() in value.lower() if ignore_case else (sub in value)

    if isinstance(value, list):
        if isinstance(needle, (dict, list)):
            return any(deep_contains(it, needle) for it in value)
        return needle in value

    if isinstance(value, dict):
        if isinstance(needle, dict):
            return deep_contains(value, needle)
        return False

    return value == needle


def parse_sse_block(block: str) -> typing.Optional[SseEvent]:
    """
    RFC style:
      event: xxx
      data: yyy
      data: yyy2
      id: 123
    """
    raw = block.strip("\r\n")
    if not raw.strip():
        return None

    ev = SseEvent(event=None, data="", id=None)
    data_lines: list[str] = []
    for ln in raw.splitlines():
        if ln.startswith(":"):
            continue
        if ":" not in ln:
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


class Nexus(object):
    """Nexus class."""

    agent_id: str = "nexus"

    def __init__(self):
        self.runs: dict[str, RunRecord] = {}

    @staticmethod
    def step_dict(step_result: StepResult) -> dict[str, typing.Any]:
        return {
            "name"       : step_result.name,
            "type"       : step_result.type,
            "ok"         : step_result.ok,
            "elapsed_ms" : step_result.elapsed_ms,
            "failures"   : step_result.failures,
            "extracted"  : step_result.extracted,
            "detail"     : step_result.detail
        }

    @staticmethod
    async def request(
        method: str,
        url: str,
        *,
        base_url: typing.Optional[str] = None,
        headers: typing.Optional[dict[str, str]] = None,
        params: typing.Optional[dict[str, typing.Any]] = None,
        json_body: typing.Optional[dict[str, typing.Any]] = None,
        body_text: typing.Optional[str] = None,
        timeout: float = 30.0,
        retries: int = 0,
        follow_redirects: bool = True
    ) -> dict[str, typing.Any]:

        method = (method or "GET").upper()
        url = url_join(base_url, url)
        headers = headers or {}

        t0 = time.perf_counter()
        last_err: typing.Optional[str] = None

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=follow_redirects) as client:
            for _ in range(max(0, int(retries)) + 1):
                try:
                    resp = await client.request(
                        method,
                        url,
                        headers=headers,
                        params=params,
                        json=json_body,
                        content=body_text,
                    )
                    elapsed_ms = ms_since(t0)

                    try:
                        body_json = resp.json()
                    except ValueError:
                        body_json = None

                    ok = 200 <= int(resp.status_code) < 400
                    return {
                        "text"        : f"{method} {url} -> {resp.status_code} ({elapsed_ms}ms)",
                        "attachments" : [],
                        "data": {
                            "ok": ok,
                            "request": {
                                "method"  : method,
                                "url"     : url,
                                "headers" : headers,
                                "params"  : params,
                                "timeout" : timeout,
                                "retries" : retries,
                            },
                            "response": {
                                "status"     : resp.status_code,
                                "headers"    : dict(resp.headers),
                                "elapsed_ms" : elapsed_ms,
                                "body_text"  : resp.text,
                                "body_json"  : body_json
                            },
                        },
                        "logs": []
                    }

                except (httpx.TimeoutException, httpx.RequestError) as e:
                    last_err = f"{type(e).__name__}: {e}"

        elapsed_ms = ms_since(t0)
        return {
            "text": f"{method} {url} -> ERROR ({elapsed_ms}ms) {last_err}",
            "attachments": [],
            "data": {
                "ok": False,
                "request": {
                    "method": method,
                    "url": url,
                    "headers": headers,
                    "params": params,
                    "timeout": timeout,
                    "retries": retries,
                },
                "error": last_err,
                "elapsed_ms": elapsed_ms,
            },
            "logs": [],
        }

    @staticmethod
    async def sse(
        url: str,
        *,
        base_url: typing.Optional[str] = None,
        headers: typing.Optional[dict[str, str]] = None,
        params: typing.Optional[dict[str, typing.Any]] = None,
        timeout: float = 30.0,
        max_events: int = 10
    ) -> dict[str, typing.Any]:

        url = url_join(base_url, url)
        headers = headers or {}

        t0 = time.perf_counter()
        events: list[dict[str, typing.Any]] = []
        last_err: typing.Optional[str] = None

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("GET", url, headers=headers, params=params) as resp:
                    if resp.status_code != 200:
                        return {
                            "text"        : f"SSE {url} -> {resp.status_code}",
                            "attachments" : [],
                            "data": {
                                "ok"     : False,
                                "status" : resp.status_code,
                                "url"    : url
                            },
                            "logs": []
                        }

                    buf = ""
                    async for chunk in resp.aiter_text():
                        buf += chunk
                        while "\n\n" in buf:
                            raw, buf = buf.split("\n\n", 1)
                            ev = parse_sse_block(raw)
                            if not ev: continue
                            events.append({"event": ev.event, "id": ev.id, "data": ev.data})

                            if len(events) >= int(max_events):
                                elapsed_ms = ms_since(t0)
                                return {
                                    "text"        : f"SSE {url} ok events={len(events)} ({elapsed_ms}ms)",
                                    "attachments" : [],
                                    "data": {
                                        "ok"         : True,
                                        "url"        : url,
                                        "status"     : resp.status_code,
                                        "elapsed_ms" : elapsed_ms,
                                        "events"     : events
                                    },
                                    "logs": []
                                }

        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"

        elapsed_ms = ms_since(t0)
        return {
            "text"        : f"SSE {url} -> ERROR ({elapsed_ms}ms) {last_err}",
            "attachments" : [],
            "data": {
                "ok"         : False,
                "url"        : url,
                "elapsed_ms" : elapsed_ms,
                "error"      : last_err,
                "events"     : events
            },
            "logs": []
        }

    @staticmethod
    async def ws(
        url: str,
        *,
        headers: typing.Optional[dict[str, str]] = None,
        sends: typing.Optional[list[str]] = None,
        timeout: float = 30.0,
        max_messages: int = 10
    ) -> dict[str, typing.Any]:

        t0 = time.perf_counter()
        headers = headers or {}
        sends = sends or []
        recv: list[str] = []
        last_err: typing.Optional[str] = None

        try:
            async with websockets.connect(url, extra_headers=headers, open_timeout=timeout) as ws:
                for s in sends:
                    await ws.send(s)

                for _ in range(int(max_messages)):
                    msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    recv.append(msg if isinstance(msg, str) else msg.decode(const.CHARSET, const.IGNORE))

        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"

        elapsed_ms = ms_since(t0)
        ok = last_err is None
        text = (
            f"WS {url} {'ok' if ok else 'ERROR'} "
            f"msgs={len(recv)} ({elapsed_ms}ms){'' if ok else ' ' + last_err}"
        )

        return {
            "text"        : text,
            "attachments" : [],
            "data": {
                "ok"         : ok,
                "url"        : url,
                "elapsed_ms" : elapsed_ms,
                "messages"   : recv,
                "error"      : last_err
            },
            "logs": []
        }

    async def mission(self, payload: dict[str, typing.Any], concurrency: int = 1) -> dict[str, typing.Any]:
        started_ms = ms_now()
        run_id = f"nexus_{started_ms}"

        env = payload.get("env") if isinstance(payload.get("env"), dict) else {}
        base_url = str(env.get("base_url") or "")
        base_headers = dict(env.get("headers") or {})
        base_timeout = float(env.get("timeout_s", env.get("timeout", 30.0)))

        options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
        fail_fast = bool(options.get("fail_fast", True))

        ctx: dict[str, typing.Any] = {}
        if isinstance(payload.get("vars"), dict):
            ctx.update(payload["vars"])

        steps_in = list(payload.get("steps") or [])
        sem = asyncio.Semaphore(max(1, int(concurrency)))

        async def run_one(i: int, st: dict[str, typing.Any]) -> tuple[int, StepResult]:
            async with sem:
                name = str(st.get("name") or f"step_{i + 1:03d}")
                typ = str(st.get("type") or "http").lower()
                req = st.get("request") or {}

                # 断言/提取也支持模板
                assertions = tmpl(st.get("assert") or [], ctx)
                extract_rules = tmpl(st.get("extract") or {}, ctx)

                req_r = tmpl(req, ctx)
                t0 = time.perf_counter()

                if typ == "http":
                    resp_pack = await self.request(
                        method=str(req_r.get("method", "GET")),
                        url=str(req_r.get("url", "")),
                        base_url=str(req_r.get("base_url") or base_url) or None,
                        headers={**base_headers, **(req_r.get("headers") or {})},
                        params=req_r.get("params"),
                        json_body=req_r.get("json"),
                        body_text=req_r.get("body"),
                        timeout=float(req_r.get("timeout_s", base_timeout)),
                        retries=int(req_r.get("retries", 0)),
                        follow_redirects=bool(req_r.get("follow_redirects", True)),
                    )
                    elapsed_ms = ms_since(t0)
                    data = resp_pack.get("data") or {}
                    failures = self.assert_http(data, assertions)
                    extracted = self.extract_http(data, extract_rules, ctx)
                    ok_step = bool(data.get("ok")) and (len(failures) == 0)
                    return i, StepResult(
                        name=name,
                        type="http",
                        ok=ok_step,
                        elapsed_ms=elapsed_ms,
                        failures=failures,
                        extracted=extracted,
                        detail={"status": (data.get("response") or {}).get("status")},
                    )

                if typ == "sse":
                    resp_pack = await self.sse(
                        url=str(req_r.get("url", "")),
                        base_url=str(req_r.get("base_url") or base_url) or None,
                        headers={**base_headers, **(req_r.get("headers") or {})},
                        params=req_r.get("params"),
                        timeout=float(req_r.get("timeout_s", base_timeout)),
                        max_events=int(req_r.get("max_events", 10)),
                    )
                    elapsed_ms = ms_since(t0)
                    data = resp_pack.get("data") or {}
                    failures = self.assert_sse(data, assertions)
                    extracted = self.extract_sse(data, extract_rules, ctx)
                    ok_step = bool(data.get("ok")) and (len(failures) == 0)
                    return i, StepResult(
                        name=name,
                        type="sse",
                        ok=ok_step,
                        elapsed_ms=elapsed_ms,
                        failures=failures,
                        extracted=extracted,
                        detail={"url": data.get("url")},
                    )

                if typ == "ws":
                    resp_pack = await self.ws(
                        url=str(req_r.get("url", "")),
                        headers={**base_headers, **(req_r.get("headers") or {})},
                        sends=list(req_r.get("sends") or []),
                        timeout=float(req_r.get("timeout_s", base_timeout)),
                        max_messages=int(req_r.get("max_messages", 10)),
                    )
                    elapsed_ms = ms_since(t0)
                    data = resp_pack.get("data") or {}
                    failures = self.assert_ws(data, assertions)
                    extracted = self.extract_ws(data, extract_rules, ctx)
                    ok_step = bool(data.get("ok")) and (len(failures) == 0)
                    return i, StepResult(
                        name=name,
                        type="ws",
                        ok=ok_step,
                        elapsed_ms=elapsed_ms,
                        failures=failures,
                        extracted=extracted,
                        detail={"url": data.get("url")},
                    )

                elapsed_ms = ms_since(t0)
                return i, StepResult(
                    name=name,
                    type=typ,
                    ok=False,
                    elapsed_ms=elapsed_ms,
                    failures=[{"type": "unknown_step_type", "value": typ}],
                )

        tasks = [asyncio.create_task(run_one(i, st)) for i, st in enumerate(steps_in)]

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
            step_results = [sr for _, sr in out]

        ok_run = all(s.ok for s in step_results) if step_results else False
        finished_ms = ms_now()

        self.runs[run_id] = RunRecord(
            run_id=run_id,
            ok=ok_run,
            started_ms=started_ms,
            finished_ms=finished_ms,
            payload=payload,
            final_ctx=ctx,
            steps=step_results,
        )

        return {
            "text"        : f"nexus_mission ok={ok_run} steps={len(step_results)} run_id={run_id}",
            "attachments" : [],
            "data": {
                "ok": ok_run,
                "run_id": run_id,
                "summary": {
                    "total"   : len(step_results),
                    "pass"    : sum(1 for s in step_results if s.ok),
                    "fail"    : sum(1 for s in step_results if not s.ok),
                    "cost_ms" : finished_ms - started_ms,
                },
                "steps"     : [self.step_dict(s) for s in step_results],
                "final_ctx" : ctx,
                "payload"   : payload
            },
            "logs": []
        }

    async def flow(self, payload: dict[str, typing.Any], concurrency: int = 1) -> dict[str, typing.Any]:
        """
        统一入口：
          {"mode":"http", "env":{...}, "method":"GET", "url":"/ping", ...}
          {"mode":"sse",  "env":{...}, "url":"/events", ...}
          {"mode":"ws",   "env":{...}, "url":"wss://...", "sends":[...], ...}
          {"mode":"flow", "env":{...}, "vars":{...}, "options":{...}, "steps":[...], ...其它字段也允许}
        """
        mode = str(payload.get("mode") or payload.get("type") or "flow").lower()

        env = payload.get("env") if isinstance(payload.get("env"), dict) else {}
        base_url = env.get("base_url")
        base_headers = env.get("headers") if isinstance(env.get("headers"), dict) else {}
        base_timeout = float(env.get("timeout_s", env.get("timeout", 30.0)))

        if mode == "http":
            return await self.request(
                method=str(payload.get("method", "GET")),
                url=str(payload.get("url", "")),
                base_url=str(payload.get("base_url") or base_url) or None,
                headers={**base_headers, **(payload.get("headers") or {})},
                params=payload.get("params"),
                json_body=payload.get("json") or payload.get("json_body"),
                body_text=payload.get("body") or payload.get("body_text"),
                timeout=float(payload.get("timeout_s", payload.get("timeout", base_timeout))),
                retries=int(payload.get("retries", 0)),
                follow_redirects=bool(payload.get("follow_redirects", True)),
            )

        if mode == "sse":
            return await self.sse(
                url=str(payload.get("url", "")),
                base_url=str(payload.get("base_url") or base_url) or None,
                headers={**base_headers, **(payload.get("headers") or {})},
                params=payload.get("params"),
                timeout=float(payload.get("timeout_s", payload.get("timeout", base_timeout))),
                max_events=int(payload.get("max_events", 10)),
            )

        if mode == "ws":
            return await self.ws(
                url=str(payload.get("url", "")),
                headers={**base_headers, **(payload.get("headers") or {})},
                sends=list(payload.get("sends") or []),
                timeout=float(payload.get("timeout_s", payload.get("timeout", base_timeout))),
                max_messages=int(payload.get("max_messages", 10)),
            )

        return await self.mission(payload, concurrency)

    @staticmethod
    def assert_http(
        data: dict[str, typing.Any],
        assertions: list[dict[str, typing.Any]]
    ) -> list[dict[str, typing.Any]]:
        failures: list[dict[str, typing.Any]] = []
        resp = data.get("response") or {}

        status = resp.get("status")
        elapsed_ms = resp.get("elapsed_ms")
        body_json = resp.get("body_json")
        body_text = resp.get("body_text") or ""
        headers = resp.get("headers") or {}

        hdr_lc = {str(k).lower(): str(v) for k, v in dict(headers).items()}

        for a in assertions:
            a = a or {}
            t = a.get("type")

            if t == "status":
                exp = a.get("eq")
                if status != exp:
                    failures.append({"type": "status", "expected": exp, "actual": status})

            elif t == "latency_lt":
                lim = int(a.get("ms", 0))
                if isinstance(elapsed_ms, int) and elapsed_ms >= lim:
                    failures.append({"type": "latency_lt", "limit_ms": lim, "actual_ms": elapsed_ms})

            elif t == "json_has":
                path = str(a.get("path") or "")
                v = json_select(body_json, path) if body_json is not None else None
                if v is None:
                    failures.append({"type": "json_has", "path": path})

            elif t == "json_eq":
                path = str(a.get("path") or "")
                exp = a.get("eq")
                v = json_select(body_json, path) if body_json is not None else None
                if v != exp:
                    failures.append({"type": "json_eq", "path": path, "expected": exp, "actual": v})

            elif t == "json_contains":
                path = str(a.get("path") or "")
                v = json_select(body_json, path) if body_json is not None else None
                spec = dict(a)
                spec.pop("type", None)
                spec.pop("path", None)
                if not match_contains(v, spec):
                    failures.append({"type": "json_contains", "path": path, "spec": spec, "actual": v})

            elif t == "header_contains":
                key = str(a.get("key") or "").lower()
                sub = str(a.get("contains") or "")
                val = hdr_lc.get(key, "")
                if not val or (sub and sub not in val):
                    failures.append({"type": "header_contains", "key": key, "contains": sub, "actual": val})

            elif t == "text_contains":
                sub = str(a.get("substr") or "")
                if sub and sub not in body_text:
                    failures.append({"type": "text_contains", "substr": sub})

            else:
                failures.append({"type": "unknown_assert", "assert": a})

        return failures

    @staticmethod
    def extract_http(
        data: dict[str, typing.Any],
        rules: dict[str, str],
        ctx: dict[str, typing.Any]
    ) -> dict[str, typing.Any]:

        extracted: dict[str, typing.Any] = {}
        resp = data.get("response") or {}
        body_json = resp.get("body_json")
        body_text = resp.get("body_text") or ""

        if not isinstance(rules, dict):
            return extracted

        for k, sel in rules.items():
            if not isinstance(sel, str):
                continue
            v = None
            if sel.startswith("$."):
                v = json_select(body_json, sel) if body_json is not None else None
            elif sel.startswith("re:"):
                pat = sel[3:]
                m = re.search(pat, body_text)
                v = m.group(1) if m and m.groups() else (m.group(0) if m else None)
            if v is not None:
                ctx[str(k)] = v
                extracted[str(k)] = v

        return extracted

    @staticmethod
    def assert_sse(
        data: dict[str, typing.Any],
        assertions: list[dict[str, typing.Any]]
    ) -> list[dict[str, typing.Any]]:

        failures: list[dict[str, typing.Any]] = []
        events = data.get("events") or []

        for a in assertions:
            a = a or {}
            t = a.get("type")

            if t == "event_count_ge":
                n = int(a.get("n", 0))
                if len(events) < n:
                    failures.append({"type": "event_count_ge", "n": n, "actual": len(events)})

            elif t == "event_any_data_contains":
                sub = str(a.get("substr") or "")
                ok = any(sub in str(ev.get("data") or "") for ev in events)
                if sub and not ok:
                    failures.append({"type": "event_any_data_contains", "substr": sub})

            else:
                failures.append({"type": "unknown_assert", "assert": a})

        return failures

    @staticmethod
    def extract_sse(
        data: dict[str, typing.Any],
        rules: dict[str, str],
        ctx: dict[str, typing.Any]
    ) -> dict[str, typing.Any]:

        extracted: dict[str, typing.Any] = {}
        events = data.get("events") or []

        if not isinstance(rules, dict):
            return extracted

        last_data = str((events[-1].get("data") if events else "") or "")
        last_json = try_json(last_data)

        for k, sel in rules.items():
            if not isinstance(sel, str):
                continue

            v = None
            if sel.startswith("$.") and last_json is not None:
                v = json_select(last_json, sel)
            elif sel.startswith("re:"):
                pat = sel[3:]
                m = re.search(pat, last_data)
                v = m.group(1) if m and m.groups() else (m.group(0) if m else None)
            if v is not None:
                ctx[str(k)] = v
                extracted[str(k)] = v

        return extracted

    @staticmethod
    def assert_ws(
        data: dict[str, typing.Any],
        assertions: list[dict[str, typing.Any]]
    ) -> list[dict[str, typing.Any]]:

        failures: list[dict[str, typing.Any]] = []
        msgs = data.get("messages") or []

        for a in assertions:
            a = a or {}
            t = a.get("type")

            if t == "msg_count_ge":
                n = int(a.get("n", 0))
                if len(msgs) < n:
                    failures.append({"type": "msg_count_ge", "n": n, "actual": len(msgs)})

            elif t == "msg_any_contains":
                sub = str(a.get("substr") or "")
                ok = any(sub in str(m) for m in msgs)
                if sub and not ok:
                    failures.append({"type": "msg_any_contains", "substr": sub})

            else:
                failures.append({"type": "unknown_assert", "assert": a})

        return failures

    @staticmethod
    def extract_ws(
        data: dict[str, typing.Any],
        rules: dict[str, str],
        ctx: dict[str, typing.Any]
    ) -> dict[str, typing.Any]:

        extracted: dict[str, typing.Any] = {}
        msgs = data.get("messages") or []

        if not isinstance(rules, dict):
            return extracted

        last_msg = str(msgs[-1]) if msgs else ""
        last_json = try_json(last_msg)

        for k, sel in rules.items():
            if not isinstance(sel, str):
                continue

            v = None
            if sel.startswith("$.") and last_json is not None:
                v = json_select(last_json, sel)
            elif sel.startswith("re:"):
                pat = sel[3:]
                m = re.search(pat, last_msg)
                v = m.group(1) if m and m.groups() else (m.group(0) if m else None)
            if v is not None:
                ctx[str(k)] = v
                extracted[str(k)] = v

        return extracted


if __name__ == '__main__':
    pass
