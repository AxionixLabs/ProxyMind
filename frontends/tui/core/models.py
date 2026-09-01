# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from frontends.tui.contracts.menu import (
    CLOSE_MENU_FOOTER_HINT,
    STANDARD_MENU_FOOTER_HINT,
    MenuAction,
    MenuActionKind,
    MenuColumnWidthMode,
    MenuDescriptionLayout,
    MenuEmptyAcceptAction,
    MenuOption,
    MenuRequest,
    MenuTab,
)
from frontends.tui.contracts.text import (
    FormattedLine,
    FormattedText,
    FragmentBlock,
    LineFill,
)
from frontends.tui.contracts.transcript import (
    MailboxEntry,
    MailboxRunRequest,
    TranscriptBacktrackRequest,
    TranscriptExportFormat,
    TranscriptExportResult,
)
from frontends.tui.contracts.views import (
    ViewCompletion,
    ViewIdentity,
)
from frontends.tui.contracts.pager import StaticPagerRequest

__all__ = (
    "CLOSE_MENU_FOOTER_HINT",
    "STANDARD_MENU_FOOTER_HINT",
    "MenuAction",
    "MenuActionKind",
    "MenuColumnWidthMode",
    "MenuDescriptionLayout",
    "MenuEmptyAcceptAction",
    "MenuOption",
    "MenuRequest",
    "MenuTab",
    "FormattedLine",
    "FormattedText",
    "FragmentBlock",
    "LineFill",
    "MailboxEntry",
    "MailboxRunRequest",
    "TranscriptBacktrackRequest",
    "TranscriptExportFormat",
    "TranscriptExportResult",
    "ViewCompletion",
    "ViewIdentity",
    "StaticPagerRequest",
)


if __name__ == '__main__':
    pass
