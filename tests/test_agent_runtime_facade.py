# -*- coding: utf-8 -*-

import asyncio
import typing

from mind_app.modes.agent.forwarding import (
    AgentExecutor,
    AutoForwardHandler
)
from mind_app.modes.agent.models import (
    AgentConfig,
    AgentForwardRequest,
    AgentLiveStatus,
    AgentSessionRuntime,
)
from mind_app.modes.agent.runtime import (
    AgentRuntime,
    AgentWorkerRuntime
)
from mind_app.modes.agent import loop as agent_loop_module


class DummyMind(object):
    """提供订阅运行时测试所需的最小 Mind 接口。"""

    def __init__(self) -> None:
        self.task_event = asyncio.Event()


class DummyClient(object):
    """记录订阅协议回写。"""

    def __init__(self) -> None:
        self.received_calls: list[dict[str, typing.Any]] = []

    async def send_mind_received(self, connection: object, **kwargs: typing.Any) -> None:
        """记录收到确认回写。"""
        _ = connection
        self.received_calls.append(kwargs)


class WaitingSupervisor(object):
    """用于测试后台启动停止的生命周期对象。"""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def run(self) -> None:
        """等待取消信号。"""
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class FinishingSupervisor(object):
    """用于测试运行委托的生命周期对象。"""

    def __init__(self) -> None:
        self.run_count = 0

    async def run(self) -> None:
        """记录运行调用。"""
        self.run_count += 1


class RecordingWorkerRuntime(object):
    """记录 agent_loop 装配出的 worker。"""

    instances: list["RecordingWorkerRuntime"] = []

    def __init__(self, mind: DummyMind) -> None:
        self.mind = mind
        self.run_count = 0
        self.instances.append(self)

    async def run(self) -> None:
        """记录 worker 运行调用。"""
        self.run_count += 1


class RecordingExecutor(AgentExecutor):
    """记录运行时发起的执行请求。"""

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
        """记录执行参数。"""
        self.executed.append({
            "mind": mind,
            "client": client,
            "connection": connection,
            "runtime": runtime,
            "request": request,
            "live_status": live_status,
        })


def build_config() -> AgentConfig:
    """构造稳定的订阅配置。"""
    return AgentConfig(
        base_url="https://agent.example",
        device_id="dev_test",
        agent_id="mind",
        client_version="1.1.2",
        platform="windows",
        arch="amd64",
    )


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


def build_request() -> AgentForwardRequest:
    """构造稳定的服务端请求。"""
    return AgentForwardRequest(
        message_id="msg_test",
        call_id="call_test",
        cid="cid_test",
        sid="sid_test",
        payload={"call_id": "call_test", "mode": "chat", "message": "你好"}
    )


def test_agent_runtime_start_and_stop_background_task() -> None:
    """订阅运行时可以启动和停止后台任务。"""

    async def scenario() -> None:
        """执行启动停止场景。"""
        supervisor = WaitingSupervisor()
        runtime = AgentRuntime(
            DummyMind(),
            config=build_config(),
            client=typing.cast(typing.Any, DummyClient()),
            supervisor=typing.cast(typing.Any, supervisor)
        )

        task = runtime.start_background()
        await supervisor.started.wait()

        assert runtime.is_running() is True
        assert runtime.status_label() == "agent · online"
        assert runtime.start_background() is task

        await runtime.stop()

        assert runtime.is_running() is False
        assert supervisor.cancelled is True
        assert runtime.status_label() == "agent · off"

    asyncio.run(scenario())


def test_agent_runtime_status_label_uses_inbox_state() -> None:
    """订阅运行时状态标签优先展示收件箱状态。"""
    runtime = AgentRuntime(
        DummyMind(),
        config=build_config(),
        client=typing.cast(typing.Any, DummyClient()),
        supervisor=typing.cast(typing.Any, WaitingSupervisor())
    )
    item = runtime.inbox.add(build_request())

    assert runtime.status_label() == "agent · 1 pending"

    item.status = "running"

    assert runtime.status_label() == "agent · running"


def test_agent_runtime_run_next_executes_queued_request() -> None:
    """订阅运行时执行收件箱中的最早请求。"""

    async def scenario() -> None:
        """执行入队和手动运行场景。"""
        mind = DummyMind()
        client = DummyClient()
        executor = RecordingExecutor()
        runtime = AgentRuntime(
            mind,
            config=build_config(),
            client=typing.cast(typing.Any, client),
            executor=executor,
            supervisor=typing.cast(typing.Any, WaitingSupervisor())
        )
        session_runtime = build_runtime()
        live_status = AgentLiveStatus()
        connection = object()
        request = build_request()

        await runtime.handler.handle(
            mind,
            typing.cast(typing.Any, client),
            connection,
            session_runtime,
            request,
            live_status
        )

        result = await runtime.run_next()

        assert result is not None
        assert result.status == "completed"
        assert runtime.status_label() == "agent · off"
        assert client.received_calls == [{
            "session_id": "sess_test",
            "cid": "cid_test",
            "sid": "sid_test",
            "call_id": "call_test",
            "acked_message_id": "msg_test",
        }]
        assert executor.executed == [{
            "mind": mind,
            "client": client,
            "connection": connection,
            "runtime": session_runtime,
            "request": request,
            "live_status": live_status,
        }]

    asyncio.run(scenario())


def test_agent_runtime_decline_removes_pending_context() -> None:
    """订阅运行时可以拒绝待处理请求。"""
    runtime = AgentRuntime(
        DummyMind(),
        config=build_config(),
        client=typing.cast(typing.Any, DummyClient()),
        supervisor=typing.cast(typing.Any, WaitingSupervisor())
    )
    request = build_request()
    item = runtime.inbox.add(request)
    runtime.contexts[request.message_id] = typing.cast(typing.Any, object())

    result = runtime.decline("msg_test", reason="manual")

    assert result is item
    assert item.status == "declined"
    assert item.error == "manual"
    assert "msg_test" not in runtime.contexts


def test_agent_worker_runtime_uses_auto_forward_handler() -> None:
    """无人值守运行时使用自动执行请求处理器。"""
    executor = RecordingExecutor()
    runtime = AgentWorkerRuntime(
        DummyMind(),
        config=build_config(),
        client=typing.cast(typing.Any, DummyClient()),
        executor=executor,
        supervisor=typing.cast(typing.Any, WaitingSupervisor())
    )

    assert isinstance(runtime.handler, AutoForwardHandler)
    assert runtime.handler.executor is executor
    assert runtime.connection.forward_handler is runtime.handler


def test_agent_worker_runtime_delegates_run_to_supervisor() -> None:
    """无人值守运行时委托生命周期控制器运行。"""
    supervisor = FinishingSupervisor()
    runtime = AgentWorkerRuntime(
        DummyMind(),
        config=build_config(),
        client=typing.cast(typing.Any, DummyClient()),
        supervisor=typing.cast(typing.Any, supervisor)
    )

    asyncio.run(runtime.run())

    assert supervisor.run_count == 1


def test_agent_loop_delegates_to_worker_runtime(monkeypatch) -> None:
    """agent 入口委托无人值守运行时。"""
    mind = DummyMind()
    RecordingWorkerRuntime.instances = []
    monkeypatch.setattr(
        "mind_app.modes.agent.runtime.AgentWorkerRuntime",
        RecordingWorkerRuntime
    )

    asyncio.run(agent_loop_module.agent_loop(mind))

    assert len(RecordingWorkerRuntime.instances) == 1
    assert RecordingWorkerRuntime.instances[0].mind is mind
    assert RecordingWorkerRuntime.instances[0].run_count == 1
