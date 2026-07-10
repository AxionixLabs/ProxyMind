# -*- coding: utf-8 -*-

import asyncio
import typing

from mind_app.modes.agent.forwarding import AutoForwardHandler
from mind_app.modes.agent.models import (
    AgentForwardRequest,
    AgentLiveStatus,
    AgentSessionRuntime,
)
from mind_app.modes.agent.ws import handle_server_message


class DummyMind(object):
    """提供订阅请求测试所需的最小 Mind 接口。"""

    def __init__(self) -> None:
        self.stop_count = 0

    async def stop_anim(self) -> None:
        """记录停止动画调用。"""
        self.stop_count += 1

    async def await_cleanup(self, awaitable: typing.Awaitable[None]) -> None:
        """直接等待清理协程。"""
        await awaitable


class DummyClient(object):
    """记录订阅协议回写。"""

    def __init__(self) -> None:
        self.received_calls: list[dict[str, typing.Any]] = []

    async def send_mind_received(self, connection: object, **kwargs: typing.Any) -> None:
        """记录收到确认的回写参数。"""
        _ = connection
        self.received_calls.append(kwargs)


class RecordingForwardHandler(object):
    """记录消息处理分派出的服务端请求。"""

    def __init__(self) -> None:
        self.requests: list[AgentForwardRequest] = []

    async def handle(
        self,
        mind: DummyMind,
        client: DummyClient,
        connection: object,
        runtime: AgentSessionRuntime,
        request: AgentForwardRequest,
        live_status: AgentLiveStatus
    ) -> None:
        """保存收到的服务端请求。"""
        _ = mind, client, connection, runtime, live_status
        self.requests.append(request)


def build_runtime() -> AgentSessionRuntime:
    """构造稳定的订阅运行态。"""
    return AgentSessionRuntime(
        session_id="sess_test",
        ws_token="ws_test",
        resume_token="resume_test",
        credential=None,
        mind_call_example=None,
        ws_url=None,
        device_id="dev_test",
        client_version="1.1.2",
    )


def test_auto_forward_handler_preserves_received_and_spawn(monkeypatch) -> None:
    """默认请求处理器发送收到确认并启动本地任务。"""
    mind = DummyMind()
    client = DummyClient()
    runtime = build_runtime()
    live_status = AgentLiveStatus()
    spawned: list[dict[str, typing.Any]] = []

    def fake_spawn_forward_task(*args: typing.Any, **kwargs: typing.Any) -> None:
        """记录本地任务启动请求。"""
        _ = args
        spawned.append(kwargs)

    monkeypatch.setattr(
        "mind_app.modes.agent.forwarding.spawn_forward_task",
        fake_spawn_forward_task
    )

    request = AgentForwardRequest(
        message_id="msg_test",
        call_id="call_test",
        cid="cid_test",
        sid="sid_test",
        payload={"call_id": "call_test", "mode": "chat", "message": "你好"}
    )

    asyncio.run(
        AutoForwardHandler().handle(
            mind,
            client,
            object(),
            runtime,
            request,
            live_status
        )
    )

    assert mind.stop_count == 1
    assert client.received_calls == [{
        "session_id": "sess_test",
        "cid": "cid_test",
        "sid": "sid_test",
        "call_id": "call_test",
        "acked_message_id": "msg_test",
    }]
    assert runtime.forwarded_message_ids == {"msg_test"}
    assert spawned == [{
        "call_id": "call_test",
        "cid": "cid_test",
        "sid": "sid_test",
        "payload": {"call_id": "call_test", "mode": "chat", "message": "你好"},
        "live_status": live_status,
    }]


def test_handle_server_message_delegates_forward_request() -> None:
    """WS 消息处理解析服务端请求并委托给传入处理器。"""
    mind = DummyMind()
    client = DummyClient()
    runtime = build_runtime()
    live_status = AgentLiveStatus()
    handler = RecordingForwardHandler()

    seq = asyncio.run(
        handle_server_message(
            mind,
            client,
            object(),
            runtime,
            {
                "type": "mind.forward",
                "seq": 8,
                "session_id": "sess_test",
                "message_id": "msg_test",
                "cid": "cid_test",
                "sid": "sid_test",
                "payload": {
                    "call_id": "call_test",
                    "mode": "chat",
                    "message": "你好"
                },
            },
            live_status,
            handler
        )
    )

    assert seq == 8
    assert len(handler.requests) == 1
    assert handler.requests[0] == AgentForwardRequest(
        message_id="msg_test",
        call_id="call_test",
        cid="cid_test",
        sid="sid_test",
        payload={
            "call_id": "call_test",
            "mode": "chat",
            "message": "你好"
        }
    )
    assert client.received_calls == []
    assert mind.stop_count == 0
