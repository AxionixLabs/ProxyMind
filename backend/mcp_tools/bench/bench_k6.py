# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import Requires
from backend.middlewares.mid_task import task_middleware
from backend.mcp_tools.bench.schemas.schema_k6 import (
    ScenarioArg,
    ScriptFileArg,
    WorkDirArg,
    VusArg,
    DurationArg,
    IterationsArg,
    EnvArg,
    TagsArg,
    SummaryExportArg,
    ExtraArgsArg
)
from backend.utilities.runtime import (
    AppContext, Idle
)
from backend.utilities.broadcast import broadcast


def bind(mcp: FastMCP, idle: Idle, ctx: AppContext) -> None:

    @mcp.tool(
        description=(
            "执行一次本地 k6 脚本压测。"
        ),
        meta={"hidden": True, "domain": "bench", "class": "k6"}
    )
    @task_middleware("k6_run_script")
    async def k6_run_script(
        script_file: ScriptFileArg,
        workdir: WorkDirArg = None,
        vus: VusArg = None,
        duration: DurationArg = None,
        iterations: IterationsArg = None,
        env: EnvArg = None,
        tags: TagsArg = None,
        summary_export: SummaryExportArg = None,
        extra_args: ExtraArgsArg = None
    ) -> CallToolResult:
        await Requires.connect_k6()

        args = {
            "script_file"    : script_file,
            "workdir"        : workdir,
            "vus"            : vus,
            "duration"       : duration,
            "iterations"     : iterations,
            "env"            : env,
            "tags"           : tags,
            "summary_export" : summary_export,
            "extra_args"     : extra_args
        }

        async def call(*_) -> dict:
            job_id = await idle.job_begin(f"{ctx.k6.agent_id}.k6_run_script", args=args)
            try:
                return await ctx.k6.run_script(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="k6_run_script",
            args=args,
            target_list=[ctx.k6],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "根据结构化压测场景自动生成临时 k6 脚本，并在本地执行压测。"
            "该工具适合直接提供 `scenario` 作为输入，不要求外部先准备脚本文件。"
            "若未传 `vus`、`duration`、`iterations`，则沿用 `scenario.options` 内的配置。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "k6"}
    )
    @task_middleware("k6_run_local")
    async def k6_run_local(
        scenario: ScenarioArg,
        vus: VusArg = None,
        duration: DurationArg = None,
        iterations: IterationsArg = None,
        env: EnvArg = None,
        tags: TagsArg = None,
        summary_export: SummaryExportArg = None,
        extra_args: ExtraArgsArg = None
    ) -> CallToolResult:
        await Requires.connect_k6()

        args = {
            "scenario"       : scenario,
            "vus"            : vus,
            "duration"       : duration,
            "iterations"     : iterations,
            "env"            : env,
            "tags"           : tags,
            "summary_export" : summary_export,
            "extra_args"     : extra_args
        }

        async def call(*_) -> dict:
            job_id = await idle.job_begin(f"{ctx.k6.agent_id}.k6_run_local", args=args)
            try:
                return await ctx.k6.run_local(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="k6_run_local",
            args=args,
            target_list=[ctx.k6],
            call=call,
            overrides=None
        )


if __name__ == '__main__':
    pass
