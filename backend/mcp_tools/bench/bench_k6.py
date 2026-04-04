# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import Requires
from backend.middlewares.mid_task import task_middleware
from backend.mcp_tools.bench.schemas.schema_k6 import (
    ScriptTextArg,
    ScriptFileArg,
    ScriptNameArg,
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
from backend.utilities.validation import marked
from backend.utilities import const


def bind(mcp: FastMCP, idle: Idle, ctx: AppContext) -> None:

    @mcp.tool(
        description=(
            "执行一次压测。"
        ),
        meta={"hidden": False, "domain": "bench", "class": "k6"}
    )
    @task_middleware("perf_run")
    async def perf_run(
        script_text: ScriptTextArg = None,
        script_file: ScriptFileArg = None,
        script_name: ScriptNameArg = None,
        workdir: WorkDirArg = None,
        vus: VusArg = None,
        duration: DurationArg = None,
        iterations: IterationsArg = None,
        env: EnvArg = None,
        tags: TagsArg = None,
        summary_export: SummaryExportArg = None,
        extra_args: ExtraArgsArg = None
    ) -> CallToolResult:
        args = {
            "script_text"    : script_text,
            "script_file"    : script_file,
            "script_name"    : script_name,
            "workdir"        : workdir,
            "vus"            : vus,
            "duration"       : duration,
            "iterations"     : iterations,
            "env"            : env,
            "tags"           : tags,
            "summary_export" : summary_export,
            "extra_args"     : extra_args
        }

        execution_inputs = [
            ("script_text", bool(str(script_text or "").strip())),
            ("script_file", bool(str(script_file or "").strip()))
        ]
        selected = [name for name, ok in execution_inputs if ok]

        if len(selected) != 1:
            raise marked.fail_tip(
                "script_text 和 script_file 必须且只能提供一个。",
                code=const.CODE_EXC,
                hint=const.HINT_HLT,
                field="script_text|script_file",
                expect="exactly_one",
                got=",".join(selected) if selected else "none"
            )

        async def call(*_) -> dict:
            job_id = await idle.job_begin(f"{ctx.k6.agent_id}.perf_run", args=args)
            try:
                if typing.cast(str, script_file or "").strip():
                    await Requires.connect_k6()
                    return await ctx.k6.run_script(
                        script_file=typing.cast(str, script_file),
                        workdir=workdir,
                        vus=vus,
                        duration=duration,
                        iterations=iterations,
                        env=env,
                        tags=tags,
                        summary_export=summary_export,
                        extra_args=extra_args
                    )

                await Requires.connect_k6()
                return await ctx.k6.run_local(
                    script_text=typing.cast(str, script_text),
                    script_name=script_name,
                    vus=vus,
                    duration=duration,
                    iterations=iterations,
                    env=env,
                    tags=tags,
                    summary_export=summary_export,
                    extra_args=extra_args
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="perf_run",
            args=args,
            target_list=[ctx.k6],
            call=call,
            overrides=None
        )


if __name__ == '__main__':
    pass
