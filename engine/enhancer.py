#  _____       _
# | ____|_ __ | |__   __ _ _ __   ___ ___ _ __
# |  _| | '_ \| '_ \ / _` | '_ \ / __/ _ \ '__|
# | |___| | | | | | | (_| | | | | (_|  __/ |
# |_____|_| |_|_| |_|\__,_|_| |_|\___\___|_|
#

import typing
import asyncio
from pathlib import Path
from loguru import logger
from mcp import ClientSession
from mcp.types import CallToolResult
from engine.tinker import StreamTyperLogger
from mindnova.report import Report
from mindnova import request


class Enhancer(object):
    """通用工具结果增强"""

    def __init__(self, session: ClientSession, model_api: dict[str, typing.Any]):
        self.session   = session
        self.model_api = model_api

    @staticmethod
    def exchange(
        name: str,
        src_arguments: dict[str, typing.Any],
        report: Report
    ) -> typing.Union[dict[str, typing.Any], str]:
        """根据操作名称决定是否增强 arguments，返回增强后的参数或原始参数。"""
        if name.startswith("ffmpeg_") and name != "ffmpeg_probe_video":
            if src_arguments.get("output_dir"):
                return src_arguments
            return src_arguments | {"output_dir": report.toolkit_path}

        match name:
            case "screenshot":
                return src_arguments | {"local": str(Path(report.cap_path) / "screenshot.png")}
            case _:
                return src_arguments

    @staticmethod
    def fields(result: CallToolResult) -> typing.Union[dict[str, typing.Any], str]:
        """Fields"""
        return sc if (sc := result.structuredContent) else result.content[0].text

    async def enhance(
        self,
        name: str,
        arguments: dict[str, typing.Any],
        result: CallToolResult,
        ok: bool,
        slog: typing.Optional[StreamTyperLogger] = None
    ) -> typing.Union[str, dict[str, typing.Any]]:
        """Enhance"""

        fields = self.fields(result)

        if not ok: return fields

        match name:
            case "ffmpeg_extract_snapshot":
                return await self.__ffmpeg_frame(result)
            case "ffmpeg_extract_keyframes":
                return await self.__ffmpeg_frame(result)
            case "ffmpeg_extract_scene":
                return await self.__ffmpeg_frame(result)
            case "screenshot":
                return await self.__screenshot(result)
            case "heal_element":
                return await self.__heal_element(arguments, result, slog)
            case "loop_steps":
                return await self.__loop_steps(result, slog)
            case _:
                return fields

    async def __ffmpeg_frame(self, result: CallToolResult) -> dict:
        fields = self.fields(result)
        attachments: list[dict[str, typing.Any]] = []

        if not (results := fields.get("data", {}).get("results")):
            return {
                "text"        : "未获取到视频帧结果",
                "attachments" : attachments,
                "data"        : {"ok": False}
            }

        per_device: dict[str, typing.Any] = {}

        for element in results:
            agent_id = element.get("agent_id", "unknown")

            if not element.get("ok"):
                per_device[agent_id] = {"ok": False, "error": element.get("text")}
                continue

            # 获取附件
            local_attachments = element.get("attachments", [])

            for a in local_attachments:
                # 如果附件类型不对，跳过
                if not isinstance(a, dict):
                    continue

                local = a.get("local")
                if not local:
                    # 跳过没有 local 的附件，或已经是 URL
                    if a.get("url"):
                        attachments.append(a)
                    continue

                # 上传附件并获取 URL
                try:
                    up = await request.upload_file_stream(local, agent_id)
                    url = up.get("url")

                    if not url:
                        per_device[agent_id] = {"ok": False, "error": f"upload returned no url: {up!r}"}
                        continue

                    # 更新附件
                    attachments.append({
                        "kind"      : "image" if a.get("kind") == "image" else "file",
                        "url"       : url,
                        "agent_id"  : agent_id,
                        "filename"  : up.get("filename", a.get("filename")),
                        "mime_type" : up.get("mime_type", a.get("mime_type"))
                    })

                    per_device[agent_id] = {
                        "ok"       : True,
                        "local"    : local,
                        "url"      : url,
                        "r2_key"   : up.get("key"),
                        "filename" : up.get("filename", a.get("filename")),
                        "mime_type": up.get("mime_type", a.get("mime_type"))
                    }

                except Exception as e:
                    # 如果上传失败，记录错误
                    per_device[agent_id] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                    continue

        # 判断是否所有上传都成功
        ok = all(v.get("ok") for v in per_device.values()) if per_device else False

        # 组织返回结构
        fields["attachments"] = attachments
        fields["data"] = {
            "ok"         : ok,
            "upload_ok"  : ok,
            "per_device" : per_device
        }
        fields["text"] = f"upload {'ok' if ok else 'done'} attachments={len(attachments)}"

        return fields

    async def __screenshot(self, result: CallToolResult) -> dict:
        fields = self.fields(result)
        attachments: list[dict[str, typing.Any]] = []

        if not (results := fields.get("data", {}).get("results")):
            return {
                "text"        : "未获取到截图结果",
                "attachments" : attachments,
                "data"        : {"ok": False, "fields": fields}
            }

        per_device: dict[str, typing.Any] = {}

        for element in results:
            agent_id = element.get("agent_id", "unknown")

            if not element.get("ok"):
                per_device[agent_id] = {"ok": False, "error": element.get("text")}
                continue

            if not (local := element.get("text")) or not isinstance(local, str):
                per_device[agent_id] = {"ok": False, "error": "missing local screenshot path"}
                continue

            try:
                up = await request.upload_file_stream(local, agent_id, "screenshots")
                if not (url := up.get("url")):
                    per_device[agent_id] = {"ok": False, "error": f"upload returned no url: {up!r}"}
                    continue

                attachments.append({"kind": "image", "url": url, "agent_id": agent_id})
                per_device[agent_id] = {
                    "ok"        : True,
                    "local"     : local,
                    "url"       : url,
                    "r2_key"    : (up or {}).get("key"),
                    "filename"  : (up or {}).get("filename"),
                    "mime_type" : (up or {}).get("mime_type")
                }
            except Exception as e:
                per_device[agent_id] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                return {
                    "text"        : f"屏幕截图上传异常：{type(e).__name__}: {e}",
                    "attachments" : attachments,
                    "data"        : {"ok": False, "per_device": per_device}
                }

        ok = all(v.get("ok") for v in per_device.values()) if per_device else False
        return {
            "text"        : "屏幕截图上传成功" if ok else "屏幕截图上传完成（存在失败）",
            "attachments" : attachments,
            "data"        : {"ok": ok, "per_device": per_device}
        }

    async def __heal_element(
        self,
        arguments: dict[str, typing.Any],
        result: CallToolResult,
        slog: typing.Optional[StreamTyperLogger] = None
    ) -> typing.Optional[dict[str, typing.Any]]:

        fields = self.fields(result)
        attachments: list[dict[str, str]] = []

        if not (results := fields.get("data", {}).get("results")):
            return {
                "text"        : "未获取到设备结果",
                "attachments" : attachments,
                "data"        : {"ok": False, "fields": fields}
            }

        per_device: dict[str, dict[str, typing.Any]] = {}

        for element in results:
            data   = element["data"]
            serial = data.pop("serial", "unknown")

            async for heal in request.stream_heal(self.model_api, **data, slog=slog):
                if heal.get("type") == "error":
                    per_device[serial] = {"ok": False, "error": heal["content"]}
                    continue

                if not (smart := heal.get("smart")):
                    continue

                reason = smart.get("details", {}).get("reason", "unknown")

                if serial not in per_device:
                    locator = {
                        "by": smart["new_selector"]["primary"]["by"],
                        "value": smart["new_selector"]["primary"]["value"]
                    }
                    per_device[serial] = {"ok": True, "locator": locator, "smart": reason}

                if slog: await slog.feed(reason)
                else: logger.debug(reason)

        matrix = {k: v["locator"] for k, v in per_device.items() if v.get("locator")}

        if not matrix:
            return {
                "text"        : "元素定位失败",
                "attachments" : attachments,
                "data"        : {"ok": False, "per_device": per_device}
            }

        if not arguments.get("should_click"):
            ok = all(v.get("ok") for v in per_device.values()) if per_device else False
            return {
                "text"        : "元素定位成功" if ok else "元素定位完成（存在失败）",
                "attachments" : attachments,
                "data"        : {"ok": ok, "per_device": per_device}
            }

        wait_s = float(arguments.get("wait") or 0)
        if wait_s > 0: await asyncio.sleep(wait_s)

        r = await self.session.call_tool("click", {"matrix": matrix})
        f = self.fields(r)

        if r.isError:
            return {
                "text"        : "点击失败",
                "attachments" : attachments,
                "data"        : {"ok": False, "per_device": per_device, "fields": f}
            }

        ok = all(v.get("ok") for v in per_device.values()) if per_device else False
        return {
            "text"        : "元素定位成功，并已点击" if ok else "元素定位完成并已点击（存在失败）",
            "attachments" : attachments,
            "data"        : {"ok": ok, "per_device": per_device, "fields": f}
        }

    async def __loop_steps(
        self,
        result: CallToolResult,
        slog: typing.Optional[StreamTyperLogger] = None
    ) -> dict[str, typing.Any]:

        async def say(line: str) -> None:
            if slog:
                return await slog.feed(f"{line}\n")

        fields = self.fields(result)

        results: list[
            dict[str, typing.Any]
        ] = fields.get("data", {}).get("results", []) if isinstance(fields, dict) else []

        payload: typing.Optional[dict[str, typing.Any]] = None

        if not results or not isinstance(results, list):
            return {
                "text"        : "loop_steps: missing structured results",
                "attachments" : [],
                "data"        : {"ok": False},
                "logs"        : []
            }

        for element in results:
            if isinstance(element, dict) and isinstance(data := element.get("data"), dict):
                payload = data
                break

        if not isinstance(payload, dict):
            return {
                "text"        : "loop_steps: missing payload in results[].data",
                "attachments" : [],
                "data"        : {"ok": False},
                "logs"        : []
            }

        if not payload.get("ok", False):
            errs = payload.get("errors") or []
            text = (
                f"loop_steps: invalid declaration\n"
                f"errors={'; '.join([str(x) for x in errs[:8]])}" if errs else ""
            )
            return {
                "text"        : text,
                "attachments" : [],
                "data"        : payload | {"executed": False},
                "logs"        : []
            }

        loops = int(payload.get("loops", 1))
        steps = payload.get("steps", [])
        stop_on_fail = bool(payload.get("stop_on_fail", True))

        if not steps or not isinstance(steps, list):
            return {
                "text"        : "loop_steps: empty steps",
                "attachments" : [],
                "data"        : payload | {"executed": False},
                "logs"        : []
            }

        if any(isinstance(step, dict) and (step.get("tool") == "loop_steps") for step in steps):
            return {
                "text"        : "loop_steps: nested loop_steps forbidden (runner guard)",
                "attachments" : [],
                "data"        : payload | {"executed": False},
                "logs"        : []
            }

        attachments: list[dict[str, typing.Any]] = []
        runs: list[dict[str, typing.Any]] = []

        await say(
            f"loop_steps: begin loops={loops} steps={len(steps)} stop_on_fail={stop_on_fail}"
        )

        for r in range(loops):
            await say(f"loop_steps: round {r + 1}/{loops}")
            round_ok = True
            round_steps: list[dict[str, typing.Any]] = []

            for i, st in enumerate(steps):
                tool = (st.get("tool") or "").strip()
                args = st.get("args") if isinstance(st.get("args"), dict) else {}

                await say(f"loop_steps:  step {i + 1}/{len(steps)} tool={tool}")

                tool_res = await self.session.call_tool(tool, args)
                ok = (not tool_res.isError)

                step_fields = self.fields(tool_res)  # 宏模式：不调用 enhance

                if isinstance(step_fields, dict):
                    atts = step_fields.get("attachments")
                    if atts and isinstance(atts, list):
                        attachments.extend(atts)

                round_steps.append({
                    "ok": ok, "tool": tool, "args": args, "fields": step_fields
                })

                if not ok:
                    round_ok = False
                    await say(f"loop_steps:  step failed tool={tool}")
                    if stop_on_fail: break

                await say(
                    f"loop_steps:  step {i + 1}/{len(steps)} tool={tool} {step_fields.get('text')}"
                )

            runs.append({"round": r + 1, "ok": round_ok, "steps": round_steps})
            if stop_on_fail and not round_ok:
                await say(f"loop_steps: stop (round {r + 1} failed)")
                break

        final_ok = bool(runs) and all(x.get("ok") for x in runs)

        brief = [
            f"tool=loop_steps ok={final_ok} rounds={len(runs)}/{loops} stop_on_fail={stop_on_fail}"
        ]
        for run in runs:
            if run.get("ok"): continue
            for step in run.get("steps", []):
                if not step.get("ok"):
                    brief.append(
                        f"fail round={run['round']} tool={step.get('tool')}"
                    )

        return {
            "text"        : "\n".join(brief),
            "attachments" : attachments,
            "data": {
                "ok"           : final_ok,
                "executed"     : True,
                "loops"        : loops,
                "steps"        : steps,
                "stop_on_fail" : stop_on_fail,
                "runs"         : runs
            },
            "logs": []
        }


if __name__ == '__main__':
    pass
