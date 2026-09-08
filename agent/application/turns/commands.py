# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing
from dataclasses import dataclass

from agent.domain import RecoveryAction
from agent.ports import (
    ProtocolCommandClient,
    ProtocolCommandError,
    RecoveryResolution,
    RunFact,
    RunPersistence,
    RunSnapshot,
    RunRecoveryResolutionRecord,
    SessionRuntimeFactory,
    TurnExecutor,
    TurnExecutorResult,
    remote_turn_binding,
)
from agent.protocol import (
    ModelStreamRequest,
    RunEvent,
    SubmitTurnCommand
)
from agent.protocol.json_value import ThawedJsonValue
from .projections import (
    RunResultProjection,
    project_run_result,
)

ResultValue = typing.TypeVar("ResultValue", bound=TurnExecutorResult)


@dataclass(frozen=True, slots=True)
class SubmitTurnResult(typing.Generic[ResultValue]):
    """返回主动 Turn 的原始结果和本地事件投影。"""

    value: ResultValue
    events: tuple[RunEvent, ...]
    projection: RunResultProjection


@dataclass(frozen=True, slots=True)
class RemoteTurnRecovery:
    """描述必须通过原 Turn attach/replay 完成的本地 Run 恢复项。"""

    snapshot: RunSnapshot
    request: ModelStreamRequest
    replay_target_seq: int


@dataclass(frozen=True, slots=True)
class SessionRecoveryResult:
    """描述一次 Session 恢复探测后仍待处理和可恢复编辑的命令。"""

    pending: tuple[RunSnapshot, ...]
    restore_commands: tuple[SubmitTurnCommand, ...]
    resolved_run_ids: tuple[str, ...]
    observe_turns: tuple[RemoteTurnRecovery, ...] = ()


class TurnApplication(typing.Generic[ResultValue]):
    """编排主动 Turn 提交、事件投影和 Session 生命周期操作。"""

    def __init__(
        self,
        persistence: RunPersistence | None = None,
        *,
        runtime_factory: SessionRuntimeFactory[ResultValue] | None = None,
    ) -> None:
        """创建由注入 runtime 持有 Session 可变状态的应用入口。"""
        self._persistence = persistence
        if not callable(runtime_factory):
            raise TypeError("session runtime factory must be callable")
        self._runtime = runtime_factory(persistence)

    @property
    def closed(self) -> bool:
        """返回底层 Session runtime 是否已经完成关闭。"""
        return self._runtime.closed

    async def submit(
        self,
        command: SubmitTurnCommand,
        executor: TurnExecutor[ResultValue],
    ) -> SubmitTurnResult[ResultValue]:
        """提交命令并从该 Run 的完整事件序列生成稳定结果投影。"""
        try:
            execution = await self._runtime.execute(command, executor)
        except asyncio.CancelledError:
            await self._runtime.cancel_session(command.session_id)
            raise
        return SubmitTurnResult(
            value=execution.value,
            events=execution.events,
            projection=project_run_result(execution.events),
        )

    async def cancel_session(self, session_id: str) -> None:
        """取消指定 Session 的活动 Run 和排队命令，并等待清理完成。"""
        await self._runtime.cancel_session(session_id)

    async def close_session(self, session_id: str) -> None:
        """关闭指定 Session，并等待已接收命令自然收束。"""
        await self._runtime.close_session(session_id)

    async def recover_session(
        self,
        session_id: str,
    ) -> tuple[RunSnapshot, ...]:
        """读取未终结 Run 及其安全恢复动作，不自动调用执行端口。"""
        return await self._runtime.recover_session(session_id)

    async def resolve_recovery(
        self,
        run_id: str,
        *,
        request_id: str,
        resolution: RecoveryResolution,
        result_payload: typing.Mapping[str, ThawedJsonValue] | None = None,
        error: str = "",
    ) -> RunRecoveryResolutionRecord:
        """保存外部权威恢复决议，并解除对应 Run 的本地恢复门禁。"""
        if self._persistence is None:
            raise RuntimeError("run persistence is unavailable")
        return await self._persistence.resolve_recovery(
            run_id,
            request_id=request_id,
            resolution=resolution,
            result_payload=result_payload,
            error=error,
        )

    async def record_remote_request(
        self,
        command: SubmitTurnCommand,
        request: ModelStreamRequest,
    ) -> None:
        """在远端提交前持久化当前 Run 的完整冻结请求。"""
        if self._persistence is None:
            raise RuntimeError("run persistence is unavailable")
        await self._persistence.save_remote_request(command, request)

    async def reconcile_remote_session(
        self,
        session_id: str,
        protocol_client: ProtocolCommandClient,
    ) -> SessionRecoveryResult:
        """使用远端权威终态解除门禁，并交回确定未执行的排队输入。"""
        recoveries = await self.recover_session(session_id)
        if not recoveries:
            return SessionRecoveryResult((), (), ())
        if self._persistence is None:
            return SessionRecoveryResult(recoveries, (), ())

        restore_commands: list[SubmitTurnCommand] = []
        resolved_run_ids: list[str] = []
        observe_turns: list[RemoteTurnRecovery] = []
        for snapshot in recoveries:
            if snapshot.recovery_action is RecoveryAction.REDISPATCH:
                await self.resolve_recovery(
                    snapshot.command.run_id,
                    request_id=(
                        f"recover_not_executed_{snapshot.command.run_id}"
                    ),
                    resolution="not_executed",
                    error="queued run was not executed",
                )
                restore_commands.append(snapshot.command)
                resolved_run_ids.append(snapshot.command.run_id)
                continue

            frozen = await self._persistence.load_remote_request(
                snapshot.command.run_id
            )
            binding = remote_turn_binding(snapshot.command)
            if frozen is None and binding is None:
                await self.resolve_recovery(
                    snapshot.command.run_id,
                    request_id=(
                        f"recover_unbound_{snapshot.command.run_id}"
                    ),
                    resolution="failed",
                    error="remote turn identity is unavailable",
                )
                resolved_run_ids.append(snapshot.command.run_id)
                continue
            cid = frozen.request.cid if frozen is not None else binding.cid
            sid = frozen.request.sid if frozen is not None else binding.sid
            turn_id = (
                frozen.request.turn_id
                if frozen is not None
                else binding.turn_id
            )
            try:
                status = await protocol_client.get_turn_status(
                    cid=cid,
                    sid=sid,
                    turn_id=turn_id,
                )
            except ProtocolCommandError as error:
                if error.status_code != 404:
                    continue
                if frozen is not None and frozen.revision > 1:
                    await self.resolve_recovery(
                        snapshot.command.run_id,
                        request_id=(
                            f"recover_continuation_missing_"
                            f"{snapshot.command.run_id}"
                        ),
                        resolution="failed",
                        error="remote continuation was not created",
                    )
                    resolved_run_ids.append(snapshot.command.run_id)
                    continue
                await self.resolve_recovery(
                    snapshot.command.run_id,
                    request_id=(
                        f"recover_not_executed_{snapshot.command.run_id}"
                    ),
                    resolution="not_executed",
                    error="remote turn does not exist",
                )
                restore_commands.append(snapshot.command)
                resolved_run_ids.append(snapshot.command.run_id)
                continue
            if frozen is not None:
                observe_turns.append(RemoteTurnRecovery(
                    snapshot=snapshot,
                    request=frozen.request,
                    replay_target_seq=status.last_event_seq,
                ))
                continue
            terminal = status.terminal
            if terminal is None:
                continue

            result_payload: dict[str, ThawedJsonValue] = {
                "type": "turn.completed",
                "turn_id": terminal.turn_id,
                "status": terminal.status,
                "error": terminal.error,
                "last_event_seq": terminal.last_event_seq,
                "completed_at": terminal.completed_at,
            }
            resolution: RecoveryResolution = (
                "committed" if terminal.status == "completed" else "failed"
            )
            await self.resolve_recovery(
                snapshot.command.run_id,
                request_id=(
                    "recover_remote_"
                    f"{snapshot.command.run_id}_{terminal.last_event_seq}"
                ),
                resolution=resolution,
                result_payload=result_payload,
                error=(
                    terminal.error or ""
                    if resolution == "committed"
                    else terminal.error
                    or f"remote turn completed with status {terminal.status}"
                ),
            )
            resolved_run_ids.append(snapshot.command.run_id)

        pending = await self.recover_session(session_id)
        return SessionRecoveryResult(
            pending=pending,
            restore_commands=tuple(restore_commands),
            resolved_run_ids=tuple(resolved_run_ids),
            observe_turns=tuple(observe_turns),
        )

    async def resolve_observed_recovery(
        self,
        recovery: RemoteTurnRecovery,
        result: ResultValue,
    ) -> RunRecoveryResolutionRecord:
        """以 attach 已消费的权威终态解除对应本地 Run 门禁。"""
        status = str(result.status or "").strip()
        if status not in {"completed", "failed", "interrupted", "cancelled"}:
            raise ValueError("observed recovery result is not terminal")
        result_payload = result.to_dict()
        if not isinstance(result_payload, dict):
            raise TypeError("observed recovery result payload must be an object")
        resolution: RecoveryResolution = (
            "committed" if status == "completed" else "failed"
        )
        error_value = result_payload.get("error")
        error = str(error_value or "").strip()
        if resolution == "failed" and not error:
            error = f"remote turn completed with status {status}"
        return await self.resolve_recovery(
            recovery.snapshot.command.run_id,
            request_id=(
                f"recover_observed_{recovery.snapshot.command.run_id}_"
                f"{recovery.request.turn_id}"
            ),
            resolution=resolution,
            result_payload=result_payload,
            error=error,
        )

    async def events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
    ) -> tuple[RunEvent, ...]:
        """按本地事件游标读取已经持久提交的 Run 事件。"""
        if self._persistence is None:
            return ()
        return await self._persistence.load_events(
            run_id,
            after_sequence=after_sequence,
        )

    async def facts(
        self,
        run_id: str,
        *,
        kind: str | None = None,
    ) -> tuple[RunFact, ...]:
        """读取最终消息、工具结果、审批决定和证据引用。"""
        if self._persistence is None:
            return ()
        return await self._persistence.load_facts(run_id, kind=kind)

    async def close(self, *, cancel_running: bool = False) -> None:
        """关闭 application 管理的全部 Session。"""
        await self._runtime.close(cancel_running=cancel_running)


async def submit_turn(
    command: SubmitTurnCommand,
    executor: TurnExecutor[ResultValue],
    *,
    runtime_factory: SessionRuntimeFactory[ResultValue],
) -> SubmitTurnResult[ResultValue]:
    """在注入的短生命周期 Session runtime 中执行一个主动 Turn 命令。"""
    application = TurnApplication(
        runtime_factory=runtime_factory,
    )
    try:
        return await application.submit(command, executor)
    finally:
        await application.close()


if __name__ == '__main__':
    pass
