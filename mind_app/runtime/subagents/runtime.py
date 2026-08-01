# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from engine.observability import observe_exception
from mind_core.agent_config import AgentSettings
from mind_core.skills import skills_payload
from mind_nova.events import EventReport
from mind_app.modes.result import RunResult
from mind_app.mcp.contracts import McpSessionLike
from mind_app.runtime.execution import (
    AgentContext,
    TurnContext
)
from mind_app.runtime.turns.executor import (
    TurnExecution,
    resolve_turn_hook_scope
)
from mind_app.runtime.subagents.control import (
    AgentControl,
    AgentNotFoundError,
    AgentSnapshot,
    AgentStateError,
    AgentTurnOperation,
    AgentWaitResult
)
from mind_app.runtime.subagents.context import (
    ForkTurns,
    build_fork_context,
    normalize_fork_turns
)
from mind_app.runtime.subagents.executor import (
    StreamSubagentExecutor,
    SubagentExecutionPort
)
from mind_app.runtime.subagents.runner import SubagentRunner
from mind_app.runtime.subagents.thread import (
    AgentThreadContext,
    AgentTurnContext
)

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind

SkillsProvider = typing.Callable[[], list[dict[str, str]]]

TranscriptPathResolver = typing.Callable[[str], str]


class SubagentTurnFailedError(RuntimeError):
    """表示子模型轮次返回了未完成结果。"""

    def __init__(self, result: RunResult) -> None:
        self.result = result
        detail = str(result.error or "").strip()
        super().__init__(detail or f"subagent turn ended as {result.status}")


class SubagentRuntime:
    """管理主控制器内按根会话隔离的子执行线程。"""

    def __init__(
        self,
        controller: "Mind",
        *,
        settings: AgentSettings | None = None,
        executor: SubagentExecutionPort | None = None,
        skills_provider: SkillsProvider | None = None,
        transcript_path_for: TranscriptPathResolver | None = None
    ) -> None:
        self._controller          = controller
        self._settings            = settings or AgentSettings()
        self._executor            = executor or StreamSubagentExecutor(controller)
        self._skills_provider     = skills_provider or self._configured_skills
        self._transcript_path_for = transcript_path_for or (lambda _sid: "")
        self._runner              = SubagentRunner(controller)
        self._lock                = asyncio.Lock()

        self._controls: dict[str, AgentControl] = {}

        self._shutdown: bool = False

    @property
    def settings(self) -> AgentSettings:
        """返回运行时使用的固定配置。"""
        return self._settings

    async def spawn(
        self,
        parent: TurnContext,
        message: str,
        pref_config: typing.Mapping[str, typing.Any],
        *,
        agent_type: str,
        task_name: str,
        fork_turns: ForkTurns = "all",
        agent_id: str | None = None
    ) -> AgentSnapshot:
        """在父轮次所属根会话中创建子执行线程。"""
        task = _normalize_task(message)

        normalized_fork_turns = normalize_fork_turns(fork_turns)

        control = await self._control(parent.agent.root_session_id)

        thread = AgentThreadContext.child(
            parent,
            agent_type,
            task_name,
            pref_config,
            skills=self._skills_provider(),
            agent_id=agent_id,
            fork_turns=normalized_fork_turns,
            fork_context=build_fork_context(
                parent.transcript_path,
                normalized_fork_turns,
            ),
            transcript_path_for=self._transcript_path_for,
        )

        return await control.spawn(
            thread,
            self._turn_operation(task),
        )

    async def submit(
        self,
        root_session_id: str,
        agent_id: str,
        message: str,
        *,
        interrupt: bool = False
    ) -> str:
        """向根会话中的已有执行线程提交或排队下一轮任务。"""
        task    = _normalize_task(message)
        control = await self._existing_control(root_session_id)

        return await control.submit(
            agent_id,
            self._turn_operation(task),
            interrupt=interrupt,
        )

    async def resume(
        self,
        root_session_id: str,
        agent_id: str
    ) -> AgentSnapshot:
        """重新开放根会话中已经关闭的执行线程。"""
        control = await self._existing_control(root_session_id)
        return await control.resume(agent_id)

    async def get(
        self,
        root_session_id: str,
        agent_id: str
    ) -> AgentSnapshot:
        """返回根会话中指定执行线程的快照。"""
        control = await self._existing_control(root_session_id)
        return await control.get(agent_id)

    async def snapshots(
        self,
        root_session_id: str,
    ) -> tuple[AgentSnapshot, ...]:
        """返回根会话中的全部执行线程快照。"""
        control = await self._existing_control(root_session_id)
        return await control.snapshots()

    async def wait(
        self,
        root_session_id: str,
        targets: typing.Iterable[str],
        *,
        timeout_sec: float | None = None
    ) -> AgentWaitResult:
        """等待根会话中的目标执行线程进入终态。"""
        control = await self._existing_control(root_session_id)
        return await control.wait(targets, timeout_sec=timeout_sec)

    async def interrupt(
        self,
        root_session_id: str,
        agent_id: str
    ) -> AgentSnapshot:
        """中断根会话中指定执行线程的当前轮次。"""
        control = await self._existing_control(root_session_id)
        return await control.interrupt(agent_id)

    async def close(
        self,
        root_session_id: str,
        agent_id: str
    ) -> AgentSnapshot:
        """关闭根会话中指定执行线程及其后代并返回关闭前快照。"""
        control = await self._existing_control(root_session_id)
        return await control.close(agent_id)

    async def shutdown_root(
        self,
        root_session_id: str
    ) -> tuple[AgentSnapshot, ...]:
        """关闭并移除指定根会话的执行树。"""
        normalized = _normalize_root_session_id(root_session_id)

        async with self._lock:
            control = self._controls.pop(normalized, None)
        if control is None:
            return ()

        return await control.shutdown()

    async def shutdown(self) -> None:
        """终止运行时并关闭全部根会话执行树。"""
        async with self._lock:
            if self._shutdown:
                return None
            self._shutdown = True
            controls = self._controls
            self._controls = {}

        if controls:
            await asyncio.gather(
                *(control.shutdown() for control in controls.values()),
                return_exceptions=False,
            )

    def _turn_operation(
        self,
        message: str
    ) -> AgentTurnOperation[RunResult]:
        """创建由控制器分配轮次序号的任务操作。"""
        async def run(turn: AgentTurnContext) -> RunResult:
            thread      = turn.thread
            pref_config = thread.config_snapshot()

            context = TurnContext.create(
                agent=thread.agent,
                cid=thread.cid,
                sid=thread.sid,
                mode=thread.mode,
                source=thread.source,
                pref_config=pref_config,
                cwd=thread.cwd,
                permissions=thread.permissions,
                transcript_path=thread.transcript_path,
                session_started=turn.turn_index == 1,
                session_start_reason="subagent",
            )

            execution = TurnExecution(
                context=context,
                message=message,
                hook_scope=resolve_turn_hook_scope(self._controller, context),
                metadata={
                    "parent_turn_id": thread.spawn_turn_id,
                    "submission_id": turn.submission_id,
                    "turn_index": turn.turn_index,
                    "task_name": thread.agent.task_name,
                    "task_path": thread.agent.task_path,
                    "fork_turns": thread.fork_turns,
                },
                additional_context=(
                    thread.fork_context
                    if turn.turn_index == 1
                    else ()
                ),
            )

            async def execute_subagent(
                prepared: TurnExecution,
                session: McpSessionLike,
                tools: list[dict[str, typing.Any]],
                event_report: EventReport
            ) -> RunResult:
                """通过运行时装配的执行端口运行固定子轮次。"""
                return await self._executor.execute(
                    pref_config=pref_config,
                    skills=thread.skills_snapshot(),
                    execution=prepared,
                    session=session,
                    tools=tools,
                    event_report=event_report,
                )

            result = await self._runner.run(
                pref_config,
                execution,
                execute_subagent,
            )
            if result.status != "completed":
                raise SubagentTurnFailedError(result)
            return result

        return run

    def _configured_skills(self) -> list[dict[str, str]]:
        """读取并固定创建线程时有效的技能描述。"""
        try:
            config = self._controller.config_session.load()
            return skills_payload(config)
        except (OSError, TypeError, ValueError) as error:
            observe_exception(
                "subagent.skills.resolve_failed",
                error,
                level="WARNING",
            )
            return []

    async def _control(self, root_session_id: str) -> AgentControl:
        """返回或创建根会话对应的执行控制器。"""
        normalized = _normalize_root_session_id(root_session_id)

        async with self._lock:
            self._require_active()
            if not self._settings.enabled:
                raise AgentStateError("subagent runtime is disabled")

            control = self._controls.get(normalized)
            if control is None:
                control = AgentControl(
                    AgentContext.root(normalized),
                    max_open_agents=(
                        self._settings.max_concurrent_threads_per_session
                    ),
                    max_depth=self._settings.max_depth,
                )
                self._controls[normalized] = control

            return control

    async def _existing_control(self, root_session_id: str) -> AgentControl:
        """返回已经建立的根会话执行控制器。"""
        normalized = _normalize_root_session_id(root_session_id)

        async with self._lock:
            self._require_active()
            control = self._controls.get(normalized)

        if control is None:
            raise AgentNotFoundError(
                f"agent root session not found: {normalized}"
            )

        return control

    def _require_active(self) -> None:
        """确认运行时仍可接受操作。"""
        if self._shutdown:
            raise AgentStateError("subagent runtime is shut down")


def _normalize_task(message: str) -> str:
    """返回非空的子轮次任务文本。"""
    if not isinstance(message, str):
        raise TypeError("subagent task must be a string")
    if not message.strip():
        raise ValueError("subagent task is required")

    return message


def _normalize_root_session_id(value: str) -> str:
    """返回非空的根会话标识。"""
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("root session id is required")
    return normalized


if __name__ == '__main__':
    pass
