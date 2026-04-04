# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import typing
import tempfile
import asyncio
from pathlib import Path
from backend.models.model_base import Attachment
from backend.models.model_device import SemanticResult
from backend.utilities import const
from backend.utilities.validation import marked


class K6Base(object):
    """k6 基类，负责命令组装与路径规范化。"""

    def __init__(self):
        self.__prefix: str = "k6"
        self.agent_id: str = self.__prefix

    @property
    def prefix(self) -> str:
        return self.__prefix

    @staticmethod
    def _decode(payload: typing.Optional[bytes]) -> str:
        if not payload:
            return ""
        return payload.decode(const.CHARSET, const.IGNORE).strip()

    @staticmethod
    def _clip(text: str, limit: int = 8000) -> str:
        if len(text or "") <= limit:
            return text or ""
        return (text or "")[:limit] + "\n...[truncated]..."

    @staticmethod
    def _env_patch(env: typing.Optional[dict[str, typing.Any]]) -> typing.Optional[dict[str, str]]:
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
        args: list[str] = []
        for item in values or []:
            token = str(item or "").strip()
            if token:
                args.append(token)
        return args

    @staticmethod
    def _inline_script_fail(message: str, **meta: typing.Any) -> RuntimeError:
        return marked.fail_tip(
            message,
            code=const.CODE_EXC,
            hint=const.HINT_HLT,
            **meta
        )

    def ensure_script_text(self, script_text: str) -> str:
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
        filename = Path(str(script_name or "").strip()).name
        if not filename:
            return "script.generated.js"
        if not Path(filename).suffix:
            return filename + ".js"
        return filename

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

        exit_code = int(transports.returncode or 0)

        ok = (exit_code == 0)

        text = f"{self.agent_id.upper()} {action}{'完成' if ok else '失败'}。exit_code={exit_code}"
        if stdout_text:
            text += f"\n{self._clip(stdout_text, limit=800)}"
        if stderr_text:
            text += f"\n{self._clip(stderr_text, limit=800)}"

        payload = {
            "ok"        : ok,
            "command"   : cmd,
            "cwd"       : cwd,
            "exit_code" : exit_code,
            "stdout"    : self._clip(stdout_text),
            "stderr"    : self._clip(stderr_text)
        }
        if data:
            payload.update(data)

        result = SemanticResult.from_text(
            text=text,
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
        extra_args: typing.Optional[list[str]] = None
    ) -> dict[str, typing.Any]:
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

        for key, value in self._normalized_pairs(env):
            cmd += ["-e", f"{key}={value}"]

        for key, value in self._normalized_pairs(tags):
            cmd += ["--tag", f"{key}={value}"]

        final_summary = None
        if summary_export:
            final_summary = marked.ensure_o(
                script_path,
                summary_export,
                "summary_export",
                suffix="json"
            )
            cmd += ["--summary-export", final_summary]

        cmd += self._normalized_args(extra_args)
        cmd.append(str(script_path))

        return {
            "cmd"            : cmd,
            "script_file"    : str(script_path),
            "workdir"        : final_workdir,
            "summary_export" : final_summary
        }


class K6(K6Base):
    """k6 工具封装。"""

    async def run_local(
        self,
        *,
        script_text: str,
        script_name: typing.Optional[str] = None,
        vus: typing.Optional[int] = None,
        duration: typing.Optional[str] = None,
        iterations: typing.Optional[int] = None,
        env: typing.Optional[dict[str, typing.Any]] = None,
        tags: typing.Optional[dict[str, typing.Any]] = None,
        summary_export: typing.Optional[str] = None,
        extra_args: typing.Optional[list[str]] = None
    ) -> dict[str, typing.Any]:
        final_script_text = self.ensure_script_text(script_text)
        final_script_name = self.normalize_script_name(script_name)

        with tempfile.TemporaryDirectory(prefix="k6_script_") as tmp_dir:
            script_path = Path(tmp_dir) / final_script_name
            script_path.write_text(final_script_text, encoding=const.CHARSET)

            result = await self.run_script(
                script_file=str(script_path),
                workdir=tmp_dir,
                vus=vus,
                duration=duration,
                iterations=iterations,
                env=env,
                tags=tags,
                summary_export=summary_export,
                extra_args=extra_args
            )
            result.setdefault("data", {})["script_name"] = script_path.name
            result["data"]["script_origin"] = "inline"
            result["data"]["script_size"] = len(final_script_text.encode(const.CHARSET, const.IGNORE))
            return result

    async def run_script(
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
        extra_args: typing.Optional[list[str]] = None
    ) -> dict[str, typing.Any]:
        plan = self.build_run_plan(
            script_file=script_file,
            workdir=workdir,
            vus=vus,
            duration=duration,
            iterations=iterations,
            env=env,
            tags=tags,
            summary_export=summary_export,
            extra_args=extra_args
        )

        result = await self.exec_cli(
            plan["cmd"],
            cwd=plan["workdir"],
            action="脚本压测",
            data={
                "script_file"    : plan["script_file"],
                "summary_export" : plan["summary_export"]
            }
        )
        result.setdefault("data", {})["summary_exists"] = bool(
            plan["summary_export"] and Path(plan["summary_export"]).exists()
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

        return result


if __name__ == '__main__':
    pass
