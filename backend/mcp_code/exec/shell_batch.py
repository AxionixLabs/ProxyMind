# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
import asyncio
from backend.mcp_code.base import (
    NativeCodingBase, NativeCodingComponent
)
from backend.mcp_code.exec.shell_exec import ShellCommandTools


class ShellBatchTools(NativeCodingComponent):
    """批量执行 shell 命令。"""

    MAX_ITEMS       = 12
    MAX_CONCURRENCY = 4

    def __init__(self, core: NativeCodingBase, *, shell_command: ShellCommandTools) -> None:
        """保存共享核心和单命令执行器。"""
        super().__init__(core)

        self._shell_command: ShellCommandTools = shell_command

    @staticmethod
    def _normalize_item_args(
        item: dict[str, typing.Any]
    ) -> tuple[str, dict[str, typing.Any]]:
        """归一化新版直接命令项。"""
        args = {
            key: item[key] for key in ("command", "cwd", "timeout_sec") if key in item
        }
        return "shell_command", args

    @staticmethod
    def _canonical_item_args(
        args: dict[str, typing.Any]
    ) -> dict[str, typing.Any]:
        """生成单命令执行策略使用的规范参数。"""
        return {
            "command"     : str(args.get("command") or ""),
            "cwd"         : str(args.get("cwd") or "."),
            "timeout_sec" : int(args.get("timeout_sec") or 60)
        }

    @classmethod
    def _canonical_items(
        cls,
        items: list[dict[str, typing.Any]]
    ) -> list[dict[str, typing.Any]]:
        """生成批量命令的规范参数列表。"""
        canonical: list[dict[str, typing.Any]] = []
        for item in items:
            if not isinstance(item, dict):
                canonical.append({"command": "", "cwd": ".", "timeout_sec": 60})
                continue
            canonical.append(cls._canonical_item_args(item))
        return canonical

    @classmethod
    def _normalize_policy_value(
        cls,
        value: typing.Any
    ) -> typing.Any:
        """把策略比较值归一化为稳定结构。"""
        if isinstance(value, dict):
            return {
                str(key): cls._normalize_policy_value(value[key])
                for key in sorted(value, key=lambda item: str(item))
            }
        if isinstance(value, list):
            return [cls._normalize_policy_value(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value

        return str(value)

    def _batch_execution_denied(
        self,
        *,
        execution: dict[str, typing.Any] | None,
        requested_items: list[dict[str, typing.Any]] | None
    ) -> dict[str, typing.Any] | None:
        """校验顶层批量 execution 的 canonicalArguments。"""
        if not isinstance(execution, dict) or not execution:
            return None

        canonical = execution.get("canonicalArguments") or execution.get("canonical_arguments")
        if not isinstance(canonical, dict):
            return None

        if isinstance(requested_items, list):
            expected_items = []
            for item in requested_items:
                if not isinstance(item, dict):
                    expected_items.append({"command": "", "cwd": ".", "timeout_sec": 60})
                    continue
                _, args = self._normalize_item_args(item)
                expected_items.append(self._canonical_item_args(args))
        else:
            expected_items = []

        expected = {"items": expected_items}
        if "items" in canonical:
            actual = {"items": self._canonical_items(canonical.get("items") or [])}
        else:
            actual = {"items": []}

        if self._normalize_policy_value(expected) == self._normalize_policy_value(actual):
            return None

        return self.fail_result(
            "execution_canonical_arguments_mismatch", error="execution_policy_blocked"
        )

    def _normalize_items(
        self,
        items: list[dict[str, typing.Any]] | None
    ) -> list[dict[str, typing.Any]]:
        """归一化 shell_command 批量请求，限制数量并保留原始顺序索引。"""
        if not isinstance(items, list):
            return []

        normalized: list[dict[str, typing.Any]] = []
        for index, item in enumerate(items[:self.MAX_ITEMS]):
            if not isinstance(item, dict):
                normalized.append({"index": index, "tool": "", "args": {}})
                continue

            tool, args = self._normalize_item_args(item)
            normalized.append({"index": index, "tool": tool, "args": args})

        return normalized

    def _item_execution(
        self,
        *,
        args: dict[str, typing.Any],
        batch_execution: dict[str, typing.Any] | None
    ) -> dict[str, typing.Any] | None:
        """从顶层批量 execution 派生单命令 execution。"""
        if not isinstance(batch_execution, dict) or not batch_execution:
            return None

        derived = dict(batch_execution)
        derived["canonicalArguments"] = self._canonical_item_args(args)
        derived.pop("canonical_arguments", None)

        return derived

    async def _execute_batch(
        self,
        *,
        items: list[dict[str, typing.Any]] | None,
        execution: dict[str, typing.Any] | None = None,
        result_tool: str = "shell_command"
    ) -> dict[str, typing.Any]:
        """并发执行允许的 shell 命令批次，并返回有序结果。"""
        requested_count = len(items) if isinstance(items, list) else 0

        normalized = self._normalize_items(items)
        if not normalized:
            return self.fail_result(
                "shell_command_items_empty",
                mode="batch",
                requested_count=requested_count,
                total=0,
                ok_count=0,
                fail_count=0,
                max_items=self.MAX_ITEMS,
                max_concurrency=self.MAX_CONCURRENCY,
                dropped_count=0,
                truncated=False,
                results=[]
            )
        if not isinstance(execution, dict) or not execution:
            return self.fail_result(
                "execution_metadata_required",
                mode="batch",
                requested_count=requested_count,
                total=0,
                ok_count=0,
                fail_count=requested_count,
                max_items=self.MAX_ITEMS,
                max_concurrency=self.MAX_CONCURRENCY,
                dropped_count=max(0, requested_count - len(normalized)),
                truncated=requested_count > len(normalized),
                results=[],
                error="execution_policy_blocked"
            )
        denied = self._batch_execution_denied(
            execution=execution,
            requested_items=items
        )
        if denied is not None:
            return denied

        semaphore = asyncio.Semaphore(self.MAX_CONCURRENCY)

        async def run_item(item: dict[str, typing.Any]) -> dict[str, typing.Any]:
            """执行单个并行 shell 调用并保留原始索引。"""
            index = int(item.get("index") or 0)
            tool  = str(item.get("tool") or "").strip()
            args  = dict(item.get("args") if isinstance(item.get("args"), dict) else {})

            try:
                async with semaphore:
                    if tool == "shell_command":
                        item_execution = self._item_execution(args=args, batch_execution=execution)
                        if item_execution is not None:
                            args["execution"] = item_execution
                        result = await self._shell_command.shell_command(**args)
                    else:
                        result = {
                            "text"        : "shell command item not allowed",
                            "attachments" : [],
                            "data"        : {"ok": False, "tool": tool, "error": "tool_not_allowed"},
                            "logs"        : []
                        }
            except Exception as exc:
                result = {
                    "text": "shell command item error",
                    "attachments": [],
                    "data": {
                        "ok"    : False,
                        "tool"  : tool,
                        "error" : f"{type(exc).__name__}: {exc}"
                    },
                    "logs": []
                }

            return {
                "index"  : index,
                "tool"   : tool,
                "args"   : args,
                "ok"     : bool((result.get("data") or {}).get("ok")) if isinstance(result, dict) else False,
                "result" : result
            }

        results: list[dict[str, typing.Any]] = list(await asyncio.gather(
            *(run_item(item) for item in normalized)
        ))
        results.sort(key=lambda item: int(item.get("index") or 0))

        ok_count      = sum(1 for item in results if item.get("ok"))
        dropped_count = max(0, requested_count - len(normalized))
        fail_count    = len(results) - ok_count + dropped_count
        truncated     = dropped_count > 0
        all_ok        = bool(results) and fail_count == 0

        data: dict[str, typing.Any] = {
            "ok"              : all_ok,
            "mode"            : "batch",
            "requested_count" : requested_count,
            "total"           : len(results),
            "ok_count"        : ok_count,
            "fail_count"      : fail_count,
            "max_items"       : self.MAX_ITEMS,
            "max_concurrency" : self.MAX_CONCURRENCY,
            "dropped_count"   : dropped_count,
            "truncated"       : truncated,
            "results"         : results
        }
        if not all_ok:
            data["reason"] = "shell_command_batch_failed" if results else "shell_command_items_empty"
            self.core.enrich_failure_facts(data)

        text = (
            f"{result_tool} batch {'ok' if all_ok else 'failed'} total={len(results)} "
            f"ok={ok_count} fail={fail_count} truncated={truncated}"
        )

        return {
            "text"        : text,
            "attachments" : [],
            "data"        : data,
            "logs"        : []
        }

    async def shell_command(
        self,
        *,
        items: list[dict[str, typing.Any]] | None = None,
        execution: dict[str, typing.Any] | None = None
    ) -> dict[str, typing.Any]:
        """批量执行 shell_command。"""
        return await self._execute_batch(
            items=items,
            execution=execution,
            result_tool="shell_command"
        )


if __name__ == '__main__':
    pass
