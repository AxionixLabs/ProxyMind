# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.server.fastmcp import Context
from mcp.types import CallToolResult
from backend.middlewares.mid_task import task_middleware
from backend.mcp_hub.hub_manage import Requires
from backend.mcp_tools.coding.schemas import (
    CodingProviderArg,
    CodingPromptArg,
    CodingWorkDirArg,
    CodingProfileArg,
    CodingModelArg,
    CodingSandboxArg,
    CodingFullAutoArg,
    CodingSkipGitRepoCheckArg,
    CodingEphemeralArg,
    CodingJsonOutputArg,
    CodingTimeoutSecArg,
    CodingExtraArgsArg
)
from backend.utilities.broadcast import broadcast
from backend.utilities.runtime import (
    AppContext,
    Idle
)


def bind(mcp: FastMCP, idle: Idle, ctx: AppContext) -> None:

    @mcp.tool(
        description=(
            "启动一次 provider 驱动的工作区任务会话。"
            " 当前仅支持 `codex exec` 非交互模式，会持续消费并打印 CLI 输出。"
            " 该工具只负责拉起任务并立即返回；最终结果请用 `coding_wait` 收束。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "session"}
    )
    @task_middleware("coding_start")
    async def coding_start(
        prompt: CodingPromptArg,
        provider: CodingProviderArg = "codex",
        workdir: CodingWorkDirArg = None,
        profile: CodingProfileArg = None,
        model: CodingModelArg = None,
        sandbox: CodingSandboxArg = "workspace-write",
        full_auto: CodingFullAutoArg = True,
        skip_git_repo_check: CodingSkipGitRepoCheckArg = True,
        ephemeral: CodingEphemeralArg = False,
        json_output: CodingJsonOutputArg = False,
        timeout_sec: CodingTimeoutSecArg = 300,
        extra_args: CodingExtraArgsArg = None,
        context: Context = None
    ) -> CallToolResult:

        await Requires.connect_codex()

        args = {
            "provider"            : provider,
            "prompt"              : prompt,
            "workdir"             : workdir,
            "profile"             : profile,
            "model"               : model,
            "sandbox"             : sandbox,
            "full_auto"           : full_auto,
            "skip_git_repo_check" : skip_git_repo_check,
            "ephemeral"           : ephemeral,
            "json_output"         : json_output,
            "timeout_sec"         : timeout_sec,
            "extra_args"          : extra_args,
        }

        async def call(*_) -> dict:
            job_id = await idle.job_begin(f"{ctx.coding.agent_id}.start", args=args)
            try:
                async def output_callback(source: str, text: str, seq: int) -> None:
                    if context is None:
                        return None
                    await context.report_progress(
                        float(seq),
                        None,
                        f"{source}: {text}"
                    )

                return await ctx.coding.start(
                    **args,
                    output_callback=output_callback
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="coding_start",
            args=args,
            target_list=[ctx.coding],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "查询当前 provider 任务会话状态。"
            " 若存在运行中的会话，返回 provider、进程信息、工作目录和最近输出摘要。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "session"}
    )
    @task_middleware("coding_status")
    async def coding_status() -> CallToolResult:

        async def call(*_) -> dict:
            job_id = await idle.job_begin(f"{ctx.coding.agent_id}.snapshot", args={})
            try:
                snapshot = await ctx.coding.snapshot()
                return {
                    "text"        : "Coding 状态已返回。",
                    "attachments" : [],
                    "data"        : snapshot.get(ctx.coding.agent_id, {}),
                    "logs"        : []
                }
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="coding_status",
            args={},
            target_list=[ctx.coding],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "等待当前运行中的 provider 任务会话结束并返回最终结果。"
            " 若当前没有活跃会话，则返回当前状态摘要。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "session"}
    )
    @task_middleware("coding_stop")
    async def coding_stop() -> CallToolResult:

        async def call(*_) -> dict:
            job_id = await idle.job_begin(f"{ctx.coding.agent_id}.shutdown", args={})
            try:
                return await ctx.coding.shutdown(reason="tool_stop")
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="coding_stop",
            args={},
            target_list=[ctx.coding],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "等待当前运行中的编码 provider 会话结束并返回最终结果。"
            " 若当前没有活跃会话，则返回当前状态摘要。"
        ),
        meta={"hidden": False, "domain": "coding", "class": "provider"}
    )
    @task_middleware("coding_wait")
    async def coding_wait(
        timeout_sec: CodingTimeoutSecArg = None
    ) -> CallToolResult:

        async def call(*_) -> dict:
            job_id = await idle.job_begin(
                f"{ctx.coding.agent_id}.wait",
                args={"timeout_sec": timeout_sec}
            )
            try:
                return await ctx.coding.wait(timeout_sec=timeout_sec)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="coding_wait",
            args={"timeout_sec": timeout_sec},
            target_list=[ctx.coding],
            call=call,
            overrides=None
        )


if __name__ == '__main__':
    pass
