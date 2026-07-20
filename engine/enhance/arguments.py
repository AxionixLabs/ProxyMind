# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from pathlib import Path


class ReportPaths(typing.Protocol):
    """描述工具参数增强需要的报告目录。"""

    toolkit_path: str
    log_path: str
    rec_path: str
    native_path: str
    cap_path: str


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

    if isinstance(src.get("request"), dict):
        merged_src = dict(src)
        merged_src["request"] = patch_request(src.get("request"))
        return merged_src

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


def exchange_arguments(
    name: str,
    src_arguments: dict[str, typing.Any],
    report: ReportPaths
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
        return nexus_artifact(src_arguments, report.toolkit_path)

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


if __name__ == '__main__':
    pass
