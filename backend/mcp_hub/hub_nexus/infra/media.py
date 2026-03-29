# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import json
import httpx
import base64
import typing
import binascii
from pathlib import Path
from backend.mcp_hub.hub_nexus.domain.extract import ExtractService
from backend.mcp_hub.hub_nexus.infra.core import UrlService
from backend.utilities import const
from backend.utilities.storage.output import mk_out_dir


class MediaService(object):

    @staticmethod
    def detect_media_kind(content_type: str) -> typing.Optional[str]:
        """根据 MIME 类型判断是图片还是视频。"""
        ct = str(content_type or "").split(";")[0].strip().lower()
        if ct.startswith("image/"):
            return "image"
        if ct.startswith("video/"):
            return "video"
        return None

    @staticmethod
    def media_suffix(content_type: str, fallback_kind: str | None = None) -> str:
        """根据 MIME 类型推断媒体文件后缀。"""
        ct = str(content_type or "").split(";")[0].strip().lower()
        mapping = {
            "image/png"        : ".png",
            "image/jpeg"       : ".jpg",
            "image/jpg"        : ".jpg",
            "image/webp"       : ".webp",
            "image/gif"        : ".gif",
            "image/bmp"        : ".bmp",
            "video/mp4"        : ".mp4",
            "video/webm"       : ".webm",
            "video/quicktime"  : ".mov",
            "video/x-matroska" : ".mkv",
            "video/ogg"        : ".ogv"
        }
        if ct in mapping:
            return mapping[ct]
        if fallback_kind == "image":
            return ".png"
        if fallback_kind == "video":
            return ".mp4"
        return ".bin"

    @staticmethod
    def mk_artifact_media_dir(artifact_dir: str) -> Path:
        """Resolve the media folder for a prepared step artifact directory."""
        out_dir = Path(artifact_dir).expanduser().resolve() / "media"
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir

    @staticmethod
    def parse_data_url(value: str) -> tuple[str, bytes] | None:
        """解析 data URL 为 MIME 类型和二进制内容。"""
        if not isinstance(value, str) or not value.startswith("data:"):
            return None

        match = re.match(r"^data:([^;,]+)?(;base64)?,(.*)$", value, re.I | re.S)
        if not match:
            return None

        mime_type = (match.group(1) or "application/octet-stream").strip().lower()
        is_b64    = bool(match.group(2))
        raw       = match.group(3)

        try:
            if is_b64:
                data = base64.b64decode(raw, validate=False)
            else:
                data = raw.encode(const.CHARSET)
            return mime_type, data
        except (binascii.Error, ValueError, TypeError, UnicodeEncodeError, LookupError):
            return None

    @staticmethod
    def parse_base64_blob(value: str) -> tuple[str | None, bytes] | None:
        """尝试把普通 base64 字符串解析为二进制内容。"""
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if not re.fullmatch(r"[A-Za-z0-9+/=\s_-]+", stripped):
            return None
        if stripped.startswith(("http://", "https://", "data:")):
            return None
        try:
            data = base64.b64decode(stripped, validate=False)
        except (binascii.Error, ValueError):
            return None
        if not data:
            return None
        return None, data

    @staticmethod
    def guess_mime_from_bytes(data: bytes, fallback_kind: str | None = None) -> str | None:
        """根据常见文件头猜测媒体 MIME 类型。"""
        if not data:
            return None
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
            return "image/gif"
        if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
            return "image/webp"
        if len(data) >= 12 and data[4:8] == b"ftyp":
            return "video/mp4"
        return "image/png" if fallback_kind == "image" else None

    @staticmethod
    def detect_media_ref(value: typing.Any) -> dict[str, typing.Any] | None:
        """从字符串或字典中识别可落地的媒体引用。"""
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith(("http://", "https://")):
                return {
                    "source"    : "url",
                    "url"       : stripped,
                    "mime_type" : None,
                    "data"      : None,
                    "kind"      : None
                }

            parsed_data_url = MediaService.parse_data_url(stripped)
            if parsed_data_url:
                mime_type, data = parsed_data_url
                return {
                    "source"    : "data_url",
                    "url"       : None,
                    "mime_type" : mime_type,
                    "data"      : data,
                    "kind"      : MediaService.detect_media_kind(mime_type)
                }

            parsed_base64 = MediaService.parse_base64_blob(stripped)
            if parsed_base64:
                mime_type, data = parsed_base64
                mime_type = mime_type or MediaService.guess_mime_from_bytes(data)
                return {
                    "source"    : "base64",
                    "url"       : None,
                    "mime_type" : mime_type,
                    "data"      : data,
                    "kind"      : MediaService.detect_media_kind(mime_type or "")
                }
            return None

        if isinstance(value, dict):
            for key in ("url", "src", "href", "image_url", "video_url"):
                if isinstance(value.get(key), str):
                    ref = MediaService.detect_media_ref(value[key])
                    if ref:
                        return {
                            **ref,
                            "source": "json_path",
                            "mime_type": ref.get("mime_type")
                                         or value.get("mime_type")
                                         or value.get("content_type"),
                            "kind": ref.get("kind") or value.get("kind")
                        }

            for key in ("data_url", "dataUrl", "base64", "content", "data"):
                if isinstance(value.get(key), str):
                    ref = MediaService.detect_media_ref(value[key])
                    if ref:
                        return ref | {"source": "json_path"}
        return None

    @staticmethod
    def collect_media_refs(value: typing.Any) -> list[dict[str, typing.Any]]:
        """递归收集值中的全部媒体引用，用于爬虫式提取。"""
        found: list[dict[str, typing.Any]] = []
        seen: set[tuple[typing.Any, typing.Any, typing.Any]] = set()

        def _push(ref: dict[str, typing.Any]) -> None:
            data = ref.get("data")
            key = (
                ref.get("url"),
                ref.get("mime_type"),
                bytes(data) if isinstance(data, (bytes, bytearray)) else data,
            )
            if key in seen:
                return
            seen.add(key)
            found.append(ref)

        def walk(current: typing.Any) -> None:
            ref = MediaService.detect_media_ref(current)
            if ref:
                _push(ref)

            if isinstance(current, list):
                for item in current:
                    walk(item)
                return

            if isinstance(current, dict):
                for item in current.values():
                    walk(item)

        walk(value)
        return found

    @staticmethod
    async def materialize_media_ref(
        *,
        ref: dict[str, typing.Any],
        tool: str,
        step_artifact_dir: str | None = None,
        default_name: str = "media",
        timeout: float = 30.0
    ) -> tuple[dict[str, typing.Any], dict[str, typing.Any]]:
        """把媒体引用下载或写盘，返回媒体信息与附件信息。"""
        source    = str(ref.get("source") or "unknown")
        url       = ref.get("url")
        mime_type = ref.get("mime_type")
        data      = ref.get("data")
        kind      = ref.get("kind") or MediaService.detect_media_kind(mime_type or "")

        if url:
            async with httpx.AsyncClient(
                **UrlService.httpx_client_kwargs(
                    url=str(url),
                    timeout=timeout,
                    follow_redirects=True
                )
            ) as client:
                resp = await client.get(str(url))
                resp.raise_for_status()
                data = resp.content
                mime_type = str(
                    resp.headers.get("content-type") or mime_type or ""
                ).split(";")[0].strip()
                kind = kind or MediaService.detect_media_kind(mime_type)

        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ValueError("empty media data")

        if not mime_type:
            mime_type = MediaService.guess_mime_from_bytes(bytes(data), fallback_kind=kind)

        kind = kind or MediaService.detect_media_kind(mime_type or "")
        if kind not in {"image", "video"}:
            raise ValueError(f"unsupported media kind: mime={mime_type!r}")

        out_dir = (
            MediaService.mk_artifact_media_dir(step_artifact_dir)
            if step_artifact_dir else
            mk_out_dir(".", engine="nexus", tool=tool)
        )
        ext = MediaService.media_suffix(mime_type or "", fallback_kind=kind)
        filename = f"{default_name}{ext}"
        out_file = out_dir / filename
        out_file.write_bytes(bytes(data))

        media_info = {
            "kind"      : kind,
            "source"    : source,
            "path"      : str(out_file),
            "filename"  : filename,
            "mime_type" : mime_type,
            "size"      : len(data)
        }
        attachment = {
            "kind"      : kind,
            "path"      : str(out_file),
            "filename"  : filename,
            "mime_type" : mime_type,
            "size"      : len(data),
            "source"    : source
        }
        return media_info, attachment

    @staticmethod
    async def collect_media(
        *,
        source_kind: str,
        source: typing.Any,
        content_type: str | None = None,
        media_path: str | None = None,
        media_index: int | None = None,
        tool: str = "media",
        step_artifact_dir: str | None = None,
        timeout: float = 30.0
    ) -> tuple[list[dict[str, typing.Any]], list[dict[str, typing.Any]], list[str]]:
        """从响应体、事件流或消息列表中提取媒体信息。"""
        media_list: list[dict[str, typing.Any]] = []
        attachments: list[dict[str, typing.Any]] = []
        logs: list[str] = []

        try:
            if source_kind == "http_body":
                kind = MediaService.detect_media_kind(content_type or "")
                if kind and isinstance(source, (bytes, bytearray)):
                    if step_artifact_dir:
                        ref = {
                            "source"    : "response_body",
                            "url"       : None,
                            "mime_type" : str(content_type or "").split(";")[0].strip(),
                            "data"      : bytes(source),
                            "kind"      : kind
                        }
                        media_info, attachment = await MediaService.materialize_media_ref(
                            ref=ref,
                            tool=tool,
                            step_artifact_dir=step_artifact_dir,
                            default_name=kind,
                            timeout=timeout
                        )
                        media_list.append(media_info)
                        attachments.append(attachment)
                    else:
                        media_list.append({
                            "kind"      : kind,
                            "source"    : "response_body",
                            "path"      : None,
                            "filename"  : None,
                            "mime_type" : str(content_type or "").split(";")[0].strip(),
                            "size"      : len(source)
                        })
                return media_list, attachments, logs

            target = source
            if source_kind in {"sse_events", "ws_messages"}:
                if media_index is not None and isinstance(target, list):
                    idx = int(media_index)
                    if idx < 0 or idx >= len(target):
                        logs.append(f"media_index[{idx}] out of range")
                        return media_list, attachments, logs
                    target = target[idx]
                elif isinstance(target, list) and not media_path:
                    if not target:
                        return media_list, attachments, logs
                    target = target[0]

                if isinstance(target, dict) and "data" in target:
                    raw_data = target.get("data")
                    if isinstance(raw_data, str):
                        stripped = raw_data.strip()
                        if stripped.startswith("{") or stripped.startswith("["):
                            try:
                                target = json.loads(stripped)
                            except (TypeError, ValueError, json.JSONDecodeError):
                                target = raw_data
                        else:
                            target = raw_data
                    elif raw_data is not None:
                        target = raw_data

            if media_path:
                ok_pick, value = ExtractService.safe_pick(target, media_path)
                if not ok_pick:
                    logs.append(f"media_path[{media_path}] -> {value}")
                    return media_list, attachments, logs
                target = value

            refs = MediaService.collect_media_refs(target)
            if not refs:
                logs.append(f"detect_media_ref fail: type={type(target).__name__}")
                return media_list, attachments, logs

            for index, ref in enumerate(refs):
                if step_artifact_dir:
                    media_info, attachment = await MediaService.materialize_media_ref(
                        ref=ref,
                        tool=tool,
                        step_artifact_dir=step_artifact_dir,
                        default_name=f"media_{index}",
                        timeout=timeout
                    )
                    media_list.append(media_info)
                    attachments.append(attachment)
                else:
                    media_list.append({
                        "kind"      : ref.get("kind"),
                        "source"    : ref.get("source"),
                        "path"      : None,
                        "filename"  : None,
                        "mime_type" : ref.get("mime_type"),
                        "size"      : len(ref["data"]) if isinstance(ref.get("data"), (bytes, bytearray)) else None
                    })

        except Exception as e:
            logs.append(f"collect_media[{source_kind}] -> {type(e).__name__}: {e}")

        return media_list, attachments, logs


if __name__ == '__main__':
    pass
