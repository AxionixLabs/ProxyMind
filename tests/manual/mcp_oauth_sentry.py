# -*- coding: utf-8 -*-

"""从独立验收配置调用真实 Sentry，只记录凭据版本、有效期与调用状态。"""

import argparse
import asyncio
import json
import math
import platform
import time
import typing
from dataclasses import (
    asdict,
    dataclass,
)
from datetime import (
    datetime,
    timezone,
)
from importlib.metadata import version
from pathlib import Path

from mcp.shared.exceptions import McpError

from agent.domain.mcp_oauth import (
    McpOAuthCredentialRecord,
    McpOAuthError,
    McpOAuthStorageError,
    McpOAuthTarget,
)
from infrastructure.config.mcp_oauth import McpOAuthServerSettings
from infrastructure.config.paths import (
    default_config_home,
    default_state_home,
)
from infrastructure.config.store import ConfigStore
from infrastructure.mcp.external_group import ExternalMcpGroup
from infrastructure.mcp.oauth_credentials import SystemMcpCredentialStore
from infrastructure.mcp.settings import normalize_mcp_servers
from metadata import const


Mode = typing.Literal["call", "wait-expiry", "observe-logout", "logged-out"]


class LiveCheckError(RuntimeError):
    """用固定验收失败码报告步骤，不携带第三方正文或账户信息。"""

    def __init__(self, code: typing.Literal[
        "logout_boundary_failed", "connection_failed", "readonly_tool_missing",
        "readonly_call_failed", "refresh_evidence_missing", "resources_not_closed",
        "logged_out_boundary_failed",
    ]) -> None:
        """保存预定义失败步骤。"""
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CredentialEvidence:
    """保存可公开的凭据事实，禁止添加令牌、客户端身份或服务 URL。"""

    generation: int
    present: bool
    expires_at: float | None
    has_refresh_token: bool
    recovery: str | None


@dataclass
class LiveReport:
    """保存真实服务运行证据；不包含工具正文或原始异常信息。"""

    time_utc: str
    platform: str
    version: str
    mcp_sdk: str
    keyring: str
    mode: Mode
    entry_kind: str = "source-runtime-adapter"
    service: str = "sentry-acceptance"
    status: str = "running"
    before: CredentialEvidence | None = None
    after: CredentialEvidence | None = None
    expired_before_connection: bool = False
    call_succeeded: bool = False
    refresh_committed: bool = False
    logout_observed: bool = False
    tools_after: int = 0
    authorization_error: str | None = None
    resources_closed: bool = False
    error_code: str | None = None


def credential_evidence(record: McpOAuthCredentialRecord) -> CredentialEvidence:
    """从已验证记录中显式选择非机密标量，绝不序列化完整快照。"""
    snapshot = record.snapshot
    token = snapshot.token if snapshot is not None else None
    return CredentialEvidence(
        generation=record.generation,
        present=token is not None,
        expires_at=token.expires_at if token is not None else None,
        has_refresh_token=token is not None and token.refresh_token is not None,
        recovery=snapshot.recovery if snapshot is not None else None,
    )


def refresh_committed(
    before: McpOAuthCredentialRecord,
    after: McpOAuthCredentialRecord,
    *,
    connected_at: float,
    observed_at: float,
) -> bool:
    """用真实到期、轮换提交和有效期变化证明刷新，成功调用本身不能替代证据。"""
    previous = before.snapshot
    current = after.snapshot
    if previous is None or current is None or previous.token is None or current.token is None:
        return False
    old = previous.token
    new = current.token
    return (
        previous.target == current.target
        and previous.client == current.client
        and old.refresh_token is not None
        and old.expires_at is not None
        and new.expires_at is not None
        and old.expires_at <= connected_at
        and new.expires_at > max(old.expires_at, observed_at)
        and old.access_token != new.access_token
        and after.generation == before.generation + 2
        and current.recovery is None
    )


async def wait_for_expiry(evidence: CredentialEvidence, timeout: float) -> None:
    """等待记录中的真实绝对有效期，不改写时间或注入模拟时钟。"""
    if not evidence.present or not evidence.has_refresh_token or evidence.expires_at is None:
        raise McpOAuthError("reauthorization_required")
    async with asyncio.timeout(timeout):
        while (remaining := evidence.expires_at - time.time()) > 0:
            print(f"Waiting for actual expiry: {remaining:.0f}s remaining.", flush=True)
            await asyncio.sleep(min(remaining, 30))


async def observe_logout(
    group: ExternalMcpGroup,
    store: SystemMcpCredentialStore,
    target: McpOAuthTarget,
    tool: str,
    timeout: float,
) -> None:
    """等待另一进程退出登录，再确认已连接的 owner 拒绝下一调用并撤下目录。"""
    print("READY: connected; run source CLI logout in another terminal.", flush=True)
    async with asyncio.timeout(timeout):
        while (await store.read(target)).snapshot is not None:
            await asyncio.sleep(0.2)
    try:
        await group.call_tool(tool, {"name": "find_releases", "arguments": {}})
    except McpError:
        await asyncio.sleep(0)
        snapshot = group.service_snapshots[0]
        if snapshot.authorization.error == "login_required" and not group.tools:
            return
    raise LiveCheckError("logout_boundary_failed")


async def run_live(
    config_root: Path,
    state_root: Path,
    server_name: str,
    mode: Mode,
    timeout: float,
    report: LiveReport,
) -> None:
    """复用生产连接和真实系统凭据库，固定执行 Sentry 的只读版本查询。"""
    raw = ConfigStore(config_root / "config.toml").read_raw(create=False)
    servers = raw.get("mcp_servers")
    if not isinstance(servers, dict) or server_name not in servers:
        raise ValueError("acceptance_registration_missing")
    settings = McpOAuthServerSettings.model_validate(servers[server_name])
    if not settings.applicable:
        raise ValueError("oauth_required")
    target = settings.target(server_name)
    store = SystemMcpCredentialStore(config_root=config_root, state_root=state_root)
    before = await store.read(target)
    report.before = credential_evidence(before)
    if not report.before.present and mode != "logged-out":
        raise McpOAuthError("login_required")
    if mode == "wait-expiry":
        await wait_for_expiry(report.before, timeout)
    group = ExternalMcpGroup(credential_store=store)
    connected_at = time.time()
    report.expired_before_connection = (
        report.before.expires_at is not None and report.before.expires_at <= connected_at
    )
    try:
        async with asyncio.timeout(90):
            await group.start(normalize_mcp_servers({server_name: servers[server_name]}))
            if mode == "logged-out":
                snapshots = group.service_snapshots
                if (
                    report.before.present or group.started or group.tools
                    or len(snapshots) != 1
                    or snapshots[0].authorization.error != "login_required"
                ):
                    raise LiveCheckError("logged_out_boundary_failed")
                report.logout_observed = True
                report.status = "passed"
                return
            if not group.started:
                raise LiveCheckError("connection_failed")
            tool = next((name for name in group.tools if name.endswith("__execute_sentry_tool")), None)
            if tool is None:
                raise LiveCheckError("readonly_tool_missing")
            result = await group.call_tool(tool, {"name": "find_releases", "arguments": {}})
            if result.isError:
                raise LiveCheckError("readonly_call_failed")
            report.call_succeeded = True
        after_call = await store.read(target)
        report.refresh_committed = refresh_committed(
            before, after_call, connected_at=connected_at, observed_at=time.time(),
        )
        if mode == "wait-expiry" and not report.refresh_committed:
            raise LiveCheckError("refresh_evidence_missing")
        if mode == "observe-logout":
            await observe_logout(group, store, target, tool, timeout)
            report.logout_observed = True
        report.status = "passed"
    finally:
        report.tools_after = len(group.tools)
        if group.service_snapshots:
            report.authorization_error = group.service_snapshots[0].authorization.error
        try:
            await group.close()
            report.resources_closed = not group.owned_keys
            if not report.resources_closed:
                raise LiveCheckError("resources_not_closed")
        finally:
            report.after = credential_evidence(await store.read(target))


def main() -> None:
    """仅使用显式验收目录，独占创建报告；失败仍保留脱敏证据。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-root", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--server", default="sentry-oauth-acceptance")
    parser.add_argument("--mode", choices=typing.get_args(Mode), default="call")
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    config_root = arguments.config_root.resolve()
    state_root = arguments.state_root.resolve()
    report_path = arguments.report.resolve()
    if config_root == default_config_home(environment={}).resolve() or state_root == default_state_home(environment={}).resolve():
        parser.error("Use isolated acceptance roots, not the current default roots")
    if not report_path.is_relative_to(state_root / "reports") or not math.isfinite(arguments.timeout) or arguments.timeout <= 0:
        parser.error("Use a new file below STATE_ROOT/reports and a positive timeout")
    mode: Mode = arguments.mode
    report = LiveReport(
        datetime.now(timezone.utc).isoformat(), platform.platform(), const.APP_VERSION,
        version("mcp"), version("keyring"), mode,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("x", encoding=const.CHARSET) as stream:
        try:
            asyncio.run(run_live(config_root, state_root, arguments.server, mode, arguments.timeout, report))
        except (McpOAuthError, McpOAuthStorageError, LiveCheckError) as error:
            report.status = "failed"
            report.error_code = error.code
        except KeyboardInterrupt:
            report.status = "interrupted"
            report.error_code = "cancelled"
        except Exception as error:
            report.status = "failed"
            report.error_code = type(error).__name__
        finally:
            json.dump(asdict(report), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    print(f"Sentry OAuth {mode}: {report.status}. Report: {report_path}")
    raise SystemExit(0 if report.status == "passed" else 130 if report.status == "interrupted" else 1)


if __name__ == "__main__":
    main()
