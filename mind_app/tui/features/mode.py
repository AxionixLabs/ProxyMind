# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_app.frontend import (
    ApplicationSink,
    ApplicationView
)
from mind_app.presentation.models import TextSpan
from mind_nova.modes import RunMode
from ..core.styles import (
    ACCENT_STYLE,
    BRIGHT_STYLE,
    fragment_block
)

MODE_LABELS: dict[RunMode, str] = {
    "chat": "Chat",
    "fast": "Fast",
    "xtra": "Xtra",
}


def render_mode_status(
    application: ApplicationSink,
    mode: RunMode,
) -> None:
    """展示当前运行模式。"""
    application.emit(ApplicationView(
        type="tui.mode.status",
        renderable=fragment_block(
            TextSpan("Mode ", ACCENT_STYLE),
            TextSpan(f"· {MODE_LABELS[mode]}", BRIGHT_STYLE),
        ),
    ))
    application.emit(ApplicationView(type="tui.gap"))


if __name__ == '__main__':
    pass
