# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import glob
import time
import typing
import mimetypes
from pathlib import Path
from engine.tinker import MindError
from mind_nova import request
from mind_nova.attachments import upload_response_attachment

UploadProgressCallback = typing.Callable[[dict[str, typing.Any]], typing.Awaitable[None]]


class Attach(object):
    """附件状态与上传编排。"""

    TEXT_ATTACHMENT_SUFFIXES: typing.ClassVar[frozenset[str]] = frozenset({
        ".txt", ".md", ".markdown", ".json", ".yaml", ".yml",
        ".csv", ".log", ".xml", ".html", ".htm", ".cfg", ".ini"
    })
    IMAGE_ATTACHMENT_SUFFIXES: typing.ClassVar[frozenset[str]] = frozenset({
        ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"
    })

    def __init__(self) -> None:
        self.pending: list[dict[str, typing.Any]] = []

    @staticmethod
    def _strip_wrapped_quotes(value: str) -> str:
        text = str(value or "").strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
            return text[1:-1].strip()
        return text

    def _resolve_attachment_path(self, raw_path: str, *, must_exist: bool) -> Path:
        text = self._strip_wrapped_quotes(raw_path)
        if not text:
            raise MindError("attach invalid: /attach <path>")

        path = Path(text).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path

        try:
            path = path.resolve(strict=False)
        except (OSError, RuntimeError):
            path = path.absolute()

        if must_exist and not path.exists():
            raise MindError(f"attach file not found: {path}")

        return path

    def _resolve_glob_base(self, raw_path: str) -> str:
        text = self._strip_wrapped_quotes(raw_path)
        if not text:
            raise MindError("attach invalid: /attach <path>")

        path = Path(text).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return str(path)

    @staticmethod
    def _dedupe_paths(paths: list[Path]) -> list[Path]:
        seen: set[str] = set()
        result: list[Path] = []
        for path in paths:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            result.append(path)
        return result

    def _expand_attachment_inputs(self, raw_path: str) -> list[Path]:
        pattern = self._resolve_glob_base(raw_path)

        if glob.has_magic(pattern):
            matched = [
                Path(item).resolve(strict=False)
                for item in sorted(glob.glob(pattern, recursive=True))
                if Path(item).is_file()
            ]
            matched = self._dedupe_paths(matched)
            if matched:
                return matched
            raise MindError(f"attach no files matched: {pattern}")

        path = self._resolve_attachment_path(raw_path, must_exist=True)
        if path.is_dir():
            items = [
                child.resolve(strict=False)
                for child in sorted(path.iterdir(), key=lambda item: item.name.lower())
                if child.is_file()
            ]
            if items:
                return self._dedupe_paths(items)
            raise MindError(f"attach directory has no files: {path}")

        return [path]

    @classmethod
    def _classify_attachment(cls, path: Path) -> tuple[str, str]:
        mime_type = mimetypes.guess_type(path.name)[0] or ""
        suffix = path.suffix.lower()

        if mime_type.startswith("image/") or suffix in cls.IMAGE_ATTACHMENT_SUFFIXES:
            return "image", mime_type or "image/png"

        if mime_type.startswith("text/") or suffix in cls.TEXT_ATTACHMENT_SUFFIXES:
            return "file", mime_type or "text/plain"

        return "file", mime_type or "application/octet-stream"

    def has_pending_attachments(self) -> bool:
        return bool(self.pending)

    def pending_attachments_snapshot(self) -> list[dict[str, typing.Any]]:
        return [dict(item) for item in self.pending]

    def add_pending_attachments(self, raw_path: str) -> dict[str, typing.Any]:
        candidates = self._expand_attachment_inputs(raw_path)
        added: list[dict[str, typing.Any]] = []
        existing: list[dict[str, typing.Any]] = []
        skipped: list[dict[str, typing.Any]] = []

        for path in candidates:
            local = str(path)

            try:
                kind, mime_type = self._classify_attachment(path)
            except MindError as error:
                skipped.append({
                    "local"    : local,
                    "filename" : path.name,
                    "error"    : str(error)
                })
                continue

            for item in self.pending:
                if item.get("local") == local:
                    existing.append(dict(item))
                    break
            else:
                payload = {
                    "local"     : local,
                    "kind"      : kind,
                    "filename"  : path.name,
                    "mime_type" : mime_type,
                    "size"      : int(path.stat().st_size)
                }
                self.pending.append(payload)
                added.append(dict(payload))

        if not added and not existing:
            if skipped:
                raise MindError(
                    "attach no supported files found in selection: "
                    f"{', '.join(item['filename'] for item in skipped[:3])}"
                )
            raise MindError("attach no files were added")

        return {
            "added"    : added,
            "existing" : existing,
            "skipped"  : skipped
        }

    def remove_pending_attachment(self, query: str) -> dict[str, typing.Any]:
        text = self._strip_wrapped_quotes(query)
        if not text:
            raise MindError("detach invalid: /detach <index|path>")
        if text.startswith("<") and text.endswith(">"):
            raise MindError("detach invalid: /detach <index|path>")

        if text.isdigit():
            index = int(text) - 1
            if 0 <= index < len(self.pending):
                return self.pending.pop(index)
            raise MindError(f"detach index out of range: {text}")

        local = str(self._resolve_attachment_path(text, must_exist=False))
        for index, item in enumerate(self.pending):
            if item.get("local") == local:
                return self.pending.pop(index)

        raise MindError(f"detach missing attachment: {local}")

    def clear_pending_attachments(self) -> int:
        count = len(self.pending)
        self.pending.clear()
        return count

    async def upload_pending_attachments(
        self,
        progress_callback: typing.Optional[UploadProgressCallback] = None
    ) -> list[dict[str, typing.Any]]:
        uploaded: list[dict[str, typing.Any]] = []
        total_items = len(self.pending)
        total_bytes = sum(int(item.get("size") or 0) for item in self.pending)
        aggregate_uploaded_before = 0
        aggregate_started_at = time.monotonic()

        agent_id = "attachments"

        for item_index, item in enumerate(self.pending, start=1):
            local = str(item.get("local") or "")
            if not local:
                continue

            async def emit_progress(payload: dict[str, typing.Any]) -> None:
                if progress_callback is None:
                    return None
                event = dict(payload)
                aggregate_uploaded = aggregate_uploaded_before + int(event.get("uploaded_bytes") or 0)
                aggregate_elapsed = max(0.0, time.monotonic() - aggregate_started_at)
                aggregate_speed = (
                    float(aggregate_uploaded) / aggregate_elapsed if aggregate_elapsed > 0 else 0.0
                )
                aggregate_percent = (
                    min(1.0, float(aggregate_uploaded) / float(total_bytes)) if total_bytes > 0 else 1.0
                )
                remaining_bytes = max(0, int(total_bytes) - aggregate_uploaded)
                aggregate_eta = (
                    (float(remaining_bytes) / aggregate_speed) if aggregate_speed > 0 and remaining_bytes > 0 else 0.0
                )
                event.update({
                    "item_index": item_index,
                    "item_total": total_items,
                    "filename": item.get("filename") or Path(local).name,
                    "local": local,
                    "kind": item.get("kind") or "file",
                    "aggregate_uploaded_bytes": aggregate_uploaded,
                    "aggregate_total_bytes": total_bytes,
                    "aggregate_percent": aggregate_percent,
                    "aggregate_elapsed_sec": aggregate_elapsed,
                    "aggregate_speed_bytes_per_sec": aggregate_speed,
                    "aggregate_eta_sec": 0.0 if bool(event.get("done")) else aggregate_eta,
                })
                await progress_callback(event)

            try:
                result = await request.upload_file_stream(
                    local,
                    agent_id,
                    prefix="prompt-attachments",
                    progress_callback=emit_progress if progress_callback is not None else None,
                )
            except Exception as exc:
                raise MindError(f"attach upload failed: {Path(local).name} ({type(exc).__name__}: {exc})") from exc

            try:
                uploaded.append(upload_response_attachment(result, context=f"attach {Path(local).name}"))
            except ValueError as exc:
                raise MindError(str(exc)) from exc
            aggregate_uploaded_before += int(item.get("size") or 0)

        return uploaded


if __name__ == '__main__':
    pass
