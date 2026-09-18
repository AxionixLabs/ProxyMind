# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from contextlib import AbstractContextManager
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LocalDeletionTarget:
    """保存线上坐标及已确认映射的本地运行身份，不把两类身份互相赋值。"""

    cid: str
    sid: str
    local_session_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LocalDeletionPlan:
    """保存清理期间不可改变的目标集合；调用方负责先确认远端删除和关闭运行资源。"""

    request_id: str
    targets: tuple[LocalDeletionTarget, ...]


class SessionDeletionConflict(ValueError):
    """表示删除身份被复用，或旧记录缺少可证明的会话归属。"""


@dataclass(frozen=True, slots=True)
class RemoteDeletionTarget:
    """保存发送给正式删除协议的线上会话坐标。"""

    cid: str
    sid: str

    def __post_init__(self) -> None:
        """拒绝未经验证的线上删除身份。"""
        if (
            not isinstance(self.cid, str)
            or not isinstance(self.sid, str)
            or not self.cid.strip()
            or not self.sid.strip()
        ):
            raise ValueError("valid deletion coordinates are required")


@dataclass(frozen=True, slots=True)
class RemoteDeletionRequest:
    """冻结一次远端删除请求，恢复时必须复用同一请求身份。"""

    request_id: str
    root: RemoteDeletionTarget
    descendants: tuple[RemoteDeletionTarget, ...] = ()

    @property
    def targets(self) -> tuple[RemoteDeletionTarget, ...]:
        """返回稳定排序后的完整删除范围。"""
        return tuple(sorted((self.root, *self.descendants), key=lambda item: (item.cid, item.sid)))


@dataclass(frozen=True, slots=True)
class RemoteDeletionReceipt:
    """表示服务端已经确认完整删除范围。"""

    request_id: str
    root: RemoteDeletionTarget
    targets: tuple[RemoteDeletionTarget, ...]


class SessionDeletionRemoteError(RuntimeError):
    """携带正式协议的拒绝或未知结果，不猜测远端是否已删除。"""

    def __init__(
        self,
        *,
        outcome: typing.Literal["rejected", "unknown"],
        code: str,
    ) -> None:
        """保存脱敏后的稳定错误分类。"""
        super().__init__(code)
        self.outcome = outcome
        self.code = code


class SessionDeletionRemote(typing.Protocol):
    """提供正式删除请求和同请求恢复查询。"""

    async def delete(self, request: RemoteDeletionRequest) -> RemoteDeletionReceipt:
        """提交冻结请求并返回完整删除回执。"""
        ...

    async def recover(self, request: RemoteDeletionRequest) -> RemoteDeletionReceipt:
        """查询原请求，不生成新的删除身份。"""
        ...


@dataclass(frozen=True, slots=True)
class SessionDeletionResult:
    """描述删除流程对调用方可见的终态。"""

    status: typing.Literal[
        "deleted", "rejected", "unknown", "local_failed", "new_unbound",
        "already_deleted", "not_current", "busy", "fork_conflict",
    ]
    request_id: str = ""
    code: str = ""

    @property
    def complete(self) -> bool:
        """返回远端和本地清理是否都已完成。"""
        return self.status == "deleted"


class TranscriptDeletionLease(typing.Protocol):
    """在文件适配器持有全部目标独占锁期间提供删除能力，退出上下文必须释放锁。"""

    def retire(self) -> None:
        """持久封锁目标路径的后续写入，失败必须传播。"""
        ...

    def delete(self) -> None:
        """删除目标文件，缺失视为成功，其他错误必须传播。"""
        ...


class TranscriptDeletionStore(typing.Protocol):
    """由文件适配器校验允许目录并取得跨进程锁，不拥有会话关闭生命周期。"""

    def deletion_identity(self, session_ids: tuple[str, ...]) -> str:
        """返回冻结文件集合的稳定标识，恢复时路径变化必须被识别。"""
        ...

    def lock_sessions(
        self, session_ids: tuple[str, ...],
    ) -> AbstractContextManager[TranscriptDeletionLease]:
        """锁定完整集合，任一文件占用或路径非法时不得开始删除。"""
        ...


class SessionDeletionStore(typing.Protocol):
    """持久化本地清理意图并幂等完成清理，阻塞操作由调用方调度到工作线程。"""

    def delete(self, plan: LocalDeletionPlan) -> None:
        """仅在所有本地资源清理完成后返回，失败保留原计划以供重试。"""
        ...

    def prepare(self, plan: LocalDeletionPlan) -> None:
        """先持久化待完成计划，供远端未知结果后的恢复查询使用。"""
        ...

    def pending(self) -> tuple[LocalDeletionPlan, ...]:
        """读取尚未完成的原始计划，不推断远端状态或生成新身份。"""
        ...


if __name__ == '__main__':
    pass
