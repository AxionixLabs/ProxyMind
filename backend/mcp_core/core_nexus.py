# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.models.model_nexus import (
    NexusKind, NexusRequest, NexusBatchRequest
)
from backend.mcp_hub.hub_nexus import NexusInspectionService
from backend.mcp_hub.hub_nexus import NexusMissionService
from backend.mcp_hub.hub_nexus import NexusExecutorRegistry
from backend.mcp_hub.hub_nexus import MemoryRunRepository


class Nexus(object):
    """Thin application facade for nexus request and batch execution."""

    agent_id: str = "nexus"

    def __init__(self) -> None:
        """初始化运行仓储、协议分发器与任务编排服务。"""
        self.run_repository = MemoryRunRepository()
        self.executor_registry = NexusExecutorRegistry()
        self.mission_service = NexusMissionService(self.executor_registry, self.run_repository)
        self.inspection_service = NexusInspectionService()

    @property
    def runs(self) -> dict[str, typing.Any]:
        """返回当前内存中的运行记录快照。"""
        return self.run_repository.runs

    async def execute_request(
        self,
        *,
        kind: NexusKind,
        request: NexusRequest,
    ) -> dict[str, typing.Any]:
        """执行单个标准化请求。"""
        return await self.mission_service.execute_request(kind=kind, request=request)

    async def execute_batch(
        self,
        *,
        kind: NexusKind,
        batch: NexusBatchRequest,
    ) -> dict[str, typing.Any]:
        """执行批量标准化请求。"""
        return await self.mission_service.execute_batch(kind=kind, batch=batch)

    def render_request(
        self,
        *,
        kind: NexusKind,
        request: NexusRequest,
        env: typing.Optional[dict[str, typing.Any]] = None,
    ) -> dict[str, typing.Any]:
        """渲染单请求的模板与共享默认值，不触发实际执行。"""
        return self.inspection_service.render_request(kind=kind, request=request, env=env)

    def render_batch(
        self,
        *,
        kind: NexusKind,
        batch: NexusBatchRequest,
    ) -> dict[str, typing.Any]:
        """渲染批量请求的模板与共享默认值，不触发实际执行。"""
        return self.inspection_service.render_batch(kind=kind, batch=batch)

    def validate_request(
        self,
        *,
        kind: NexusKind,
        request: NexusRequest,
        env: typing.Optional[dict[str, typing.Any]] = None,
    ) -> dict[str, typing.Any]:
        """校验单请求结构并返回渲染后的结果。"""
        return self.inspection_service.validate_request(kind=kind, request=request, env=env)

    def validate_batch(
        self,
        *,
        kind: NexusKind,
        batch: NexusBatchRequest,
    ) -> dict[str, typing.Any]:
        """校验批量请求结构并返回渲染后的结果。"""
        return self.inspection_service.validate_batch(kind=kind, batch=batch)


if __name__ == '__main__':
    pass
