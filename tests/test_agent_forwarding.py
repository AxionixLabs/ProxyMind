# -*- coding: utf-8 -*-

import asyncio
import typing

from mind_app.modes.agent.forwarding import (
    AgentInbox,
    AgentExecutor,
    AutoForwardHandler,
    InboxForwardHandler
)
from mind_app.modes.agent.models import (
    AgentInboxItem,
    AgentForwardRequest,
    AgentLiveStatus,
    AgentSessionRuntime,
)
from mind_app.modes.agent.ws import handle_server_message


class DummyMind(object):
    """提供订阅请求测试所需的最小 Mind 接口。"""

    def __init__(self) -> None:
        self.stop_count = 0
        self.calling_calls: list[dict[str, typing.Any]] = []

    async def stop_anim(self) -> None:
        """记录停止动画调用。"""
        self.stop_count += 1

    async def await_cleanup(self, awaitable: typing.Awaitable[None]) -> None:
        """直接等待清理协程。"""
        await awaitable

    async def calling(self, **kwargs: typing.Any) -> None:
        """记录单次调用参数。"""
        self.calling_calls.append(kwargs)


class DummyClient(object):
    """记录订阅协议回写。"""

    def __init__(self) -> None:
        self.received_calls: list[dict[str, typing.Any]] = []
        self.started_calls: list[dict[str, typing.Any]] = []
        self.completed_calls: list[dict[str, typing.Any]] = []

    async def send_mind_received(self, connection: object, **kwargs: typing.Any) -> None:
        """记录收到确认的回写参数。"""
        _ = connection
        self.received_calls.append(kwargs)

    async def send_mind_started(self, connection: object, **kwargs: typing.Any) -> None:
        """记录开始执行的回写参数。"""
        _ = connection
        self.started_calls.append(kwargs)

    async def send_mind_completed(self, connection: object, **kwargs: typing.Any) -> None:
        """记录完成执行的回写参数。"""
        _ = connection
        self.completed_calls.append(kwargs)


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


class RecordingExecutor(AgentExecutor):
    """记录服务端任务启动请求。"""

    def __init__(self) -> None:
        self.spawned: list[dict[str, typing.Any]] = []

    def spawn(
        self,
        mind: DummyMind,
        client: DummyClient,
        connection: object,
        runtime: AgentSessionRuntime,
        request: AgentForwardRequest,
        live_status: AgentLiveStatus
    ) -> None:
        """记录执行器收到的启动参数。"""
        self.spawned.append({
            "mind": mind,
            "client": client,
            "connection": connection,
            "runtime": runtime,
            "request": request,
            "live_status": live_status,
        })


class AwaitableRecordingExecutor(AgentExecutor):
    """记录同步执行请求。"""

    def __init__(self) -> None:
        self.executed: list[dict[str, typing.Any]] = []

    async def execute(
        self,
        mind: DummyMind,
        client: DummyClient,
        connection: object,
        runtime: AgentSessionRuntime,
        request: AgentForwardRequest,
        live_status: AgentLiveStatus | None = None
    ) -> None:
        """记录执行器收到的执行参数。"""
        self.executed.append({
            "mind": mind,
            "client": client,
            "connection": connection,
            "runtime": runtime,
            "request": request,
            "live_status": live_status,
        })


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


def test_auto_forward_handler_preserves_received_and_spawn() -> None:
    """默认请求处理器发送收到确认并启动本地任务。"""
    mind = DummyMind()
    client = DummyClient()
    runtime = build_runtime()
    live_status = AgentLiveStatus()
    executor = RecordingExecutor()
    connection = object()

    request = AgentForwardRequest(
        message_id="msg_test",
        call_id="call_test",
        cid="cid_test",
        sid="sid_test",
        payload={"call_id": "call_test", "mode": "chat", "message": "你好"}
    )

    asyncio.run(
        AutoForwardHandler(executor).handle(
            mind,
            client,
            connection,
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
    assert executor.spawned == [{
        "mind": mind,
        "client": client,
        "connection": connection,
        "runtime": runtime,
        "request": request,
        "live_status": live_status,
    }]


def test_inbox_forward_handler_queues_request_without_execution() -> None:
    """收件箱请求处理器发送收到确认并保存待处理请求。"""
    mind = DummyMind()
    client = DummyClient()
    runtime = build_runtime()
    live_status = AgentLiveStatus()
    inbox = AgentInbox()
    connection = object()
    request = AgentForwardRequest(
        message_id="msg_test",
        call_id="call_test",
        cid="cid_test",
        sid="sid_test",
        payload={"call_id": "call_test", "mode": "chat", "message": "你好"}
    )

    asyncio.run(
        InboxForwardHandler(inbox).handle(
            mind,
            client,
            connection,
            runtime,
            request,
            live_status
        )
    )

    assert client.received_calls == [{
        "session_id": "sess_test",
        "cid": "cid_test",
        "sid": "sid_test",
        "call_id": "call_test",
        "acked_message_id": "msg_test",
    }]
    assert runtime.forwarded_message_ids == {"msg_test"}
    assert inbox.pending_count() == 1
    assert inbox.pending_items()[0] == AgentInboxItem(request=request)
    assert live_status.snapshot() == ("Task Received", "Queued call_test")


def test_inbox_forward_handler_skips_duplicate_message() -> None:
    """收件箱请求处理器忽略已经处理过的消息标识。"""
    mind = DummyMind()
    client = DummyClient()
    runtime = build_runtime()
    runtime.forwarded_message_ids = {"msg_test"}
    live_status = AgentLiveStatus()
    inbox = AgentInbox()
    request = AgentForwardRequest(
        message_id="msg_test",
        call_id="call_test",
        cid="cid_test",
        sid="sid_test",
        payload={"call_id": "call_test", "mode": "chat", "message": "你好"}
    )

    asyncio.run(
        InboxForwardHandler(inbox).handle(
            mind,
            client,
            object(),
            runtime,
            request,
            live_status
        )
    )

    assert inbox.pending_count() == 0
    assert len(client.received_calls) == 1


def test_agent_inbox_accept_next_executes_pending_request() -> None:
    """收件箱执行最早的待处理请求并调整状态。"""
    mind = DummyMind()
    client = DummyClient()
    runtime = build_runtime()
    live_status = AgentLiveStatus()
    executor = AwaitableRecordingExecutor()
    inbox = AgentInbox()
    connection = object()
    request = AgentForwardRequest(
        message_id="msg_test",
        call_id="call_test",
        cid="cid_test",
        sid="sid_test",
        payload={"call_id": "call_test", "mode": "chat", "message": "你好"}
    )
    item = inbox.add(request)

    result = asyncio.run(
        inbox.accept_next(
            executor=executor,
            mind=mind,
            client=client,
            connection=connection,
            runtime=runtime,
            live_status=live_status
        )
    )

    assert result is item
    assert item.status == "completed"
    assert item.error is None
    assert inbox.pending_count() == 0
    assert executor.executed == [{
        "mind": mind,
        "client": client,
        "connection": connection,
        "runtime": runtime,
        "request": request,
        "live_status": live_status,
    }]


def test_agent_inbox_decline_marks_pending_request() -> None:
    """收件箱可以标记待处理请求为已拒绝。"""
    inbox = AgentInbox()
    request = AgentForwardRequest(
        message_id="msg_test",
        call_id="call_test",
        cid="cid_test",
        sid="sid_test",
        payload={"call_id": "call_test", "mode": "chat", "message": "你好"}
    )
    item = inbox.add(request)

    result = inbox.decline("msg_test", reason="manual")

    assert result is item
    assert item.status == "declined"
    assert item.error == "manual"
    assert inbox.pending_count() == 0


def test_agent_executor_runs_message_and_reports_result() -> None:
    """服务端任务执行器回写执行状态并调用本地入口。"""
    mind = DummyMind()
    client = DummyClient()
    runtime = build_runtime()
    live_status = AgentLiveStatus()
    connection = object()
    request = AgentForwardRequest(
        message_id="msg_test",
        call_id="call_test",
        cid="cid_test",
        sid="sid_test",
        payload={
            "call_id": "call_test",
            "mode": "chat",
            "message": "你好",
            "metadata": {"caller_tag": "remote"}
        }
    )

    asyncio.run(
        AgentExecutor().execute(
            mind,
            client,
            connection,
            runtime,
            request,
            live_status
        )
    )

    assert client.started_calls == [{
        "session_id": "sess_test",
        "cid": "cid_test",
        "sid": "sid_test",
        "call_id": "call_test",
    }]
    assert client.completed_calls == [{
        "session_id": "sess_test",
        "cid": "cid_test",
        "sid": "sid_test",
        "call_id": "call_test",
    }]
    assert mind.calling_calls == [{
        "message": "你好",
        "mode": "chat",
        "metadata": {
            "caller_tag": "remote",
            "cid": "cid_test",
            "sid": "sid_test",
        },
    }]
    assert live_status.snapshot() == ("Server Task Received", "chat · call_test")


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
