# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import json
import uuid
import socket
import shutil
import typing
import asyncio
import tempfile
import contextlib
from pathlib import Path
from dataclasses import (
    dataclass,
    field
)
from datetime import (
    datetime,
    timezone
)
from urllib.parse import (
    parse_qsl,
    urlsplit
)
from backend.utilities.tool_result import ToolOutput

if typing.TYPE_CHECKING:
    from backend.mcp_hub.hub_device import Device
    from backend.utilities.runtime.idle import Idle


@dataclass
class CaptureSession(object):
    """描述一次抓包会话的运行状态和落盘路径。"""

    capture_id: str
    device: "Device"
    package_name: str
    proxy_endpoint: str
    previous_proxy: str
    directory: Path
    flow_path: Path
    index_path: Path
    har_path: Path
    process: asyncio.subprocess.Process
    idle: "Idle"
    active: bool = True
    stopping: bool = False
    proxy_restored: bool | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def session_key(self) -> str:
        """返回 Idle 使用的稳定会话键。"""
        return f"capture:{self.capture_id}"


class Capture(object):
    """管理设备代理抓包会话。"""

    def __init__(self) -> None:
        """初始化抓包会话注册表。"""
        self.agent_id = "capture"

        self.sessions: dict[str, CaptureSession] = {}
        self.active_by_serial: dict[str, str]    = {}
        self.starting_serials: set[str]          = set()

        self.lock: asyncio.Lock = asyncio.Lock()

    @staticmethod
    def _har(flows: list[dict[str, typing.Any]]) -> dict[str, typing.Any]:
        """把完整流索引转换为 HAR 表示。"""
        def headers(value: typing.Any) -> list[dict[str, str]]:
            if not isinstance(value, list):
                return []
            return [
                {"name": str(item[0]), "value": str(item[1])}
                for item in value
                if isinstance(item, list) and len(item) == 2
            ]

        def header_value(items: list[dict[str, str]], name: str) -> str:
            expected = name.lower()
            for item in items:
                if item["name"].lower() == expected:
                    return item["value"]
            return ""

        def content(value: typing.Any, mime_type: str) -> dict[str, typing.Any]:
            body = value if isinstance(value, dict) else {}
            result: dict[str, typing.Any] = {
                "size": int(body.get("size") or 0),
                "mimeType": mime_type,
                "text": str(body.get("text") or ""),
            }
            if body.get("encoding") == "base64":
                result["encoding"] = "base64"
            return result

        entries: list[dict[str, typing.Any]] = []
        for flow in flows:
            request   = flow.get("request") or {}
            response  = flow.get("response") or {}
            host      = str(request.get("host") or "")
            port      = request.get("port")
            scheme    = str(request.get("scheme") or "http")
            path      = str(request.get("path") or "/")
            authority = host if port in (None, 80, 443) else f"{host}:{port}"
            url       = str(request.get("url") or f"{scheme}://{authority}{path}")
            request_headers = headers(request.get("headers"))
            response_headers = headers(response.get("headers"))
            request_body = request.get("body") if isinstance(request.get("body"), dict) else None
            query = [
                {"name": name, "value": value}
                for name, value in parse_qsl(urlsplit(url).query, keep_blank_values=True)
            ]
            post_data: dict[str, typing.Any] | None = None
            if request_body is not None:
                post_data = content(request_body, header_value(request_headers, "content-type"))
                post_data["mimeType"] = header_value(request_headers, "content-type")

            entries.append({
                "startedDateTime" : flow.get("started_at") or datetime.now(timezone.utc).isoformat(),
                "time"            : flow.get("duration_ms") or 0,
                "request"         : {
                    "method": request.get("method") or "GET", "url": url,
                    "httpVersion": request.get("http_version") or "HTTP/1.1",
                    "headers": request_headers, "queryString": query, "cookies": [],
                    "headersSize": -1,
                    "bodySize": int((request_body or {}).get("size") or 0),
                    **({"postData": post_data} if post_data is not None else {}),
                },
                "response"        : {
                    "status": response.get("status_code") or 0,
                    "statusText": response.get("reason") or "",
                    "httpVersion": response.get("http_version") or "HTTP/1.1",
                    "headers": response_headers, "cookies": [],
                    "content": content(response.get("body"), header_value(response_headers, "content-type")),
                    "redirectURL": header_value(response_headers, "location"),
                    "headersSize": -1,
                    "bodySize": int((response.get("body") or {}).get("size") or 0),
                },
                "cache"           : {},
                "timings"         : {"wait": flow.get("duration_ms") or 0}
            })

        return {
            "log": {
                "version" : "1.2",
                "creator" : {"name": "Helix", "version": "1.0"}, "entries": entries
            }
        }

    @staticmethod
    def _session_id() -> str:
        """生成短会话标识。"""
        return f"cap_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _host_ip() -> str:
        """推断物理设备可访问的本机局域网地址。"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            address = str(sock.getsockname()[0]).strip()
        finally:
            sock.close()

        if not address or address.startswith("127."):
            raise RuntimeError("Cannot determine a LAN address; pass proxy_host explicitly")
        return address

    @staticmethod
    def _addon_source(index_path: Path) -> str:
        """生成写入完整流索引的 mitmproxy addon。"""
        target = json.dumps(str(index_path))
        return f'''# -*- coding: utf-8 -*-
import base64
import json
from datetime import datetime, timezone

INDEX_PATH = {target}

def _stamp(value):
    if not value:
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")

def _headers(message):
    return [[key, value] for key, value in message.headers.items(multi=True)] if message else []

def _body(message):
    raw = message.raw_content or b"" if message else b""
    try:
        return {{"text": raw.decode("utf-8"), "encoding": "utf-8", "size": len(raw)}}
    except UnicodeDecodeError:
        return {{"text": base64.b64encode(raw).decode("ascii"), "encoding": "base64", "size": len(raw)}}

def _item(flow, status_code=None, error=None):
    request = flow.request
    response = flow.response
    started = request.timestamp_start or 0
    ended = (response.timestamp_end if response else None) or request.timestamp_end or started
    return {{
        "started_at": _stamp(started),
        "completed_at": _stamp(ended),
        "duration_ms": max(0, int((ended - started) * 1000)),
        "request": {{
            "method": request.method,
            "scheme": request.scheme,
            "host": request.host,
            "port": request.port,
            "path": request.path.split("?", 1)[0],
            "url": request.pretty_url,
            "http_version": request.http_version,
            "headers": _headers(request),
            "body": _body(request),
        }},
        "response": {{
            "status_code": status_code,
            "reason": response.reason if response else "",
            "http_version": response.http_version if response else "",
            "headers": _headers(response),
            "body": _body(response),
        }},
        "error": error,
    }}

def _append(item):
    with open(INDEX_PATH, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\\n")

def response(flow):
    _append(_item(flow, status_code=flow.response.status_code))

def error(flow):
    _append(_item(flow, error=str(flow.error) if flow.error else "request_error"))
'''

    @staticmethod
    def _normalize_proxy(value: str) -> str:
        """标准化 Android settings 返回的代理文本。"""
        raw = str(value or "").strip()
        return "" if raw.lower() in {"", "null", ":0"} else raw

    @staticmethod
    def _read_flows(path: Path) -> list[dict[str, typing.Any]]:
        """读取增量写入的脱敏流索引。"""
        if not path.is_file():
            return []

        items: list[dict[str, typing.Any]] = []
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    items.append(item)
        return items

    @staticmethod
    def _match(
        flow: dict[str, typing.Any],
        *,
        host: str | None = None,
        path: str | None = None,
        method: str | None = None,
        status_code: int | None = None
    ) -> bool:
        """判断流记录是否满足筛选条件。"""
        request  = flow.get("request") or {}
        response = flow.get("response") or {}

        if host and str(request.get("host") or "") != str(host):
            return False
        if path and str(path) not in str(request.get("path") or ""):
            return False
        if method and str(request.get("method") or "").upper() != str(method).upper():
            return False
        if status_code is not None and response.get("status_code") != int(status_code):
            return False

        return True

    @staticmethod
    async def _free_port(host: str) -> int:
        """分配一个当前可绑定的 TCP 端口。"""
        server = await asyncio.start_server(lambda _r, _w: None, host=host, port=0)
        try:
            sockets = server.sockets or []
            if not sockets:
                raise RuntimeError("Cannot allocate capture port")
            return int(sockets[0].getsockname()[1])
        finally:
            server.close()
            await server.wait_closed()

    def _get(self, capture_id: str) -> CaptureSession:
        """读取指定会话。"""
        key = str(capture_id or "").strip()
        if not key or key not in self.sessions:
            raise RuntimeError(f"Capture not found: {capture_id}")
        return self.sessions[key]

    def query(
        self,
        capture_id: str,
        *,
        host: str | None = None,
        path: str | None = None,
        method: str | None = None,
        status_code: int | None = None,
        limit: int = 50,
    ) -> ToolOutput:
        """按条件查询会话中的完整流记录。"""
        session = self._get(capture_id)

        items = [
            item for item in self._read_flows(session.index_path)
            if self._match(item, host=host, path=path, method=method, status_code=status_code)
        ]
        limited = items[: max(1, min(int(limit), 500))]

        return ToolOutput(
            text=f"查询到 {len(items)} 条流记录。",
            data={
                "capture_id" : capture_id,
                "active"     : session.active,
                "count"      : len(items),
                "flows"      : limited,
                "truncated"  : len(limited) < len(items)
            }
        )

    def export(self, capture_id: str, export_format: str = "har") -> ToolOutput:
        """导出会话中的完整流记录。"""
        session    = self._get(capture_id)
        normalized = str(export_format or "har").lower()
        flows      = self._read_flows(session.index_path)

        if normalized == "json":
            target = session.directory / "flows.json"
            target.write_text(json.dumps(flows, ensure_ascii=False, indent=2), encoding="utf-8")
        elif normalized == "har":
            target = session.har_path
            target.write_text(json.dumps(self._har(flows), ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            raise ValueError("format must be har or json")
        return ToolOutput(
            text=f"已导出 {len(flows)} 条流记录。",
            data={"capture_id": capture_id, "format": normalized, "path": str(target), "flow_count": len(flows)},
        )

    def assert_rule(self, capture_id: str, rule: dict[str, typing.Any]) -> ToolOutput:
        """执行结构化流量断言。"""
        if not isinstance(rule, dict):
            raise ValueError("rule must be an object")

        allowed = {"host", "path", "method", "status_code", "min_count", "max_count", "max_duration_ms"}
        unknown = sorted(set(rule) - allowed)

        if unknown:
            raise ValueError(f"Unsupported rule fields: {unknown}")
        session = self._get(capture_id)

        items = [
            item for item in self._read_flows(session.index_path)
            if self._match(
                item,
                host=rule.get("host"),
                path=rule.get("path"),
                method=rule.get("method"),
                status_code=rule.get("status_code")
            )
        ]
        failures: list[dict[str, typing.Any]] = []

        min_count = int(rule.get("min_count", 1))

        if len(items) < min_count:
            failures.append({"field": "min_count", "expected": min_count, "actual": len(items)})
        if rule.get("max_count") is not None and len(items) > int(rule["max_count"]):
            failures.append({"field": "max_count", "expected": int(rule["max_count"]), "actual": len(items)})

        if rule.get("max_duration_ms") is not None:
            limit = int(rule["max_duration_ms"])
            slow  = [item for item in items if int(item.get("duration_ms") or 0) > limit]

            if slow:
                failures.append({
                    "field"    : "max_duration_ms",
                    "expected" : limit,
                    "actual"   : [item.get("duration_ms") for item in slow]
                })

        passed = not failures

        return ToolOutput(
            ok=passed,
            text="抓包断言通过。" if passed else "抓包断言未通过。",
            data={
                "capture_id"  : capture_id,
                "passed"      : passed,
                "rule"        : rule,
                "match_count" : len(items),
                "failures"    : failures
            }
        )

    async def _proxy_address(self, device: "Device", proxy_host: str | None) -> tuple[str, str]:
        """确定代理监听地址和设备应使用的地址。"""
        requested = str(proxy_host or "").strip()
        if requested:
            return requested, requested

        if await device.phone.is_emulator():
            return "127.0.0.1", "10.0.2.2"

        address = self._host_ip()
        return address, address

    async def _watch(self, session: CaptureSession) -> None:
        """监听抓包进程意外退出并恢复设备代理。"""
        await session.process.wait()
        async with session.lock:
            if session.active:
                await self._finalize(session)

    async def _finalize(self, session: CaptureSession) -> bool:
        """结束会话并在未被外部改写时恢复设备代理。"""
        if not session.active:
            return bool(session.proxy_restored)

        session.active = False

        current_proxy = self._normalize_proxy(await session.device.phone.http_proxy_get())

        restored = current_proxy == session.proxy_endpoint
        if restored:
            if session.previous_proxy:
                await session.device.phone.http_proxy_set(session.previous_proxy)
            else:
                await session.device.phone.http_proxy_clear()

        async with self.lock:
            self.active_by_serial.pop(session.device.serial, None)
        session.proxy_restored = restored
        await session.idle.session_final(session.session_key)
        return restored

    async def start(
        self,
        *,
        device: "Device",
        package_name: str,
        idle: "Idle",
        proxy_host: str | None = None,
    ) -> ToolOutput:
        """启动设备代理与 mitmdump 抓包进程。"""
        if not shutil.which("mitmdump"):
            raise RuntimeError("mitmdump not found in PATH")

        serial = device.serial
        async with self.lock:
            if serial in self.active_by_serial or serial in self.starting_serials:
                raise RuntimeError(f"Capture already active for device: {serial}")
            self.starting_serials.add(serial)

        listen_host, device_host = await self._proxy_address(device, proxy_host)

        port = await self._free_port(listen_host)

        capture_id = self._session_id()
        directory  = Path(tempfile.gettempdir()) / "helix-captures" / capture_id
        flow_path  = directory / "capture.mitm"
        index_path = directory / "flows.jsonl"
        har_path   = directory / "capture.har"
        conf_dir   = directory / "mitmproxy"
        addon_path = directory / "capture_addon.py"

        directory.mkdir(parents=True, exist_ok=False)
        conf_dir.mkdir(parents=True, exist_ok=True)
        addon_path.write_text(self._addon_source(index_path), encoding="utf-8")

        process: asyncio.subprocess.Process | None = None

        previous_proxy: str = ""
        proxy_changed: bool = False

        endpoint = f"{device_host}:{port}"

        try:
            command = [
                "mitmdump", "-q",
                "--set", f"confdir={conf_dir}",
                "--listen-host", listen_host,
                "--listen-port", str(port),
                "-w", str(flow_path),
                "-s", str(addon_path),
            ]
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.sleep(0.35)
            if process.returncode is not None:
                raise RuntimeError(f"mitmdump exited during startup (rc={process.returncode})")

            previous_proxy = self._normalize_proxy(await device.phone.http_proxy_get())
            await device.phone.http_proxy_set(endpoint)
            proxy_changed = True
            await device.phone.app_start(package_name)

            session = CaptureSession(
                capture_id=capture_id,
                device=device,
                package_name=package_name,
                proxy_endpoint=endpoint,
                previous_proxy=previous_proxy,
                directory=directory,
                flow_path=flow_path,
                index_path=index_path,
                har_path=har_path,
                process=process,
                idle=idle
            )
            await idle.session_begin(
                key=session.session_key,
                name="capture.capture_start",
                args={"capture_id": capture_id, "serial": serial, "package_name": package_name},
                replace=False,
                handle=session,
            )
            async with self.lock:
                self.sessions[capture_id] = session
                self.active_by_serial[serial] = capture_id
                self.starting_serials.discard(serial)
            asyncio.create_task(self._watch(session))

            certificate = conf_dir / "mitmproxy-ca-cert.cer"
            return ToolOutput(
                text="抓包已启动。HTTPS 解密需要应用信任 mitmproxy CA 证书。",
                data={
                    "capture_id"         : capture_id,
                    "serial"             : serial,
                    "package_name"       : package_name,
                    "proxy"              : {"host": device_host, "port": port, "endpoint": endpoint},
                    "certificate_path"   : str(certificate),
                    "https_interception" : "requires_ca_trust",
                    "flow_path"          : str(flow_path)
                }
            )
        except Exception:
            if proxy_changed:
                if previous_proxy:
                    with contextlib.suppress(Exception):
                        await device.phone.http_proxy_set(previous_proxy)
                else:
                    with contextlib.suppress(Exception):
                        await device.phone.http_proxy_clear()
            if process and process.returncode is None:
                process.terminate()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(process.wait(), timeout=2.0)
            async with self.lock:
                self.starting_serials.discard(serial)
            raise

    async def stop(self, capture_id: str) -> ToolOutput:
        """停止指定会话并尝试恢复设备代理。"""
        session = self._get(capture_id)
        async with session.lock:
            if not session.active:
                return ToolOutput(
                    text="抓包会话已结束。",
                    data={
                        "capture_id": capture_id,
                        "active": False,
                        "flow_count": len(self._read_flows(session.index_path))
                    }
                )
            session.stopping = True
            if session.process.returncode is None:
                session.process.terminate()

        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(session.process.wait(), timeout=4.0)
        if session.process.returncode is None:
            session.process.kill()
            await session.process.wait()

        async with session.lock:
            restored = await self._finalize(session)
        return ToolOutput(
            text="抓包已停止。" if restored else "抓包已停止；设备代理已被外部修改，未覆盖其设置。",
            data={
                "capture_id"     : capture_id,
                "active"         : False,
                "proxy_restored" : restored,
                "flow_count"     : len(self._read_flows(session.index_path)),
                "flow_path"      : str(session.flow_path),
            }
        )

    async def close_all(self) -> None:
        """在服务退出时收束所有活跃抓包会话。"""
        active = [session.capture_id for session in self.sessions.values() if session.active]
        await asyncio.gather(*(self.stop(capture_id) for capture_id in active), return_exceptions=True)

    async def snapshot(self) -> dict[str, typing.Any]:
        """返回抓包会话状态摘要。"""
        async with self.lock:
            sessions = list(self.sessions.values())

        return {
            "capture": {
                "count": len(sessions),
                "active_count": sum(1 for session in sessions if session.active),
                "sessions": [
                    {
                        "capture_id"   : session.capture_id,
                        "serial"       : session.device.serial,
                        "package_name" : session.package_name,
                        "active"       : session.active
                    }
                    for session in sessions[-10:]
                ],
            }
        }


if __name__ == "__main__":
    pass
