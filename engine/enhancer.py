# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import typing
from pathlib import Path
from mcp.types import CallToolResult
from mind_core.api import Api
from mind_nova.report import Report
from mind_nova import request
from mind_nova.attachments import upload_response_attachment
from mind_app.stream_ui import StreamUI

if typing.TYPE_CHECKING:
    from mind_app.mcp import McpSessionLike


class Enhancer(object):
    """统一处理工具参数补全、结果归一和附件上传。"""

    def __init__(
        self,
        session: "McpSessionLike",
        mode: str,
        pref_config: dict[str, typing.Any],
        metadata: dict[str, typing.Any]
    ):
        """初始化工具结果增强上下文。"""

        self.session     = session
        self.mode        = mode
        self.pref_config = pref_config
        self.metadata    = metadata

    @staticmethod
    def nexus_artifact(
        src: dict[str, typing.Any],
        default: str
    ) -> dict[str, typing.Any]:
        """为 nexus 参数补齐默认产物目录。"""
        item_reserved = {"name", "extract", "asserts", "request"}

        def has_dir(x: typing.Any) -> bool:
            """判断参数中是否已有目录值。"""
            return isinstance(x, str) and bool(x.strip())

        def patch_request(req: typing.Any) -> dict[str, typing.Any]:
            """为单个请求补齐产物目录。"""
            merged = dict(req) if isinstance(req, dict) else {}
            if not has_dir(merged.get("artifact_dir")):
                merged["artifact_dir"] = default
            return merged

        def patch_item(item_data: dict[str, typing.Any]) -> dict[str, typing.Any]:
            """为批量请求中的单项补齐产物目录。"""
            merged_item = dict(item_data)
            if isinstance(merged_item.get("request"), dict):
                merged_item["request"] = patch_request(merged_item.get("request"))
                return merged_item

            flat_request = {
                key: value for key, value in merged_item.items()
                if key not in item_reserved
            }
            if not flat_request:
                return merged_item

            preserved = {
                key: value for key, value in merged_item.items()
                if key in item_reserved - {"request"}
            }
            preserved["request"] = patch_request(flat_request)
            return preserved

        # 单请求边界：request
        if isinstance(src.get("request"), dict):
            merged_src = dict(src)
            merged_src["request"] = patch_request(src.get("request"))
            return merged_src

        # 批量边界：items
        if isinstance(src.get("items"), list):
            merged_src = dict(src)
            patched_items: list[dict[str, typing.Any]] = []

            for raw_item in src["items"]:
                if not isinstance(raw_item, dict):
                    patched_items.append(raw_item)
                    continue
                patched_items.append(patch_item(raw_item))

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

        elif name in {"perf_run", "perf_run_file"}:
            if src_arguments.get("summary_export"):
                return src_arguments
            return src_arguments | {"summary_export": report.toolkit_path}

        elif name.startswith("nexus_"):
            return Enhancer.nexus_artifact(src_arguments, report.toolkit_path)

        elif name.startswith("file_logcat_dump"):
            if src_arguments.get("saved"):
                return src_arguments
            return src_arguments | {"saved": report.log_path}

        elif name.startswith("monkey_start"):
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
                if p.exists() and p.is_dir():
                    return src_arguments | {"local": str(p / "screenshot.png")}

                suf = p.suffix.lower()

                if suf in {".png", ".jpg", ".jpeg", ".webp"}:
                    return src_arguments

                if suf:
                    fixed = p.with_suffix(".png")
                    return src_arguments | {"local": str(fixed)}

                fixed = p.with_suffix(".png")
                return src_arguments | {"local": str(fixed)}

            return src_arguments | {"local": str(Path(report.cap_path) / "screenshot.png")}

        else:
            return src_arguments

    @staticmethod
    def fields(result: CallToolResult) -> typing.Union[dict[str, typing.Any], str]:
        """提取工具返回的结构化字段或首段文本。"""
        return sc if (sc := result.structuredContent) else result.content[0].text

    @staticmethod
    def fields_map(result: CallToolResult) -> dict[str, typing.Any]:
        """把工具返回归一为包含 text、attachments、data 的字典。"""
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
                "ok"          : True,
                "text"        : fields,
                "attachments" : [],
                "data"        : {}
            }

        return {
            "ok"          : True,
            "text"        : str(fields),
            "attachments" : [],
            "data"        : {}
        }

    @staticmethod
    def normalize_element(element: dict[str, typing.Any]) -> dict[str, typing.Any]:
        """归一化单个 agent 结果并保留本地附件描述。"""
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
        ok = ok_raw if isinstance(ok_raw, bool) else False

        return {
            "ok"          : ok,
            "text"        : str(element.get("text") or ""),
            "attachments" : normalized
        }

    @staticmethod
    def tool_data(fields: dict[str, typing.Any]) -> dict[str, typing.Any]:
        """提取工具结果的结构化 data。"""
        data = fields.get("data")
        return data if isinstance(data, dict) else {}

    @staticmethod
    def tool_payload(fields: dict[str, typing.Any]) -> dict[str, typing.Any]:
        """提取单次工具结果中的业务载荷。"""
        return Enhancer.tool_data(fields)

    @staticmethod
    def tool_target(fields: dict[str, typing.Any], payload: dict[str, typing.Any]) -> str:
        """提取单次工具结果对应的目标标识。"""
        target = fields.get("target") or payload.get("serial") or fields.get("tool")
        return str(target or "default")

    @staticmethod
    def pref_slot_value(pref_config: dict[str, typing.Any], key: str) -> typing.Any:
        """从顶层或 primary 模型槽位中容错读取偏好字段。"""
        value = pref_config.get(key)
        if value not in (None, ""):
            return value

        primary = pref_config.get("primary")
        if isinstance(primary, dict):
            return primary.get(key)

        return value

    @staticmethod
    async def upload_local(
        local: str,
        agent_id: str,
        bucket: str,
        filename: typing.Optional[str] = None,
        mime_type: typing.Optional[str] = None
    ) -> tuple[typing.Optional[dict[str, typing.Any]], dict[str, typing.Any]]:
        """上传本地文件并返回服务端标准附件和内部上传记录。"""
        up = await request.upload_file_stream(local, agent_id, bucket)

        attachment = upload_response_attachment(up, context=f"upload {Path(local).name}")

        uploaded = {
            "ok"        : True,
            "local"     : local,
            "url"       : attachment.get("url"),
            "r2_key"    : up.get("key"),
            "filename"  : attachment.get("filename", filename),
            "mime_type" : attachment.get("mime_type", mime_type)
        }

        return attachment, uploaded

    @staticmethod
    async def upload_attachments(
        local_attachments: list[dict[str, typing.Any]],
        agent_id: str,
        bucket: str
    ) -> tuple[list[dict[str, typing.Any]], list[dict[str, typing.Any]]]:
        """批量上传本地附件并返回服务端标准附件列表。"""
        attachments: list[dict[str, typing.Any]] = []
        uploads: list[dict[str, typing.Any]]     = []

        for a in local_attachments:
            if not isinstance(a, dict):
                continue

            local = a.get("local")
            if not local:
                continue

            attachment, uploaded = await Enhancer.upload_local(
                local=local,
                agent_id=agent_id,
                bucket=bucket,
                filename=a.get("filename"),
                mime_type=a.get("mime_type")
            )
            uploads.append(uploaded)
            if attachment:
                attachments.append(attachment)

        return attachments, uploads

    async def enhance(
        self,
        name: str,
        result: CallToolResult,
        ok: bool,
        slog: typing.Optional[StreamUI] = None
    ) -> typing.Union[str, dict[str, typing.Any]]:
        """按工具名称增强成功结果。"""
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
            return await self.__heal_element(result, slog)

        if name == "loop_steps":
            return await self.__loop_steps(result, slog)

        return fields

    async def __upload_tool_result(
        self,
        result: CallToolResult,
        *,
        bucket: str,
        missing_text: str,
        success_text: str,
        partial_text: str
    ) -> dict[str, typing.Any]:
        """处理带附件产物的工具结果并汇总上传状态。"""
        attachments: list[dict[str, typing.Any]] = []

        fields  = self.fields_map(result)
        payload = self.tool_payload(fields)

        if not payload and not fields.get("attachments"):
            return {
                "ok"          : False,
                "text"        : missing_text,
                "attachments" : attachments,
                "data"        : {"upload_ok": False, "fields": fields}
            }

        target = self.tool_target(fields, payload)
        element = dict(fields)
        element["data"] = payload

        uploaded_attachments, upload_payload = await self.__upload_tool_element(
            element, target, bucket=bucket
        )
        attachments.extend(uploaded_attachments)
        ok = bool(upload_payload.get("ok"))

        return {
            "ok"          : ok,
            "text"        : success_text if ok else partial_text,
            "target"      : target,
            "attachments" : attachments,
            "data": {
                "upload_ok" : ok,
                "uploads"   : upload_payload.get("uploads", [])
            }
        }

    async def __upload_tool_element(
        self,
        element: dict[str, typing.Any],
        agent_id: str,
        *,
        bucket: str
    ) -> tuple[list[dict[str, typing.Any]], dict[str, typing.Any]]:
        """处理单个 agent 结果中的本地附件上传。"""
        normalized = self.normalize_element(element)

        if not normalized["ok"]:
            return [], {
                "ok"      : False,
                "uploads" : [{"ok": False, "error": normalized["text"]}]
            }

        uploaded_attachments, uploads = await self.upload_attachments(
            normalized["attachments"], agent_id, bucket=bucket
        )

        if not uploads:
            uploads = [{"ok": False, "error": "missing uploadable attachments"}]

        return uploaded_attachments, {
            "ok"      : all(item.get("ok") for item in uploads),
            "uploads" : uploads
        }

    async def __nexus(
        self,
        result: CallToolResult,
        slog: typing.Optional[StreamUI] = None
    ) -> typing.Union[str, dict[str, typing.Any]]:
        """Nexus: 全量静默落盘并返回原始 fields。"""
        fields = self.fields(result)

        if slog and isinstance(fields, dict):
            await slog.feed(
                json.dumps(fields, ensure_ascii=False, indent=2) + "\n",
                echo=False,
                display=StreamUI.BLOCK
            )

        return fields

    async def __free_rule(
        self,
        result: CallToolResult,
        slog: typing.Optional[StreamUI] = None
    ) -> dict[str, typing.Any]:
        """执行自由规则并汇总各 agent 输出。"""
        fields = self.fields_map(result)

        attachments: list[dict[str, typing.Any]] = []

        payload = self.tool_payload(fields)
        if not payload:
            return {
                "ok"          : False,
                "text"        : "未获取到提示词（自由规则）的结果",
                "attachments" : attachments,
                "data"        : {}
            }

        if slog:
            await slog.open()

        try:
            message = payload.get("message")
            context = payload.get("context") or {}

            ok = True
            error: typing.Optional[dict[str, typing.Any]] = None
            chunks: list[str] = []
            async for rule_event in request.stream_rule(
                self.mode, self.pref_config, message, context, self.metadata
            ):
                if rule_event.get("type") == "turn.failed":
                    ok = False
                    error = rule_event
                    continue
                if rule_event.get("type") not in {"text.delta", "text.done"}:
                    continue

                chunk = str(rule_event.get("text") or "")
                if not chunk:
                    continue
                chunks.append(chunk)
                if slog:
                    await slog.feed(chunk, display_chunk=StreamUI.STREAM)

            return {
                "ok"          : ok,
                "text"        : "free rule completed",
                "attachments" : attachments,
                "data": {
                    "mode"    : self.mode,
                    "api"     : self.pref_slot_value(self.pref_config, "api"),
                    "model"   : self.pref_slot_value(self.pref_config, "model"),
                    "message" : message,
                    "chunks"  : chunks,
                    "error"   : error
                }
            }
        finally:
            if slog:
                await slog.stop()

    async def __artifact_upload(
        self,
        name: str,
        result: CallToolResult
    ) -> dict:
        """按工具名称选择附件上传配置。"""
        specs = {
            "ffmpeg_extract_snapshot": {
                "bucket"       : "frames",
                "missing_text" : "未获取到视频帧结果",
                "success_text" : "视频帧上传成功",
                "partial_text" : "视频帧上传完成（存在失败）"
            },
            "ffmpeg_extract_keyframes": {
                "bucket"       : "frames",
                "missing_text" : "未获取到视频帧结果",
                "success_text" : "视频帧上传成功",
                "partial_text" : "视频帧上传完成（存在失败）"
            },
            "ffmpeg_extract_scene": {
                "bucket"       : "frames",
                "missing_text" : "未获取到视频帧结果",
                "success_text" : "视频帧上传成功",
                "partial_text" : "视频帧上传完成（存在失败）"
            },
            "file_logcat_dump": {
                "bucket"       : "logcat",
                "missing_text" : "未获取到 logcat 结果",
                "success_text" : "logcat 上传成功",
                "partial_text" : "logcat 上传完成（存在失败）"
            },
            "screenshot": {
                "bucket"       : "screenshots",
                "missing_text" : "未获取到截图结果",
                "success_text" : "屏幕截图上传成功",
                "partial_text" : "屏幕截图上传完成（存在失败）"
            }
        }
        return await self.__upload_tool_result(result, **specs[name])

    async def __heal_element(
        self,
        result: CallToolResult,
        slog: typing.Optional[StreamUI] = None
    ) -> typing.Optional[dict[str, typing.Any]]:
        """调用远程元素自愈服务并汇总定位结果。"""
        fields_map = self.fields_map(result)

        attachments: list[dict[str, str]] = []

        heal_status = await Api.heal_license() or {}
        if not heal_status.get("enabled", False):
            return {
                "ok"          : False,
                "text"        : "远程元素自愈服务暂不可用",
                "attachments" : attachments,
                "data"        : {"fields": fields_map}
            }

        payload = self.tool_payload(fields_map)
        if not payload:
            return {
                "ok"          : False,
                "text"        : "未获取到设备结果",
                "attachments" : attachments,
                "data"        : {"fields": fields_map}
            }

        target = self.tool_target(fields_map, payload)

        async def collect_heal_locator() -> tuple[
            typing.Optional[dict[str, typing.Any]],
            dict[str, typing.Any]
        ]:
            """收集单个目标的自愈定位结果。"""
            data = dict(payload)
            data.pop("serial", None)

            result_data: dict[str, typing.Any] = {}

            async for heal_event in request.stream_heal(self.pref_config, **data, slog=slog):
                if heal_event.get("type") == "heal.failed":
                    result_data.update({
                        "target" : target,
                        "error"  : heal_event.get("error")
                    })
                    continue

                if heal_event.get("type") != "heal.result":
                    continue

                heal_result = heal_event.get("result")
                if not isinstance(heal_result, dict):
                    continue

                reason   = (heal_result.get("details") or {}).get("reason", "unknown")
                selector = ((heal_result.get("new_selector") or {}).get("primary") or {})

                heal_locator = {
                    "by"    : selector.get("by"),
                    "value" : selector.get("value")
                }
                result_data.update({
                    "target"  : target,
                    "locator" : heal_locator,
                    "reason"  : reason
                })

                if slog:
                    await slog.update_heal_status_summary(reason)
                    await slog.feed(reason, display=StreamUI.BLOCK)
                return heal_locator, result_data

            return None, result_data

        if slog:
            await slog.begin_heal_status()

        try:
            locator, heal_result_data = await collect_heal_locator()
        finally:
            if slog:
                await slog.end_status()

        if not locator:
            return {
                "ok"          : False,
                "text"        : "元素定位失败",
                "target"      : target,
                "attachments" : attachments,
                "data"        : heal_result_data
            }

        return {
            "ok"          : True,
            "text"        : "元素定位成功",
            "target"      : target,
            "attachments" : attachments,
            "data"        : heal_result_data
        }

    async def __loop_steps(
        self,
        result: CallToolResult,
        slog: typing.Optional[StreamUI] = None
    ) -> dict[str, typing.Any]:
        """执行 loop_steps 声明并汇总每轮步骤结果。"""

        async def say(line: str) -> None:
            """向流式界面输出 loop_steps 进度。"""
            if slog:
                return await slog.feed(line, display=StreamUI.BLOCK)

        fields = self.fields_map(result)

        payload = self.tool_payload(fields)
        if not payload:
            return {
                "ok"          : False,
                "text"        : "loop_steps: missing structured payload",
                "attachments" : [],
                "data"        : {},
                "logs"        : []
            }

        declaration_ok = bool(fields.get("ok")) if isinstance(fields.get("ok"), bool) else False

        if not declaration_ok:
            errs = payload.get("errors") or []
            text = (
                f"loop_steps: invalid declaration\n"
                f"errors={'; '.join([str(x) for x in errs[:8]])}" if errs else ""
            )
            return {
                "ok"          : False,
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
                "ok"          : False,
                "text"        : "loop_steps: empty steps",
                "attachments" : [],
                "data"        : payload | {"executed": False},
                "logs"        : []
            }

        if any(isinstance(step, dict) and (step.get("tool") == "loop_steps") for step in steps):
            return {
                "ok"          : False,
                "text"        : "loop_steps: nested loop_steps forbidden (runner guard)",
                "attachments" : [],
                "data"        : payload | {"executed": False},
                "logs"        : []
            }

        attachments: list[dict[str, typing.Any]] = []
        runs: list[dict[str, typing.Any]] = []

        if slog:
            await slog.end_status()
            await slog.begin_loop_status()
        await say(
            f"loop_steps: begin loops={loops} steps={len(steps)} stop_on_fail={stop_on_fail}"
        )

        for r in range(loops):
            if slog:
                await slog.update_loop_status_summary(f"round {r + 1}/{loops}")

            await say(f"loop_steps: round {r + 1}/{loops}")
            round_ok = True

            round_steps: list[dict[str, typing.Any]] = []

            for i, st in enumerate(steps):
                tool = (st.get("tool") or "").strip()
                args = st.get("args") if isinstance(st.get("args"), dict) else {}

                if slog:
                    await slog.update_loop_status_summary(
                        f"round {r + 1}/{loops} step {i + 1}/{len(steps)}"
                    )
                await say(f"loop_steps:  step {i + 1}/{len(steps)} tool={tool}")

                tool_res = await self.session.call_tool(tool, args)
                ok       = (not tool_res.isError)

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
            if slog:
                await slog.update_loop_status_summary(
                    f"round {r + 1}/{loops} {'done' if round_ok else 'failed'}"
                )
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

        try:
            return {
                "ok"          : final_ok,
                "text"        : "\n".join(brief),
                "attachments" : attachments,
                "data": {
                    "executed"     : True,
                    "loops"        : loops,
                    "steps"        : steps,
                    "stop_on_fail" : stop_on_fail,
                    "runs"         : runs
                },
                "logs": []
            }
        finally:
            if slog:
                await slog.end_status()


if __name__ == '__main__':
    pass
