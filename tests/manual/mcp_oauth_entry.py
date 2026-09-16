# -*- coding: utf-8 -*-

"""用隔离目录及合成凭据验证运行入口，证据不包含命令输出或真实账户信息。"""

import argparse
import json
import os
import platform
import subprocess
import tempfile
import typing

import anyio
from datetime import (
    datetime,
    timezone,
)
from importlib.metadata import version
from pathlib import Path

from pydantic import BaseModel

from agent.domain.mcp_oauth import McpOAuthTarget
from infrastructure.mcp.oauth_credentials import SystemMcpCredentialStore
from metadata import const
from tests.manual.mcp_credential_storage import run_operation


class _Status(BaseModel):
    """只接收运行入口公开的本地凭据状态。"""

    state: str
    expires_at: float | None
    error: str | None


class _Server(BaseModel):
    """在子进程输出边界验证所需字段，不使用未验证字典。"""

    name: str
    oauth: _Status


class _Report(typing.TypedDict):
    """保存无机密的入口检查结果，不代表真实 OAuth 账户验收。"""

    time_utc: str
    platform: str
    version: str
    mcp_sdk: str
    keyring: str
    entry_kind: typing.Literal["source", "installed"]
    service: str
    status: str
    checks: list[str]
    real_service: str


async def require_deleted(root: Path) -> None:
    """从另一个存储实例确认运行入口确实删除了原命名空间的凭据。"""
    store = SystemMcpCredentialStore(config_root=root / "config", state_root=root / "state")
    record = await store.read(McpOAuthTarget("credential-storage-acceptance", "https://example.invalid/mcp"))
    if record.snapshot is not None:
        raise RuntimeError("Entry logout did not remove the synthetic credential")


def check_entry(command: list[str], entry_kind: typing.Literal["source", "installed"]) -> _Report:
    """从隔离工作目录调用指定入口；配置和环境仅派生后传给子进程。"""
    checks: list[str] = []
    service = "credential-storage-acceptance"
    with tempfile.TemporaryDirectory(prefix="mcp-oauth-entry-") as directory:
        root = Path(directory)
        environment = {
            **os.environ,
            "MIND_HOME": str(root / "config"),
            "MIND_STATE_HOME": str(root / "state"),
            "MIND_NO_UPDATE_CHECK": "1",
            "PYTHONPATH": "",
            "PYTHONUTF8": "1",
        }

        def cli(label: str, arguments: list[str], *, expected: int = 0) -> str:
            """检查返回码和合成机密泄露，不将子进程原始文本写入报告。"""
            result = subprocess.run(
                [*command, *arguments], cwd=root, env=environment,
                capture_output=True, text=True, encoding=const.CHARSET, errors="replace", timeout=60,
            )
            if result.returncode != expected:
                raise RuntimeError(f"{label}: expected exit {expected}, received {result.returncode}")
            if any(secret in result.stdout + result.stderr for secret in ("synthetic-access-", "synthetic-refresh-")):
                raise RuntimeError(f"{label}: synthetic credential appeared in output")
            checks.append(label)
            return result.stdout

        try:
            entry_version = cli("version", ["--version"]).strip()
            if entry_version != f"{const.APP_DESC} {const.APP_VERSION}":
                raise RuntimeError("Entry version does not match the source used for verification")
            cli("login_help", ["mcp", "login", "--help"])
            cli("logout_help", ["mcp", "logout", "--help"])
            cli("login_syntax", ["mcp", "login"], expected=2)
            cli("registration", ["mcp", "add", service, "--url", "https://example.invalid/mcp"])
            missing = _Server.model_validate_json(cli("missing", ["mcp", "get", service, "--json"]))
            if missing.oauth.state != "missing":
                raise RuntimeError("Entry credential backend is unavailable or not isolated")
            anyio.run(run_operation, root, "write")
            restored = _Server.model_validate_json(cli("restore", ["mcp", "get", service, "--json"]))
            if restored.name != service or restored.oauth != _Status(state="expired", expires_at=2000, error=None):
                raise RuntimeError("Entry did not restore the synthetic credential and its expiration")
            cli("logout", ["mcp", "logout", service])
            anyio.run(require_deleted, root)
            cli("repeated_logout", ["mcp", "logout", service])
            cleared = _Server.model_validate_json(cli("cleared", ["mcp", "get", service, "--json"]))
            if cleared.oauth.state != "missing":
                raise RuntimeError("Credential remained visible after logout")
            cli("remove_registration", ["mcp", "remove", service])
        finally:
            anyio.run(run_operation, root, "delete")
    return {
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "version": entry_version,
        "mcp_sdk": version("mcp"),
        "keyring": version("keyring"),
        "entry_kind": entry_kind,
        "service": "synthetic-local-credential",
        "status": "passed",
        "checks": checks,
        "real_service": "pending",
    }


def main() -> None:
    """运行指定命令，并仅向指定 reports 路径创建新的脱敏证据。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--entry-kind", choices=("source", "installed"), default="source")
    parser.add_argument("--command", nargs=argparse.REMAINDER, required=True)
    arguments = parser.parse_args()
    if not arguments.command:
        parser.error("--command requires the absolute Python/source paths or installed entry")
    entry_kind: typing.Literal["source", "installed"] = "installed" if arguments.entry_kind == "installed" else "source"
    report = check_entry(arguments.command, entry_kind)
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    with arguments.report.open("x", encoding=const.CHARSET) as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print("MCP OAuth entry, system credential restore, expiration and logout passed.")


if __name__ == "__main__":
    main()
