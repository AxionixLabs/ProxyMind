# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import json
import typing
import tempfile
import contextlib
from pathlib import Path
from mind_nova import const
from .config import McpConfigError


class McpServerRegistry(object):
    """保真读写外部 MCP 服务注册表。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def list(self) -> tuple[tuple[str, dict[str, typing.Any]], ...]:
        """返回按名称排序的服务配置。"""
        _, servers = self._load()
        return tuple(
            (name, dict(value))
            for name, value in sorted(servers.items())
        )

    def get(self, name: str) -> dict[str, typing.Any]:
        """返回指定服务的原始配置。"""
        key = self._name(name)
        _, servers = self._load()
        value = servers.get(key)

        if not isinstance(value, dict):
            raise McpConfigError(f"MCP server not found: {key}")
        return dict(value)

    def add(self, name: str, value: dict[str, typing.Any]) -> None:
        """添加一项服务配置。"""
        key = self._name(name)
        document, servers = self._load()

        if key in servers:
            raise McpConfigError(f"MCP server already exists: {key}")
        servers[key] = dict(value)
        self._write(document)

    def remove(self, name: str) -> dict[str, typing.Any]:
        """删除并返回指定服务配置。"""
        key = self._name(name)
        document, servers = self._load()
        value = servers.get(key)

        if not isinstance(value, dict):
            raise McpConfigError(f"MCP server not found: {key}")
        removed = dict(value)
        del servers[key]
        self._write(document)
        return removed

    def set_enabled(self, name: str, enabled: bool) -> None:
        """设置指定服务的启用状态。"""
        key = self._name(name)
        document, servers = self._load()
        value = servers.get(key)

        if not isinstance(value, dict):
            raise McpConfigError(f"MCP server not found: {key}")
        updated = dict(value)
        updated["enabled"] = enabled
        servers[key] = updated
        self._write(document)

    @staticmethod
    def _name(value: str) -> str:
        """验证服务名称。"""
        name = str(value or "").strip()
        if not name:
            raise McpConfigError("MCP server name is empty")
        return name

    def _load(
        self,
    ) -> tuple[dict[str, typing.Any], dict[str, typing.Any]]:
        """读取原始文档和服务映射。"""
        try:
            text = self.path.read_text(encoding=const.CHARSET)
        except FileNotFoundError:
            document: dict[str, typing.Any] = {"mcpServers": {}}
        except OSError as error:
            raise McpConfigError(
                f"MCP config is not readable: {self.path}"
            ) from error
        else:
            if not text.strip():
                document = {"mcpServers": {}}
            else:
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError as error:
                    raise McpConfigError(
                        f"Invalid MCP config {self.path.name} at "
                        f"line {error.lineno}, column {error.colno}: "
                        f"{error.msg}"
                    ) from error
                if not isinstance(parsed, dict):
                    raise McpConfigError("MCP config root must be an object")
                document = dict(parsed)

        raw_servers = document.get("mcpServers")
        if raw_servers is None:
            servers: dict[str, typing.Any] = {}
            document["mcpServers"] = servers
        elif isinstance(raw_servers, dict):
            servers = raw_servers
        else:
            raise McpConfigError("MCP config mcpServers must be an object")

        invalid = next(
            (
                name
                for name, value in servers.items()
                if not isinstance(value, dict)
            ),
            None,
        )
        if invalid is not None:
            raise McpConfigError(
                f"MCP server configuration must be an object: {invalid}"
            )
        return document, servers

    def _write(self, document: dict[str, typing.Any]) -> None:
        """在同目录中原子替换配置文件。"""
        descriptor: int | None = None
        temporary_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
            )
            temporary_path = Path(temporary)
            with os.fdopen(
                descriptor,
                "w",
                encoding=const.CHARSET,
                newline="\n",
            ) as stream:
                descriptor = None
                json.dump(document, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
        except (OSError, TypeError, ValueError) as error:
            raise McpConfigError(
                f"MCP config is not writable: {self.path}"
            ) from error
        finally:
            if descriptor is not None:
                with contextlib.suppress(OSError):
                    os.close(descriptor)
            if temporary_path is not None:
                with contextlib.suppress(OSError):
                    temporary_path.unlink(missing_ok=True)


if __name__ == "__main__":
    pass
