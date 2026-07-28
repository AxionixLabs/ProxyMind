# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
from dataclasses import dataclass
from engine.enhance import exchange_arguments
from engine.observability import observe
from mind_app.client_tools.planning import normalize_plan_arguments
from mind_app.mcp.contracts import McpSessionLike
from mind_app.mcp.tool_result import normalize_call_tool_result
from mind_app.mcp.tool_store import has_tool
from mind_app.runtime.execution import (
    ToolInvocation,
    TurnContext
)
from .execution_policy import (
    is_execution_ignored,
    validate_execution_policy
)
from .router import execute_tool

FAILURE_PREVIEW_LIMIT = 8
RESULT_TEXT_LIMIT     = 800


@dataclass(slots=True)
class PlanStepResult:
    """描述计划中单个步骤的执行结果。"""
    run: int
    index: int
    tool: str
    ok: bool
    text: str
    cost_ms: int = 0


@dataclass(slots=True)
class PlanExecutionReport:
    """汇总计划执行结果并提供展示与回传字段。"""
    ok: bool
    text: str
    data: dict[str, typing.Any]
    cost_ms: int
    results: list[PlanStepResult]

    @property
    def fields(self) -> dict[str, typing.Any]:
        """返回标准工具结果字段。"""
        return {
            "ok"          : self.ok,
            "tool"        : "plan_steps",
            "text"        : self.text,
            "attachments" : [],
            "data"        : self.data
        }


class StepPlanExecutor:
    """执行模型通过 plan_steps 提交的本地工具计划。"""

    def __init__(
        self,
        *,
        session: McpSessionLike,
        tools: list[dict[str, typing.Any]],
        report: typing.Any,
        turn_context: TurnContext
    ) -> None:
        self.session      = session
        self.tools        = tools
        self.report       = report
        self.turn_context = turn_context

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
        loops        = int(plan.get("loops") or 1)
        stop_on_fail = bool(plan.get("stop_on_fail", True))
        steps        = _plan_steps(plan)

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
        name          = str(step.get("tool") or "").strip()
        raw_arguments = step.get("args")
        arguments     = dict(raw_arguments) if isinstance(raw_arguments, dict) else {}
        execution     = step.get("execution") if isinstance(step.get("execution"), dict) else None

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

        policy_result = validate_execution_policy(
            name=name,
            execution=execution
        )
        if policy_result:
            return await self._failure(
                run_index,
                step_index,
                name,
                self._policy_failure_text(policy_result)
            )

        try:
            exchanged_args = exchange_arguments(name, arguments, self.report)
            if not isinstance(exchanged_args, dict):
                raise TypeError(f"invalid arguments for {name}")

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
                arguments=exchanged_args,
                execution=execution,
            )

            result = await execute_tool(
                self.session,
                tools=self.tools,
                invocation=invocation,
            )

            step_result = PlanStepResult(
                run=run_index,
                index=step_index,
                tool=name,
                ok=result.isError is not True,
                text=self._result_text(result),
                cost_ms=int((time.perf_counter() - started_at) * 1000)
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
        text: str
    ) -> PlanStepResult:
        """记录计划步骤失败。"""
        step_result = PlanStepResult(
            run=run_index,
            index=step_index,
            tool=name,
            ok=False,
            text=text
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
        """根据计划和步骤结果生成有界汇总。"""
        steps          = _plan_steps(plan)
        requested_runs = int(plan.get("loops") or 1)
        step_count     = len(steps)

        calls_by_run: dict[int, int] = {}

        results_by_step: dict[int, list[PlanStepResult]] = {
            index: [] for index in range(1, step_count + 1)
        }

        failures: list[dict[str, typing.Any]] = []

        ok_count: int = 0

        for item in results:
            calls_by_run[item.run] = calls_by_run.get(item.run, 0) + 1
            results_by_step.setdefault(item.index, []).append(item)
            if item.ok:
                ok_count += 1
            else:
                failures.append({
                    "run": item.run,
                    "step": item.index,
                    "tool": item.tool,
                    "text": cls._bounded_text(item.text),
                    "cost_ms": item.cost_ms
                })

        fail_count = len(results) - ok_count

        completed_runs = sum(
            1 for count in calls_by_run.values()
            if 0 < step_count <= count
        )

        stopped = bool(fail_count and plan.get("stop_on_fail", True))
        ok      = not errors and fail_count == 0

        step_results: list[dict[str, typing.Any]] = []

        for step_index, step in enumerate(steps, start=1):
            items = results_by_step[step_index]
            last  = items[-1] if items else None

            step_results.append({
                "index"      : step_index,
                "tool"       : str(step.get("tool") or ""),
                "call_count" : len(items),
                "ok_count"   : sum(1 for item in items if item.ok),
                "fail_count" : sum(1 for item in items if not item.ok),
                "last_text"  : cls._bounded_text(last.text if last else "")
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
            "requested_runs"   : requested_runs,
            "completed_runs"   : completed_runs,
            "attempted_runs"   : max(calls_by_run, default=0),
            "steps_per_run"    : step_count,
            "call_count"       : len(results),
            "ok_count"         : ok_count,
            "fail_count"       : fail_count,
            "stopped"          : stopped,
            "elapsed_ms"       : cost_ms,
            "step_results"     : step_results,
            "failures"         : failures[:FAILURE_PREVIEW_LIMIT],
            "omitted_failures" : max(0, len(failures) - FAILURE_PREVIEW_LIMIT),
            "errors"           : errors[:FAILURE_PREVIEW_LIMIT]
        }
        return PlanExecutionReport(
            ok=ok,
            text=text,
            data=data,
            cost_ms=cost_ms,
            results=results
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
            return f"plan rejected · {'; '.join(errors[:FAILURE_PREVIEW_LIMIT])}"
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
    def _result_text(result: typing.Any) -> str:
        """从 MCP 工具结果中读取文本摘要。"""
        return normalize_call_tool_result(result).display_text

    @staticmethod
    def _bounded_text(value: typing.Any) -> str:
        """把结果文本压缩为有界单行摘要。"""
        text = " ".join(str(value or "").split())
        if len(text) <= RESULT_TEXT_LIMIT:
            return text
        return f"{text[:RESULT_TEXT_LIMIT - 3]}..."

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

    @staticmethod
    def _policy_failure_text(policy_result: dict[str, typing.Any]) -> str:
        """返回执行策略拒绝对应的失败文本。"""
        if is_execution_ignored(policy_result):
            return str(policy_result.get("reason") or "execution ignored")
        return str(policy_result.get("error") or "execution denied")

def _plan_steps(plan: dict[str, typing.Any]) -> list[dict[str, typing.Any]]:
    """读取已标准化计划中的步骤列表。"""
    raw_steps = plan.get("steps")
    if not isinstance(raw_steps, list):
        return []
    return [step for step in raw_steps if isinstance(step, dict)]


if __name__ == '__main__':
    pass
