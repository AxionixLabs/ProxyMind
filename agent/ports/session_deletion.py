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

    def pending(self) -> tuple[LocalDeletionPlan, ...]:
        """读取尚未完成的原始计划，不推断远端状态或生成新身份。"""
        ...


if __name__ == '__main__':
    pass
