"""Verify real durable Queue recovery through a local fault proxy."""

import argparse
import asyncio
import contextlib
import hashlib
import json
import socket
from collections.abc import AsyncIterator

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.responses import StreamingResponse
from starlette.routing import Route

from agent.adapters.protocol.client import MindChatProtocolClient
from agent.ports import ModelCapabilityError
from agent.ports import ProtocolCommandError
from agent.ports import TransportRecoveryPhase
from agent.protocol import TurnObservationRequest
from protocol.schema.identifiers import new_cid
from protocol.schema.identifiers import new_request_id
from protocol.schema.identifiers import new_sid
from protocol.schema.identifiers import new_submission_id
from protocol.schema.identifiers import short_uid
from protocol.transport.endpoints import service_endpoints
from tests.live_durable_turn_runtime import LiveVerificationConfig
from tests.live_durable_turn_runtime import TurnObservation
from tests.live_durable_turn_runtime import _consume_stream
from tests.live_durable_turn_runtime import _load_config
from tests.live_durable_turn_runtime import _print_observation
from tests.live_durable_turn_runtime import _request


_HOP_BY_HOP_HEADERS = frozenset({
    "connection",
    "content-encoding",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
})


class QueueFaultProxy:
    """Forward real traffic while losing two receipts and one attach stream."""

    def __init__(self, upstream: str, *, delayed_response_sec: float) -> None:
        self.upstream = upstream.rstrip("/")
        self.delayed_response_sec = delayed_response_sec
        self.request_counts: dict[str, int] = {}
        self.request_hashes: dict[str, list[str]] = {}
        self.upstream_statuses: dict[str, list[int]] = {}
        self.attach_after_sequences: list[int] = []
        self.stream_drops = 0
        self._client = httpx.AsyncClient(timeout=None, trust_env=False)
        self._listener: socket.socket | None = None
        self._server: uvicorn.Server | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._base_url = ""
        self._app = Starlette(routes=[
            Route("/{path:path}", self._forward, methods=["GET", "POST"]),
        ])

    @property
    def base_url(self) -> str:
        if not self._base_url:
            raise RuntimeError("fault proxy has not started")
        return self._base_url

    async def start(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(socket.SOMAXCONN)
        listener.setblocking(False)
        address = listener.getsockname()
        if not isinstance(address, tuple) or len(address) < 2:
            listener.close()
            raise RuntimeError("fault proxy did not receive a TCP address")
        port = address[1]
        if isinstance(port, bool) or not isinstance(port, int):
            listener.close()
            raise RuntimeError("fault proxy did not receive a TCP port")

        server = uvicorn.Server(uvicorn.Config(
            self._app,
            lifespan="off",
            log_level="critical",
            timeout_graceful_shutdown=2,
        ))
        self._listener = listener
        self._server = server
        self._base_url = f"http://127.0.0.1:{port}"
        task = asyncio.create_task(
            server.serve(sockets=[listener]),
            name="live Queue fault proxy",
        )
        self._server_task = task
        for _ in range(200):
            if server.started:
                return
            if task.done():
                await task
                raise RuntimeError("fault proxy stopped during startup")
            await asyncio.sleep(0.01)
        raise TimeoutError("fault proxy did not start")

    async def close(self) -> None:
        server = self._server
        task = self._server_task
        if server is not None:
            server.should_exit = True
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except TimeoutError:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await self._client.aclose()
        listener = self._listener
        if listener is not None:
            with contextlib.suppress(OSError):
                listener.close()

    async def _forward(self, request: Request) -> Response:
        path = request.url.path
        attempt = self.request_counts.get(path, 0) + 1
        self.request_counts[path] = attempt
        headers = {
            name: value
            for name, value in request.headers.items()
            if name.casefold() not in _HOP_BY_HOP_HEADERS
        }
        body = await request.body()
        if path in {"/queue", "/queue/start"}:
            digest = hashlib.sha256(body).hexdigest()
            self.request_hashes.setdefault(path, []).append(digest)
        if path == "/mind-attach":
            self._record_attach_cursor(body)

        upstream_url = f"{self.upstream}{path}"
        if request.url.query:
            upstream_url = f"{upstream_url}?{request.url.query}"
        if path in {"/mind-chat", "/mind-attach"}:
            return await self._forward_stream(
                path=path,
                url=upstream_url,
                headers=headers,
                body=body,
            )

        response = await self._client.request(
            request.method,
            upstream_url,
            headers=headers,
            content=body,
        )
        self.upstream_statuses.setdefault(path, []).append(response.status_code)
        if path in {"/queue", "/queue/start"} and attempt == 1:
            await asyncio.sleep(self.delayed_response_sec)
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=_response_headers(response.headers),
        )

    def _record_attach_cursor(self, body: bytes) -> None:
        try:
            payload = json.loads(body)
        except (TypeError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        after_seq = payload.get("after_seq")
        if isinstance(after_seq, int) and not isinstance(after_seq, bool):
            self.attach_after_sequences.append(after_seq)

    async def _forward_stream(
        self,
        *,
        path: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> StreamingResponse:
        upstream_request = self._client.build_request(
            "POST",
            url,
            headers=headers,
            content=body,
        )
        response = await self._client.send(upstream_request, stream=True)
        self.upstream_statuses.setdefault(path, []).append(response.status_code)

        async def chunks() -> AsyncIterator[bytes]:
            buffered = b""
            try:
                async for chunk in response.aiter_bytes():
                    buffered += chunk
                    while True:
                        frame, buffered = _next_sse_frame(buffered)
                        if frame is None:
                            break
                        drop_after_frame = (
                            path == "/mind-attach"
                            and self.stream_drops == 0
                            and b"event_seq" in frame
                            and b'"type":"ping"' not in frame
                        )
                        yield frame
                        if drop_after_frame:
                            self.stream_drops += 1
                            return
                if buffered:
                    yield buffered
            finally:
                await response.aclose()

        return StreamingResponse(
            chunks(),
            status_code=response.status_code,
            headers=_response_headers(response.headers),
        )


def _next_sse_frame(buffered: bytes) -> tuple[bytes | None, bytes]:
    candidates = tuple(
        (index, delimiter)
        for delimiter in (b"\r\n\r\n", b"\n\n")
        for index in (buffered.find(delimiter),)
        if index >= 0
    )
    if not candidates:
        return None, buffered
    index, delimiter = min(candidates, key=lambda value: value[0])
    end = index + len(delimiter)
    return buffered[:end], buffered[end:]


def _response_headers(headers: httpx.Headers) -> dict[str, str]:
    return {
        name: value
        for name, value in headers.items()
        if name.casefold() not in _HOP_BY_HOP_HEADERS
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify real Queue response-loss and SSE reconnect behavior.",
    )
    parser.add_argument(
        "--profile",
        help="Use ~/.mind/<profile>.config.toml through ConfigSession.",
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--terminal-deadline", type=float, default=45.0)
    parser.add_argument("--interrupt-ack-deadline", type=float, default=3.0)
    parser.add_argument("--delayed-response-sec", type=float, default=10.75)
    return parser


async def _verify(config: LiveVerificationConfig, delay: float) -> None:
    cid = new_cid("cid")
    sid = new_sid(cid, "sid")
    client = MindChatProtocolClient()
    proxy = QueueFaultProxy(config.domain, delayed_response_sec=delay)
    await proxy.start()
    service_endpoints.configure(proxy.base_url)

    try:
        queued_request = _request(
            config,
            cid=cid,
            sid=sid,
            turn_id=f"turn_{short_uid(20)}",
            message=(
                "Reply with exactly 80 numbered lines, each line containing "
                "QUEUE-FAULT-OK and its number."
            ),
        )
        submission_id = new_submission_id("submission_fault")
        client_message_id = f"message_{short_uid(20)}"
        add_request_id = new_request_id("queue_add_fault")
        add_receipt = await asyncio.wait_for(
            client.add_queue_submission(
                queued_request,
                submission_id=submission_id,
                client_message_id=client_message_id,
                request_id=add_request_id,
            ),
            timeout=delay + 20.0,
        )
        add_attempts = proxy.request_counts.get("/queue", 0)
        add_hashes = proxy.request_hashes.get("/queue", [])
        if add_attempts < 2 or len(set(add_hashes)) != 1:
            raise AssertionError(
                "queue.add did not retry one frozen payload after response loss"
            )
        duplicate_add = await client.add_queue_submission(
            queued_request,
            submission_id=submission_id,
            client_message_id=client_message_id,
            request_id=add_request_id,
        )
        if duplicate_add != add_receipt:
            raise AssertionError("queue.add retry returned a different receipt")
        snapshot = await client.list_queue_submissions(cid=cid, sid=sid)
        if len(snapshot.items) != 1:
            raise AssertionError("queue.add response loss created duplicate items")
        item = snapshot.items[0]
        if (
            item.submission_id != submission_id
            or item.client_message_id != client_message_id
            or item.turn_id != queued_request.turn_id
        ):
            raise AssertionError("queue.add recovery changed the input identity")
        print(
            f"PASS queue.add response lost: attempts={add_attempts} "
            f"items={len(snapshot.items)} identity=stable"
        )

        start_request_id = new_request_id("queue_start_fault")
        start_receipt = await asyncio.wait_for(
            client.start_queue_submission(
                cid=cid,
                sid=sid,
                submission_id=submission_id,
                request_id=start_request_id,
            ),
            timeout=delay + 20.0,
        )
        start_attempts = proxy.request_counts.get("/queue/start", 0)
        start_hashes = proxy.request_hashes.get("/queue/start", [])
        if start_attempts < 2 or len(set(start_hashes)) != 1:
            raise AssertionError(
                "queue.start did not retry one frozen payload after response loss"
            )
        duplicate_start = await client.start_queue_submission(
            cid=cid,
            sid=sid,
            submission_id=submission_id,
            request_id=start_request_id,
        )
        if duplicate_start != start_receipt:
            raise AssertionError("queue.start retry returned a different receipt")
        if start_receipt.turn_id != queued_request.turn_id:
            raise AssertionError("queue.start changed the preallocated Turn identity")
        started_snapshot = await client.list_queue_submissions(cid=cid, sid=sid)
        if started_snapshot.items:
            raise AssertionError("started Queue item remained in the Queue snapshot")
        print(
            f"PASS queue.start response lost: attempts={start_attempts} "
            "turn_id=stable queue=empty"
        )

        recovery_phases: list[tuple[TransportRecoveryPhase, int]] = []

        async def record_recovery(
            phase: TransportRecoveryPhase,
            event_seq: int,
        ) -> None:
            recovery_phases.append((phase, event_seq))

        observed_stream = client.observe(
            TurnObservationRequest(
                cid=cid,
                sid=sid,
                turn_id=queued_request.turn_id,
                timeout=config.timeout,
            ),
            on_recovery_status=record_recovery,
        )
        queued_turn = await asyncio.wait_for(
            _consume_stream(
                queued_request.turn_id,
                observed_stream,
            ),
            timeout=config.timeout + config.terminal_deadline,
        )
        if queued_turn.terminal_status != "completed":
            raise AssertionError(
                f"queued Turn ended as {queued_turn.terminal_status}: "
                f"{queued_turn.terminal_error or 'no error'}"
            )
        if proxy.stream_drops != 1:
            raise AssertionError("the first attach stream was not disconnected once")
        attach_count = proxy.request_counts.get("/mind-attach", 0)
        if attach_count < 2:
            raise AssertionError("SSE disconnect did not reopen /mind-attach")
        cursors = proxy.attach_after_sequences
        if len(cursors) < 2 or cursors[1] <= cursors[0]:
            raise AssertionError("reopened attach did not advance its Session cursor")
        phases = tuple(phase for phase, _event_seq in recovery_phases)
        if "reconnecting" not in phases or "replaying" not in phases:
            raise AssertionError("SSE disconnect did not enter recovery replay")
        _print_observation("Queue attach reconnect", queued_turn)
        print(
            f"PASS Queue SSE reconnect: drops=1 attach={attach_count} "
            f"after_seq={cursors[0]}->{cursors[1]}"
        )

        next_request = _request(
            config,
            cid=cid,
            sid=sid,
            turn_id=f"turn_{short_uid(20)}",
            message="Reply with only QUEUE-NEXT-OK.",
        )
        next_turn = await asyncio.wait_for(
            _consume_stream(next_request.turn_id, client.stream(next_request)),
            timeout=config.timeout + config.terminal_deadline,
        )
        if next_turn.terminal_status != "completed":
            raise AssertionError(
                f"next Turn ended as {next_turn.terminal_status}: "
                f"{next_turn.terminal_error or 'no error'}"
            )
        if next_turn.event_sequences[0] <= queued_turn.terminal_sequence:
            raise AssertionError("next Turn did not advance the Session cursor")
        if proxy.request_counts.get("/mind-chat", 0) != 1:
            raise AssertionError("Queue Turn recovery submitted an extra /mind-chat")
        _print_observation("post-Queue next Turn", next_turn)
        print(
            "PASS Queue fault matrix: exactly-one input, attach-only recovery, "
            "and next-Turn cursor continuity"
        )
    finally:
        service_endpoints.configure(config.domain)
        await proxy.close()


async def _main() -> int:
    args = _parser().parse_args()
    if args.delayed_response_sec <= 10.0:
        print("FAIL delayed response must exceed the production 10s Queue timeout")
        return 1
    try:
        config = await _load_config(args)
        print(f"CONFIG service.domain={config.domain} model_credentials=ready")
        await _verify(config, float(args.delayed_response_sec))
    except ProtocolCommandError as error:
        print(
            f"FAIL {type(error).__name__}: {error}; code={error.code}; "
            f"retryable={error.retryable}; details={error.details}"
        )
        return 1
    except (
        AssertionError,
        ModelCapabilityError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as error:
        print(f"FAIL {type(error).__name__}: {error}")
        return 1
    print("PASS durable Queue live fault verification completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
