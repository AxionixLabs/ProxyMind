# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from .models import TextStyle

TITLE_STYLE                 = TextStyle(foreground="#D7E7FF", bold=True)
PREVIEW_STYLE               = TextStyle(foreground="#8FA4B8", dim=True)
PREVIEW_PATH_STYLE          = TextStyle(foreground="#A9B8C8", bold=True)
PREVIEW_LINE_STYLE          = TextStyle(foreground="#95A6B8", bold=True)
PREVIEW_TEXT_STYLE          = TextStyle(foreground="#A5B3C2", dim=True)
PREVIEW_HUNK_STYLE          = TextStyle(foreground="#8FB8FF", bold=True)
PREVIEW_CODE_TEXT_STYLE     = TextStyle(foreground="#BCC9D6")
PREVIEW_CODE_KEYWORD_STYLE  = TextStyle(foreground="#B9A6D8", bold=True)
PREVIEW_CODE_NAME_STYLE     = TextStyle(foreground="#CAD5DF")
PREVIEW_CODE_STRING_STYLE   = TextStyle(foreground="#A9CDBB")
PREVIEW_CODE_NUMBER_STYLE   = TextStyle(foreground="#D3C27C")
PREVIEW_CODE_COMMENT_STYLE  = TextStyle(foreground="#8FA4B8", dim=True)
PREVIEW_CODE_OPERATOR_STYLE = TextStyle(foreground="#AAB8C6")
PREVIEW_MORE_STYLE          = TextStyle(foreground="#7E8FA3", dim=True)
PREVIEW_COUNT_STYLE         = TextStyle(foreground="#A0ADBA", bold=True)
ERROR_STYLE                 = TextStyle(foreground="#FF7A7A", bold=True)
ERROR_PREVIEW_HEAD_STYLE    = TextStyle(foreground="#C66A6A", dim=True)
ERROR_PREVIEW_LINE_STYLE    = TextStyle(foreground="#7DD3FC")
ERROR_PREVIEW_MESSAGE_STYLE = TextStyle(foreground="#D98A8A")
ERROR_PREVIEW_TEXT_STYLE    = TextStyle(foreground="#C66A6A", dim=True)
SUCCESS_DOT_STYLE           = TextStyle(foreground="#6EE7A8", bold=True)
ERROR_DOT_STYLE             = TextStyle(foreground="#FF6B6B", bold=True)
TOOL_CALLING_DOT_STYLE      = TextStyle(foreground="#D8B77F")
DELTA_ADD_STYLE             = TextStyle(foreground="#6EE7A8", bold=True)
DELTA_REMOVE_STYLE          = TextStyle(foreground="#FF8A8A", bold=True)
ACTION_EDIT_STYLE           = TextStyle(foreground="#B8C7D9", bold=True)
ACTION_RUN_STYLE            = TextStyle(foreground="#B8C7D9", bold=True)
ACTION_TOOL_STYLE           = TextStyle(foreground="#7DD3FC", bold=True)
ACTION_TOOL_CALLING_STYLE   = TextStyle(foreground="#D8B77F")
ACTION_TOOL_CALLED_STYLE    = TextStyle(foreground="#7DD3FC")
COMMAND_STYLE               = TextStyle(foreground="#C8D2DD")
COMMAND_HEAD_STYLE          = TextStyle(foreground="#4DE3FF", bold=True)
COMMAND_FLAG_STYLE          = TextStyle(foreground="#9AB7D4")
COMMAND_PATH_STYLE          = TextStyle(foreground="#D2DAE3", bold=True)
COMMAND_STRING_STYLE        = TextStyle(foreground="#A8D5C2")
COMMAND_NUMBER_STYLE        = TextStyle(foreground="#CFC17A")
COMMAND_OPERATOR_STYLE      = TextStyle(foreground="#7D8A98", bold=True)


if __name__ == '__main__':
    pass
