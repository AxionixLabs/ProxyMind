# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
from dataclasses import dataclass
from observability import observe
from agent.application.tools.planning import normalize_plan_arguments
from agent.ports import McpSessionPort
from infrastructure.mcp.tool_results import normalize_call_tool_result
from agent.application.tools.catalog import has_tool
from agent.application.turns.context import (
    ToolInvocation,
    TurnContext
)
from agent.application.hooks.models import (
    ToolOperationResult,
    ToolResultSnapshot
)
from mind_app.runtime.hooks.tool import ToolCallCoordinator
from .router import execute_tool
from .run import hook_tool_response

ERROR_PREVIEW_LIMIT = 8
FAILURE_GROUP_LIMIT = 8


@dataclass(slots=True)
class PlanStepResult:
    """描述计划中单个步骤的执行结果。"""
    run: int
    index: int
    tool: str
    ok: bool
    text: str
    result: dict[str, typing.Any]
    cost_ms: int = 0
    additional_context: tuple[str, ...] = ()


@dataclass(slots=True)
class PlanExecutionReport:
    """汇总计划执行结果并提供展示与回传字段。"""
    ok: bool
    text: str
    data: dict[str, typing.Any]
    attachments: list[dict[str, typing.Any]]
    cost_ms: int
    results: list[PlanStepResult]
    additional_context: tuple[str, ...] = ()

    @property
    def fields(self) -> dict[str, typing.Any]:
        """返回标准工具结果字段。"""
        return {
            "ok": self.ok,
            "text": self.text,
            "attachments": self.attachments,
            "data": self.data
        }


class StepPlanExecutor:
    """执行模型通过 plan_steps 提交的本地工具计划。"""

    def __init__(
        self,
        *,
        session: McpSessionPort,
        tools: list[dict[str, typing.Any]],
        turn_context: TurnContext,
        pref_config: typing.Mapping[str, typing.Any],
        tool_call_coordinator: ToolCallCoordinator
    ) -> None:
        self.session = session
        self.tools = tools
        self.turn_context = turn_context
        self.pref_config = dict(pref_config)
        self.tool_call_coordinator = tool_call_coordinator

    async def execute_tool_call(
        self,
        *,
        arguments: dict[str, typing.Any],
        call_id: str | None = None,
    ) -> PlanExecutionReport:
        """执行一次 plan_steps 工具调用并返回统一报告。"""
        started_at = time.perf_counter()

        valid, plan, errors = normalize_plan_arguments(arguments)

        results = await self.execute_plan(plan, call_id=call_id) if valid else []
        cost_ms = int((time.perf_counter() - started_at) * 1000)

        return self._build_report(
            plan=plan,
            results=results,
            errors=errors,
            cost_ms=cost_ms
        )

    async def execute_plan(
        self,
        plan: dict[str, typing.Any],
        *,
        call_id: str | None = None
    ) -> list[PlanStepResult]:
        """按计划声明顺序执行所有步骤。"""
        loops = int(plan.get("loops") or 1)
        stop_on_fail = bool(plan.get("stop_on_fail", True))
        steps = _plan_steps(plan)

        results: list[PlanStepResult] = []

        stopped: bool = False

        for run_index in range(1, loops + 1):
            if stopped:
                break

            for step_index, step in enumerate(steps, start=1):
                result = await self._execute_step(
                    run_index,
                    step_index,
                    step,
                    call_id=call_id,
                )
                results.append(result)
                if stop_on_fail and not result.ok:
                    stopped = True
                    break

        return results

    async def _execute_step(
        self,
        run_index: int,
        step_index: int,
        step: dict[str, typing.Any],
        *,
        call_id: str | None,
    ) -> PlanStepResult:
        """执行计划中的单个步骤。"""
        name = str(step.get("tool") or "").strip()
        raw_arguments = step.get("args")
        arguments = dict(raw_arguments) if isinstance(raw_arguments, dict) else {}
        observe(
            "plan_step.start",
            run=run_index,
            step=step_index,
            tool=name,
        )

        if not has_tool(self.tools, name):
            return await self._failure(
                run_index,
                step_index,
                name,
                f"unknown plan tool: {name}"
            )

        try:
            started_at = time.perf_counter()

            step_call_id = ":".join((
                str(call_id or "plan"),
                str(run_index),
                str(step_index),
            ))

            invocation = ToolInvocation(
                turn=self.turn_context,
                call_id=step_call_id,
                name=name,
                arguments=arguments,
            )

            async def execute_step(
                prepared: ToolInvocation,
            ) -> ToolOperationResult[typing.Any]:
                """执行当前计划步骤。"""
                result = await execute_tool(
                    self.session,
                    tools=self.tools,
                    invocation=prepared,
                    pref_config=self.pref_config,
                )

                normalized = normalize_call_tool_result(result)

                return ToolOperationResult(
                    value=result,
                    snapshot=ToolResultSnapshot(
                        ok=normalized.ok,
                        text=normalized.display_text,
                        fields=normalized.fields,
                    ),
                    hook_response=hook_tool_response(
                        name,
                        result,
                        fields=normalized.fields,
                        text=normalized.display_text,
                        tools=self.tools,
                    ),
                )

            hook_run = await self.tool_call_coordinator.run_invocation(
                invocation,
                execute_step,
            )
            if not hook_run.allowed:
                return await self._failure(
                    run_index,
                    step_index,
                    name,
                    hook_run.reason,
                    additional_context=hook_run.additional_context,
                )

            if hook_run.value is None:
                raise RuntimeError(f"empty tool result for {name}")

            visible = hook_run.visible_result
            if visible is None:
                raise RuntimeError(f"empty visible tool result for {name}")

            step_result = PlanStepResult(
                run=run_index,
                index=step_index,
                tool=name,
                ok=visible.ok,
                text=visible.text,
                result=dict(visible.fields),
                cost_ms=int((time.perf_counter() - started_at) * 1000),
                additional_context=visible.additional_context,
            )
            self._log_step_result(step_result)
            return step_result
        except Exception as exc:
            return await self._failure(
                run_index,
                step_index,
                name,
                f"{type(exc).__name__}: {exc}"
            )

    async def _failure(
        self,
        run_index: int,
        step_index: int,
        name: str,
        text: str,
        *,
        additional_context: typing.Iterable[str] = (),
    ) -> PlanStepResult:
        """记录计划步骤失败。"""
        step_result = PlanStepResult(
            run=run_index,
            index=step_index,
            tool=name,
            ok=False,
            text=text,
            result={
                "ok": False,
                "text": text,
                "attachments": [],
                "data": {"error": text},
            },
            additional_context=tuple(additional_context),
        )
        self._log_step_result(step_result)
        return step_result

    @classmethod
    def _build_report(
        cls,
        *,
        plan: dict[str, typing.Any],
        results: list[PlanStepResult],
        errors: list[str],
        cost_ms: int
    ) -> PlanExecutionReport:
        """根据计划和步骤结果生成执行汇总与回传数据。"""
        steps = _plan_steps(plan)
        requested_runs = int(plan.get("loops") or 1)
        step_count = len(steps)

        calls_by_run: dict[int, int] = {}

        results_by_step: dict[int, list[PlanStepResult]] = {
            index: [] for index in range(1, step_count + 1)
        }

        ok_count: int = 0

        for item in results:
            calls_by_run[item.run] = calls_by_run.get(item.run, 0) + 1
            results_by_step.setdefault(item.index, []).append(item)
            if item.ok:
                ok_count += 1

        fail_count = len(results) - ok_count

        completed_runs = sum(
            1 for count in calls_by_run.values()
            if 0 < step_count <= count
        )

        stopped = bool(fail_count and plan.get("stop_on_fail", True))
        ok = not errors and fail_count == 0

        step_summaries: list[dict[str, typing.Any]] = []

        for step_index, step in enumerate(steps, start=1):
            items = results_by_step[step_index]

            step_summaries.append({
                "index": step_index,
                "tool": str(step.get("tool") or ""),
                "call_count": len(items),
                "ok_count": sum(1 for item in items if item.ok),
                "fail_count": sum(1 for item in items if not item.ok),
                "elapsed_ms": sum(item.cost_ms for item in items),
            })

        complete_results: list[dict[str, typing.Any]] = []
        attachments: list[dict[str, typing.Any]]      = []

        if requested_runs == 1:
            for item in results:
                result_fields = dict(item.result)

                result_attachments = [
                    dict(attachment)
                    for attachment in result_fields.get("attachments", [])
                    if isinstance(attachment, dict)
                ]
                result_fields["attachments"] = result_attachments

                attachments.extend(result_attachments)

                complete_results.append({
                    "run": item.run,
                    "step": item.index,
                    "tool": item.tool,
                    "ok": item.ok,
                    "cost_ms": item.cost_ms,
                    "result": result_fields,
                })

        text = cls._report_text(
            errors=errors,
            ok=ok,
            stopped=stopped,
            requested_runs=requested_runs,
            completed_runs=completed_runs,
            call_count=len(results),
            ok_count=ok_count,
            fail_count=fail_count,
            cost_ms=cost_ms
        )

        data = {
            "requested_runs": requested_runs,
            "completed_runs": completed_runs,
            "attempted_runs": max(calls_by_run, default=0),
            "steps_per_run": step_count,
            "call_count": len(results),
            "ok_count": ok_count,
            "fail_count": fail_count,
            "stopped": stopped,
            "elapsed_ms": cost_ms,
            "result_mode": "full" if requested_runs == 1 else "aggregate",
            "steps": step_summaries,
            "errors": errors[:ERROR_PREVIEW_LIMIT]
        }

        if requested_runs == 1:
            data["results"] = complete_results
        else:
            failure_groups = cls._failure_groups(results)
            data["failure_groups"] = failure_groups[:FAILURE_GROUP_LIMIT]
            data["omitted_failure_groups"] = max(
                0,
                len(failure_groups) - FAILURE_GROUP_LIMIT,
            )

        return PlanExecutionReport(
            ok=ok,
            text=text,
            data=data,
            attachments=attachments,
            cost_ms=cost_ms,
            results=results,
            additional_context=tuple(
                context
                for result in results
                for context in result.additional_context
            ),
        )

    @classmethod
    def _report_text(
        cls,
        *,
        errors: list[str],
        ok: bool,
        stopped: bool,
        requested_runs: int,
        completed_runs: int,
        call_count: int,
        ok_count: int,
        fail_count: int,
        cost_ms: int
    ) -> str:
        """生成计划执行的最终摘要。"""
        if errors:
            return f"plan rejected · {'; '.join(errors[:ERROR_PREVIEW_LIMIT])}"
        if ok:
            return (
                f"{completed_runs}/{requested_runs} runs · {call_count} calls · "
                f"all succeeded · {cls._duration_text(cost_ms)}"
            )

        state = "stopped" if stopped else "completed with failures"

        return (
            f"{completed_runs}/{requested_runs} runs · {ok_count} succeeded · "
            f"{fail_count} failed · {state} · {cls._duration_text(cost_ms)}"
        )

    @staticmethod
    def _log_step_result(result: PlanStepResult) -> None:
        """记录计划步骤的调试结果。"""
        observe(
            "plan_step.complete",
            run=result.run,
            step=result.index,
            tool=result.tool,
            ok=result.ok,
            cost_ms=result.cost_ms,
        )

    @staticmethod
    def _failure_groups(
        results: list[PlanStepResult],
    ) -> list[dict[str, typing.Any]]:
        """按步骤、工具和原因归并重复失败。"""
        groups: list[dict[str, typing.Any]] = []

        groups_by_key: dict[tuple[int, str, str], dict[str, typing.Any]] = {}

        for item in results:
            if item.ok:
                continue

            key = (item.index, item.tool, item.text)
            group = groups_by_key.get(key)

            if group is None:
                group = {
                    "step": item.index,
                    "tool": item.tool,
                    "reason": item.text,
                    "count": 0,
                    "first_run": item.run,
                    "last_run": item.run,
                }
                groups_by_key[key] = group
                groups.append(group)

            group["count"] = int(group["count"]) + 1
            group["first_run"] = min(int(group["first_run"]), item.run)
            group["last_run"] = max(int(group["last_run"]), item.run)

        return groups

    @staticmethod
    def _duration_text(cost_ms: int) -> str:
        """把毫秒耗时转换为紧凑文本。"""
        elapsed_ms = max(0, int(cost_ms or 0))
        if elapsed_ms < 1000:
            return f"{elapsed_ms}ms"
        elapsed_sec = elapsed_ms / 1000
        if elapsed_sec < 60:
            return f"{elapsed_sec:.1f}s"
        minutes, seconds = divmod(int(elapsed_sec), 60)
        return f"{minutes}m {seconds:02d}s"


def _plan_steps(plan: dict[str, typing.Any]) -> list[dict[str, typing.Any]]:
    """读取已标准化计划中的步骤列表。"""
    raw_steps = plan.get("steps")
    if not isinstance(raw_steps, list):
        return []
    return [step for step in raw_steps if isinstance(step, dict)]


if __name__ == '__main__':
    pass
