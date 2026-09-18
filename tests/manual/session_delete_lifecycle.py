# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

"""使用真实本地 HTTP 服务和独立解释器验收阶段三删除生命周期。"""

import argparse
import asyncio
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agent.adapters.protocol.session_deletion import ProtocolSessionDeletionAdapter
from agent.ports.session_deletion import (
    RemoteDeletionRequest,
    RemoteDeletionTarget,
    SessionDeletionRemoteError,
)
from protocol.client.session_deletion import (
    SessionDeletionRequestError,
    delete_sessions,
)
from protocol.schema.session_deletion import (
    SessionDeletionRequest,
    SessionDeletionTarget,
)
from protocol.transport.endpoints import service_endpoints


class _State:
    def __init__(self) -> None:
        self.mode = "success"
        self.post_count = 0
        self.get_count = 0
        self.post_started = threading.Event()
        self.last_payload: dict[str, object] = {}
        self._lock = threading.Lock()

    def set_mode(self, mode: str) -> None:
        with self._lock:
            self.mode = mode

    def snapshot(self) -> tuple[str, int, int]:
        with self._lock:
            return self.mode, self.post_count, self.get_count


class _Handler(BaseHTTPRequestHandler):
    state: _State

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        with self.state._lock:
            self.state.post_count += 1
            mode = self.state.mode
            if isinstance(payload, dict):
                self.state.last_payload = payload
        self.state.post_started.set()
        if mode == "delay":
            time.sleep(0.15)
        if mode == "lost":
            self.connection.close()
            return
        if mode == "reject":
            self._write(409, {"details": {"code": "session_busy", "retryable": False}})
            return
        if mode == "unknown":
            self._write(200, {"invalid": True})
            return
        self._write(200, _receipt(self.state.last_payload))

    def do_GET(self) -> None:  # noqa: N802
        with self.state._lock:
            self.state.get_count += 1
            mode = self.state.mode
        if mode == "reject":
            self._write(404, {"details": {"code": "request_not_found", "retryable": False}})
            return
        query = parse_qs(urlparse(self.path).query)
        request_id = query.get("request_id", [""])[0]
        payload = (
            {
                "request_id": "delete_http_123456",
                "cid": "cid_delete_12345678",
                "sid": "sid_delete_0_123456",
            }
            if request_id == "delete_http_123456"
            else self.state.last_payload
        )
        self._write(200, _receipt(payload))

    def _write(self, status: int, body: dict[str, object]) -> None:
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except OSError:
            return None

    def log_message(self, _format: str, *_args: object) -> None:
        return None


def _receipt(payload: dict[str, object] | None = None) -> dict[str, object]:
    value = payload or {}
    cid = value.get("cid") if isinstance(value.get("cid"), str) else "cid_delete_12345678"
    sid = value.get("sid") if isinstance(value.get("sid"), str) else "sid_delete_0_123456"
    request_id = value.get("request_id") if isinstance(value.get("request_id"), str) else "delete_http_123456"
    descendants = value.get("descendants")
    targets = [{"cid": cid, "sid": sid}]
    if isinstance(descendants, list):
        targets.extend(item for item in descendants if isinstance(item, dict))
    return {
        "request_id": request_id,
        "cid": cid,
        "sid": sid,
        "status": "deleted",
        "targets": targets,
    }


class _MemoryStore:
    def __init__(self) -> None:
        self.prepared = []
        self.deleted = []
        self.pending_plans = []

    def prepare(self, plan) -> None:
        self.prepared.append(plan)
        self.pending_plans = [plan]

    def delete(self, plan) -> None:
        self.deleted.append(plan)
        self.pending_plans = []

    def pending(self):
        return tuple(self.pending_plans)


def _request() -> RemoteDeletionRequest:
    return RemoteDeletionRequest(
        request_id="delete_http_123456",
        root=RemoteDeletionTarget("cid_delete_12345678", "sid_delete_0_123456"),
    )


def _formal_request() -> SessionDeletionRequest:
    return SessionDeletionRequest(
        request_id="delete_http_123456",
        root=SessionDeletionTarget("cid_delete_12345678", "sid_delete_0_123456"),
    )


def _configure(url: str) -> None:
    service_endpoints.endpoint = lambda _path: url
    import protocol.client.session_deletion as client

    client.build_service_headers = lambda: {}


def _run_child(url: str) -> dict[str, object]:
    code = (
        "import asyncio, json; "
        "from agent.adapters.protocol.session_deletion import ProtocolSessionDeletionAdapter; "
        "from agent.ports.session_deletion import RemoteDeletionRequest, RemoteDeletionTarget; "
        "from protocol.transport.endpoints import service_endpoints; "
        "import protocol.client.session_deletion as client; "
        f"service_endpoints.endpoint=lambda _path: {url!r}; "
        "client.build_service_headers=lambda: {}; "
        "request=RemoteDeletionRequest('delete_http_123456', RemoteDeletionTarget('cid_delete_12345678','sid_delete_0_123456')); "
        "receipt=asyncio.run(ProtocolSessionDeletionAdapter().recover(request)); "
        "print(json.dumps({'request_id': receipt.request_id, 'status': 'deleted'}))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout.strip())


async def _accept(url: str, state: _State) -> dict[str, object]:
    _configure(url)
    adapter = ProtocolSessionDeletionAdapter()
    request = _request()
    evidence: dict[str, object] = {"scenarios": {}}

    state.set_mode("success")
    receipt = await adapter.delete(request)
    assert receipt.request_id == request.request_id
    evidence["scenarios"]["success"] = {"status": "deleted"}

    from agent.harness.sessions.conversation import ConversationState
    from tests.composition.test_controller_runtime_cleanup import _root_session

    lifecycle_session, lifecycle_resources = _root_session(ConversationState(
        cid="cid_delete_12345678",
        sid="sid_delete_0_123456",
        fork_source_available=True,
    ))
    lifecycle_resources.shutdown_root.return_value = ()
    lifecycle_session._session_deletion_remote = adapter
    lifecycle_session._session_deletion_store = _MemoryStore()
    lifecycle_result = await lifecycle_session.delete_current("delete_root_123456")
    assert lifecycle_result.status == "deleted"
    evidence["source_lifecycle"] = {
        "status": lifecycle_result.status,
        "hook_reason": lifecycle_resources.lifecycle.end.call_args.kwargs["reason"],
    }

    state.set_mode("reject")
    try:
        await adapter.delete(request)
    except SessionDeletionRemoteError as error:
        assert error.outcome == "rejected" and error.code == "session_busy"
        evidence["scenarios"]["reject"] = {"status": error.outcome, "code": error.code}
    else:
        raise AssertionError("server rejection was reported as success")

    state.set_mode("unknown")
    try:
        await adapter.delete(request)
    except SessionDeletionRemoteError as error:
        assert error.outcome == "unknown"
        evidence["scenarios"]["lost_response"] = {"status": error.outcome, "code": error.code}
    else:
        raise AssertionError("invalid response was reported as success")

    state.set_mode("delay")
    try:
        await delete_sessions(_formal_request(), timeout=0.01)
    except SessionDeletionRequestError as error:
        assert error.outcome == "unknown"
        evidence["scenarios"]["timeout"] = {"status": error.outcome, "code": error.code}
    else:
        raise AssertionError("timeout was reported as success")

    state.set_mode("delay")
    state.post_started.clear()
    task = asyncio.create_task(adapter.delete(request))
    await asyncio.to_thread(state.post_started.wait, 2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        evidence["scenarios"]["cancel"] = {"status": "cancelled"}
    else:
        raise AssertionError("cancelled request completed unexpectedly")

    state.set_mode("success")
    recovered = await adapter.recover(request)
    assert recovered.request_id == request.request_id
    evidence["scenarios"]["recover"] = {"status": "deleted"}
    evidence["child_process"] = _run_child(url)
    mode, post_count, get_count = state.snapshot()
    evidence["request_counts"] = {
        "post": post_count,
        "get": get_count,
        "final_mode": mode,
    }
    assert post_count == 6
    assert get_count == 2
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    state = _State()
    handler = type("LifecycleHandler", (_Handler,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/session/delete"
    try:
        evidence = asyncio.run(_accept(url, state))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    encoded = json.dumps(evidence, ensure_ascii=False, indent=2)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
