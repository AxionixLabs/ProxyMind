# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from engine.enhance import exchange_arguments
from mind_app.client_tools.planning import normalize_plan_arguments
from mind_app.mcp import McpSessionLike
from mind_app.mcp.tool_store import meta_for_tool
from mind_app.stream_ui import StreamUI
from mind_nova import craft
from .display import (
    show_tool_result,
    show_tool_start
)
from .execution_policy import (
    is_execution_ignored,
    should_pass_execution_to_tool,
    validate_execution_policy
)
from .run import run_tool_step


@dataclass(slots=True)
class PlanStepResult:
    """描述计划中单个步骤的执行结果。"""
    run: int
    index: int
    tool: str
    ok: bool
    text: str
    cost_ms: int = 0


class StepPlanExecutor:
    """执行模型通过 plan_steps 提交的本地工具计划。"""

    def __init__(
        self,
        *,
        session: McpSessionLike,
        stream_ui: StreamUI,
        tools: list[dict[str, typing.Any]],
        mode: str,
        pref_config: dict[str, typing.Any],
        metadata: dict[str, typing.Any],
        report: typing.Any
    ) -> None:
        self.session     = session
        self.stream_ui   = stream_ui
        self.tools       = tools
        self.mode        = mode
        self.pref_config = pref_config
        self.metadata    = metadata
        self.report      = report

    async def execute_tool_call(
        self,
        *,
        arguments: dict[str, typing.Any]
    ) -> list[PlanStepResult]:
        """执行一次 plan_steps 工具调用。"""
        ok, plan, _ = normalize_plan_arguments(arguments)
        if not ok:
            return []

        return await self.execute_plan(plan)

    async def execute_plan(
        self,
        plan: dict[str, typing.Any]
    ) -> list[PlanStepResult]:
        """按计划声明顺序执行所有步骤。"""
        loops = int(plan.get("loops") or 1)
        stop_on_fail = bool(plan.get("stop_on_fail", True))
        steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []

        results: list[PlanStepResult] = []
        stopped = False
        await self.stream_ui.begin_loop_status(
            self._loop_summary(
                run_index=1,
                total_runs=loops,
                step_index=0,
                total_steps=len(steps),
                tool=""
            )
        )

        for run_index in range(1, loops + 1):
            if stopped:
                break

            for step_index, step in enumerate(steps, start=1):
                await self.stream_ui.update_loop_status_summary(
                    self._loop_summary(
                        run_index=run_index,
                        total_runs=loops,
                        step_index=step_index,
                        total_steps=len(steps),
                        tool=str(step.get("tool") or "").strip()
                    )
                )
                result = await self._execute_step(
                    run_index,
                    step_index,
                    step,
                    total_runs=loops,
                    total_steps=len(steps)
                )
                results.append(result)

                if stop_on_fail and not result.ok:
                    stopped = True
                    break

        await self.stream_ui.update_loop_status_summary(
            self._done_summary(results)
        )
        return results

    async def _execute_step(
        self,
        run_index: int,
        step_index: int,
        step: dict[str, typing.Any],
        *,
        total_runs: int,
        total_steps: int
    ) -> PlanStepResult:
        """执行计划中的单个步骤。"""
        name = str(step.get("tool") or "").strip()
        arguments = step.get("args") if isinstance(step.get("args"), dict) else {}
        arguments = dict(arguments)
        step_meta = step.get("meta") if isinstance(step.get("meta"), dict) else None
        step_execution = step.get("execution") if isinstance(step.get("execution"), dict) else None
        effective_meta = {**meta_for_tool(self.tools, name), **(step_meta or {})} or None

        policy_result = validate_execution_policy(
            name=name,
            arguments=arguments,
            execution=step_execution
        )
        if policy_result:
            result = self._policy_failure(run_index, step_index, name, policy_result)
            await self.stream_ui.update_loop_status_summary(
                self._loop_summary(
                    run_index=run_index,
                    total_runs=total_runs,
                    step_index=step_index,
                    total_steps=total_steps,
                    tool=name,
                    done=True
                )
            )
            return result

        call_id = craft.short_uid()
        await show_tool_start(
            self.stream_ui,
            name,
            arguments,
            call_id=call_id
        )

        try:
            exchanged_args = exchange_arguments(name, arguments, self.report)
            if should_pass_execution_to_tool(name, step_execution):
                exchanged_args = {**exchanged_args, "execution": step_execution}

            tool_run = await run_tool_step(
                self.session,
                stream_ui=self.stream_ui,
                tools=self.tools,
                name=name,
                arguments=exchanged_args,
                meta=effective_meta,
                mode=self.mode,
                pref_config=self.pref_config,
                metadata=self.metadata,
                enable_progress_notify=True,
                stream_callback=lambda text: self.stream_ui.feed(
                    text, display=StreamUI.BLOCK
                )
            )

            await show_tool_result(
                self.stream_ui,
                name,
                exchanged_args,
                tool_run
            )
            await self.stream_ui.update_loop_status_summary(
                self._loop_summary(
                    run_index=run_index,
                    total_runs=total_runs,
                    step_index=step_index,
                    total_steps=total_steps,
                    tool=name,
                    done=True
                )
            )

            return PlanStepResult(
                run=run_index,
                index=step_index,
                tool=name,
                ok=tool_run.ok,
                text=tool_run.text,
                cost_ms=tool_run.cost_ms
            )
        except Exception as exc:
            text = f"{type(exc).__name__}: {exc}"
            await self.stream_ui.update_loop_status_summary(
                self._loop_summary(
                    run_index=run_index,
                    total_runs=total_runs,
                    step_index=step_index,
                    total_steps=total_steps,
                    tool=name,
                    done=True
                )
            )
            return PlanStepResult(
                run=run_index,
                index=step_index,
                tool=name,
                ok=False,
                text=text
            )

    @staticmethod
    def _loop_summary(
        *,
        run_index: int,
        total_runs: int,
        step_index: int,
        total_steps: int,
        tool: str,
        done: bool = False
    ) -> str:
        """生成循环步骤状态摘要。"""
        parts = [
            f"run {max(1, run_index)}/{max(1, total_runs)}",
            f"step {max(0, step_index)}/{max(0, total_steps)}"
        ]
        clean_tool = str(tool or "").strip()
        if clean_tool:
            parts.append(clean_tool)
        if done:
            parts.append("done")
        return " · ".join(parts)

    @staticmethod
    def _done_summary(results: list[PlanStepResult]) -> str:
        """生成计划完成状态摘要。"""
        ok_count = sum(1 for item in results if item.ok)
        fail_count = sum(1 for item in results if not item.ok)
        return f"done · ok {ok_count} · failed {fail_count}"

    @staticmethod
    def _policy_failure(
        run_index: int,
        step_index: int,
        name: str,
        policy_result: dict[str, typing.Any]
    ) -> PlanStepResult:
        """把执行策略拒绝转换为步骤失败。"""
        if is_execution_ignored(policy_result):
            text = str(policy_result.get("reason") or "execution ignored")
        else:
            text = str(policy_result.get("error") or "execution denied")
        return PlanStepResult(
            run=run_index,
            index=step_index,
            tool=name,
            ok=False,
            text=text
        )


if __name__ == '__main__':
    pass
