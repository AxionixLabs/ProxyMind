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
    MenuFooterCommand,
    MenuFooterHint,
    MenuFooterTone,
    MenuOption,
    MenuRequest,
    MenuRowDisplay,
    MenuShortcutAction,
    MenuTab,
    MenuTextInputMode,
)
from frontends.tui.contracts.pager import StaticPagerRequest
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
    TranscriptExportResult,
)
from frontends.tui.contracts.views import (
    ViewCompletion,
    ViewIdentity,
)

__all__ = (
    "CLOSE_MENU_FOOTER_HINT",
    "STANDARD_MENU_FOOTER_HINT",
    "MenuAction",
    "MenuActionKind",
    "MenuColumnWidthMode",
    "MenuDescriptionLayout",
    "MenuEmptyAcceptAction",
    "MenuFooterTone",
    "MenuFooterCommand",
    "MenuFooterHint",
    "MenuOption",
    "MenuRequest",
    "MenuRowDisplay",
    "MenuShortcutAction",
    "MenuTab",
    "MenuTextInputMode",
    "FormattedLine",
    "FormattedText",
    "FragmentBlock",
    "LineFill",
    "MailboxEntry",
    "MailboxRunRequest",
    "TranscriptBacktrackRequest",
    "TranscriptExportResult",
    "ViewCompletion",
    "ViewIdentity",
    "StaticPagerRequest",
)


if __name__ == '__main__':
    pass
