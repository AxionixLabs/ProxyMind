# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from loguru import logger
from backend.mcp_hub.hub_nexus.domain.assertion import AssertionService
from backend.mcp_hub.hub_nexus.domain.extract import ExtractService
from backend.utilities.trace import clip_text, summarize_args


class CheckService(object):
    """负责 extract/asserts 的检查编排与统一结果收尾。"""

    @staticmethod
    def apply_extract_assert(
        source: dict[str, typing.Any],
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None
    ) -> dict[str, typing.Any]:
        """对源数据同时执行 extract 提取与 asserts 断言。"""
        extract = extract or {}
        asserts = asserts or []

        logs: list[str] = []
        extracted: dict[str, typing.Any] = {}

        for alias, path in extract.items():
            ok_pick, value = ExtractService.safe_pick(source, str(path))
            if ok_pick:
                extracted[alias] = value
            else:
                extracted[alias] = None
                logs.append(f"extract[{alias}] {path} -> {value}")

        results: list[dict[str, typing.Any]] = []
        fail_count = 0

        for rule in asserts:
            if not isinstance(rule, dict):
                continue

            path = str(rule.get("path") or "").strip()
            op = str(rule.get("op") or "eq").strip().lower()
            expected = rule.get("value")
            ok_pick, actual = ExtractService.safe_pick(source, path)

            if op == "exists":
                passed = bool(ok_pick)
                result = {
                    "path"     : path,
                    "op"       : op,
                    "expected" : None,
                    "actual"   : actual if ok_pick else None,
                    "ok"       : passed,
                    "error"    : None if ok_pick else actual
                }
            elif not ok_pick:
                passed = False
                result = {
                    "path"     : path,
                    "op"       : op,
                    "expected" : expected,
                    "actual"   : None,
                    "ok"       : False,
                    "error"    : actual
                }
            else:
                try:
                    passed = AssertionService.compare(actual, op, expected)
                    result = {
                        "path"     : path,
                        "op"       : op,
                        "expected" : expected,
                        "actual"   : actual,
                        "ok"       : passed,
                        "error"    : None
                    }
                except Exception as e:
                    passed = False
                    result = {
                        "path"     : path,
                        "op"       : op,
                        "expected" : expected,
                        "actual"   : actual,
                        "ok"       : False,
                        "error"    : f"{type(e).__name__}: {e}"
                    }

            if not passed:
                fail_count += 1
            results.append(result)

        total = len(results)
        if logs:
            logger.warning(f"check extract miss logs={summarize_args({'items': logs})}")
        if fail_count > 0:
            failed = [item for item in results if not item.get("ok")]
            failed_view = [
                {
                    "path": item.get("path"),
                    "op": item.get("op"),
                    "expected": item.get("expected"),
                    "actual": item.get("actual"),
                    "error": clip_text(str(item.get("error")), limit=120) if item.get("error") else None
                }
                for item in failed[:3]
            ]
            logger.warning(
                f"check assert fail total={total} fail={fail_count} "
                f"failed={clip_text(str(failed_view), limit=360)}"
            )
        elif extract or asserts:
            logger.debug(
                f"check assert ok extract={len(extracted)} asserts={total} fail={fail_count}"
            )
        return {
            "ok": fail_count == 0,
            "extract": extracted,
            "asserts": results,
            "summary": {
                "total" : total,
                "pass"  : total - fail_count,
                "fail"  : fail_count
            },
            "logs": logs
        }

    @staticmethod
    def finalize_pack(
        pack: dict[str, typing.Any],
        *,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None
    ) -> dict[str, typing.Any]:
        """在统一返回包上追加 extract 与 asserts 的执行结果。"""
        checked = CheckService.apply_extract_assert(
            pack.get("data") or {},
            extract=extract,
            asserts=asserts
        )

        data = pack.setdefault("data", {})
        logs = pack.setdefault("logs", [])

        data["extract"] = checked["extract"]
        data["asserts"] = checked["asserts"]
        data["assert_summary"] = checked["summary"]
        data["assert_ok"] = bool(checked["ok"])
        pack["ok"] = bool(pack.get("ok")) and bool(checked["ok"])
        logs.extend(checked["logs"])

        if extract or asserts:
            pack["text"] = str(pack.get("text") or "") + (
                f" extract={len(checked['extract'])}"
                f" fail={checked['summary']['fail']}"
            )

        return pack


if __name__ == '__main__':
    pass
