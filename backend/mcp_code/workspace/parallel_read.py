# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
import asyncio
from backend.mcp_code.base import NativeCodingComponent


class ParallelReadCore(typing.Protocol):
    """描述并行读取依赖的只读工作区接口。"""

    def read_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """读取工作区内的文本文件。"""
        ...


class ParallelReadTools(NativeCodingComponent):
    """并行读取工作区上下文的只读组合工具。"""

    ALLOWED_TOOLS = {
        "workspace_read_file"
    }

    MAX_ITEMS       = 12
    MAX_CONCURRENCY = 4

    @staticmethod
    def _parallel_read_item_reason(
        item: dict[str, typing.Any]
    ) -> str:
        """从单项读取结果中提取失败原因。"""
        result = item.get("result") if isinstance(item, dict) else None

        data = result.get("data") if isinstance(result, dict) else None
        if isinstance(data, dict):
            reason = str(data.get("reason") or "").strip()
            if reason:
                return reason

        return "item_failed"

    def _normalize_items(
        self,
        items: list[dict[str, typing.Any]]
    ) -> list[dict[str, typing.Any]]:
        """归一化批量读取请求，限制数量并保留原始顺序索引。"""
        if not isinstance(items, list):
            return []

        normalized: list[dict[str, typing.Any]] = []
        for index, item in enumerate(items[:self.MAX_ITEMS]):
            if not isinstance(item, dict):
                normalized.append({"index": index, "tool": "", "args": {}})
                continue

            tool = str(item.get("tool") or "").strip()
            args = item.get("args") if isinstance(item.get("args"), dict) else {}
            normalized.append({"index": index, "tool": tool, "args": args})

        return normalized

    def _parallel_read_failure_reasons(
        self,
        results: list[dict[str, typing.Any]]
    ) -> dict[str, int]:
        """按原因统计并行读取失败项。"""
        reasons: dict[str, int] = {}

        for item in results:
            if item.get("ok"):
                continue
            reason = self._parallel_read_item_reason(item)
            reasons[reason] = reasons.get(reason, 0) + 1

        return reasons

    def _parallel_read_failures(
        self,
        results: list[dict[str, typing.Any]]
    ) -> list[dict[str, typing.Any]]:
        """提取失败项的索引、工具、参数和原因。"""
        failures: list[dict[str, typing.Any]] = []

        for item in results:
            if item.get("ok"):
                continue
            failures.append({
                "index": item.get("index"),
                "tool": item.get("tool"),
                "args": item.get("args") if isinstance(item.get("args"), dict) else {},
                "reason": self._parallel_read_item_reason(item)
            })

        return failures

    async def parallel_read(
        self,
        items: list[dict[str, typing.Any]]
    ) -> dict[str, typing.Any]:
        """并发执行允许的只读工作区工具，并返回有序结果和后续建议。"""
        normalized = self._normalize_items(items)
        core       = typing.cast(ParallelReadCore, typing.cast(object, self.core))
        semaphore  = asyncio.Semaphore(self.MAX_CONCURRENCY)

        async def run_item(item: dict[str, typing.Any]) -> dict[str, typing.Any]:
            """执行单个并行读取条目并保留原始索引。"""
            index = int(item.get("index") or 0)
            tool  = str(item.get("tool") or "").strip()
            args  = item.get("args") if isinstance(item.get("args"), dict) else {}

            try:
                async with semaphore:
                    if tool == "workspace_read_file":
                        result = await asyncio.to_thread(core.read_file, **args)
                    else:
                        result = self.fail_result("tool_not_allowed", tool=tool)
            except Exception as exc:
                result = self.fail_result(
                    "parallel_read_item_failed",
                    tool=tool,
                    error=f"{type(exc).__name__}: {exc}"
                )

            return {
                "index"  : index,
                "tool"   : tool,
                "args"   : args,
                "ok"     : bool((result.get("data") or {}).get("ok")) if isinstance(result, dict) else False,
                "result" : result
            }

        results: list[dict[str, typing.Any]] = list(await asyncio.gather(
            *(run_item(item) for item in normalized),
            return_exceptions=False
        ))
        results.sort(key=lambda item: int(item.get("index") or 0))

        ok_count        = sum(1 for item in results if item.get("ok"))
        fail_count      = len(results) - ok_count
        requested_count = len(items) if isinstance(items, list) else 0
        dropped_count   = max(0, requested_count - len(normalized))
        truncated       = dropped_count > 0
        failures        = self._parallel_read_failures(results)
        failure_reasons = self._parallel_read_failure_reasons(results)

        return self.ok_result(
            (
                f"native parallel read ok total={len(results)} "
                f"ok={ok_count} fail={fail_count} truncated={truncated}"
            ),
            requested_count=requested_count,
            total=len(results),
            ok_count=ok_count,
            fail_count=fail_count,
            failure_reasons=failure_reasons,
            failures=failures,
            max_items=self.MAX_ITEMS,
            max_concurrency=self.MAX_CONCURRENCY,
            dropped_count=dropped_count,
            truncated=truncated,
            results=results
        )


if __name__ == '__main__':
    pass
