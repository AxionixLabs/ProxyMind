# -*- coding: utf-8 -*-

import typing
from pathlib import Path

from agent.ports import TurnSessionContextPort
from infrastructure.skills import skills_payload
from mind_app.interaction.environment import capture_turn_environment
from observability import observe_exception

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind


class ControllerTurnSessionContext(TurnSessionContextPort):
    """把 Controller 的配置和环境能力适配为流式会话上下文端口。"""

    def __init__(self, controller: "Mind") -> None:
        """绑定组合根提供的 Controller 适配边界。"""
        self._controller = controller

    @property
    def animate(self) -> bool:
        """返回当前输出是否启用动画。"""
        return bool(getattr(self._controller, "animate", True))

    def capture_environment(
        self,
        *,
        cwd: str,
        workspace_root: str,
    ) -> dict[str, typing.Any] | None:
        """捕获当前轮次使用的客户端环境快照。"""
        return capture_turn_environment(
            self._controller,
            cwd=Path(cwd),
            workspace_root=Path(workspace_root),
        )

    def skills_payload(self) -> list[dict[str, typing.Any]]:
        """读取配置并构造当前 skills 请求 payload。"""
        try:
            config = self._controller.config_session.load()
        except (OSError, TypeError, ValueError) as error:
            observe_exception(
                "skills.config.failed",
                error,
                level="WARNING",
            )
            config = {}
        return skills_payload(config)


__all__ = ("ControllerTurnSessionContext",)
