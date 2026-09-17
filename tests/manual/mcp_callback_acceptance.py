# -*- coding: utf-8 -*-

"""以真实 PTY、源码 CLI、本机 HTTP OAuth 和系统凭据库验证隐藏回调及终端恢复。"""

import argparse
import json
import os
import platform
import re
import socket
import subprocess
import sys
from pathlib import Path

import httpx
from pydantic import BaseModel

from infrastructure.config.store import ConfigStore
from tests.manual.oauth_http_fixture import OAuthHttpFixture
from tests.pty import (
    PtyKey,
    TerminalSize,
    spawn_terminal,
)


def terminal_state() -> str:
    """在验收进程中读取系统输入模式，仅用于前后相等比较，不记录输入。"""
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetConsoleMode.restype = wintypes.BOOL
        mode = wintypes.DWORD()
        if not kernel.GetConsoleMode(wintypes.HANDLE(msvcrt.get_osfhandle(sys.stdin.fileno())), ctypes.byref(mode)):
            raise OSError("Terminal state unavailable")
        return str(mode.value)
    import termios
    return repr(termios.tcgetattr(sys.stdin.fileno()))


def child(timeout: float) -> None:
    """运行生产 CLI 路由和组合工厂，记录退出码后验证普通回显输入仍可使用。"""
    from frontends.cli.entry import run
    from mind import create_mcp_oauth_service

    before = terminal_state()
    code = run(arguments=["mcp", "login", "acceptance", "--manual", "--timeout-sec", str(timeout)], mcp_oauth_factory=create_mcp_oauth_service)
    print(f"CLI_EXIT={code} RESTORED={terminal_state() == before}", flush=True)
    assert input("RESTORE_CHECK> ") == "echo-restored"
    print("ECHO_OK", flush=True)


class _Authorization(BaseModel):
    """只读取公开凭据状态，忽略 CLI 其他配置字段。"""

    credentials: str


class _CliServer(BaseModel):
    """校验子进程 JSON 返回的认证状态。"""

    authorization: _Authorization


def run_case(directory: Path, scenario: str, repository: Path) -> dict[str, str | int | bool]:
    """每场景创建真实协议服务和独立凭据命名空间，结束时删除合成凭据。"""
    root = directory / scenario
    root.mkdir()
    fixture = OAuthHttpFixture(block_token=scenario in ("token_cancel", "token_timeout", "logout"))
    environment = {
        **os.environ, "MIND_HOME": str(root / "config"), "MIND_STATE_HOME": str(root / "state"),
        "MIND_NO_UPDATE_CHECK": "1", "PYTHONUTF8": "1", "PYTHONPATH": str(repository),
    }
    ConfigStore(root / "config/config.toml").update({("mcp_servers", "acceptance"): {"url": fixture.base + "/mcp"}})

    def cli(action: str) -> subprocess.CompletedProcess[str]:
        """调用独立源码进程，输出只在内存中供状态校验。"""
        return subprocess.run(
            [sys.executable, str(repository / "mind.py"), "mcp", action, "acceptance", *( ["--json"] if action == "get" else [])],
            cwd=root, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=20,
        )

    expected = 0 if scenario in ("paste", "bracketed_paste", "loopback") else 130 if scenario in ("cancel", "paste_cancel", "token_cancel") else 1
    timeout = 5 if scenario in ("timeout", "token_timeout") else 20
    try:
        with spawn_terminal(
            [sys.executable, "-m", "tests.manual.mcp_callback_acceptance", "--child", "--timeout", str(timeout)],
            cwd=root, env=environment, size=TerminalSize(rows=30, columns=1000),
        ) as terminal:
            terminal.wait_for_screen_text("Callback URL (input hidden", timeout=15)
            text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", terminal.session.output_text())
            match = re.search(r"http://127\.0\.0\.1:\d+/authorize\?\S+", text)
            assert match is not None
            with httpx.Client(trust_env=False, follow_redirects=False) as browser:
                response = browser.get(match.group())
                assert response.status_code == 302
                callback = httpx.URL(response.headers["Location"])
                if scenario == "loopback":
                    assert browser.get(callback).status_code == 200
                elif scenario == "cancel":
                    terminal.send_key(PtyKey.CTRL_C)
                elif scenario == "paste_cancel":
                    terminal.write_user_text("\x1b[200~" + fixture.code)
                    terminal.send_key(PtyKey.CTRL_C)
                elif scenario == "eof":
                    terminal.write_user_text("\x04")
                elif scenario != "timeout":
                    if scenario == "wrong_state":
                        callback = callback.copy_set_param("state", "wrong")
                    elif scenario == "wrong_issuer":
                        callback = callback.copy_set_param("iss", "https://wrong.invalid")
                    elif scenario == "denied":
                        callback = callback.copy_remove_param("code").copy_add_param("error", "access_denied")
                    value = str(callback) if scenario not in ("oversize", "unfinished_paste") else fixture.code + "x" * (64 * 1024)
                    if scenario in ("bracketed_paste", "unfinished_paste"):
                        value = "\x1b[200~" + value + ("\x1b[201~" if scenario == "bracketed_paste" else "")
                    terminal.write_user_text(value)
                    if scenario not in ("oversize", "unfinished_paste"):
                        terminal.send_key(PtyKey.ENTER)
                    if scenario in ("token_cancel", "token_timeout", "logout"):
                        assert fixture.token_entered.wait(10)
                        if scenario == "token_cancel":
                            terminal.send_key(PtyKey.CTRL_C)
                        elif scenario == "logout":
                            assert cli("logout").returncode == 0
                            fixture.token_release.set()
            terminal.wait_for_screen_text(f"CLI_EXIT={expected} RESTORED=True", timeout=15)
            terminal.write_user_text("echo-restored")
            terminal.send_key(PtyKey.ENTER)
            assert terminal.wait_for_exit(10) == 0
            output = terminal.session.output_text()
            if scenario in ("oversize", "unfinished_paste"):
                assert "OAuth callback URL exceeds 64 KiB." in output
            assert "ECHO_OK" in output and "echo-restored" in output
            assert fixture.code not in output and "synthetic-private-access" not in output and "synthetic-private-refresh" not in output
            pid = terminal.session.pid
            assert callback.port is not None
            try:
                connection = socket.create_connection((callback.host, callback.port), timeout=0.3)
            except OSError:
                pass
            else:
                connection.close()
                raise AssertionError("Callback listener remained open")
            result = cli("get")
            assert result.returncode == 0
            credentials = _CliServer.model_validate_json(result.stdout).authorization.credentials
            assert credentials == ("stored" if expected == 0 else "missing")
            assert fixture.token_calls == (1 if scenario in ("paste", "bracketed_paste", "loopback", "token_cancel", "token_timeout", "logout") else 0)
            return {"scenario": scenario, "passed": True, "pid": pid, "exit_code": expected, "terminal_restored": True, "callback_closed": True, "input_hidden": True, "token_calls": fixture.token_calls}
    finally:
        fixture.close()
        assert cli("logout").returncode == 0


def main() -> None:
    """独占创建脱敏报告目录，子进程模式只供验收驱动调用。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--scenario")
    args = parser.parse_args()
    if args.child:
        child(args.timeout)
        return
    if args.directory is None:
        parser.error("--directory is required")
    repository = args.repository.resolve()
    if not (repository / "mind.py").is_file():
        parser.error("--repository must be the source repository root")
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    scenarios = ("paste", "bracketed_paste", "loopback", "wrong_state", "wrong_issuer", "denied", "oversize", "unfinished_paste", "paste_cancel", "cancel", "eof", "timeout", "token_cancel", "token_timeout", "logout")
    if args.scenario is not None and args.scenario not in scenarios:
        parser.error("Unknown acceptance scenario")
    reports = []
    try:
        for scenario in scenarios:
            if args.scenario is not None and args.scenario != scenario:
                continue
            reports.append(run_case(directory, scenario, repository))
            print(f"{scenario}: passed", flush=True)
    finally:
        with (directory / "report.json").open("x", encoding="utf-8") as stream:
            json.dump({"platform": platform.platform(), "entry_kind": "source-cli-pty-local-http-oauth", "terminal": sys.stdin.isatty(), "scenarios": reports}, stream, indent=2)


if __name__ == "__main__":
    main()
