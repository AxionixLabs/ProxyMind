# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
import hashlib
from agent.application import (
    EffectJournal,
    EffectJournalPersistenceError,
    LocalEffectReconciliationRequired,
)
from dataclasses import (
    dataclass,
    field,
    replace
)
from mcp import types as mcp_types
from observability import observe_exception
from mind_app.client_tools.types import (
    NESTED_TOOL_DISPATCH_META_KEY,
    TURN_INTERRUPT_META_KEY,
)
from agent.ports import McpSessionPort
from protocol.client.effects import post_effect_reconciliation
from protocol.client.tools import (
    ToolResultEnvelope,
    build_tool_result_envelope,
    build_tool_result_payload,
)
from mind_app.presentation.output import (
    OutputControlPort,
    OutputStatusPort
)
from mind_app.presentation.contracts import PresentationSink
from agent.application.turns.context import (
    ToolInvocation,
    TurnContext
)
from protocol.client.turn_control import TurnControlRequestError
from agent.application.hooks.models import (
    ToolOperationResult,
    ToolResultSnapshot
)
from mind_app.runtime.hooks.tool import ToolCallCoordinator
from mind_app.presentation.stream.tool_traces import coding_trace_tool
from mind_app.presentation.tool_policy import (
    is_two_stage_tool,
    tool_status_text
)
from metadata import const
from .display import (
    show_tool_result,
    show_tool_start
)
from .run import (
    ToolRunResult,
    run_tool_step
)
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


def _client_result_envelope(
    *,
    name: str,
    ok: bool,
    fallback_args: typing.Mapping[str, typing.Any],
    fallback_text: str,
    fields: typing.Mapping[str, typing.Any],
) -> ToolResultEnvelope:
    """从客户端规范字段显式构建 wire 工具结果信封。"""
    compact_fields = frozenset({"ok", "text", "attachments", "data"})
    envelope_fields = frozenset({
        "ok",
        "tool",
        "source",
        "args",
        "text",
        "attachments",
        "data",
    })
    field_names = set(fields)
    if field_names != compact_fields and field_names != envelope_fields:
        unknown = sorted(field_names.difference(envelope_fields))
        if unknown:
            raise ValueError(
                "client tool result contains unknown fields: "
                + ", ".join(unknown)
            )
        missing = sorted(compact_fields.difference(field_names))
        raise ValueError(
            "client tool result is missing fields: " + ", ".join(missing)
        )
    field_ok = fields.get("ok")
    if not isinstance(field_ok, bool) or field_ok != ok:
        raise ValueError("client tool result ok does not match execution status")
    field_tool = fields.get("tool", name)
    if field_tool != name:
        raise ValueError("client tool result tool does not match invocation")
    if "source" in fields and fields.get("source") != "client":
        raise ValueError("client tool result source must be client")
    args = fields.get("args", fallback_args)
    text = fields.get("text", fallback_text)
    attachments = fields.get("attachments")
    data = fields.get("data")
    if not isinstance(args, dict):
        raise TypeError("client tool result args must be an object")
    if not isinstance(text, str):
        raise TypeError("client tool result text must be a string")
    if not isinstance(attachments, list):
        raise TypeError("client tool result attachments must be a list")
    if not isinstance(data, dict):
        raise TypeError("client tool result data must be an object")
    return build_tool_result_envelope(
        tool=name,
        ok=ok,
        args=args,
        text=text,
        attachments=attachments,
        data=data,
    )


ClientToolOperation = typing.Callable[
    [ToolInvocation],
    typing.Awaitable[ToolOperationResult[ClientToolCallResult]],
]


class ClientToolCallRunner:
    """执行单个客户端工具调用并生成本地展示。"""

    def __init__(
        self,
        *,
        session: McpSessionPort,
        output_control: OutputControlPort,
        status_control: OutputStatusPort,
        presentation: PresentationSink,
        tools: list[dict[str, typing.Any]],
        pref_config: dict[str, typing.Any],
        tool_call_coordinator: ToolCallCoordinator,
        effect_journal: EffectJournal,
        patch_preview: typing.Callable[..., dict[str, typing.Any]] | None = None,
        effect_reconciler: typing.Callable[..., typing.Awaitable[
            dict[str, typing.Any]
        ]] | None = None,
        interrupt_turn: typing.Callable[[str], typing.Awaitable[bool]] | None = None,
    ) -> None:
        """绑定工具生命周期端口和持久效果依赖。"""
        self.session               = session
        self.output_control        = output_control
        self.status_control        = status_control
        self.presentation          = presentation
        self.tools                 = tools
        self.pref_config           = pref_config
        self.tool_call_coordinator = tool_call_coordinator
        self.patch_preview         = patch_preview
        self.effect_journal        = effect_journal
        self.effect_reconciler     = effect_reconciler or post_effect_reconciliation
        self.interrupt_turn        = interrupt_turn

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
    def _effect_request_suffix(effect_id: str) -> str:
        """为效果核对命令生成固定长度的稳定请求后缀。"""
        return hashlib.sha256(
            str(effect_id or "").encode(const.CHARSET)
        ).hexdigest()[:32]

    @staticmethod
    def _denied_result(
        invocation: ToolInvocation,
        reason: str
    ) -> ClientToolCallResult:
        """构建被前置 Hook 阻止的工具结果。"""
        text = str(reason or "tool use denied by hook")

        fields = build_tool_result_envelope(
            tool=invocation.name,
            ok=False,
            args=invocation.arguments,
            text=text,
            attachments=[],
            data={
                "hook_denied": True,
                "error": text,
            },
        )

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
        error: BaseException
    ) -> ClientToolCallOutcome:
        """构建本地副作用尚未开始时的确定失败结果。"""
        detail = f"{type(error).__name__}: {error}"
        text = f"{status}: {detail}"
        fields = build_tool_result_envelope(
            tool=invocation.name,
            ok=False,
            args=invocation.arguments,
            text=text,
            attachments=[],
            data={
                "executed": False,
                "status": status,
                "error": detail,
            },
        )
        return ClientToolCallOutcome(result=ClientToolCallResult(
            name=invocation.name,
            arguments=dict(invocation.arguments),
            ok=False,
            text=text,
            call_id=invocation.call_id,
            fields=fields,
        ))

    def _preview_patch(
        self,
        invocation: ToolInvocation
    ) -> dict[str, typing.Any] | None:
        """在补丁执行前请求只读预览，失败时不影响真实调用。"""
        if self.patch_preview is None:
            return None
        if invocation.turn.permissions.sandbox_mode == "read-only":
            return None

        arguments = invocation.arguments
        patch     = str(arguments.get("patch") or "")

        expected_sha256 = arguments.get("expected_sha256")
        if not isinstance(expected_sha256, dict):
            expected_sha256 = None

        try:
            raw = self.patch_preview(
                patch=patch,
                expected_sha256=expected_sha256,
                force=bool(arguments.get("force", False)),
            )
        except (OSError, TypeError, ValueError, UnicodeError, KeyError):
            return None

        if not isinstance(raw, dict) or not bool(raw.get("ok")):
            return None
        data = raw.get("data")
        if not isinstance(data, dict):
            return None
        files = data.get("files")
        delta = data.get("delta")
        changes = delta.get("changes") if isinstance(delta, dict) else None
        if (
            not isinstance(files, list)
            or not isinstance(delta, dict)
            or not isinstance(changes, list)
            or any(not isinstance(item, dict) for item in files)
            or any(not isinstance(item, dict) for item in changes)
            or any(
                not isinstance(item.get("hunks"), list)
                for item in changes
            )
        ):
            return None
        return dict(data)

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
            additional_context=outcome.additional_context,
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
            if (
                (
                    isinstance(result, dict)
                    and result.get("ok") is False
                )
                or (
                    isinstance(data, dict)
                    and data.get("executed") is False
                )
            )
            else "committed"
        )
        reconciliation_error = ""
        if resolution == "failed":
            if isinstance(data, dict):
                reconciliation_error = str(data.get("error") or "").strip()
            if not reconciliation_error and isinstance(result, dict):
                reconciliation_error = str(result.get("text") or "").strip()
            if not reconciliation_error:
                reconciliation_error = "client tool result reported failure"

        await self.effect_reconciler(
            effect_id=effect_id,
            request_id=f"effect-reconcile-{request_suffix}",
            resolution=resolution,
            result_payload=result_payload,
            error=reconciliation_error,
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
            ) -> mcp_types.CallToolResult:
                """把嵌套调用接入同一客户端工具生命周期。"""
                return await self._execute_nested_tool(
                    invocation.turn,
                    tool_name=tool_name,
                    arguments=tool_arguments,
                    call_id=nested_call_id,
                )

            runtime_meta = {
                **(invocation.meta or {}),
                NESTED_TOOL_DISPATCH_META_KEY: dispatch_nested_tool,
            }
            if self.interrupt_turn is not None:
                runtime_meta[TURN_INTERRUPT_META_KEY] = self.interrupt_turn

            invocation = replace(
                invocation,
                meta=runtime_meta,
            )

        try:
            patch_preview = (
                self._preview_patch(invocation)
                if name == "apply_patch"
                else None
            )
            if name == "apply_patch":
                record_patch_start = getattr(
                    self.tool_call_coordinator,
                    "record_patch_start",
                    None,
                )
                if callable(record_patch_start) and isinstance(patch_preview, dict):
                    record_patch_start(
                        invocation,
                        preview_data=patch_preview,
                    )

            if display:
                self.output_control.record_tool_arguments(
                    name,
                    arguments,
                    call_id=call_id,
                )
                if is_two_stage_tool(name):
                    start_kwargs: dict[str, typing.Any] = {
                        "call_id": call_id,
                    }
                    if name == "apply_patch":
                        start_kwargs["patch_preview"] = patch_preview
                    await show_tool_start(
                        self.presentation,
                        name,
                        arguments,
                        **start_kwargs,
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

        except TurnControlRequestError:
            raise
        except Exception as exc:
            text = f"{type(exc).__name__}: {exc}"
            ok   = False

            fields = build_tool_result_envelope(
                tool=name,
                ok=False,
                args=arguments,
                text=text,
                attachments=[],
                data={"error": text},
            )

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

        envelope = _client_result_envelope(
            name=name,
            ok=ok,
            fallback_args=arguments,
            fallback_text=str(text or ""),
            fields=fields,
        )

        return ClientToolCallResult(
            name=name,
            arguments=dict(envelope["args"]),
            ok=ok,
            text=str(text or ""),
            cost_ms=cost_ms,
            call_id=call_id,
            fields=envelope,
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
    ) -> mcp_types.CallToolResult:
        """通过普通工具生命周期执行内核发起的嵌套调用。"""
        outcome = await self.execute(
            ToolInvocation(
                turn=turn,
                call_id=call_id,
                name=tool_name,
                arguments=arguments,
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
        display: bool = True,
        operation_handler: ClientToolOperation | None = None,
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

        async def default_operation(
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

        operation = operation_handler or default_operation

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

            envelope = _client_result_envelope(
                name=hook_run.value.name,
                ok=visible.ok,
                fallback_args=hook_run.value.arguments,
                fallback_text=visible.text,
                fields=visible.fields,
            )
            outcome = ClientToolCallOutcome(
                result=ClientToolCallResult(
                    name=hook_run.value.name,
                    arguments=dict(envelope["args"]),
                    ok=visible.ok,
                    text=visible.text,
                    cost_ms=hook_run.value.cost_ms,
                    call_id=hook_run.value.call_id,
                    fields=envelope,
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
