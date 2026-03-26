# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from pathlib import Path
from mcp import ClientSession
from mind_core.design import Design
from mind_core import authorize
from mind_nova.request import EventReport
from mind_nova import const
from .mind_static import static_looper
from .mind_stream import stream_looper

if typing.TYPE_CHECKING:
    from .mind_core import Mind


async def _run_mode(
    mind: "Mind",
    model_api: dict[str, typing.Any],
    message: str,
    mode: typing.Literal["chat", "fast", "plan"],
    runner: typing.Callable[..., typing.Awaitable[None]],
    **kwargs
) -> None:
    """模式执行包装器：在共享 MCP 会话中运行指定模式。"""

    async def function(
        session: ClientSession,
        openai_tools: list[dict[str, typing.Any]],
        tool_meta: dict[str, dict[str, typing.Any]]
    ) -> None:
        """把共享会话转交给具体模式执行器。"""
        await runner(mind, session, mode, model_api, message, openai_tools, tool_meta, **kwargs)

    return await mind.with_mcp_session(model_api, function)


async def mind_chat(mind: "Mind", model_api: dict[str, typing.Any], message: str, *_, **kwargs) -> None:
    """对话模式入口：绑定 chat 模式到流式执行器。"""
    return await _run_mode(mind, model_api, message, "chat", stream_looper, **kwargs)


async def mind_fast(mind: "Mind", model_api: dict[str, typing.Any], message: str, *_, **kwargs) -> None:
    """高速模式入口：绑定 fast 模式到流式执行器。"""
    return await _run_mode(mind, model_api, message, "fast", stream_looper, **kwargs)


async def mind_plan(mind: "Mind", model_api: dict[str, typing.Any], message: str, *_, **kwargs) -> None:
    """编排模式入口：绑定 plan 模式到静态执行器。"""
    return await _run_mode(mind, model_api, message, "plan", static_looper, **kwargs)


async def mind_loop(mind: "Mind") -> None:
    """交互式循环入口：处理命令切换、参数更新和模式调度。"""

    async def exchange(
        matcher: re.Match[str],
        types: typing.Literal["model", "apikey"]
    ) -> typing.Optional[str]:
        """解析 `/model` 与 `/apikey` 指令，并给出交互提示。"""
        if pref_name := matcher.group(1).strip() if matcher.group(1) else None:
            return pref_name

        styles: list[str] = []

        match types:
            case "model":
                styles = ["<model> (Model name or ID)"]
            case "apikey":
                styles = ["<apikey> (Provider API key)"]

        for s in styles:
            Design.console.print(f"[bold #AFC7D8]  • {s}[/]")
        return Design.console.print(f"[bold #FF5F5F]\n {types} invalid: /{types} {const.ERR}{pref_name}")

    async def function(
        session: ClientSession,
        openai_tools: list[dict[str, typing.Any]],
        tool_meta: dict[str, dict[str, typing.Any]],
    ) -> None:
        """在共享 MCP 会话中运行交互循环。"""
        pref_cfg = mind.pref.to_config()
        primary  = pref_cfg.get("primary") or {}
        model    = primary.get("model", "")
        apikey   = primary.get("apikey", "")

        metadata = mind.begin_session()

        quit_set: set[str] = {"/quit", "/q", "quit", "exit"}
        help_set: set[str] = {"/help", "/h"}
        seal_set: set[str] = {"/license", "/lic"}
        subs_set: set[str] = {"/subscription", "/sub"}

        doc = """\
            [bold]
            [bold #AFD7FF]/help, /h[/]                 指令索引（用法/示例/约定）
            [bold #5FD7AF]/license, /lic[/]            授权许可（License/特性）
            [bold #5FD7AF]/subscription, /sub[/]       订阅信息（授权状态/到期）
            [bold #FF5F5F]/quit, /q, quit, exit[/]     断开会话（安全退出）
            [bold #AFD7FF]/model <name>[/]             引擎切换（选择推理内核）
            [bold #AFD7FF]/apikey <key>[/]             凭证更新（替换访问密钥）
            [bold #FFD75F]/chat[/]                     对话模式（全域能力接入/自然语言交互）
            [bold #FFD75F]/fast[/]                     高速模式（高吞吐任务流/数据媒体直达）
            [bold #FFD75F]/plan[/]                     编排模式（结构任务拆解/确定路径执行）
            [/]"""

        re_model  = re.compile(r"^\s*/model(?:\s+(.*))?\s*$", re.IGNORECASE)
        re_apikey = re.compile(r"^\s*/apikey(?:\s+(.*))?\s*$", re.IGNORECASE)

        tag: typing.Literal["CHAT", "FAST", "PLAN"] = "CHAT"

        while not mind.task_event.is_set():
            try:
                raw = await mind.prompt_box.prompt_async(tag=tag, model=model)
            except KeyboardInterrupt:
                mind.task_event.set()
                break
            except (EOFError, UnicodeDecodeError):
                continue

            if raw.lower() in quit_set:
                mind.task_event.set()
                break

            if raw.lower() in help_set:
                Design.console.print(doc)
                continue

            if raw.lower() in seal_set:
                Design.startup_logo()
                continue

            if raw.lower() in subs_set:
                lic_file = Path(mind.src_opera_place) / const.LIC_FILE
                await authorize.verify_license(lic_file)
                continue

            if raw.lower() == "/chat":
                Design.console.print()
                tag = "CHAT"
                continue

            if raw.lower() == "/fast":
                Design.console.print()
                tag = "FAST"
                continue

            if raw.lower() == "/plan":
                Design.console.print()
                tag = "PLAN"
                continue

            if m := re_model.match(raw):
                model = await exchange(m, types="model") or model
                continue

            if m := re_apikey.match(raw):
                apikey = await exchange(m, types="apikey") or apikey
                continue

            async def guarded_with_report(
                mode: typing.Literal["chat", "fast", "plan"],
                runner: typing.Callable[..., typing.Awaitable[None]],
                *,
                anim_mode: typing.Literal["chat", "fast", "plan"]
            ) -> None:
                """为单轮交互附加事件上报和统一保护层。"""
                ev_report = EventReport(mode, metadata["cid"], metadata["sid"])
                await ev_report.open()

                try:
                    await mind.with_mcp_guard(
                        runner,
                        mode=mode,
                        anim_mode=anim_mode,
                        session=session,
                        model_api=model_api,
                        message=raw,
                        openai_tools=openai_tools,
                        tool_meta=tool_meta,
                        metadata=metadata,
                        ev_report=ev_report
                    )
                finally:
                    await ev_report.flush()
                    await ev_report.close()

            if tag == "CHAT":
                await guarded_with_report("chat", mind.stream_looper, anim_mode="chat")
            elif tag == "FAST":
                await guarded_with_report("fast", mind.stream_looper, anim_mode="fast")
            else:
                await guarded_with_report("plan", mind.static_looper, anim_mode="plan")

    model_api = mind.pref.to_config()
    return await mind.with_mcp_session(model_api, function)


if __name__ == '__main__':
    pass
