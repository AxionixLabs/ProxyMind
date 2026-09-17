# -*- coding: utf-8 -*-

"""在独立配置与系统凭据命名空间，从真实终端调用源码 CLI 和 TUI。"""

import argparse
import json
import os
import subprocess
import sys
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path

from pydantic import BaseModel

from agent.domain.mcp_authorization import McpAuthorizationStatus
from infrastructure.config.store import ConfigStore
from metadata import const


class CliAuthorization(BaseModel):
    """只接收 CLI 的公开认证事实，报告不保存配置或输出全文。"""

    authorization: McpAuthorizationStatus


def setup(directory: Path, source: Path, server: str) -> None:
    """派生独立配置，保持模型连接可用但只启用指定验收服务。"""
    directory.mkdir(parents=True, exist_ok=False)
    config = ConfigStore(source).read_raw(create=False)
    servers = config.get("mcp_servers")
    if not isinstance(servers, dict) or server not in servers:
        raise ValueError("acceptance_registration_missing")
    config["mcp_servers"] = {server: servers[server]}
    ConfigStore(directory / "config" / "config.toml").update({(key,): value for key, value in config.items()})
    (directory / "workspace").mkdir()
    (directory / "state" / "reports").mkdir(parents=True)
    print("Isolated source acceptance configuration created; no credentials copied.")


def main() -> None:
    """运行显式动作，凭据环境只派生给子进程，退出码和认证事实另存为脱敏证据。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--source-config", type=Path)
    parser.add_argument("--server", default="sentry")
    parser.add_argument("action", choices=("setup", "get", "list", "login", "logout", "tui"))
    args = parser.parse_args()
    directory = args.directory.resolve()
    if args.action == "setup":
        if args.source_config is None:
            parser.error("setup requires --source-config")
        setup(directory, args.source_config, args.server)
        return
    repository = Path(__file__).resolve().parents[2]
    environment = {
        **os.environ, "MIND_HOME": str(directory / "config"),
        "MIND_STATE_HOME": str(directory / "state"), "MIND_NO_UPDATE_CHECK": "1",
        "PYTHONUTF8": "1", "PYTHONPATH": "",
    }
    arguments = [] if args.action == "tui" else ["mcp", args.action]
    if args.action not in ("list", "tui"):
        arguments.append(args.server)
    if args.action == "get":
        arguments.append("--json")
    if args.action == "login":
        arguments.extend(["--timeout-sec", "600"])
    captured = args.action == "get"
    result = subprocess.run(
        [sys.executable, str(repository / "mind.py"), *arguments],
        cwd=directory / "workspace", env=environment,
        capture_output=captured, text=True, encoding=const.CHARSET,
    )
    authorization = None
    if captured and result.returncode == 0:
        authorization = CliAuthorization.model_validate_json(result.stdout)
        print(authorization.model_dump_json(indent=2))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    report = directory / "state" / "reports" / f"{stamp}-{args.action}.json"
    with report.open("x", encoding=const.CHARSET) as stream:
        json.dump({
            "entry_kind": "source-cli-tty", "terminal": sys.stdin.isatty(),
            "action": args.action, "exit_code": result.returncode,
            "authorization": authorization.model_dump()["authorization"] if authorization is not None else None,
        }, stream, indent=2)
    print(f"Source {args.action} exit: {result.returncode}. Report: {report}", flush=True)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
