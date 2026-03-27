# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import typing
import asyncio
from pathlib import Path
from loguru import logger
from mcp import ClientSession
from mcp.types import CallToolResult
from mind_core.api import Api
from mind_nova.report import Report
from mind_nova import request
from mind_app.stream_ui import StreamUI


class Enhancer(object):
    """通用工具结果增强"""

    def __init__(
        self,
        session: ClientSession,
        mode: str,
        model_api: dict[str, typing.Any],
        metadata: dict[str, typing.Any],
    ):

        self.session   = session
        self.mode      = mode
        self.model_api = model_api
        self.metadata  = metadata

    @staticmethod
    def nexus_artifact(src: dict[str, typing.Any], default: str) -> dict[str, typing.Any]:

        def has_dir(x: typing.Any) -> bool:
            return isinstance(x, str) and bool(x.strip())

        def patch_request(req: typing.Any) -> dict[str, typing.Any]:
            merged = dict(req) if isinstance(req, dict) else {}
            if not has_dir(merged.get("artifact_dir")):
                merged["artifact_dir"] = default
            return merged

        # 单请求边界：request
        if isinstance(src.get("request"), dict):
            merged_src = dict(src)
            merged_src["request"] = patch_request(src.get("request"))
            return merged_src

        # 批量边界：items
        if isinstance(src.get("items"), list):
            merged_src = dict(src)
            patched_items: list[dict[str, typing.Any]] = []

            for item in src["items"]:
                if not isinstance(item, dict):
                    patched_items.append(item)
                    continue

                merged_item = dict(item)
                merged_item["request"] = patch_request(item.get("request"))
                patched_items.append(merged_item)

            merged_src["items"] = patched_items
            return merged_src

        return src

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

        elif name.startswith("nexus_"):
            return Enhancer.nexus_artifact(src_arguments, report.toolkit_path)

        elif name.startswith("file_logcat_dump"):
            if src_arguments.get("saved"):
                return src_arguments
            return src_arguments | {"saved": report.log_path}

        elif name.startswith("scrcpy_record"):
            if src_arguments.get("directory"):
                return src_arguments
            return src_arguments | {"directory": report.rec_path}

        elif name.startswith("fx_frame_analyzer"):
            if src_arguments.get("total"):
                return src_arguments
            return src_arguments | {"total": report.native_path}

        elif name.startswith("screenshot"):
            if local := src_arguments.get("local"):
                p = Path(str(local)).expanduser()
                # 1) 如果传的是目录：默认落到该目录下 screenshot.png
                if p.exists() and p.is_dir():
                    return src_arguments | {"local": str(p / "screenshot.png")}

                suf = p.suffix.lower()

                # 2) 有后缀且合法：原样返回
                if suf in {".png", ".jpg", ".jpeg", ".webp"}:
                    return src_arguments

                # 3) 有后缀但不合法：强制改成 .png（避免 weird 容器）
                if suf:
                    fixed = p.with_suffix(".png")
                    return src_arguments | {"local": str(fixed)}

                # 4) 没后缀：补 .png
                fixed = p.with_suffix(".png")
                return src_arguments | {"local": str(fixed)}

            return src_arguments | {"local": str(Path(report.cap_path) / "screenshot.png")}

        else:
            return src_arguments

    @staticmethod
    def fields(result: CallToolResult) -> typing.Union[dict[str, typing.Any], str]:
        """Fields"""
        return sc if (sc := result.structuredContent) else result.content[0].text

    @staticmethod
    def fields_map(result: CallToolResult) -> dict[str, typing.Any]:
        """
        把工具返回统一归一成 dict。

        输入：
        - `result.structuredContent` 为 dict：直接返回。
        - `result.content[0].text` 为 JSON 对象字符串：解析后返回。
        - 其他文本/对象：包装成最小结构返回。

        输出最小结构：
        - `text: str`
        - `attachments: list`
        - `data: dict`

        说明：
        - 后续通用上传链路只依赖这三个字段，因此这里负责把“可能是 str 的结果”
          收敛成稳定的 mapping，避免下游直接 `.get()` 时触发 AttributeError。
        """
        fields = Enhancer.fields(result)
        if isinstance(fields, dict):
            return fields

        if isinstance(fields, str):
            raw = fields.strip()
            if raw:
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    pass
                else:
                    if isinstance(parsed, dict):
                        return parsed

            return {
                "text"        : fields,
                "attachments" : [],
                "data"        : {}
            }

        return {
            "text"        : str(fields),
            "attachments" : [],
            "data"        : {}
        }

    @staticmethod
    def normalize_element(element: dict[str, typing.Any]) -> dict[str, typing.Any]:
        """
        把 `fields["data"]["results"][]` 中的单个元素归一化。

        期望输入结构（broadcast 的单 agent 结果）：
        - `agent_id: str`
        - `ok: bool` 或 `data.ok: bool`
        - `text: str`
        - `attachments: list[dict | AttachmentLike]`

        其中 `attachments[]` 的上传约定为：
        - 本地文件：`{"local", "kind"?, "filename"?, "mime_type"?}`
        - 已上传附件：`{"url", "kind"?, "filename"?, "mime_type"?}`

        输出结构：
        - `ok: bool`
        - `text: str`
        - `attachments: list[dict]`

        说明：
        - 这里会把带 `to_dict()` 的附件对象转成普通 dict。
        - 这里不处理上传，只做单元素的“判定 + 标准化”。
        """
        attachments = element.get("attachments", [])
        if not isinstance(attachments, list):
            attachments = []

        normalized: list[dict[str, typing.Any]] = []
        for item in attachments:
            if isinstance(item, dict):
                normalized.append(item)
            elif hasattr(item, "to_dict"):
                normalized.append(item.to_dict())

        ok_raw = element.get("ok")
        data = element.get("data")

        return {
            "ok"          : ok_raw if isinstance(ok_raw, bool) else (
                bool(data.get("ok")) if isinstance(data, dict) else False
            ),
            "text"        : str(element.get("text") or ""),
            "attachments" : normalized
        }

    @staticmethod
    async def upload_local(
        local: str,
        agent_id: str,
        bucket: str,
        kind: str = "file",
        filename: typing.Optional[str] = None,
        mime_type: typing.Optional[str] = None
    ) -> tuple[typing.Optional[dict[str, typing.Any]], dict[str, typing.Any]]:
        """
        上传单个本地文件，并返回两份结果：

        1. 给上游继续透传的附件：
           - `{"kind", "url", "agent_id", "filename", "mime_type"}`
        2. 给内部汇总/审计的上传记录：
           - `{"ok", "local", "url"?, "r2_key"?, "filename"?, "mime_type"?, "error"?}`

        说明：
        - `bucket` 决定远端上传前缀，例如 `screenshots` / `logcat` / `frames`。
        - `kind` 只用于生成返回附件元信息，不影响实际上传内容。
        """
        try:
            up = await request.upload_file_stream(local, agent_id, bucket)
            url = up.get("url")

            if not url:
                return None, {
                    "ok"    : False,
                    "local" : local,
                    "error" : f"upload returned no url: {up!r}"
                }

            attachment = {
                "kind"      : kind,
                "url"       : url,
                "agent_id"  : agent_id,
                "filename"  : up.get("filename", filename),
                "mime_type" : up.get("mime_type", mime_type)
            }
            uploaded = {
                "ok"        : True,
                "local"     : local,
                "url"       : url,
                "r2_key"    : up.get("key"),
                "filename"  : up.get("filename", filename),
                "mime_type" : up.get("mime_type", mime_type)
            }
            return attachment, uploaded
        except Exception as e:
            return None, {
                "ok"    : False,
                "local" : local,
                "error" : f"{type(e).__name__}: {e}"
            }

    @staticmethod
    async def upload_attachments(
        local_attachments: list[dict[str, typing.Any]],
        agent_id: str,
        bucket: str,
        default_kind: str = "file"
    ) -> tuple[list[dict[str, typing.Any]], list[dict[str, typing.Any]]]:
        """
        批量处理并上传附件列表。

        输入 `local_attachments` 要求是归一化后的附件 dict 列表，单项支持两种形态：
        - 本地附件：`{"local", "kind"?, "filename"?, "mime_type"?}`
        - 已有远端地址：`{"url", "kind"?, "filename"?, "mime_type"?}`

        返回：
        - `attachments`：可继续上抛给模型/前端的附件列表
        - `uploads`：本次上传的逐项执行结果，供 `per_agent.uploads` 汇总

        规则：
        - 有 `local`：执行上传
        - 无 `local` 但有 `url`：视为已上传，直接透传
        - 两者都没有：跳过
        """
        attachments: list[dict[str, typing.Any]] = []
        uploads: list[dict[str, typing.Any]] = []

        for a in local_attachments:
            if not isinstance(a, dict):
                continue

            local = a.get("local")
            if not local:
                if a.get("url"):
                    attachments.append(a)
                continue

            attachment, uploaded = await Enhancer.upload_local(
                local=local,
                agent_id=agent_id,
                bucket=bucket,
                kind="image" if a.get("kind") == "image" else default_kind,
                filename=a.get("filename"),
                mime_type=a.get("mime_type")
            )
            uploads.append(uploaded)
            if attachment:
                attachments.append(attachment)

        return attachments, uploads

    async def __upload_tool_result(
        self,
        result: CallToolResult,
        *,
        bucket: str,
        missing_text: str,
        success_text: str,
        partial_text: str,
        default_kind: str = "file"
    ) -> dict[str, typing.Any]:
        """
        处理“带附件产物”的工具结果，并统一完成上传汇总。

        当前仅服务于：
        - `screenshot`
        - `file_logcat_dump`
        - `ffmpeg_extract_snapshot`
        - `ffmpeg_extract_keyframes`
        - `ffmpeg_extract_scene`

        期望 `result` 的结构化内容至少满足：
        - `fields.data.results: list[element]`
        - 每个 `element` 由 `normalize_element()` 负责归一化

        输出统一结构：
        - `text`
        - `attachments`
        - `data.ok`
        - `data.upload_ok`
        - `data.per_agent`

        这样上层不再关心具体工具是截图、logcat 还是抽帧，只看统一上传结果。
        """
        attachments: list[dict[str, typing.Any]] = []
        fields = self.fields_map(result)

        if not (results := fields.get("data", {}).get("results")):
            return {
                "text"        : missing_text,
                "attachments" : attachments,
                "data"        : {"ok": False, "upload_ok": False, "per_agent": {}, "fields": fields}
            }

        per_agent: dict[str, typing.Any] = {}

        for element in results:
            agent_id = element.get("agent_id", "unknown")
            uploaded_attachments, payload = await self.__upload_tool_element(
                element, agent_id, bucket=bucket, default_kind=default_kind
            )
            attachments.extend(uploaded_attachments)
            per_agent[agent_id] = payload

        ok = all(v.get("ok") for v in per_agent.values()) if per_agent else False

        return {
            "text"        : success_text if ok else partial_text,
            "attachments" : attachments,
            "data": {
                "ok"        : ok,
                "upload_ok" : ok,
                "per_agent" : per_agent
            }
        }

    async def __upload_tool_element(
        self,
        element: dict[str, typing.Any],
        agent_id: str,
        *,
        bucket: str,
        default_kind: str = "file"
    ) -> tuple[list[dict[str, typing.Any]], dict[str, typing.Any]]:
        """
        处理单个 agent 的附件上传。

        输入：
        - `element`：`results[]` 中的单项
        - `agent_id`：当前 agent 标识

        输出：
        - `uploaded_attachments`：已可对外返回的附件列表
        - `payload`：当前 agent 的汇总结果
          结构为 `{"ok": bool, "uploads": list[dict]}`

        说明：
        - 如果单 agent 本身执行失败，不会尝试上传，直接把失败文本写入 `uploads`。
        - 如果执行成功但没有可上传附件，会返回 `missing uploadable attachments`。
        """
        normalized = self.normalize_element(element)

        if not normalized["ok"]:
            return [], {
                "ok"      : False,
                "uploads" : [{"ok": False, "error": normalized["text"]}]
            }

        uploaded_attachments, uploads = await self.upload_attachments(
            normalized["attachments"], agent_id, bucket=bucket, default_kind=default_kind
        )

        if not uploads:
            uploads = [{"ok": False, "error": "missing uploadable attachments"}]

        return uploaded_attachments, {
            "ok"      : all(item.get("ok") for item in uploads),
            "uploads" : uploads
        }

    async def enhance(
        self,
        name: str,
        arguments: dict[str, typing.Any],
        result: CallToolResult,
        ok: bool,
        slog: typing.Optional[StreamUI] = None
    ) -> typing.Union[str, dict[str, typing.Any]]:
        """Enhance"""
        fields = self.fields(result)

        if not ok:
            return fields

        if name.startswith("nexus_"):
            return await self.__nexus(result, slog)

        if name == "free_rule":
            return await self.__free_rule(result, slog)

        if name in {
            "ffmpeg_extract_snapshot",
            "ffmpeg_extract_keyframes",
            "ffmpeg_extract_scene",
            "file_logcat_dump",
            "screenshot"
        }:
            return await self.__artifact_upload(name, result)

        if name == "heal_element":
            return await self.__heal_element(arguments, result, slog)

        if name == "loop_steps":
            return await self.__loop_steps(result, slog)

        return fields

    async def __nexus(
        self,
        result: CallToolResult,
        slog: typing.Optional[StreamUI] = None
    ) -> typing.Union[str, dict[str, typing.Any]]:
        """Nexus: 全量静默落盘并返回原始 fields。"""
        fields = self.fields(result)

        if slog and isinstance(fields, dict):
            await slog.feed(
                json.dumps(fields, ensure_ascii=False, indent=2) + "\n", echo=False
            )

        return fields

    async def __free_rule(
        self,
        result: CallToolResult,
        slog: typing.Optional[StreamUI] = None
    ) -> dict[str, typing.Any]:
        """Free Rule"""

        fields = self.fields_map(result)
        attachments: list[dict[str, typing.Any]] = []

        if not (results := fields.get("data", {}).get("results")):
            return {
                "text"        : "未获取到提示词（自由规则）的结果",
                "attachments" : attachments,
                "data"        : {"ok": False}
            }

        per_agent: dict[str, typing.Any] = {}

        if slog:
            await slog.open()

        try:
            for element in results:
                agent_id = element.get("agent_id", "unknown")

                message = element.get("data", {}).get("message")
                context = element.get("data", {}).get("context") or {}

                chunks: list[str] = []
                async for rule_event in request.stream_rule(
                    self.mode, self.model_api, message, context, self.metadata
                ):
                    if rule_event.get("type") == "turn.failed":
                        per_agent[agent_id] = {"ok": False, "message": message, "error": rule_event}
                        continue
                    if rule_event.get("type") not in {"text.delta", "text.done"}:
                        continue

                    chunk = str(rule_event.get("text") or "")
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    if slog:
                        await slog.feed(chunk)

                per_agent[agent_id] = {"ok": True, "message": message, "chunks": chunks}

            ok = all(v.get("ok") for v in per_agent.values()) if per_agent else False

            return {
                "text"        : "free rule completed",
                "attachments" : attachments,
                "data": {
                    "ok"        : ok,
                    "mode"      : self.mode,
                    "api"       : self.model_api.get("api"),
                    "model"     : self.model_api.get("model"),
                    "per_agent" : per_agent
                }
            }
        finally:
            if slog:
                await slog.stop()

    async def __artifact_upload(self, name: str, result: CallToolResult) -> dict:
        """
        为指定工具选择上传配置，然后转发到统一上传链路。

        这里只做“工具名 -> 上传策略”的映射：
        - bucket
        - 缺失结果文案
        - 成功文案
        - 部分失败文案
        - 默认 kind
        """
        specs = {
            "ffmpeg_extract_snapshot": {
                "bucket"       : "frames",
                "missing_text" : "未获取到视频帧结果",
                "success_text" : "视频帧上传成功",
                "partial_text" : "视频帧上传完成（存在失败）",
                "default_kind" : "file"
            },
            "ffmpeg_extract_keyframes": {
                "bucket"       : "frames",
                "missing_text" : "未获取到视频帧结果",
                "success_text" : "视频帧上传成功",
                "partial_text" : "视频帧上传完成（存在失败）",
                "default_kind" : "file"
            },
            "ffmpeg_extract_scene": {
                "bucket"       : "frames",
                "missing_text" : "未获取到视频帧结果",
                "success_text" : "视频帧上传成功",
                "partial_text" : "视频帧上传完成（存在失败）",
                "default_kind" : "file"
            },
            "file_logcat_dump": {
                "bucket"       : "logcat",
                "missing_text" : "未获取到 logcat 结果",
                "success_text" : "logcat 上传成功",
                "partial_text" : "logcat 上传完成（存在失败）",
                "default_kind" : "file"
            },
            "screenshot": {
                "bucket"       : "screenshots",
                "missing_text" : "未获取到截图结果",
                "success_text" : "屏幕截图上传成功",
                "partial_text" : "屏幕截图上传完成（存在失败）",
                "default_kind" : "file"
            }
        }
        return await self.__upload_tool_result(result, **specs[name])

    async def __heal_element(
        self,
        arguments: dict[str, typing.Any],
        result: CallToolResult,
        slog: typing.Optional[StreamUI] = None
    ) -> typing.Optional[dict[str, typing.Any]]:
        fields_map = self.fields_map(result)
        attachments: list[dict[str, str]] = []

        heal_status = await Api.heal_license() or {}
        if not heal_status.get("enabled", False):
            return {
                "text"        : "远程元素自愈服务暂不可用",
                "attachments" : attachments,
                "data"        : {"ok": False, "fields": fields_map}
            }

        if not (results := fields_map.get("data", {}).get("results")):
            return {
                "text"        : "未获取到设备结果",
                "attachments" : attachments,
                "data"        : {"ok": False, "fields": fields_map}
            }

        per_agent: dict[str, dict[str, typing.Any]] = {}

        for element in results:
            data   = element["data"]
            serial = data.pop("serial", "unknown")

            async for heal_event in request.stream_heal(self.model_api, **data, slog=slog):
                if heal_event.get("type") == "heal.failed":
                    per_agent[serial] = {"ok": False, "error": heal_event.get("error")}
                    continue

                if heal_event.get("type") != "heal.result":
                    continue

                heal_result = heal_event.get("result")
                if not isinstance(heal_result, dict):
                    continue

                reason = (heal_result.get("details") or {}).get("reason", "unknown")

                if serial not in per_agent:
                    selector = ((heal_result.get("new_selector") or {}).get("primary") or {})
                    locator = {
                        "by": selector.get("by"),
                        "value": selector.get("value")
                    }
                    per_agent[serial] = {"ok": True, "locator": locator, "reason": reason}

                if slog: await slog.feed(reason, display=StreamUI.BLOCK)
                else: logger.debug(reason)

        matrix = {k: v["locator"] for k, v in per_agent.items() if v.get("locator")}

        if not matrix:
            return {
                "text"        : "元素定位失败",
                "attachments" : attachments,
                "data"        : {"ok": False, "per_agent": per_agent}
            }

        if not arguments.get("should_click"):
            ok = all(v.get("ok") for v in per_agent.values()) if per_agent else False
            return {
                "text"        : "元素定位成功" if ok else "元素定位完成（存在失败）",
                "attachments" : attachments,
                "data"        : {"ok": ok, "per_agent": per_agent}
            }

        wait_s = float(arguments.get("wait") or 0)
        if wait_s > 0: await asyncio.sleep(wait_s)

        r = await self.session.call_tool("click", {"matrix": matrix})
        f = self.fields(r)

        if r.isError:
            return {
                "text"        : "点击失败",
                "attachments" : attachments,
                "data"        : {"ok": False, "per_agent": per_agent, "fields": f}
            }

        ok = all(v.get("ok") for v in per_agent.values()) if per_agent else False
        return {
            "text"        : "元素定位成功，并已点击" if ok else "元素定位完成并已点击（存在失败）",
            "attachments" : attachments,
            "data"        : {"ok": ok, "per_agent": per_agent, "fields": f}
        }

    async def __loop_steps(
        self,
        result: CallToolResult,
        slog: typing.Optional[StreamUI] = None
    ) -> dict[str, typing.Any]:

        async def say(line: str) -> None:
            if slog:
                return await slog.feed(line, display=StreamUI.BLOCK)

        fields = self.fields_map(result)

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
                    f"loop_steps:  step {i + 1}/{len(steps)} tool={tool} "
                    f"{step_fields.get('text') if isinstance(step_fields, dict) else str(step_fields)}"
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
