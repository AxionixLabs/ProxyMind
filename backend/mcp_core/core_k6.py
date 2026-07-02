# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import json
import typing
import asyncio
import tempfile
from pathlib import Path
from backend.models.model_base import Attachment
from backend.models.model_device import SemanticResult
from backend.utilities.storage.output import mk_out_dir
from backend.utilities.validation import marked
from backend.utilities import const


class K6Base(object):
    """k6 基类，负责命令组装与路径规范化。"""

    response_preview_prefix: typing.ClassVar[str] = "__PERF_RESPONSE_PREVIEW__="

    def __init__(self):
        """初始化执行器前缀与对外标识。"""
        self.__prefix: str = "k6"
        self.agent_id: str = self.__prefix

    @property
    def prefix(self) -> str:
        """返回底层命令前缀。"""
        return self.__prefix

    @staticmethod
    def _decode(payload: typing.Optional[bytes]) -> str:
        """将命令输出字节解码为文本。"""
        if not payload:
            return ""
        return payload.decode(const.CHARSET, const.IGNORE).strip()

    @staticmethod
    def _clip(text: str, limit: int = 8000) -> str:
        """裁剪过长文本，避免结果体积失控。"""
        if len(text or "") <= limit:
            return text or ""
        return (text or "")[:limit] + "\n...[truncated]..."

    @staticmethod
    def _env_patch(env: typing.Optional[dict[str, typing.Any]]) -> typing.Optional[dict[str, str]]:
        """合并并规范化子进程环境变量。"""
        if not env:
            return None

        merged = os.environ.copy()
        for key, value in dict(env).items():
            if value is None:
                continue
            merged[str(key)] = str(value)
        return merged

    @staticmethod
    def _normalized_pairs(values: typing.Optional[dict[str, typing.Any]]) -> list[tuple[str, str]]:
        """将键值映射清洗为稳定排序的字符串对列表。"""
        if not values:
            return []

        pairs: list[tuple[str, str]] = []
        for key, value in dict(values).items():
            key_str = str(key).strip()
            if not key_str:
                continue
            pairs.append((key_str, "" if value is None else str(value)))

        return sorted(pairs, key=lambda item: item[0])

    @staticmethod
    def _normalized_args(values: typing.Optional[list[str]]) -> list[str]:
        """清洗附加参数列表，移除空白项。"""
        args: list[str] = []
        for item in values or []:
            token = str(item or "").strip()
            if token:
                args.append(token)
        return args

    @staticmethod
    def _inline_script_fail(message: str, **meta: typing.Any) -> RuntimeError:
        """构造统一格式的内联脚本校验异常。"""
        return marked.fail_tip(
            message,
            code=const.CODE_EXC,
            hint=const.HINT_HLT,
            **meta
        )

    def ensure_script_text(self, script_text: str) -> str:
        """校验内联脚本文本，并返回可执行内容。"""
        if not isinstance(script_text, str):
            raise self._inline_script_fail(
                "script_text 类型错误（需要字符串）。",
                field="script_text",
                expect="str",
                got=type(script_text).__name__
            )

        if not script_text.strip():
            raise self._inline_script_fail(
                "script_text 为空。",
                field="script_text",
                expect="non_empty",
                got=script_text
            )

        return script_text

    @staticmethod
    def normalize_script_name(script_name: typing.Optional[str]) -> str:
        """规范化脚本文件名，并补齐默认扩展名。"""
        filename = Path(str(script_name or "").strip()).name
        if not filename:
            return "script.generated.js"
        if not Path(filename).suffix:
            return filename + ".js"
        return filename

    @staticmethod
    def default_summary_export(
        base_dir: str,
        *,
        tool: str = "perf_run"
    ) -> str:
        """生成汇总结果文件的默认输出路径。"""
        out_dir = mk_out_dir(base_dir or ".", engine="k6", tool=tool)
        return str(out_dir / "summary.json")

    @staticmethod
    def default_response_export(summary_export: str) -> str:
        """根据汇总文件路径推导响应结果文件路径。"""
        summary_path = Path(str(summary_export)).expanduser()
        return str(summary_path.with_name("responses.json"))

    @staticmethod
    def resolve_execution_mode(
        execution_mode: typing.Optional[str],
        *,
        vus: typing.Optional[int] = None,
        duration: typing.Optional[str] = None,
        iterations: typing.Optional[int] = None
    ) -> str:
        """解析执行模式；自动模式下根据规模参数做弱判断。"""
        value = str(execution_mode or "auto").strip().lower() or "auto"
        if value in {"load", "probe"}:
            return value
        if str(duration or "").strip():
            return "load"
        if vus is not None and int(vus) > 1:
            return "load"
        if iterations is not None and int(iterations) > 1:
            return "load"
        return "probe"

    @staticmethod
    def resolve_response_capture(
        response_capture: typing.Optional[str],
        *,
        execution_mode: str
    ) -> tuple[bool, str]:
        """解析响应采集开关，并返回原因说明。"""
        value = str(response_capture or "auto").strip().lower() or "auto"
        if value == "on":
            return True, "explicit_on"
        if value == "off":
            return False, "explicit_off"
        if execution_mode == "probe":
            return True, "probe_default"
        return False, "load_default"

    @classmethod
    def extract_response_preview(cls, stdout_text: str) -> tuple[str, list[dict[str, typing.Any]]]:
        """从标准输出中提取响应预览标记，并返回清洗后的输出文本。"""
        previews: list[dict[str, typing.Any]] = []
        remain: list[str] = []
        prefix = cls.response_preview_prefix

        for line in (stdout_text or "").splitlines():
            marker = line.strip()
            if marker.startswith(prefix):
                payload = marker[len(prefix):].strip()
                try:
                    value = json.loads(payload)
                    if isinstance(value, dict):
                        previews.append(value)
                    else:
                        previews.append({"value": value})
                except Exception as exc:
                    previews.append({
                        "raw"         : cls._clip(payload, limit=2000),
                        "parse_error" : f"{type(exc).__name__}: {exc}"
                    })
                continue
            remain.append(line)

        return "\n".join(remain).strip(), previews

    def resolve_response_export(
        self,
        summary_export: str,
        response_export: typing.Optional[str]
    ) -> str:
        """解析响应结果输出路径；默认与汇总文件同目录。"""
        value = str(response_export or "").strip()
        if not value:
            return self.default_response_export(summary_export)

        candidate = Path(value).expanduser()
        if candidate.exists() and candidate.is_dir():
            return str(candidate / "responses.json")

        return marked.ensure_o(
            summary_export,
            value,
            "response_export",
            suffix="json"
        )

    async def exec_cli(
        self,
        args: list[str],
        *,
        cwd: typing.Optional[str] = None,
        env: typing.Optional[dict[str, typing.Any]] = None,
        action: str,
        attachments: typing.Optional[list[Attachment]] = None,
        data: typing.Optional[dict[str, typing.Any]] = None
    ) -> dict[str, typing.Any]:
        """执行底层命令，并统一封装标准结果结构。"""
        cmd = [self.prefix] + list(args or [])

        transports = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd or None,
            env=self._env_patch(env),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await transports.communicate()

        stdout_text = self._decode(stdout)
        stderr_text = self._decode(stderr)
        stdout_text, response_preview = self.extract_response_preview(stdout_text)

        exit_code = int(transports.returncode or 0)

        ok = (exit_code == 0)

        text = f"{self.agent_id.upper()} {action}{'完成' if ok else '失败'}。exit_code={exit_code}"

        payload = {
            "command"                : cmd,
            "cwd"                    : cwd,
            "exit_code"              : exit_code,
            "stdout"                 : self._clip(stdout_text),
            "stderr"                 : self._clip(stderr_text),
            "response_preview"       : response_preview,
            "response_preview_count" : len(response_preview)
        }
        if data:
            payload.update(data)

        result = SemanticResult.from_text(
            text=text,
            ok=ok,
            attachments=attachments or [],
            data=payload,
            logs=[item for item in [stdout_text, stderr_text] if item]
        )
        return result.to_dict()

    def build_run_plan(
        self,
        *,
        script_file: str,
        workdir: typing.Optional[str] = None,
        vus: typing.Optional[int] = None,
        duration: typing.Optional[str] = None,
        iterations: typing.Optional[int] = None,
        env: typing.Optional[dict[str, typing.Any]] = None,
        tags: typing.Optional[dict[str, typing.Any]] = None,
        summary_export: typing.Optional[str] = None,
        extra_args: typing.Optional[list[str]] = None,
        response_protocol: bool = False,
        execution_mode: typing.Optional[str] = "auto",
        response_capture: typing.Optional[str] = "auto",
        response_export: typing.Optional[str] = None,
        tool: str = "k6_run_script"
    ) -> dict[str, typing.Any]:
        """根据输入参数组装最终执行计划与输出路径。"""
        script_path = Path(marked.ensure_f(script_file, "script_file"))

        final_workdir = marked.ensure_d(workdir, "workdir") if workdir else str(script_path.parent)
        duration_text = str(duration or "").strip()
        cmd = ["run"]

        if vus is not None:
            cmd += ["--vus", str(max(1, int(vus)))]

        if duration_text:
            cmd += ["--duration", duration_text]

        if iterations is not None:
            cmd += ["--iterations", str(max(1, int(iterations)))]

        for key, value in self._normalized_pairs(tags):
            cmd += ["--tag", f"{key}={value}"]

        if summary_export:
            candidate = Path(str(summary_export)).expanduser()
            if candidate.exists() and candidate.is_dir():
                final_summary = self.default_summary_export(str(candidate), tool=tool)
            else:
                final_summary = marked.ensure_o(
                    script_path,
                    summary_export,
                    "summary_export",
                    suffix="json"
                )
        else:
            final_summary = self.default_summary_export(
                final_workdir,
                tool=tool
            )
        cmd += ["--summary-export", final_summary]

        final_response_export = None
        final_env = dict(env or {})
        final_execution_mode: typing.Optional[str] = None
        response_capture_active = False
        response_capture_reason = "local_script_passthrough"
        response_capture_supported = bool(response_protocol)

        if response_protocol:
            final_execution_mode = self.resolve_execution_mode(
                execution_mode,
                vus=vus,
                duration=duration_text,
                iterations=iterations
            )
            response_capture_active, response_capture_reason = self.resolve_response_capture(
                response_capture,
                execution_mode=typing.cast(str, final_execution_mode)
            )
            final_env.update({
                "PERF_EXECUTION_MODE"         : final_execution_mode,
                "PERF_RESPONSE_CAPTURE"       : "on" if response_capture_active else "off",
                "PERF_RESPONSE_CAPTURE_RULE"  : response_capture_reason,
                "PERF_RESPONSE_STDOUT_PREFIX" : self.response_preview_prefix
            })
            if response_capture_active:
                final_response_export = self.resolve_response_export(final_summary, response_export)
                final_env["PERF_RESPONSE_EXPORT"] = final_response_export

        for key, value in self._normalized_pairs(final_env):
            cmd += ["-e", f"{key}={value}"]

        cmd += self._normalized_args(extra_args)
        cmd.append(str(script_path))

        return {
            "cmd"                     : cmd,
            "env"                     : final_env,
            "script_file"             : str(script_path),
            "workdir"                 : final_workdir,
            "summary_export"          : final_summary,
            "execution_mode"          : final_execution_mode,
            "response_capture_supported": response_capture_supported,
            "response_capture_active" : response_capture_active,
            "response_capture_rule"   : response_capture_reason,
            "response_export"         : final_response_export
        }


class K6(K6Base):
    """k6 工具封装。"""

    async def run_inline(
        self,
        *,
        script_text: str,
        script_name: typing.Optional[str] = None,
        env: typing.Optional[dict[str, typing.Any]] = None,
        summary_export: typing.Optional[str] = None,
        extra_args: typing.Optional[list[str]] = None,
        execution_mode: typing.Optional[str] = "auto",
        response_capture: typing.Optional[str] = "auto",
        response_export: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        """执行内联脚本内容，并返回本次结果。"""
        final_script_text = self.ensure_script_text(script_text)
        final_script_name = self.normalize_script_name(script_name)

        with tempfile.TemporaryDirectory(prefix="k6_script_") as tmp_dir:
            script_path = Path(tmp_dir) / final_script_name
            script_path.write_text(final_script_text, encoding=const.CHARSET)
            result = await self.run_file(
                script_file=str(script_path),
                workdir=tmp_dir,
                env=env,
                summary_export=summary_export,
                extra_args=extra_args,
                response_protocol=True,
                execution_mode=execution_mode,
                response_capture=response_capture,
                response_export=response_export,
                tool="perf_run"
            )
            result.setdefault("data", {})["script_name"] = script_path.name
            result["data"]["script_origin"] = "inline"
            result["data"]["script_size"] = len(final_script_text.encode(const.CHARSET, const.IGNORE))
            return result

    async def run_file(
        self,
        *,
        script_file: str,
        workdir: typing.Optional[str] = None,
        vus: typing.Optional[int] = None,
        duration: typing.Optional[str] = None,
        iterations: typing.Optional[int] = None,
        env: typing.Optional[dict[str, typing.Any]] = None,
        tags: typing.Optional[dict[str, typing.Any]] = None,
        summary_export: typing.Optional[str] = None,
        extra_args: typing.Optional[list[str]] = None,
        response_protocol: bool = False,
        execution_mode: typing.Optional[str] = "auto",
        response_capture: typing.Optional[str] = "auto",
        response_export: typing.Optional[str] = None,
        tool: str = "perf_run_file"
    ) -> dict[str, typing.Any]:
        """执行本地脚本文件，并补充汇总产物信息。"""
        plan = self.build_run_plan(
            script_file=script_file,
            workdir=workdir,
            vus=vus,
            duration=duration,
            iterations=iterations,
            env=env,
            tags=tags,
            summary_export=summary_export,
            extra_args=extra_args,
            response_protocol=response_protocol,
            execution_mode=execution_mode,
            response_capture=response_capture,
            response_export=response_export,
            tool=tool
        )

        result = await self.exec_cli(
            plan["cmd"],
            cwd=plan["workdir"],
            env=plan["env"],
            action="脚本执行",
            data={
                "script_file"                : plan["script_file"],
                "summary_export"             : plan["summary_export"],
                "execution_mode"             : plan["execution_mode"],
                "response_capture_supported" : plan["response_capture_supported"],
                "response_capture_active"    : plan["response_capture_active"],
                "response_capture_rule"      : plan["response_capture_rule"],
                "response_export"            : plan["response_export"]
            }
        )
        result.setdefault("data", {})["summary_exists"] = bool(
            plan["summary_export"] and Path(plan["summary_export"]).exists()
        )
        result["data"]["response_exists"] = bool(
            plan["response_export"] and Path(plan["response_export"]).exists()
        )

        if result["data"]["summary_exists"]:
            result.setdefault("attachments", []).append(
                Attachment(
                    kind="file",
                    local=plan["summary_export"],
                    filename=Path(plan["summary_export"]).name,
                    mime_type="application/json"
                ).to_dict()
            )

        if result["data"]["response_exists"]:
            result.setdefault("attachments", []).append(
                Attachment(
                    kind="file",
                    local=typing.cast(str, plan["response_export"]),
                    filename=Path(typing.cast(str, plan["response_export"])).name,
                    mime_type="application/json"
                ).to_dict()
            )

        return result


if __name__ == '__main__':
    pass
