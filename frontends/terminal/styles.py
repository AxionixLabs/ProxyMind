# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from agent.ports.presentation import TextStyle
from .semantic_styles import (
    TerminalSemanticRole,
    semantic_text_style,
)

TITLE_STYLE = semantic_text_style(TerminalSemanticRole.PRIMARY, bold=True)
PREVIEW_STYLE = semantic_text_style(TerminalSemanticRole.SECONDARY)
PREVIEW_PATH_STYLE = semantic_text_style(TerminalSemanticRole.PRIMARY, bold=True)
PREVIEW_LINE_STYLE = semantic_text_style(TerminalSemanticRole.PRIMARY, bold=True)
PREVIEW_TEXT_STYLE = semantic_text_style(TerminalSemanticRole.SECONDARY)
PREVIEW_HUNK_STYLE = semantic_text_style(TerminalSemanticRole.ACCENT, bold=True)
PREVIEW_MORE_STYLE = semantic_text_style(TerminalSemanticRole.SECONDARY)
PREVIEW_COUNT_STYLE = semantic_text_style(TerminalSemanticRole.PRIMARY, bold=True)
ERROR_STYLE = semantic_text_style(TerminalSemanticRole.FAILURE)
ERROR_PREVIEW_HEAD_STYLE = semantic_text_style(
    TerminalSemanticRole.FAILURE,
    dim=True,
)
ERROR_PREVIEW_LINE_STYLE = semantic_text_style(TerminalSemanticRole.ACCENT)
ERROR_PREVIEW_MESSAGE_STYLE = semantic_text_style(TerminalSemanticRole.FAILURE)
ERROR_PREVIEW_TEXT_STYLE = semantic_text_style(
    TerminalSemanticRole.FAILURE,
    dim=True,
)
SUCCESS_DOT_STYLE = semantic_text_style(TerminalSemanticRole.SUCCESS, bold=True)
ERROR_DOT_STYLE = semantic_text_style(TerminalSemanticRole.FAILURE)
TOOL_CALLING_DOT_STYLE = semantic_text_style(
    TerminalSemanticRole.ATTENTION,
    bold=True,
)
DELTA_ADD_STYLE = semantic_text_style(TerminalSemanticRole.SUCCESS, bold=True)
DELTA_REMOVE_STYLE = semantic_text_style(TerminalSemanticRole.FAILURE, bold=True)
ACTION_EDIT_STYLE = semantic_text_style(TerminalSemanticRole.PRIMARY, bold=True)
ACTION_RUN_STYLE = semantic_text_style(TerminalSemanticRole.PRIMARY, bold=True)
ACTION_TOOL_STYLE = semantic_text_style(TerminalSemanticRole.ACCENT, bold=True)
ACTION_TOOL_CALLING_STYLE = semantic_text_style(
    TerminalSemanticRole.ATTENTION,
    bold=True,
)
ACTION_TOOL_INVOKED_STYLE = semantic_text_style(
    TerminalSemanticRole.ACCENT,
    bold=True,
)
ACTION_TERMINAL_STYLE = TextStyle(bold=True, dim=False)
COMMAND_STYLE = semantic_text_style(TerminalSemanticRole.PRIMARY)
COMMAND_HEAD_STYLE = semantic_text_style(TerminalSemanticRole.ACCENT, bold=True)
COMMAND_FLAG_STYLE = semantic_text_style(TerminalSemanticRole.ACCENT)
COMMAND_PATH_STYLE = semantic_text_style(TerminalSemanticRole.PRIMARY, bold=True)
COMMAND_STRING_STYLE = semantic_text_style(TerminalSemanticRole.SUCCESS)
COMMAND_NUMBER_STYLE = semantic_text_style(
    TerminalSemanticRole.ATTENTION,
    bold=False,
)
COMMAND_OPERATOR_STYLE = semantic_text_style(
    TerminalSemanticRole.SECONDARY,
    bold=True,
)


if __name__ == '__main__':
    pass
