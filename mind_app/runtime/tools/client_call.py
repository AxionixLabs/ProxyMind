# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
import hashlib
from dataclasses import (
    dataclass,
    field,
    replace
)
from mcp import types as mcp_types
from engine.observability import observe_exception
from mind_app.client_tools.types import NESTED_TOOL_DISPATCH_META_KEY
from mind_app.mcp.contracts import McpSessionLike
from mind_nova.requests.effects import post_effect_reconciliation
from mind_nova.requests.tools import build_tool_result_payload
from mind_app.output import (
    OutputControlPort,
    OutputStatusPort
)
from mind_app.presentation.contracts import PresentationSink
from mind_app.runtime.execution import (
    ToolInvocation,
    TurnContext
)
from mind_app.runtime.hooks.models import (
    ToolOperationResult,
    ToolResultSnapshot
)
from mind_app.runtime.hooks.tool import ToolCallCoordinator
from mind_app.stream_events.tool_trace import coding_trace_tool
from mind_app.presentation.tool_policy import (
    is_two_stage_tool,
    tool_status_text
)
from .display import (
    show_tool_result,
    show_tool_start
)
from .run import (
    ToolRunResult,
    run_tool_step
)
from ..durable_effects import (
    EffectJournalPersistenceError,
    LocalEffectJournal,
    LocalEffectReconciliationRequired
)
from ..workspace_artifacts import WorkspaceArtifactGate


@dataclass(slots=True)
class ClientToolCallResult:
    """描述一次客户端工具执行结果。"""
    name: str
    arguments: dict[str, typing.Any]
    ok: bool
    text: str
    cost_ms: int = 0
    call_id: str = ""
    fields: dict[str, typing.Any] = field(default_factory=dict)
    hook_response: typing.Any = None
    response: mcp_types.CallToolResult | None = None


@dataclass(frozen=True, slots=True)
class ClientToolCallOutcome:
    """描述客户端工具执行和 Hook 反馈的组合结果。"""

    result: ClientToolCallResult
    additional_context: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """规范化 Hook 反馈文本。"""
        if not isinstance(self.result, ClientToolCallResult):
            raise TypeError(
                "client tool execution must return ClientToolCallResult"
            )
        if not isinstance(self.additional_context, tuple) or any(
            not isinstance(value, str)
            for value in self.additional_context
        ):
            raise TypeError("additional context must be a tuple of strings")
        object.__setattr__(
            self,
            "additional_context",
            tuple(
                text
                for value in self.additional_context
                for text in [value.strip()]
                if text
            ),
        )


def build_client_tool_post_kwargs(
    outcome: ClientToolCallOutcome,
    *,
    execution: dict[str, typing.Any] | None
) -> dict[str, typing.Any]:
    """构建客户端工具结果回传参数。"""
    post_kwargs: dict[str, typing.Any] = {
        "execution": execution,
    }

    if outcome.additional_context:
        post_kwargs["additional_context"] = outcome.additional_context

    return post_kwargs


class ClientToolCallRunner:
    """执行单个客户端工具调用并生成本地展示。"""

    def __init__(
        self,
        *,
        session: McpSessionLike,
        output_control: OutputControlPort,
        status_control: OutputStatusPort,
        presentation: PresentationSink,
        tools: list[dict[str, typing.Any]],
        pref_config: dict[str, typing.Any],
        tool_call_coordinator: ToolCallCoordinator,
        effect_journal: LocalEffectJournal | None = None,
        artifact_gate: WorkspaceArtifactGate | None = None,
        effect_reconciler: typing.Callable[..., typing.Awaitable[
            dict[str, typing.Any]
        ]] | None = None,
    ) -> None:
        """绑定工具生命周期端口和本地持久化门禁。"""
        self.session               = session
        self.output_control        = output_control
        self.status_control        = status_control
        self.presentation          = presentation
        self.tools                 = tools
        self.pref_config           = pref_config
        self.tool_call_coordinator = tool_call_coordinator
        self.effect_journal        = effect_journal or LocalEffectJournal()
        self.artifact_gate         = artifact_gate or WorkspaceArtifactGate()
        self.effect_reconciler     = effect_reconciler or post_effect_reconciliation

    @staticmethod
    def _outcome_payload(outcome: ClientToolCallOutcome) -> dict[str, typing.Any]:
        """把最终可见结果编码为可持久复用的基础结构。"""
        result = outcome.result
        return {
            "name": result.name,
            "arguments": dict(result.arguments),
            "ok": result.ok,
            "text": result.text,
            "cost_ms": result.cost_ms,
            "call_id": result.call_id,
            "fields": dict(result.fields),
            "additional_context": list(outcome.additional_context),
        }

    @staticmethod
    def _outcome_from_payload(payload: dict[str, typing.Any]) -> ClientToolCallOutcome:
        """从本地效果账本恢复最终可见结果。"""
        arguments = payload.get("arguments")
        fields    = payload.get("fields")
        contexts  = payload.get("additional_context")

        if not isinstance(arguments, dict) or not isinstance(fields, dict):
            raise ValueError("persisted local effect result is invalid")
        if not isinstance(contexts, list) or any(
            not isinstance(value, str) for value in contexts
        ):
            raise ValueError("persisted local effect context is invalid")

        return ClientToolCallOutcome(
            result=ClientToolCallResult(
                name=str(payload.get("name") or ""),
                arguments=arguments,
                ok=bool(payload.get("ok")),
                text=str(payload.get("text") or ""),
                cost_ms=max(0, int(payload.get("cost_ms") or 0)),
                call_id=str(payload.get("call_id") or ""),
                fields=fields,
            ),
            additional_context=tuple(contexts),
        )

    @staticmethod
    def _execution_with_effect(invocation: ToolInvocation) -> dict[str, typing.Any]:
        """构建包含规范效果身份的工具结果执行信封。"""
        effect = invocation.effect
        execution = dict(invocation.execution or {})
        if effect is not None:
            execution["effect"] = {
                "effect_id": effect.effect_id,
                "fingerprint": effect.fingerprint,
                "class": effect.effect_class,
                "replay_policy": effect.replay_policy,
                "scope": effect.scope,
                "provider_idempotency_key": effect.provider_idempotency_key,
                "status": effect.status,
                "dispatch_required": effect.dispatch_required,
                "dispatch_count": effect.dispatch_count,
            }
        return execution

    @staticmethod
    def _effect_request_suffix(effect_id: str) -> str:
        """为效果核对命令生成固定长度的稳定请求后缀。"""
        return hashlib.sha256(str(effect_id or "").encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _denied_result(
        invocation: ToolInvocation,
        reason: str
    ) -> ClientToolCallResult:
        """构建被前置 Hook 阻止的工具结果。"""
        text = str(reason or "tool use denied by hook")

        fields = {
            "ok": False,
            "text": text,
            "data": {
                "hook_denied": True,
                "error": text,
            },
        }

        return ClientToolCallResult(
            name=invocation.name,
            arguments=dict(invocation.arguments),
            ok=False,
            text=text,
            call_id=invocation.call_id,
            fields=fields,
        )

    @staticmethod
    def _not_executed_outcome(
        invocation: ToolInvocation,
        *,
        status: str,
        error: BaseException,
    ) -> ClientToolCallOutcome:
        """构建本地副作用尚未开始时的确定失败结果。"""
        detail = f"{type(error).__name__}: {error}"
        text = f"{status}: {detail}"
        fields = {
            "ok": False,
            "text": text,
            "data": {
                "executed": False,
                "status": status,
                "error": detail,
            },
        }
        return ClientToolCallOutcome(result=ClientToolCallResult(
            name=invocation.name,
            arguments=dict(invocation.arguments),
            ok=False,
            text=text,
            call_id=invocation.call_id,
            fields=fields,
        ))

    def _reconciliation_result_payload(
        self,
        invocation: ToolInvocation,
        outcome: ClientToolCallOutcome
    ) -> dict[str, typing.Any]:
        """构建可持久保存并直接提交控制面的完整工具结果。"""
        effect = invocation.effect
        if effect is None:
            raise ValueError("local effect is required for reconciliation")
        result = outcome.result
        request_suffix = self._effect_request_suffix(effect.effect_id)
        return build_tool_result_payload(
            cid=invocation.turn.cid,
            sid=invocation.turn.sid,
            call_id=invocation.call_id,
            name=result.name,
            ok=result.ok,
            result=result.fields,
            execution=self._execution_with_effect(invocation),
            additional_context=outcome.additional_context,
            arguments=invocation.arguments,
            request_id=f"effect-tool-result-{request_suffix}",
        )

    async def _submit_effect_reconciliation(
        self,
        effect_id: str,
        result_payload: dict[str, typing.Any],
    ) -> None:
        """使用稳定请求标识提交已知完成的本地效果结果。"""
        request_suffix = self._effect_request_suffix(effect_id)

        result = result_payload.get("result")
        data   = result.get("data") if isinstance(result, dict) else None

        resolution: typing.Literal["failed", "committed"] = (
            "failed"
            if isinstance(data, dict) and data.get("executed") is False
            else "committed"
        )

        await self.effect_reconciler(
            effect_id=effect_id,
            request_id=f"effect-reconcile-{request_suffix}",
            resolution=resolution,
            result_payload=result_payload,
            error="",
            metadata={"source": "client_local_journal"},
        )

    async def _reconcile_known_outcome(
        self,
        invocation: ToolInvocation,
        outcome: ClientToolCallOutcome,
    ) -> None:
        """把本地已完成但日志提交失败的结果原子提交到服务端。"""
        effect = invocation.effect
        if effect is None:
            raise ValueError("local effect is required for reconciliation")
        await self._submit_effect_reconciliation(
            effect.effect_id,
            self._reconciliation_result_payload(invocation, outcome),
        )

    async def reconcile_known_effect(self, effect_id: str) -> bool:
        """使用本地确定结果自动恢复服务端暂停的持久效果。"""
        normalized_effect_id = str(effect_id or "").strip()

        result_payload = await self.effect_journal.reconciliation_result(
            normalized_effect_id
        )

        if result_payload is None:
            return False
        await self._submit_effect_reconciliation(
            normalized_effect_id,
            result_payload,
        )

        try:
            await self.effect_journal.mark_reconciled(normalized_effect_id)
        except EffectJournalPersistenceError as error:
            observe_exception(
                "client_effect.journal.reconcile.failed",
                error,
                level="WARNING",
                effect_id=normalized_effect_id,
            )
        return True

    async def _execute_allowed_call(
        self,
        invocation: ToolInvocation,
        *,
        use_coding_trace: bool,
        display: bool,
    ) -> ClientToolCallResult:
        """执行已通过前置检查的客户端工具调用。"""
        name      = invocation.name
        arguments = dict(invocation.arguments)
        call_id   = invocation.call_id
        cost_ms   = 0

        response: mcp_types.CallToolResult | None = None

        if name == "js_repl":
            async def dispatch_nested_tool(
                tool_name: str,
                tool_arguments: dict[str, typing.Any],
                nested_call_id: str,
                execution: dict[str, typing.Any] | None,
            ) -> mcp_types.CallToolResult:
                """把嵌套调用接入同一客户端工具生命周期。"""
                return await self._execute_nested_tool(
                    invocation.turn,
                    tool_name=tool_name,
                    arguments=tool_arguments,
                    call_id=nested_call_id,
                    execution=execution,
                )

            invocation = replace(
                invocation,
                meta={
                    **(invocation.meta or {}),
                    NESTED_TOOL_DISPATCH_META_KEY: dispatch_nested_tool,
                },
            )

        try:
            if display:
                self.output_control.record_tool_arguments(
                    name,
                    arguments,
                    call_id=call_id,
                )
                if is_two_stage_tool(name):
                    await show_tool_start(
                        self.presentation,
                        name,
                        arguments,
                        call_id=call_id,
                    )

            tool_run = await run_tool_step(
                self.session,
                status_control=self.status_control,
                presentation=self.presentation,
                tools=self.tools,
                invocation=invocation,
                pref_config=self.pref_config,
                enable_progress_notify=True,
                status_text=tool_status_text(name),
            )

            ok      = tool_run.ok
            fields  = tool_run.fields
            text    = tool_run.text
            cost_ms = tool_run.cost_ms

            raw_response = getattr(tool_run, "result", None)
            if isinstance(raw_response, mcp_types.CallToolResult):
                response = raw_response

            hook_response = getattr(tool_run, "hook_response", fields)

        except Exception as exc:
            text = f"{type(exc).__name__}: {exc}"
            ok   = False

            fields = {
                "ok": False,
                "text": text,
                "data": {"error": text},
            }

            hook_response = None

            tool_run = ToolRunResult(
                result=None,
                ok=False,
                fields=fields,
                text=text,
                data=fields["data"],
                hook_response=None,
                cost_ms=cost_ms,
                status="failed",
            )

        if display:
            if use_coding_trace:
                await self.status_control.end_status()
            await show_tool_result(
                self.presentation,
                name,
                arguments,
                tool_run,
                ok=ok,
                text=text,
                use_coding_trace=use_coding_trace,
                call_id=call_id,
            )

        return ClientToolCallResult(
            name=name,
            arguments=arguments,
            ok=ok,
            text=str(text or ""),
            cost_ms=cost_ms,
            call_id=call_id,
            fields=fields,
            hook_response=hook_response,
            response=response,
        )

    async def _execute_nested_tool(
        self,
        turn: TurnContext,
        *,
        tool_name: str,
        arguments: dict[str, typing.Any],
        call_id: str,
        execution: dict[str, typing.Any] | None
    ) -> mcp_types.CallToolResult:
        """通过普通工具生命周期执行内核发起的嵌套调用。"""
        outcome = await self.execute(
            ToolInvocation(
                turn=turn,
                call_id=call_id,
                name=tool_name,
                arguments=arguments,
                execution=execution,
            ),
            use_coding_trace=coding_trace_tool(tool_name),
            display=False,
        )
        result = outcome.result
        if result.response is None:
            raise RuntimeError(result.text or f"nested {tool_name} call failed")
        return result.response

    async def execute(
        self,
        invocation: ToolInvocation,
        *,
        use_coding_trace: bool,
        display: bool = True
    ) -> ClientToolCallOutcome:
        """执行经过 Hook 协调的客户端工具调用。"""
        effect = invocation.effect
        if effect is not None:
            try:
                decision = await self.effect_journal.inspect(effect)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                return self._not_executed_outcome(
                    invocation,
                    status="local_effect_journal_unavailable",
                    error=error,
                )
            if decision.action == "reuse":
                return self._outcome_from_payload(decision.result_payload or {})
            if decision.action == "reconcile":
                raise LocalEffectReconciliationRequired(effect.effect_id)
            if effect.scope == "workspace":
                try:
                    await self.artifact_gate.ensure(
                        invocation.checkpoint,
                        expected_workspace_root=invocation.turn.cwd,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    outcome = self._not_executed_outcome(
                        invocation,
                        status="workspace_checkpoint_unavailable",
                        error=error,
                    )
                    persisted_outcome = {
                        **self._outcome_payload(outcome),
                        "reconciliation_result_payload": (
                            self._reconciliation_result_payload(invocation, outcome)
                        ),
                    }
                    try:
                        await asyncio.shield(self.effect_journal.commit_unexecuted(
                            effect,
                            persisted_outcome,
                        ))
                    except asyncio.CancelledError:
                        raise
                    except Exception as persist_error:
                        observe_exception(
                            "client_effect.journal.unexecuted.failed",
                            persist_error,
                            level="WARNING",
                            effect_id=effect.effect_id,
                        )
                    return outcome
            try:
                decision = await self.effect_journal.begin(effect)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                return self._not_executed_outcome(
                    invocation,
                    status="local_effect_journal_unavailable",
                    error=error,
                )
            if decision.action == "reuse":
                return self._outcome_from_payload(decision.result_payload or {})
            if decision.action == "reconcile":
                raise LocalEffectReconciliationRequired(effect.effect_id)

        async def operation(
            prepared: ToolInvocation
        ) -> ToolOperationResult[ClientToolCallResult]:
            """执行已获准的本地操作。"""
            result = await self._execute_allowed_call(
                prepared,
                use_coding_trace=use_coding_trace,
                display=display,
            )
            return ToolOperationResult(
                value=result,
                snapshot=ToolResultSnapshot(
                    ok=result.ok,
                    text=result.text,
                    fields=result.fields,
                ),
                hook_response=result.hook_response,
            )

        try:
            hook_run = await self.tool_call_coordinator.run_invocation(
                invocation,
                operation,
            )
        except BaseException as error:
            if effect is not None:
                try:
                    await asyncio.shield(self.effect_journal.mark_unknown(effect, error))
                except (asyncio.CancelledError, Exception):
                    pass
            raise

        if not hook_run.allowed:
            outcome = ClientToolCallOutcome(
                result=self._denied_result(invocation, hook_run.reason),
                additional_context=hook_run.additional_context,
            )
        elif hook_run.value is None:
            raise RuntimeError("tool execution returned no result")
        elif hook_run.visible_result is None:
            raise RuntimeError("tool execution returned no visible result")
        else:
            visible = hook_run.visible_result

            outcome = ClientToolCallOutcome(
                result=ClientToolCallResult(
                    name=hook_run.value.name,
                    arguments=dict(hook_run.value.arguments),
                    ok=visible.ok,
                    text=visible.text,
                    cost_ms=hook_run.value.cost_ms,
                    call_id=hook_run.value.call_id,
                    fields=visible.fields,
                    hook_response=hook_run.value.hook_response,
                    response=hook_run.value.response,
                ),
                additional_context=visible.additional_context,
            )

        if effect is not None:
            persisted_outcome = {
                **self._outcome_payload(outcome),
                "reconciliation_result_payload": self._reconciliation_result_payload(
                    invocation,
                    outcome,
                ),
            }
            try:
                await asyncio.shield(self.effect_journal.commit(
                    effect,
                    persisted_outcome,
                ))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                try:
                    await asyncio.shield(self.effect_journal.mark_unknown(
                        effect,
                        error,
                        result_payload=persisted_outcome,
                    ))
                except (asyncio.CancelledError, Exception):
                    pass
                try:
                    await self._reconcile_known_outcome(invocation, outcome)
                except (asyncio.CancelledError, Exception) as reconcile_error:
                    raise LocalEffectReconciliationRequired(
                        effect.effect_id
                    ) from reconcile_error
                try:
                    await asyncio.shield(self.effect_journal.commit(
                        effect,
                        persisted_outcome,
                    ))
                except (asyncio.CancelledError, Exception):
                    pass
        return outcome


if __name__ == '__main__':
    pass
