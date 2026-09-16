# -*- coding: utf-8 -*-

"""使用临时命名空间和合成令牌验证系统凭据库的跨进程读写及清理。"""

import argparse
import subprocess
import sys
import tempfile

import anyio
from pathlib import Path

from agent.domain.mcp_oauth import (
    McpOAuthClientInfo,
    McpOAuthCredentialSnapshot,
    McpOAuthTarget,
    McpOAuthToken,
)
from infrastructure.mcp.oauth_credentials import SystemMcpCredentialStore


async def run_operation(root: Path, operation: str) -> None:
    """只操作传入临时目录命名空间对应的合成凭据，不连接真实账户。"""
    target = McpOAuthTarget("credential-storage-acceptance", "https://example.invalid/mcp")
    store = SystemMcpCredentialStore(config_root=root / "config", state_root=root / "state", clock=lambda: 1950)
    if operation == "write":
        value = McpOAuthCredentialSnapshot(
            target, "https://issuer.invalid", target.server_url, "https://issuer.invalid/token",
            McpOAuthClientInfo("synthetic-public-client", ("http://127.0.0.1:8888/callback",), "registered"),
            0, McpOAuthToken("synthetic-access-" * 400, "synthetic-refresh-" * 300, 2000, ("read",)),
        )
        async with store.transaction(target) as transaction:
            assert (await transaction.save(value)).generation == 1
    elif operation == "read":
        record = await store.read(target)
        assert record.generation == 1 and record.snapshot is not None
        token = record.snapshot.token
        assert token is not None and token.remaining_lifetime(1950) == 50
        assert token.access_token == "synthetic-access-" * 400
        assert token.refresh_token == "synthetic-refresh-" * 300
    elif operation == "delete":
        await store.delete(target)
        assert (await store.read(target)).snapshot is None


def main() -> None:
    """在独立进程执行写入、恢复和删除，并在失败时重试本次命名空间的清理。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--operation", choices=("write", "read", "delete"))
    arguments = parser.parse_args()
    if arguments.root is not None and arguments.operation is not None:
        anyio.run(run_operation, arguments.root, arguments.operation)
        return
    with tempfile.TemporaryDirectory(prefix="mcp-credential-acceptance-") as directory:
        root = Path(directory)
        try:
            for operation in ("write", "read", "delete"):
                subprocess.run(
                    [sys.executable, "-m", "tests.manual.mcp_credential_storage", "--root", str(root), "--operation", operation],
                    check=True, timeout=60,
                )
        finally:
            anyio.run(run_operation, root, "delete")
    print("System credential storage: cross-process restore, large tokens, expiry and cleanup passed.")


if __name__ == "__main__":
    main()
