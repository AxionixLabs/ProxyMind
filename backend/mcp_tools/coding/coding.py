# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.server.fastmcp import Context
from mcp.types import CallToolResult
from backend.middlewares.mid_task import task_middleware
from backend.mcp_hub.hub_manage import Requires
from backend.mcp_tools.coding.schemas import (
    CodingPromptArg,
    CodingProfileArg,
    CodingModelArg,
    CodingSandboxArg,
    CodingSkipGitRepoCheckArg,
    CodingEphemeralArg,
    CodingJsonOutputArg,
    CodingTimeoutSecArg,
    CodingExtraArgsArg
)
from backend.utilities.broadcast import broadcast
from backend.utilities.runtime import (
    AppContext, Idle
)


def bind(mcp: FastMCP, idle: Idle, ctx: AppContext) -> None:

    @mcp.tool(
        description=(
            "执行一次 codex 工作区任务并等待结束后返回最终结果。"
            " 当前固定使用 `codex exec` 非交互模式启动任务，并持续消费打印 CLI 输出。"
            " 工具调用期间会持续上报 CLI 输出进度，返回时包含 exit_code、日志摘要和最终状态。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "codex"}
    )
    @task_middleware("coding")
    async def coding(
        prompt: CodingPromptArg,
        profile: CodingProfileArg = None,
        model: CodingModelArg = None,
        sandbox: CodingSandboxArg = "workspace-write",
        skip_git_repo_check: CodingSkipGitRepoCheckArg = True,
        ephemeral: CodingEphemeralArg = False,
        json_output: CodingJsonOutputArg = False,
        timeout_sec: CodingTimeoutSecArg = 300,
        extra_args: CodingExtraArgsArg = None,
        context: Context = None
    ) -> CallToolResult:

        await Requires.connect_codex()

        args = {
            "prompt"              : prompt,
            "profile"             : profile,
            "model"               : model,
            "sandbox"             : sandbox,
            "skip_git_repo_check" : skip_git_repo_check,
            "ephemeral"           : ephemeral,
            "json_output"         : json_output,
            "timeout_sec"         : timeout_sec,
            "extra_args"          : extra_args
        }

        async def call(*_) -> dict:
            job_id = await idle.job_begin(f"{ctx.coding.agent_id}.start", args=args)
            try:
                async def output_callback(source: str, text: str, seq: int) -> None:
                    if context is None:
                        return None
                    await context.report_progress(
                        float(seq), None, f"{source}: {text}"
                    )

                start_result = await ctx.coding.start(
                    **args, output_callback=output_callback
                )
                start_data = start_result.get("data") if isinstance(start_result, dict) else {}
                if not bool((start_data or {}).get("ok")):
                    return start_result
                final_result = await ctx.coding.wait(timeout_sec=timeout_sec)

                return ctx.coding.with_flow_details(
                    start_result=start_result,
                    final_result=final_result
                )

            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="coding",
            args=args,
            target_list=[ctx.coding],
            call=call,
            overrides=None
        )


if __name__ == '__main__':
    pass
