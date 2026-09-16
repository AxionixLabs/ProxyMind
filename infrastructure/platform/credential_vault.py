# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import typing
from dataclasses import dataclass

from keyring.backend import KeyringBackend
from keyring.errors import KeyringError

from agent.domain.mcp_oauth import McpOAuthStorageError


class CredentialVault(typing.Protocol):
    """隔离系统机密存储；同步方法由存储 owner 在线程中调用并等待完成。

    实现方必须区分不存在和不可访问，不记录载荷。write_new 只接收从未使用的标识；
    delete 幂等，成功表示对应系统记录已不存在。不得回退到普通文件。
    """

    def read(self, key: str) -> str | None:
        """读取机密；不存在返回 None，后端故障抛出安全错误。"""
        ...

    def write_new(self, key: str, value: str) -> None:
        """写入独立新记录，不覆盖任何正在使用的凭据。"""
        ...

    def delete(self, key: str) -> None:
        """幂等删除指定记录，不枚举或修改其他应用凭据。"""
        ...


@dataclass(frozen=True)
class _SystemBackend:
    """绑定固定系统实现与其原生错误类型，不参与全局 keyring 自动发现。"""

    keyring: KeyringBackend
    errors: tuple[type[Exception], ...]


def _system_backend() -> _SystemBackend:
    """只选择当前平台的系统凭据库，拒绝插件链及文件后端。"""
    errors: tuple[type[Exception], ...] = (KeyringError, OSError, RuntimeError, ValueError)
    try:
        backend: KeyringBackend
        if sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring
            from win32ctypes.pywin32.pywintypes import error

            backend = WinVaultKeyring()
            errors += (error,)
        elif sys.platform == "darwin":
            from keyring.backends.macOS import Keyring

            backend = Keyring()
        elif sys.platform.startswith("linux"):
            from jeepney.wrappers import DBusError
            from keyring.backends.SecretService import Keyring
            from secretstorage.exceptions import SecretStorageException

            backend = Keyring()
            errors += (DBusError, SecretStorageException)
        else:
            raise McpOAuthStorageError("storage_unavailable")
        if backend.priority <= 0:
            raise McpOAuthStorageError("storage_unavailable")
        return _SystemBackend(backend, errors)
    except (ImportError, *errors):
        raise McpOAuthStorageError("storage_unavailable") from None


class SystemCredentialVault:
    """以独立服务名保存机密片段，避免 Windows 后端覆盖时复制旧密码的行为。"""

    def read(self, key: str) -> str | None:
        """读取指定系统记录并在边界校验第三方返回类型。"""
        backend = _system_backend()
        try:
            value = backend.keyring.get_password(key, "oauth")
            if value is not None and not isinstance(value, str):
                raise McpOAuthStorageError("storage_corrupt")
            return value
        except McpOAuthStorageError:
            raise
        except backend.errors:
            raise McpOAuthStorageError("storage_unavailable") from None

    def write_new(self, key: str, value: str) -> None:
        """拒绝覆盖现有标识，保存后读回确认系统已接收机密。"""
        backend = _system_backend()
        try:
            if backend.keyring.get_password(key, "oauth") is not None:
                raise McpOAuthStorageError("credential_conflict")
            backend.keyring.set_password(key, "oauth", value)
            if backend.keyring.get_password(key, "oauth") != value:
                raise McpOAuthStorageError("storage_unavailable")
        except McpOAuthStorageError:
            raise
        except backend.errors:
            raise McpOAuthStorageError("storage_unavailable") from None

    def delete(self, key: str) -> None:
        """仅将确实不存在视为幂等成功，删除错误仍向调用方报告。"""
        backend = _system_backend()
        try:
            if backend.keyring.get_password(key, "oauth") is None:
                return
            backend.keyring.delete_password(key, "oauth")
            if backend.keyring.get_password(key, "oauth") is not None:
                raise McpOAuthStorageError("storage_unavailable")
        except McpOAuthStorageError:
            raise
        except backend.errors:
            raise McpOAuthStorageError("storage_unavailable") from None


if __name__ == '__main__':
    pass
