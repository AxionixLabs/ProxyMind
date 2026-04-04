# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import json
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
    def _is_absolute_url(value: typing.Any) -> bool:
        text = str(value or "").strip().lower()
        return text.startswith("http://") or text.startswith("https://")

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
    def _normalized_headers(values: typing.Optional[dict[str, typing.Any]]) -> dict[str, str]:
        return {
            str(k): str(v)
            for k, v in sorted((values or {}).items(), key=lambda item: str(item[0]))
            if str(k).strip() and v is not None
        }

    @staticmethod
    def _scenario_fail(message: str, **meta: typing.Any) -> RuntimeError:
        return marked.fail_tip(
            message,
            code=const.CODE_EXC,
            hint=const.HINT_HLT,
            **meta
        )

    def normalize_scenario(self, scenario: dict[str, typing.Any]) -> dict[str, typing.Any]:
        if not isinstance(scenario, dict):
            raise self._scenario_fail(
                "scenario 类型错误（需要对象）。",
                field="scenario",
                expect="dict",
                got=type(scenario).__name__
            )

        base_url = str(
            scenario.get("base_url")
            or scenario.get("url")
            or ""
        ).strip()

        raw_steps = (
            scenario.get("steps")
            or scenario.get("requests")
            or scenario.get("items")
        )
        if not isinstance(raw_steps, list) or not raw_steps:
            raise self._scenario_fail(
                "scenario.steps 为空（至少需要一个请求步骤）。",
                field="scenario.steps",
                expect="non_empty_list",
                got=repr(raw_steps)
            )

        steps: list[dict[str, typing.Any]] = []
        absolute_step_count = 0
        for index, item in enumerate(raw_steps):
            if not isinstance(item, dict):
                raise self._scenario_fail(
                    "scenario.steps[*] 类型错误（需要对象）。",
                    field=f"scenario.steps[{index}]",
                    expect="dict",
                    got=type(item).__name__
                )

            path = str(
                item.get("path")
                or item.get("url")
                or item.get("endpoint")
                or ""
            ).strip()
            if not path:
                raise self._scenario_fail(
                    "scenario.steps[*].path 为空（也可使用 url 或 endpoint）。",
                    field=f"scenario.steps[{index}].path",
                    expect="non_empty",
                    got=path
                )
            if self._is_absolute_url(path):
                absolute_step_count += 1

            raw_checks = item.get("checks")
            if raw_checks is None:
                if item.get("status") is not None or item.get("status_code") is not None:
                    raw_checks = [
                        {
                            "name"  : str(item.get("check_name") or f"step_{index + 1}_status"),
                            "kind"  : "status_eq",
                            "value" : item.get("status", item.get("status_code"))
                        }
                    ]
                elif item.get("body_contains") is not None:
                    raw_checks = [
                        {
                            "name"  : str(item.get("check_name") or f"step_{index + 1}_body_contains"),
                            "kind"  : "body_contains",
                            "value" : item.get("body_contains")
                        }
                    ]
                elif item.get("json_has") is not None:
                    raw_checks = [
                        {
                            "name"  : str(item.get("check_name") or f"step_{index + 1}_json_has"),
                            "kind"  : "json_has",
                            "value" : item.get("json_has")
                        }
                    ]
            checks: list[dict[str, typing.Any]] = []
            if raw_checks is not None:
                if not isinstance(raw_checks, list):
                    raise self._scenario_fail(
                        "scenario.steps[*].checks 类型错误（需要列表）。",
                        field=f"scenario.steps[{index}].checks",
                        expect="list",
                        got=type(raw_checks).__name__
                    )
                for c_index, check_item in enumerate(raw_checks):
                    if not isinstance(check_item, dict):
                        raise self._scenario_fail(
                            "scenario.steps[*].checks[*] 类型错误（需要对象）。",
                            field=f"scenario.steps[{index}].checks[{c_index}]",
                            expect="dict",
                            got=type(check_item).__name__
                        )
                    checks.append(
                        {
                            "name"  : str(check_item.get("name") or f"step_{index + 1}_check_{c_index + 1}"),
                            "kind"  : str(check_item.get("kind") or "status_eq").strip().lower(),
                            "value" : check_item.get("value")
                        }
                    )

            steps.append(
                {
                    "name"      : str(item.get("name") or item.get("title") or f"step_{index + 1}"),
                    "method"    : str(item.get("method") or item.get("verb") or "GET").upper(),
                    "path"      : path,
                    "params"    : dict(item.get("params") or item.get("query") or {}),
                    "headers"   : self._normalized_headers(item.get("headers") or item.get("request_headers")),
                    "tags"      : self._normalized_headers(item.get("tags")),
                    "body"      : item.get("body") if item.get("body") is not None else item.get("json"),
                    "checks"    : checks,
                    "sleep_sec" : float(item.get("sleep_sec") or item.get("sleep") or 0)
                }
            )

        if not base_url and absolute_step_count != len(steps):
            raise self._scenario_fail(
                "scenario.base_url 为空时，所有 steps 都必须提供绝对 URL。",
                field="scenario.base_url",
                expect="non_empty_or_all_absolute_step_urls",
                got=base_url
            )

        options = dict(scenario.get("options") or scenario.get("load") or {})
        thresholds = dict(scenario.get("thresholds") or {})

        return {
            "name"       : str(scenario.get("name") or scenario.get("title") or "k6_scenario"),
            "base_url"   : base_url.rstrip("/"),
            "headers"    : self._normalized_headers(scenario.get("headers") or scenario.get("default_headers")),
            "tags"       : self._normalized_headers(scenario.get("tags") or scenario.get("labels")),
            "steps"      : steps,
            "options"    : options,
            "thresholds" : thresholds
        }

    @staticmethod
    def _scenario_options(
        normalized: dict[str, typing.Any],
        *,
        vus: typing.Optional[int] = None,
        duration: typing.Optional[str] = None,
        iterations: typing.Optional[int] = None
    ) -> dict[str, typing.Any]:
        options = dict(normalized.get("options") or {})
        thresholds = dict(normalized.get("thresholds") or {})
        if thresholds:
            options["thresholds"] = thresholds

        duration_text = str(duration or "").strip()
        if vus is not None:
            options["vus"] = max(1, int(vus))
        if duration_text:
            options["duration"] = duration_text
        if iterations is not None:
            options["iterations"] = max(1, int(iterations))

        return options

    @staticmethod
    def render_script(
        normalized: dict[str, typing.Any],
        *,
        vus: typing.Optional[int] = None,
        duration: typing.Optional[str] = None,
        iterations: typing.Optional[int] = None
    ) -> str:
        scenario_json = json.dumps(normalized, ensure_ascii=False, indent=2)
        options_json = json.dumps(
            K6Base._scenario_options(
                normalized,
                vus=vus,
                duration=duration,
                iterations=iterations
            ),
            ensure_ascii=False,
            indent=2
        )

        return (
            "import http from 'k6/http';\n"
            "import { check, sleep } from 'k6';\n\n"
            f"export const options = {options_json};\n\n"
            f"const SCENARIO = {scenario_json};\n\n"
            "function buildUrl(baseUrl, path, params) {\n"
            "  const raw = String(path || '').trim();\n"
            "  const isAbsolute = /^https?:\\/\\//i.test(raw);\n"
            "  const direct = isAbsolute ? raw : null;\n"
            "  const root = String(baseUrl || '').replace(/\\/+$/, '');\n"
            "  const tail = raw.replace(/^\\/+/, '');\n"
            "  const url = direct || (tail ? `${root}/${tail}` : root);\n"
            "  if (!params || Object.keys(params).length === 0) return url;\n"
            "  const qs = new URLSearchParams();\n"
            "  Object.entries(params).forEach(([k, v]) => {\n"
            "    if (v === undefined || v === null) return;\n"
            "    qs.append(k, String(v));\n"
            "  });\n"
            "  const text = qs.toString();\n"
            "  if (!text) return url;\n"
            "  return `${url}${url.includes('?') ? '&' : '?'}${text}`;\n"
            "}\n\n"
            "function getByPath(data, path) {\n"
            "  if (!path) return data;\n"
            "  return String(path).split('.').reduce((acc, key) => {\n"
            "    if (acc === null || acc === undefined) return undefined;\n"
            "    return acc[key];\n"
            "  }, data);\n"
            "}\n\n"
            "function bodyOf(step) {\n"
            "  if (step.body === undefined || step.body === null) return null;\n"
            "  if (typeof step.body === 'string') return step.body;\n"
            "  return JSON.stringify(step.body);\n"
            "}\n\n"
            "function makeChecks(step) {\n"
            "  const suite = {};\n"
            "  (step.checks || []).forEach((item, idx) => {\n"
            "    const name = String(item.name || `${step.name || 'step'}_${idx + 1}`);\n"
            "    suite[name] = (res) => {\n"
            "      const kind = String(item.kind || 'status_eq');\n"
            "      if (kind === 'status_eq') return res.status === Number(item.value ?? 200);\n"
            "      if (kind === 'status_in') return Array.isArray(item.value) && item.value.map(Number).includes(res.status);\n"
            "      if (kind === 'body_contains') return String(res.body || '').includes(String(item.value || ''));\n"
            "      if (kind === 'body_not_empty') return String(res.body || '').length > 0;\n"
            "      if (kind === 'json_has') {\n"
            "        try {\n"
            "          return getByPath(res.json(), item.value) !== undefined;\n"
            "        } catch (_) {\n"
            "          return false;\n"
            "        }\n"
            "      }\n"
            "      return true;\n"
            "    };\n"
            "  });\n"
            "  return suite;\n"
            "}\n\n"
            "export default function () {\n"
            "  const globalHeaders = SCENARIO.headers || {};\n"
            "  const globalTags = SCENARIO.tags || {};\n"
            "  for (const step of (SCENARIO.steps || [])) {\n"
            "    const url = buildUrl(SCENARIO.base_url, step.path, step.params || {});\n"
            "    const params = {\n"
            "      headers: { ...globalHeaders, ...(step.headers || {}) },\n"
            "      tags: { ...globalTags, ...(step.tags || {}) }\n"
            "    };\n"
            "    const res = http.request(String(step.method || 'GET').toUpperCase(), url, bodyOf(step), params);\n"
            "    const suite = makeChecks(step);\n"
            "    if (Object.keys(suite).length > 0) check(res, suite);\n"
            "    const delay = Number(step.sleep_sec || 0);\n"
            "    if (delay > 0) sleep(delay);\n"
            "  }\n"
            "}\n"
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
            text += f"\nstdout={self._clip(stdout_text, limit=800)}"
        if stderr_text:
            text += f"\nstderr={self._clip(stderr_text, limit=800)}"

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
        scenario: dict[str, typing.Any],
        vus: typing.Optional[int] = None,
        duration: typing.Optional[str] = None,
        iterations: typing.Optional[int] = None,
        env: typing.Optional[dict[str, typing.Any]] = None,
        tags: typing.Optional[dict[str, typing.Any]] = None,
        summary_export: typing.Optional[str] = None,
        extra_args: typing.Optional[list[str]] = None
    ) -> dict[str, typing.Any]:
        normalized = self.normalize_scenario(scenario)
        if tags:
            normalized["tags"] = {
                **normalized.get("tags", {}),
                **self._normalized_headers(tags)
            }

        script_text = self.render_script(
            normalized,
            vus=vus,
            duration=duration,
            iterations=iterations
        )

        with tempfile.TemporaryDirectory(prefix="k6_scene_") as tmp_dir:
            script_path = Path(tmp_dir) / "scenario.generated.js"
            script_path.write_text(script_text, encoding=const.CHARSET)

            result = await self.run_script(
                script_file=str(script_path),
                workdir=tmp_dir,
                vus=None,
                duration=None,
                iterations=None,
                env=env,
                tags=None,
                summary_export=summary_export,
                extra_args=extra_args
            )
            result.setdefault("data", {})["scenario"] = normalized
            result["data"]["generated_script_name"] = script_path.name
            result["data"]["generated_script_size"] = len(script_text.encode(const.CHARSET, const.IGNORE))
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
