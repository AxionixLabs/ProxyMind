# -*- coding: utf-8 -*-

import asyncio
import typing

from mind_app.modes.agent import loop as agent_loop_module
from mind_app.modes.agent.loop import (
    AgentConnection,
    AgentSupervisor
)
from mind_app.modes.agent.models import (
    AgentConfig,
    AgentLiveStatus,
    AgentSessionRuntime,
)


class DummyMind(object):
    """提供连接层测试所需的最小 Mind 接口。"""

    def __init__(self) -> None:
        self.task_event = asyncio.Event()
        self.stop_count = 0

    async def stop_anim(self) -> None:
        """记录停止动画调用。"""
        self.stop_count += 1

    async def await_cleanup(self, awaitable: typing.Awaitable[None]) -> None:
        """直接等待清理协程。"""
        await awaitable


class DummyClient(object):
    """记录订阅会话请求。"""

    def __init__(self, resume_payload: dict[str, typing.Any] | None = None) -> None:
        self.resume_payload = resume_payload or {}
        self.resume_calls: list[dict[str, typing.Any]] = []

    @staticmethod
    def unwrap_data(payload: dict[str, typing.Any]) -> dict[str, typing.Any]:
        """读取响应中的数据对象。"""
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    async def resume_session(self, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """记录恢复会话请求并返回配置响应。"""
        self.resume_calls.append(kwargs)
        return self.resume_payload


def build_config() -> AgentConfig:
    """构造稳定的订阅配置。"""
    return AgentConfig(
        base_url="https://agent.example",
        device_id="dev_config",
        agent_id="mind",
        client_version="1.1.2",
        platform="windows",
        arch="amd64",
    )


def build_runtime() -> AgentSessionRuntime:
    """构造稳定的订阅运行态。"""
    return AgentSessionRuntime(
        session_id="sess_old",
        ws_token="ws_old",
        resume_token="resume_old",
        credential="cred_old",
        mind_call_example={"old": True},
        ws_url="wss://old.example/agents/ws",
        device_id="dev_old",
        client_version="1.1.1",
        last_acked_seq=7,
        ready_received=True,
        pre_ready_connect_failures=2,
        forwarded_message_ids={"msg_seen"},
        pending_tasks=set(),
    )


def open_payload() -> dict[str, typing.Any]:
    """构造打开或恢复会话响应。"""
    return {
        "data": {
            "session_id": "sess_current",
            "ws_token": "ws_current",
            "ws_url": "wss://current.example/agents/ws",
            "resume_token": "resume_current",
            "credential": {"token": "cred_current"},
            "examples": {"mind_call": {"current": True}},
        }
    }


class RecordingConnection(object):
    """记录生命周期控制发起的连接调用。"""

    def __init__(self, runtime: AgentSessionRuntime) -> None:
        self.config = build_config()
        self.runtime = runtime
        self.open_calls: list[AgentSessionRuntime | None] = []
        self.connect_calls: list[AgentSessionRuntime] = []

    async def open_session_runtime(
        self,
        *,
        previous: AgentSessionRuntime | None = None
    ) -> AgentSessionRuntime:
        """记录打开订阅会话请求。"""
        self.open_calls.append(previous)
        return self.runtime

    async def connect_once(self, runtime: AgentSessionRuntime) -> None:
        """记录单次连接请求。"""
        self.connect_calls.append(runtime)


def test_agent_connection_open_session_runtime_preserves_runtime_state(monkeypatch) -> None:
    """打开订阅会话时复用本地去重和任务状态。"""
    client = DummyClient()
    config = build_config()
    live_status = AgentLiveStatus()
    previous = build_runtime()
    calls: list[tuple[DummyClient, AgentConfig]] = []

    async def fake_open_runtime(
        actual_client: DummyClient,
        actual_config: AgentConfig
    ) -> tuple[dict[str, typing.Any], str]:
        """返回固定打开会话响应。"""
        calls.append((actual_client, actual_config))
        return open_payload(), "dev_current"

    monkeypatch.setattr(agent_loop_module, "open_runtime", fake_open_runtime)

    result = asyncio.run(
        AgentConnection(
            DummyMind(),
            client,
            config,
            live_status
        ).open_session_runtime(previous=previous)
    )

    assert calls == [(client, config)]
    assert result.session_id == "sess_current"
    assert result.ws_token == "ws_current"
    assert result.resume_token == "resume_current"
    assert result.credential == "cred_current"
    assert result.mind_call_example == {"current": True}
    assert result.ws_url == "wss://current.example/agents/ws"
    assert result.device_id == "dev_current"
    assert result.client_version == "1.1.2"
    assert result.forwarded_message_ids is previous.forwarded_message_ids
    assert result.pending_tasks is previous.pending_tasks


def test_agent_connection_resume_or_open_uses_resume_response() -> None:
    """恢复订阅会话时保留本地恢复水位和任务状态。"""
    payload = open_payload()
    payload["data"]["resumable"] = True
    client = DummyClient(payload)
    config = build_config()
    live_status = AgentLiveStatus()
    runtime = build_runtime()

    result = asyncio.run(
        AgentConnection(
            DummyMind(),
            client,
            config,
            live_status
        ).resume_or_open(runtime)
    )

    assert client.resume_calls == [{
        "session_id": "sess_old",
        "resume_token": "resume_old",
        "last_acked_seq": 7,
        "device_id": "dev_old",
        "agent_id": "mind",
    }]
    assert result.session_id == "sess_current"
    assert result.device_id == "dev_old"
    assert result.client_version == "1.1.1"
    assert result.last_acked_seq == 7
    assert result.ready_received is True
    assert result.pre_ready_connect_failures == 2
    assert result.forwarded_message_ids is runtime.forwarded_message_ids
    assert result.pending_tasks is runtime.pending_tasks
    assert live_status.snapshot() == (
        "Resume Succeeded",
        "Refreshing handshake and reusing session",
    )


def test_agent_connection_connect_once_delegates_handler(monkeypatch) -> None:
    """单次 WS 连接把请求处理器传给消息处理层。"""
    client = DummyClient()
    config = build_config()
    live_status = AgentLiveStatus()
    runtime = build_runtime()
    mind = DummyMind()
    handler = object()
    calls: list[tuple[typing.Any, ...]] = []

    async def fake_connect_once(*args: typing.Any) -> None:
        """记录单次连接调用参数。"""
        calls.append(args)

    monkeypatch.setattr(agent_loop_module, "connect_once", fake_connect_once)

    asyncio.run(
        AgentConnection(
            mind,
            client,
            config,
            live_status,
            handler
        ).connect_once(runtime)
    )

    assert calls == [(
        mind,
        client,
        runtime,
        live_status,
        handler,
    )]


def test_agent_supervisor_run_initial_connection_lifecycle(monkeypatch) -> None:
    """生命周期控制器执行订阅启动、连接和清理流程。"""
    mind = DummyMind()
    runtime = build_runtime()
    connection = RecordingConnection(runtime)
    live_status = AgentLiveStatus()
    events: list[typing.Any] = []

    async def fake_start_connect_animation(
        actual_mind: DummyMind,
        actual_status: AgentLiveStatus
    ) -> None:
        """记录连接动画启动。"""
        events.append(("connect_anim", actual_mind, actual_status))

    async def fake_start_status_animation(
        actual_mind: DummyMind,
        actual_status: AgentLiveStatus
    ) -> None:
        """记录等待动画启动。"""
        events.append(("status_anim", actual_mind, actual_status))

    async def fake_publish_external_access(actual_runtime: AgentSessionRuntime) -> None:
        """记录外部调用示例发布。"""
        events.append(("publish", actual_runtime))

    def fake_show_external_access_link() -> None:
        """记录外部调用示例展示。"""
        events.append(("show",))

    async def fake_cancel_runtime_tasks(actual_runtime: AgentSessionRuntime) -> None:
        """记录任务清理。"""
        events.append(("cancel", actual_runtime))

    monkeypatch.setattr(agent_loop_module, "start_connect_animation", fake_start_connect_animation)
    monkeypatch.setattr(agent_loop_module, "start_status_animation", fake_start_status_animation)
    monkeypatch.setattr(agent_loop_module, "publish_external_access", fake_publish_external_access)
    monkeypatch.setattr(agent_loop_module, "show_external_access_link", fake_show_external_access_link)
    monkeypatch.setattr(agent_loop_module, "cancel_runtime_tasks", fake_cancel_runtime_tasks)

    asyncio.run(
        AgentSupervisor(
            mind,
            typing.cast(AgentConnection, connection),
            live_status
        ).run()
    )

    assert connection.open_calls == [None]
    assert connection.connect_calls == [runtime]
    assert mind.stop_count == 2
    assert events == [
        ("connect_anim", mind, live_status),
        ("publish", runtime),
        ("show",),
        ("status_anim", mind, live_status),
        ("cancel", runtime),
    ]
    assert live_status.snapshot() == (
        "Exiting Subscription",
        "Cleaning tasks and stopping animation",
    )
