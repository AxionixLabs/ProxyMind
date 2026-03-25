# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import sys
import json
import copy
import random
import shutil
import typing
import asyncio
import webbrowser
from loguru import logger
from rich.text import Text
from rich.console import Console
from rich.logging import (
    LogRecord, RichHandler
)
from engine.terminal import Terminal
from mindcore.design import (
    Design, TypewriterStreamSession
)
from mindnova import const


class _MindBaseError(BaseException):
    """_MindBaseError class."""
    pass


class MindError(_MindBaseError):
    """MindError class."""

    def __init__(self, msg: typing.Any):
        self.msg = msg

    def __str__(self):
        return f"<{const.APP_DESC}Error> {self.msg}"

    __repr__ = __str__


class Active(object):
    """Active class."""

    class _RichSink(RichHandler):
        debug_color = [
            "#00E5FF",  # 电青
            "#39FF14",  # 霓虹绿
            "#FF2D95",  # 霓虹粉
        ]
        info_color = [
            "#FFD300",  # 电黄
            "#7CFF6B",  # 亮绿
            "#64748B",  # 蓝灰
        ]
        level_style = {
            "DEBUG"    : f"bold {random.choice(debug_color)}",
            "INFO"     : f"bold {random.choice(info_color)}",
            "WARNING"  : "bold #FFD700",
            "ERROR"    : "bold #FF4500",
            "CRITICAL" : "bold #FF1493",
        }

        def __init__(self, console: "Console"):
            super().__init__(
                console=console,
                rich_tracebacks=True,
                show_path=False,
                show_time=False,
                markup=False
            )

        def emit(self, record: "LogRecord") -> None:
            self.console.print(
                const.PRINT_HEAD, Text(self.format(record), style=self.level_style.get(
                    record.levelname, "bold #ADD8E6"
                ))
            )

    @staticmethod
    def active(log_level: str) -> None:
        logger.remove()
        logger.add(
            Active._RichSink(Design.console), level=log_level, format=const.PRINT_FORMAT
        )


class FileAssist(object):
    """FileAssist class."""

    CHROME_CANDIDATES_WIN = (
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    )

    @staticmethod
    async def open(file: str) -> typing.Optional[str]:
        if sys.platform == "win32":
            cmd = ["notepad++"] if shutil.which("notepad++") else ["Notepad"]
        else:
            cmd = ["open", "-W", "-a", "TextEdit"]
        return await Terminal.cmd_line(cmd + [file])

    @staticmethod
    async def open_url(url: str) -> None:
        if sys.platform == "darwin":
            if os.path.exists("/Applications/Google Chrome.app"):
                await Terminal.cmd_link(["open", "-a", "Google Chrome", url])
                return None

        elif sys.platform == "win32":
            chrome = FileAssist._find_windows_chrome()
            if chrome:
                await Terminal.cmd_link([chrome, url])
                return None

        else:
            for chrome_cmd in ("google-chrome", "google-chrome-stable", "chrome", "chromium", "chromium-browser"):
                if shutil.which(chrome_cmd):
                    await Terminal.cmd_link([chrome_cmd, url])
                    return None

        await asyncio.to_thread(webbrowser.open, url)

    @staticmethod
    def _find_windows_chrome() -> typing.Optional[str]:
        chrome_exec = shutil.which("chrome") or shutil.which("chrome.exe")
        if chrome_exec:
            return chrome_exec

        for candidate in FileAssist.CHROME_CANDIDATES_WIN:
            if candidate and os.path.exists(candidate):
                return candidate
        return None

    @staticmethod
    def read_json(file: str) -> dict:
        with open(file, "r", encoding=const.CHARSET) as f:
            return json.loads(f.read())

    @staticmethod
    def dump_json(src: str, dst: dict) -> None:
        with open(src, "w", encoding=const.CHARSET) as f:
            f.write(json.dumps(dst, indent=4, separators=(",", ":"), ensure_ascii=False))


class Tooling(object):

    @staticmethod
    def normalize_openai_schema(schema: typing.Any) -> dict[str, typing.Any]:
        """
        将 MCP inputSchema 归一化为 OpenAI function parameters
        可接受的保守 JSON Schema。

        这里不追求完整保留原始 schema 语义，而是优先保证：
        1. 顶层一定是 object
        2. properties 一定是 dict
        3. required 一定是 list
        4. 属性定义缺失或异常时统一降级，避免把复杂 schema 直接透传给上游
        5. 显式关闭 additionalProperties，减少参数漂移
        """
        if not isinstance(schema, dict) or not schema:
            return {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            }

        schema = copy.deepcopy(schema)

        if schema.get("type") != "object":
            return {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            }

        properties = schema.get("properties")
        if not isinstance(properties, dict):
            schema["properties"] = {}

        required = schema.get("required")
        if required is None:
            schema["required"] = []
        elif not isinstance(required, list):
            schema["required"] = []

        cleaned_properties: dict[str, dict[str, typing.Any]] = {}
        for name, prop in schema["properties"].items():
            if not isinstance(prop, dict):
                cleaned_properties[name] = {
                    "type": "string",
                    "description": f"{name}",
                }
                continue

            prop = copy.deepcopy(prop)

            if "type" not in prop:
                prop["type"] = "string"

            cleaned_properties[str(name)] = prop

        schema["properties"] = cleaned_properties
        schema["additionalProperties"] = False

        return schema

    @staticmethod
    def filter_tools(
        openai_tools: list[dict[str, typing.Any]],
        tool_meta: dict[str, dict[str, typing.Any]],
        domains: typing.Optional[typing.Iterable[str]] = None,
        classes: typing.Optional[typing.Iterable[str]] = None,
        *,
        include_hidden: bool = False,
        exclude: typing.Optional[list[dict[str, typing.Any]]] = None
    ) -> list[dict[str, typing.Any]]:
        """
        - domains/classes: allowlist（AND 叠加：传哪个就按哪个过滤）
        - exclude: 排除规则列表；每条规则是 AND 匹配（命中就剔除）
          支持键：domain / class / name
          例：exclude=[{"domain":"media","class":"scrcpy"}]
        """
        want_domain = {
            str(d).strip() for d in domains if str(d).strip()
        } if domains else None

        want_class = {
            str(c).strip() for c in classes if str(c).strip()
        } if classes else None

        exclude = exclude or []

        out: list[dict[str, typing.Any]] = []

        for item in openai_tools:
            func = (item or {}).get("function") or {}
            name = func.get("name")
            if not name: continue

            meta = tool_meta.get(name) or {}

            if not include_hidden and bool(meta.get("hidden", False)):
                continue

            if want_domain is not None and meta.get("domain") not in want_domain:
                continue

            if want_class is not None and meta.get("class") not in want_class:
                continue

            # 排除规则：每条规则内部是 AND
            hit_exclude = False
            for rule in exclude:
                if not isinstance(rule, dict):
                    continue
                ok = True
                if "name" in rule:
                    ok = ok and (name == rule["name"])
                if "domain" in rule:
                    ok = ok and (meta.get("domain") == rule["domain"])
                if "class" in rule:
                    ok = ok and (meta.get("class") == rule["class"])
                if ok:
                    hit_exclude = True
                    break

            if hit_exclude:
                continue
            out.append(item)

        return out

    @staticmethod
    def needs_wakeup(
        meta_map: dict[str, dict[str, typing.Any]],
        name: str
    ) -> bool:
        """判断某工具是否需要“连接/设备准备”等前置动作。"""
        cls = str((meta_map.get(name) or {}).get("class") or "")
        return cls not in {
            "tool", "framix", "nexus", "inspect", "security", "runtime", "audio", "ffmpeg"
        }

    @staticmethod
    def summarize_tool_arguments(tool_name: str, tool_args: typing.Any) -> str:
        def short_text(raw_value: typing.Any, limit: int = 48) -> str:
            text = str(raw_value).replace("\n", " ").strip()
            return text if len(text) <= limit else f"{text[:limit - 3]}..."

        def short_value(raw_value: typing.Any) -> str:
            if isinstance(raw_value, str):
                return short_text(raw_value)
            if isinstance(raw_value, bool):
                return "true" if raw_value else "false"
            if raw_value is None:
                return "null"
            if isinstance(raw_value, (int, float)):
                return str(raw_value)
            if isinstance(raw_value, list):
                return f"[{len(raw_value)} items]"
            if isinstance(raw_value, dict):
                keys = list(raw_value.keys())
                head = ", ".join(map(str, keys[:3]))
                suffix = "" if len(keys) <= 3 else f", +{len(keys) - 3}"
                return f"{{{head}{suffix}}}"
            return short_text(raw_value)

        if not isinstance(tool_args, dict):
            summary = short_text(tool_args, 120)
            return f"{tool_name}: {summary}" if tool_name else summary

        parts: list[str] = []
        for key, value in list(tool_args.items())[:4]:
            parts.append(f"{key}={short_value(value)}")

        if len(tool_args) > 4:
            parts.append(f"+{len(tool_args) - 4} fields")

        summary = ", ".join(parts)
        return f"{tool_name}: {summary}" if tool_name else summary


class StreamTyperLogger(object):
    """打字机 + 流式转录日志。"""

    STREAM          = "stream"
    BLOCK           = "block"
    ELLIPSIS        = " ..."
    MIN_LINE_LIMIT  = 48
    MAX_LINE_LIMIT  = 160
    LINE_PADDING    = 6
    MIN_BLOCK_LIMIT = 96
    MAX_BLOCK_LIMIT = 320
    BLOCK_LINES     = 2

    def __init__(self, log_file: str) -> None:
        self.log_file = log_file
        self.buffer: str = ""
        self.fp: typing.Optional[typing.TextIO] = None
        self.typewriter: TypewriterStreamSession = TypewriterStreamSession()
        self.display_segments: list[dict[str, str]] = []
        self.display_text: str = ""
        self.log_at_line_start: bool = True
        self.display_at_line_start: bool = True

    async def start(self) -> None:
        await self.typewriter.start()

    async def stop(self) -> None:
        self.flush()
        await self.typewriter.stop()

        if self.fp:
            try:
                self.fp.flush()
            finally:
                self.fp.close()
            self.fp = None

    async def open(self) -> None:
        if self.fp: return None

        os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
        self.fp = open(
            self.log_file, "a", encoding=const.CHARSET, buffering=1, newline=""
        )

    async def feed(
        self,
        chunk: typing.Optional[str],
        *,
        echo: bool = True,
        display: str = STREAM,
        display_chunk: typing.Optional[str] = None
    ) -> None:
        """Feed"""
        if not chunk: return None

        delta = str(chunk)
        visible_delta = str(display_chunk) if display_chunk is not None else delta
        echo_now = bool(echo)

        if display == self.BLOCK:
            # BLOCK 统一按“段落”处理：外部不传分隔换行，内部负责按需断行并在块后留空行。
            delta = self._normalize_block_text(delta, at_line_start=self.log_at_line_start)
            visible_delta = self._normalize_block_text(
                visible_delta, at_line_start=self.display_at_line_start
            )

        if not delta:
            return None

        # 1) ==== 全量落盘 ====
        self.buffer += delta
        while True:
            if (pos := self.buffer.find("\n")) < 0:
                break
            line = self.buffer[:pos + 1]
            self.buffer = self.buffer[pos + 1:]
            if self.fp:
                self.fp.write(line)

        self.log_at_line_start = delta.endswith("\n")

        if not echo_now:
            return None

        if not visible_delta:
            return None

        self._append_segment(display, visible_delta)
        visible = self._compose_visible_text()
        animate = (display == self.STREAM and visible.startswith(self.display_text))
        self.display_text = visible
        self.display_at_line_start = visible_delta.endswith("\n")
        await self.typewriter.sync(visible, animate=animate)

    def _append_segment(self, display: str, delta: str) -> None:
        if (
            display == self.STREAM
            and self.display_segments
            and self.display_segments[-1]["mode"] == self.STREAM
        ):
            self.display_segments[-1]["text"] += delta
            return None

        self.display_segments.append({"mode": display, "text": delta})

    def _compose_visible_text(self) -> str:
        parts: list[str] = []
        line_limit = self._line_limit()
        block_limit = self._block_limit(line_limit)

        for segment in self.display_segments:
            mode = segment["mode"]
            text = segment["text"]
            if mode == self.BLOCK:
                parts.append(self._render_block(text, block_limit))
                continue
            parts.append(self._render_stream(text, line_limit))

        return "".join(parts)

    def _render_block(self, delta: str, limit: int) -> str:
        parts: list[str] = []

        visible: int = 0
        trimmed: bool = False

        for ch in delta:
            if ch == "\n":
                parts.append(ch)
                continue

            if visible < limit:
                parts.append(ch)
                visible += 1
                continue

            trimmed = True
            break

        out = "".join(parts)

        if trimmed:
            self._trim_visible_tail(parts, limit, len(self.ELLIPSIS))
            out = "".join(parts).rstrip("\n")
            if not out.endswith(self.ELLIPSIS):
                out = f"{out}{self.ELLIPSIS}"
            if delta.endswith("\n") and not out.endswith("\n"):
                out += "\n"

        return out

    def _render_stream(self, delta: str, limit: int) -> str:
        parts: list[str] = []

        line_start = 0
        line_len = 0
        line_cut = False

        for ch in delta:
            if ch == "\n":
                parts.append("\n")
                line_start = len(parts)
                line_len = 0
                line_cut = False
                continue

            if line_cut:
                continue

            if line_len < limit:
                parts.append(ch)
                line_len += 1
                continue

            need = max(0, line_len - (limit - len(self.ELLIPSIS)))
            removed = self._trim_tail(parts, line_start, need)
            if removed == need:
                parts.append(self.ELLIPSIS)
            line_cut = True

        return "".join(parts)

    @staticmethod
    def _normalize_block_text(text: str, *, at_line_start: bool) -> str:
        out = text.strip("\n")
        if not out:
            return "\n" if not at_line_start else ""
        prefix = "" if at_line_start else "\n"
        return f"{prefix}{out}\n\n"

    def _line_limit(self) -> int:
        width = max(0, int(getattr(Design.console, "width", 0) or 0))
        limit = width - self.LINE_PADDING
        return max(self.MIN_LINE_LIMIT, min(self.MAX_LINE_LIMIT, limit))

    def _block_limit(self, line_limit: int) -> int:
        limit = line_limit * self.BLOCK_LINES
        return max(self.MIN_BLOCK_LIMIT, min(self.MAX_BLOCK_LIMIT, limit))

    @staticmethod
    def _trim_tail(parts: list[str], line_start: int, count: int) -> int:
        removed = 0
        while count > 0 and len(parts) > line_start:
            parts.pop()
            count -= 1
            removed += 1
        return removed

    @staticmethod
    def _trim_visible_tail(parts: list[str], limit: int, reserve: int) -> None:
        keep = max(0, limit - reserve)
        visible = 0
        kept: list[str] = []

        for ch in parts:
            if ch == "\n":
                kept.append(ch)
                continue
            if visible >= keep:
                continue
            kept.append(ch)
            visible += 1

        parts[:] = kept

    def flush(self) -> None:
        if not self.buffer:
            return None

        line = self.buffer
        self.buffer = ""

        if not line.endswith("\n"):
            line += "\n"

        if self.fp:
            self.fp.write(line)
            self.fp.flush()


if __name__ == '__main__':
    pass
