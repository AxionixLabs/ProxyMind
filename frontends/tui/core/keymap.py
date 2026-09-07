# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import itertools
import typing
from dataclasses import (
    dataclass,
    fields,
)

from prompt_toolkit.filters import FilterOrBool
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.keys import Keys

from frontends.tui.contracts.keyboard import enhanced_key_token
from frontends.tui.contracts.keyboard import is_enhanced_key_token


@dataclass(frozen=True, slots=True)
class TuiKeyStroke:
    """保存一个逻辑按键及其终端输入序列。"""
    keys: tuple[Keys | str, ...]
    key_sequences: tuple[tuple[Keys | str, ...], ...]
    label: str
    key_name: str
    modifiers: frozenset[str]


@dataclass(frozen=True, slots=True)
class TuiKeyBinding(object):
    """保存一个已解析的按键序列及其展示标签。"""
    keys: tuple[Keys | str, ...]
    key_sequences: tuple[tuple[Keys | str, ...], ...]
    label: str
    strokes: tuple[TuiKeyStroke, ...]

    @property
    def is_chord(self) -> bool:
        """返回当前绑定是否由两个逻辑按键组成。"""
        return len(self.strokes) == 2


TuiActionBindings = tuple[TuiKeyBinding, ...]
TuiKeymapConfigValue: typing.TypeAlias = str | list[str] | tuple[str, ...]
TuiKeymapConfig: typing.TypeAlias = typing.Mapping[
    str,
    TuiKeymapConfigValue,
]
KEY_CHORD_TIMEOUT_SEC: typing.Final[float] = 1.0
TUI_KEYMAP_CONTEXT_ORDER: typing.Final[tuple[str, ...]] = (
    "global",
    "chat",
    "composer",
    "editor",
    "pager",
    "list",
    "approval",
)
TUI_KEYMAP_OVERLAP_GROUPS: typing.Final[tuple[frozenset[str], ...]] = (
    frozenset({"global", "chat", "composer", "editor"}),
)
_LEGACY_CONTROL_ALIASES: typing.Final[dict[str, str]] = {
    "@": "NUL",
    "h": "Backspace",
    "i": "Tab",
    "m": "Enter",
    "[": "Esc",
}


@dataclass(frozen=True, slots=True)
class TuiResolvedKeyAction:
    """保存稳定动作身份及其已解析绑定。"""
    action_id: str
    bindings: TuiActionBindings


@dataclass(frozen=True, slots=True)
class TuiGlobalKeymap(object):
    """保存主界面全局动作的按键映射。"""
    open_transcript: TuiActionBindings
    copy_last_response: TuiActionBindings
    clear_terminal: TuiActionBindings
    transcript_page_up: TuiActionBindings
    transcript_page_down: TuiActionBindings


@dataclass(frozen=True, slots=True)
class TuiChatKeymap(object):
    """保存 Turn 与排队输入动作的按键映射。"""
    interrupt_turn: TuiActionBindings
    edit_queued_message: TuiActionBindings


@dataclass(frozen=True, slots=True)
class TuiComposerKeymap(object):
    """保存输入提交和补全动作的按键映射。"""
    submit: TuiActionBindings
    queue: TuiActionBindings
    enter_shell_mode: TuiActionBindings
    previous_completion: TuiActionBindings
    toggle_shortcuts: TuiActionBindings
    history_search_previous: TuiActionBindings
    history_search_next: TuiActionBindings


@dataclass(frozen=True, slots=True)
class TuiEditorKeymap(object):
    """保存输入编辑动作的按键映射。"""
    interrupt: TuiActionBindings
    exit: TuiActionBindings
    delete_line: TuiActionBindings
    cancel_shell_mode: TuiActionBindings
    cancel_completion: TuiActionBindings
    delete_backward: TuiActionBindings
    delete_forward: TuiActionBindings
    delete_word_backward: TuiActionBindings
    undo: TuiActionBindings
    move_left: TuiActionBindings
    move_right: TuiActionBindings
    move_up: TuiActionBindings
    move_down: TuiActionBindings
    insert_newline: TuiActionBindings
    completion_previous: TuiActionBindings
    completion_next: TuiActionBindings
    move_line_start: TuiActionBindings
    move_line_end: TuiActionBindings
    move_word_left: TuiActionBindings
    move_word_right: TuiActionBindings
    delete_word_forward: TuiActionBindings
    delete_to_line_end: TuiActionBindings
    yank: TuiActionBindings


@dataclass(frozen=True, slots=True)
class TuiListKeymap(object):
    """保存通用选择列表的按键映射。"""
    accept: TuiActionBindings
    toggle: TuiActionBindings
    alternate: TuiActionBindings
    move_down: TuiActionBindings
    move_up: TuiActionBindings
    page_down: TuiActionBindings
    page_up: TuiActionBindings
    jump_top: TuiActionBindings
    jump_bottom: TuiActionBindings
    move_right: TuiActionBindings
    move_left: TuiActionBindings
    delete_query_character: TuiActionBindings
    clear_query: TuiActionBindings
    delete_query_word: TuiActionBindings
    cancel: TuiActionBindings
    interrupt: TuiActionBindings


@dataclass(frozen=True, slots=True)
class TuiApprovalKeymap(object):
    """保存审批表面的按键映射。"""
    expand_details: TuiActionBindings
    accept_selected: TuiActionBindings
    move_down: TuiActionBindings
    move_up: TuiActionBindings
    decline: TuiActionBindings
    accept_once: TuiActionBindings
    accept_session: TuiActionBindings
    strict_review: TuiActionBindings
    persist_rule: TuiActionBindings
    deny: TuiActionBindings
    cancel: TuiActionBindings


@dataclass(frozen=True, slots=True)
class TuiPagerKeymap(object):
    """保存完整记录和静态页面使用的按键映射。"""
    scroll_up: tuple[TuiKeyBinding, ...]
    scroll_down: tuple[TuiKeyBinding, ...]
    page_up: tuple[TuiKeyBinding, ...]
    page_down: tuple[TuiKeyBinding, ...]
    half_page_up: tuple[TuiKeyBinding, ...]
    half_page_down: tuple[TuiKeyBinding, ...]
    jump_top: tuple[TuiKeyBinding, ...]
    jump_bottom: tuple[TuiKeyBinding, ...]
    toggle_raw: tuple[TuiKeyBinding, ...]
    search: tuple[TuiKeyBinding, ...]
    search_next: tuple[TuiKeyBinding, ...]
    search_previous: tuple[TuiKeyBinding, ...]
    export: tuple[TuiKeyBinding, ...]
    close: tuple[TuiKeyBinding, ...]
    close_transcript: tuple[TuiKeyBinding, ...]


@dataclass(frozen=True, slots=True)
class TuiRuntimeKeymap(object):
    """保存完成默认值解析和冲突校验的 TUI 按键映射。"""
    global_keys: TuiGlobalKeymap
    chat: TuiChatKeymap
    composer: TuiComposerKeymap
    editor: TuiEditorKeymap
    list: TuiListKeymap
    approval: TuiApprovalKeymap
    pager: TuiPagerKeymap

    @classmethod
    def defaults(cls) -> "TuiRuntimeKeymap":
        """返回内置按键映射。"""
        return cls.from_config({})

    @classmethod
    def from_config(cls, config: typing.Any) -> "TuiRuntimeKeymap":
        """从有效配置中解析全部 TUI 动作按键。"""
        root = config if isinstance(config, dict) else {}
        tui = root.get("tui") if isinstance(root.get("tui"), dict) else {}

        keymap = (
            tui.get("keymap")
            if isinstance(tui.get("keymap"), dict)
            else {}
        )

        context_config = {
            context: (
                keymap.get(context)
                if isinstance(keymap.get(context), dict)
                else {}
            )
            for context in TUI_KEYMAP_CONTEXT_ORDER
        }

        global_defaults = _default_global_keymap()
        chat_defaults = _default_chat_keymap()
        composer_defaults = _default_composer_keymap()
        editor_defaults = _default_editor_keymap()
        list_defaults = _default_list_keymap()
        approval_defaults = _default_approval_keymap()

        global_keys = TuiGlobalKeymap(**_resolve_context_values(
            "global",
            global_defaults,
            context_config["global"],
        ))
        chat = TuiChatKeymap(**_resolve_context_values(
            "chat",
            chat_defaults,
            context_config["chat"],
        ))
        composer = TuiComposerKeymap(**_resolve_context_values(
            "composer",
            composer_defaults,
            context_config["composer"],
            fallback_config=context_config["global"],
            fallback_actions=frozenset({
                "submit",
                "queue",
                "toggle_shortcuts",
            }),
        ))
        editor = TuiEditorKeymap(**_resolve_context_values(
            "editor",
            editor_defaults,
            context_config["editor"],
            fixed_actions=frozenset({
                "interrupt",
                "exit",
                "cancel_shell_mode",
                "cancel_completion",
            }),
        ))
        list_keys = TuiListKeymap(**_resolve_context_values(
            "list",
            list_defaults,
            context_config["list"],
            fixed_actions=frozenset({"interrupt"}),
        ))
        approval = TuiApprovalKeymap(**_resolve_context_values(
            "approval",
            approval_defaults,
            context_config["approval"],
        ))
        pager = _resolve_pager_keymap(context_config["pager"])

        _validate_reserved_pager_bindings(pager)
        _validate_configured_binding_shapes(context_config)
        for context_name, context in (
            ("global", global_keys),
            ("chat", chat),
            ("composer", composer),
            ("editor", editor),
            ("list", list_keys),
            ("approval", approval),
            ("pager", pager),
        ):
            _validate_context_conflicts(context_name, context)
        _validate_overlapping_contexts(
            global_keys,
            chat,
            composer,
            editor,
        )
        _validate_chord_conflicts(
            global_keys,
            chat,
            composer,
            editor,
            pager,
            list_keys,
            approval,
        )

        return cls(
            global_keys=global_keys,
            chat=chat,
            composer=composer,
            editor=editor,
            list=list_keys,
            approval=approval,
            pager=pager,
        )

    @property
    def open_transcript(self) -> TuiActionBindings:
        """返回兼容现有调用方的完整记录绑定。"""
        return self.global_keys.open_transcript

    @property
    def open_transcript_label(self) -> str:
        """返回打开完整记录的首选按键标签。"""
        return _primary_label(self.global_keys.open_transcript)

    def actions(self) -> tuple[TuiResolvedKeyAction, ...]:
        """按稳定上下文顺序返回全部运行时动作。"""
        resolved_contexts = {
            "global": self.global_keys,
            "chat": self.chat,
            "composer": self.composer,
            "editor": self.editor,
            "pager": self.pager,
            "list": self.list,
            "approval": self.approval,
        }
        contexts: tuple[tuple[str, TuiKeymapContext], ...] = tuple(
            (name, resolved_contexts[name])
            for name in TUI_KEYMAP_CONTEXT_ORDER
        )
        return tuple(
            TuiResolvedKeyAction(
                action_id=f"{context_name}.{action}",
                bindings=bindings,
            )
            for context_name, context in contexts
            for action, bindings in _context_actions(context)
        )

    def bindings_for(self, action_id: str) -> TuiActionBindings:
        """按稳定动作身份返回绑定，不接受隐式别名。"""
        for action in self.actions():
            if action.action_id == action_id:
                return action.bindings
        raise KeyError(action_id)

    def validate_main_conflicts(
        self,
        reserved: typing.Iterable[
            tuple[str, tuple[Keys | str, ...]]
        ]
    ) -> None:
        """拒绝可配置的全局记录入口覆盖主输入动作。"""
        owners = {keys: action for action, keys in reserved}
        for binding in self.global_keys.open_transcript:
            for key_sequence in binding.key_sequences:
                previous = owners.get(key_sequence)
                if previous is not None:
                    raise ValueError(
                        "tui.keymap.global.open_transcript conflicts with "
                        f"{previous}: {binding.label}"
                    )


TuiKeymapContext = (
    TuiGlobalKeymap
    | TuiChatKeymap
    | TuiComposerKeymap
    | TuiEditorKeymap
    | TuiListKeymap
    | TuiApprovalKeymap
    | TuiPagerKeymap
)


def bind_key_action(
    bindings: KeyBindings,
    configured: TuiActionBindings,
    *,
    eager: FilterOrBool = False,
    binding_filter: FilterOrBool = True,
    save_before: typing.Callable[[KeyPressEvent], bool] | None = None,
) -> typing.Callable[
    [typing.Callable[[KeyPressEvent], None]],
    typing.Callable[[KeyPressEvent], None],
]:
    """把一个已解析动作的全部按键注册到当前输入上下文。"""
    def register(
        handler: typing.Callable[[KeyPressEvent], None],
    ) -> typing.Callable[[KeyPressEvent], None]:
        for binding in configured:
            for key_sequence in binding.key_sequences:
                if save_before is None:
                    bindings.add(
                        *key_sequence,
                        eager=eager,
                        filter=binding_filter,
                    )(handler)
                else:
                    bindings.add(
                        *key_sequence,
                        eager=eager,
                        filter=binding_filter,
                        save_before=save_before,
                    )(handler)
            if binding.is_chord:
                for prefix in binding.strokes[0].key_sequences:
                    def cancel_pending_chord(event: KeyPressEvent) -> None:
                        _ = event

                    bindings.add(
                        *prefix,
                        Keys.Escape,
                        eager=True,
                        filter=binding_filter,
                    )(cancel_pending_chord)
        return handler

    return register


def key_action_matches(
    configured: TuiActionBindings,
    key_sequence: tuple[Keys | str, ...],
) -> bool:
    """判断规范化按键序列是否属于指定运行时动作。"""
    normalized: list[Keys | str] = []
    for key in key_sequence:
        if isinstance(key, Keys) or len(key) == 1:
            normalized.append(key)
            continue
        alias = key
        if alias.startswith("c-"):
            alias = f"ctrl-{alias[2:]}"
        try:
            normalized.extend(_parse_binding(
                alias,
                path="runtime key event",
            ).keys)
        except ValueError:
            normalized.append(key)
    resolved = tuple(normalized)
    return any(
        resolved in binding.key_sequences
        for binding in configured
    )


def _default_bindings(action: str, *values: str) -> TuiActionBindings:
    """解析一个内置动作的默认按键。"""
    return tuple(
        _parse_binding(value, path=f"tui.keymap.{action}")
        for value in values
    )


def _default_global_keymap() -> TuiGlobalKeymap:
    """返回主界面全局动作的默认按键。"""
    return TuiGlobalKeymap(
        open_transcript=_default_bindings(
            "global.open_transcript",
            "ctrl-t",
        ),
        copy_last_response=_default_bindings(
            "global.copy_last_response",
            "ctrl-o",
        ),
        clear_terminal=_default_bindings(
            "global.clear_terminal",
            "ctrl-l",
        ),
        transcript_page_up=_default_bindings(
            "global.transcript_page_up",
            "page-up",
        ),
        transcript_page_down=_default_bindings(
            "global.transcript_page_down",
            "page-down",
        ),
    )


def _default_chat_keymap() -> TuiChatKeymap:
    """返回 Turn 输入动作的默认按键。"""
    return TuiChatKeymap(
        interrupt_turn=_default_bindings("chat.interrupt_turn", "esc"),
        edit_queued_message=_default_bindings(
            "chat.edit_queued_message",
            "alt-up",
            "shift-left",
        ),
    )


def _default_composer_keymap() -> TuiComposerKeymap:
    """返回输入提交动作的默认按键。"""
    return TuiComposerKeymap(
        submit=_default_bindings("composer.submit", "enter"),
        queue=_default_bindings("composer.queue", "tab"),
        enter_shell_mode=_default_bindings("composer.enter_shell_mode", "!"),
        previous_completion=_default_bindings(
            "composer.previous_completion",
            "shift-tab",
        ),
        toggle_shortcuts=_default_bindings(
            "composer.toggle_shortcuts",
            "?",
        ),
        history_search_previous=_default_bindings(
            "composer.history_search_previous",
            "ctrl-r",
        ),
        history_search_next=_default_bindings(
            "composer.history_search_next",
            "ctrl-s",
        ),
    )


def _default_editor_keymap() -> TuiEditorKeymap:
    """返回输入编辑动作的默认按键。"""
    return TuiEditorKeymap(
        interrupt=_default_bindings("editor.interrupt", "ctrl-c"),
        exit=_default_bindings("editor.exit", "ctrl-d"),
        delete_line=_default_bindings("editor.delete_line", "ctrl-u"),
        cancel_shell_mode=_default_bindings(
            "editor.cancel_shell_mode",
            "backspace",
            "esc",
        ),
        cancel_completion=_default_bindings(
            "editor.cancel_completion",
            "esc",
        ),
        delete_backward=_default_bindings(
            "editor.delete_backward",
            "backspace",
            "shift-backspace",
            "ctrl-h",
        ),
        delete_forward=_default_bindings(
            "editor.delete_forward",
            "delete",
            "shift-delete",
            "ctrl-d",
        ),
        delete_word_backward=_default_bindings(
            "editor.delete_word_backward",
            "alt-backspace",
            "ctrl-backspace",
            "ctrl-shift-backspace",
            "ctrl-w",
            "ctrl-alt-h",
        ),
        undo=_default_bindings("editor.undo", "ctrl-z"),
        move_left=_default_bindings("editor.move_left", "left", "ctrl-b"),
        move_right=_default_bindings("editor.move_right", "right", "ctrl-f"),
        move_up=_default_bindings("editor.move_up", "up"),
        move_down=_default_bindings("editor.move_down", "down"),
        insert_newline=_default_bindings(
            "editor.insert_newline",
            "ctrl-j",
            "ctrl-m",
            "enter",
            "shift-enter",
            "alt-enter",
        ),
        completion_previous=_default_bindings(
            "editor.completion_previous",
            "ctrl-p",
        ),
        completion_next=_default_bindings(
            "editor.completion_next",
            "ctrl-n",
        ),
        move_line_start=_default_bindings(
            "editor.move_line_start",
            "home",
            "ctrl-a",
        ),
        move_line_end=_default_bindings(
            "editor.move_line_end",
            "end",
            "ctrl-e",
        ),
        move_word_left=_default_bindings(
            "editor.move_word_left",
            "alt-b",
            "alt-left",
            "ctrl-left",
        ),
        move_word_right=_default_bindings(
            "editor.move_word_right",
            "alt-f",
            "alt-right",
            "ctrl-right",
        ),
        delete_word_forward=_default_bindings(
            "editor.delete_word_forward",
            "alt-delete",
            "ctrl-delete",
            "ctrl-shift-delete",
            "alt-d",
        ),
        delete_to_line_end=_default_bindings(
            "editor.delete_to_line_end",
            "ctrl-k",
        ),
        yank=_default_bindings("editor.yank", "ctrl-y"),
    )


def _default_list_keymap() -> TuiListKeymap:
    """返回通用选择列表的默认按键。"""
    return TuiListKeymap(
        accept=_default_bindings("list.accept", "enter"),
        toggle=_default_bindings("list.toggle", "space"),
        alternate=_default_bindings("list.alternate", "t"),
        move_down=_default_bindings(
            "list.move_down",
            "down",
            "ctrl-n",
            "ctrl-j",
            "j",
        ),
        move_up=_default_bindings(
            "list.move_up",
            "up",
            "ctrl-p",
            "ctrl-k",
            "k",
        ),
        page_down=_default_bindings(
            "list.page_down",
            "page-down",
            "ctrl-f",
        ),
        page_up=_default_bindings(
            "list.page_up",
            "page-up",
            "ctrl-b",
        ),
        jump_top=_default_bindings("list.jump_top", "home"),
        jump_bottom=_default_bindings("list.jump_bottom", "end"),
        move_right=_default_bindings("list.move_right", "right", "ctrl-l"),
        move_left=_default_bindings("list.move_left", "left", "ctrl-h"),
        delete_query_character=_default_bindings(
            "list.delete_query_character",
            "backspace",
        ),
        clear_query=_default_bindings("list.clear_query", "ctrl-u"),
        delete_query_word=_default_bindings(
            "list.delete_query_word",
            "ctrl-w",
        ),
        cancel=_default_bindings("list.cancel", "esc"),
        interrupt=_default_bindings("list.interrupt", "ctrl-c"),
    )


def _default_approval_keymap() -> TuiApprovalKeymap:
    """返回审批表面的默认按键。"""
    return TuiApprovalKeymap(
        expand_details=_default_bindings(
            "approval.expand_details",
            "ctrl-a",
            "ctrl-shift-a",
        ),
        accept_selected=_default_bindings(
            "approval.accept_selected",
            "enter",
        ),
        move_down=_default_bindings(
            "approval.move_down",
            "down",
            "ctrl-n",
        ),
        move_up=_default_bindings(
            "approval.move_up",
            "up",
            "ctrl-p",
        ),
        decline=_default_bindings("approval.decline", "esc", "n"),
        accept_once=_default_bindings("approval.accept_once", "y"),
        accept_session=_default_bindings("approval.accept_session", "a"),
        strict_review=_default_bindings("approval.strict_review", "r"),
        persist_rule=_default_bindings("approval.persist_rule", "p"),
        deny=_default_bindings("approval.deny", "d"),
        cancel=_default_bindings("approval.cancel", "c"),
    )


def binding_labels(bindings: tuple[TuiKeyBinding, ...]) -> str:
    """把一组按键转换为斜线分隔的展示标签。"""
    return "/".join(binding.label for binding in bindings)


def approval_decision_shortcut_label(
    keymap: TuiApprovalKeymap,
    decision: str,
    *,
    kind: str,
) -> str:
    """返回当前审批类型中一个正式决定的实际快捷键标签。"""
    if decision in {"accept", "grantForTurn"}:
        configured = keymap.accept_once
    elif decision in {"acceptForSession", "grantForSession"}:
        configured = keymap.accept_session
    elif decision == "grantForTurnWithStrictAutoReview":
        configured = keymap.strict_review
    elif decision in {
        "acceptAndRemember",
        "acceptWithExecpolicyAmendment",
        "applyNetworkPolicyAmendment",
    }:
        configured = keymap.persist_rule
    elif decision == "decline":
        configured = (
            tuple(
                binding
                for binding in keymap.decline
                if binding.label != "Esc"
            )
            if kind == "mcp_tool_call"
            else keymap.deny
        )
    elif decision == "cancel":
        configured = tuple(
            binding
            for binding in (
                (
                    *tuple(
                        item
                        for item in keymap.decline
                        if item.label == "Esc"
                    ),
                    *keymap.cancel,
                )
                if kind == "mcp_tool_call"
                else (
                    ()
                    if kind == "request_permissions"
                    else keymap.decline
                )
            )
        )
    else:
        configured = ()

    return "/".join(
        binding.label.lower()
        if len(binding.label) == 1
        else binding.label
        for binding in configured
    )


def primary_binding_label(bindings: tuple[TuiKeyBinding, ...]) -> str:
    """返回一组按键中的首选展示标签。"""
    return _primary_label(bindings)


def _resolve_context_values(
    context: str,
    defaults: TuiKeymapContext,
    config: TuiKeymapConfig,
    *,
    fallback_config: TuiKeymapConfig | None = None,
    fallback_actions: frozenset[str] = frozenset(),
    fixed_actions: frozenset[str] = frozenset(),
) -> dict[str, TuiActionBindings]:
    """按局部、合法全局回退和默认值解析一个上下文。"""
    values: dict[str, TuiActionBindings] = {}
    for field_info in fields(defaults):
        action = field_info.name
        default_bindings = _bindings_for_action(defaults, action)
        if action in fixed_actions:
            if action in config:
                raise ValueError(
                    f"tui.keymap.{context}.{action} is a fixed safety binding"
                )
            values[action] = default_bindings
            continue

        source = config
        source_context = context
        if (
            action not in source
            and fallback_config is not None
            and action in fallback_actions
            and action in fallback_config
        ):
            source = fallback_config
            source_context = "global"

        if action not in source:
            values[action] = default_bindings
            continue

        values[action] = _resolve_bindings(
            source,
            action,
            defaults=(),
            path=f"tui.keymap.{source_context}.{action}",
        )
    return values


def _resolve_bindings(
    config: TuiKeymapConfig,
    action: str,
    *,
    defaults: tuple[str, ...],
    path: str
) -> tuple[TuiKeyBinding, ...]:
    """解析一个动作的配置值，并在未配置时使用默认值。"""
    raw = config.get(action, defaults)

    values = [raw] if isinstance(raw, str) else raw
    if not isinstance(values, (list, tuple)) or not all(
        isinstance(item, str) for item in values
    ):
        raise ValueError(f"{path} must be a string or an array of strings")

    out: list[TuiKeyBinding] = []
    seen: set[tuple[tuple[Keys | str, ...], ...]] = set()

    for value in values:
        binding = _parse_binding(value, path=path)
        identity = binding.key_sequences
        if identity in seen:
            continue
        seen.add(identity)
        out.append(binding)

    return tuple(out)


def _resolve_pager_keymap(config: TuiKeymapConfig) -> TuiPagerKeymap:
    """解析页面按键，并让显式覆盖优先于其他动作的默认键。"""
    defaults: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("scroll_up", ("up", "k")),
        ("scroll_down", ("down", "j")),
        ("page_up", ("page-up", "shift-space", "ctrl-b")),
        ("page_down", ("page-down", "space", "ctrl-f")),
        ("half_page_up", ("ctrl-u",)),
        ("half_page_down", ("ctrl-d",)),
        ("jump_top", ("home",)),
        ("jump_bottom", ("end",)),
        ("toggle_raw", ("r",)),
        ("search", ("/",)),
        ("search_next", ("n",)),
        ("search_previous", ("shift-n",)),
        ("export", ("e",)),
        ("close", ("q", "ctrl-c")),
        ("close_transcript", ("ctrl-t",)),
    )
    resolved: dict[str, tuple[TuiKeyBinding, ...]] = {}
    owners: dict[tuple[Keys | str, ...], tuple[str, bool]] = {}

    for action, action_defaults in defaults:
        explicit = action in config
        bindings = _resolve_bindings(
            config,
            action,
            defaults=action_defaults,
            path=f"tui.keymap.pager.{action}",
        )
        accepted: list[TuiKeyBinding] = []

        for binding in bindings:
            binding_sequences = frozenset(binding.key_sequences)
            collisions = {
                owner
                for sequence in binding_sequences
                if (owner := owners.get(sequence)) is not None
                and owner[0] != action
            }
            if not collisions:
                for sequence in binding_sequences:
                    owners[sequence] = action, explicit
                accepted.append(binding)
                continue

            if not explicit and any(
                previous_explicit
                for _previous_action, previous_explicit in collisions
            ):
                continue
            if explicit and all(
                not previous_explicit
                for _previous_action, previous_explicit in collisions
            ):
                for previous_action, _previous_explicit in collisions:
                    resolved[previous_action] = tuple(
                        item
                        for item in resolved[previous_action]
                        if binding_sequences.isdisjoint(item.key_sequences)
                    )
                    owners = {
                        sequence: owner
                        for sequence, owner in owners.items()
                        if owner[0] != previous_action
                    }
                    for remaining in resolved[previous_action]:
                        for sequence in remaining.key_sequences:
                            owners[sequence] = previous_action, False
                for sequence in binding_sequences:
                    owners[sequence] = action, True
                accepted.append(binding)
                continue
            previous_action = sorted(
                previous_action
                for previous_action, _previous_explicit in collisions
            )[0]
            raise ValueError(
                f"tui.keymap.pager.{action} conflicts with "
                f"tui.keymap.pager.{previous_action}: {binding.label}"
            )

        resolved[action] = tuple(accepted)

    return TuiPagerKeymap(**resolved)


def _parse_binding(value: str, *, path: str) -> TuiKeyBinding:
    """把规范按键字符串转换为 prompt_toolkit 按键序列。"""
    text = str(value or "").strip().lower()
    if not text:
        raise ValueError(f"{path} contains an empty key binding")
    raw_strokes = text.split()
    if len(raw_strokes) > 2:
        raise ValueError(
            f"{path} has unsupported key chord: {value}; "
            "use at most two strokes"
        )

    strokes = tuple(
        _parse_stroke(stroke, path=path, value=value)
        for stroke in raw_strokes
    )
    key_sequences = _combine_stroke_sequences(strokes)
    return TuiKeyBinding(
        keys=key_sequences[0],
        key_sequences=key_sequences,
        label=" ".join(stroke.label for stroke in strokes),
        strokes=strokes,
    )


def _parse_stroke(raw: str, *, path: str, value: str) -> TuiKeyStroke:
    """解析一个逻辑按键并保留修饰键事实。"""
    aliases = {"control": "ctrl", "option": "alt"}
    segments = raw.split("-")
    modifiers: list[str] = []
    while segments and aliases.get(segments[0], segments[0]) in {
        "ctrl",
        "alt",
        "shift",
    }:
        raw_modifier = segments.pop(0)
        modifier = aliases.get(raw_modifier, raw_modifier)
        if modifier in modifiers:
            raise ValueError(
                f"{path} has duplicate modifier in key binding: {value}"
            )
        modifiers.append(modifier)

    base = "-".join(segments)
    if (
        not base
        or any(aliases.get(item, item) in {"ctrl", "alt", "shift"}
               for item in segments)
    ):
        raise ValueError(f"{path} has unsupported key binding: {value}")

    prompt_key, plain_label = _plain_key(base, path=path, value=value)
    modifier_set = frozenset(modifiers)
    key_name = _canonical_key_name(base)
    prompt_sequences = _modified_prompt_sequences(
        prompt_key,
        key_name,
        modifier_set,
        path=path,
        value=value,
    )
    ordered_labels = tuple(
        label
        for modifier, label in (
            ("ctrl", "Ctrl"),
            ("alt", "Alt"),
            ("shift", "Shift"),
        )
        if modifier in modifier_set
    )
    label = "+".join((*ordered_labels, plain_label))
    return TuiKeyStroke(
        keys=prompt_sequences[0],
        key_sequences=prompt_sequences,
        label=label,
        key_name=key_name,
        modifiers=modifier_set,
    )


def _modified_prompt_sequences(
    prompt_key: str,
    key_name: str,
    modifiers: frozenset[str],
    *,
    path: str,
    value: str,
) -> tuple[tuple[Keys | str, ...], ...]:
    """生成同一逻辑按键的旧式 VT 与增强事件表示。"""
    core = prompt_key
    if "shift" in modifiers:
        if len(core) == 1 and core.isalpha():
            core = core.upper()
        else:
            core = f"s-{core}"
    if "ctrl" in modifiers:
        core = f"c-{core}"

    sequence = (core,)
    if "alt" in modifiers:
        sequence = ("escape", *sequence)

    resolved: list[tuple[Keys | str, ...]] = []
    legacy_ambiguous = bool(
        modifiers == frozenset({"ctrl"})
        and key_name in _LEGACY_CONTROL_ALIASES
    )
    try:
        legacy = _prompt_keys(sequence, path=path, value=value)
    except ValueError:
        legacy = None
    if legacy is not None and not legacy_ambiguous:
        resolved.append(legacy)

    enhanced = enhanced_key_token(key_name, modifiers)
    if enhanced is not None:
        enhanced_sequence = (enhanced,)
        if enhanced_sequence not in resolved:
            resolved.append(enhanced_sequence)

    if not resolved:
        raise ValueError(
            f"{path} has unsupported key binding in the current terminal "
            f"decoder: {value}"
        )
    return tuple(resolved)


def _combine_stroke_sequences(
    strokes: tuple[TuiKeyStroke, ...],
) -> tuple[tuple[Keys | str, ...], ...]:
    """按输入顺序组合 chord 各 stroke 的可用终端表示。"""
    choices: list[tuple[tuple[Keys | str, ...], ...]] = []
    for index, stroke in enumerate(strokes):
        sequences = stroke.key_sequences
        if index > 0 and "alt" in stroke.modifiers:
            sequences = tuple(
                sequence
                for sequence in sequences
                if len(sequence) == 1
                and isinstance(sequence[0], str)
                and is_enhanced_key_token(sequence[0])
            )
        choices.append(sequences)

    combined = tuple(
        tuple(key for sequence in selection for key in sequence)
        for selection in itertools.product(*choices)
    )
    if not combined:
        raise ValueError("key chord has no terminal representation")
    return combined


def _canonical_key_name(base: str) -> str:
    """返回用于冲突诊断的规范键名。"""
    aliases = {
        "escape": "esc",
        "return": "enter",
        "spacebar": "space",
        "pageup": "page-up",
        "pagedown": "page-down",
        "pgup": "page-up",
        "pgdn": "page-down",
        "del": "delete",
    }
    return aliases.get(base, base)


def _plain_key(base: str, *, path: str, value: str) -> tuple[str, str]:
    """解析无修饰键并返回 prompt 名称和展示标签。"""
    named = {
        "esc": ("escape", "Esc"),
        "escape": ("escape", "Esc"),
        "enter": ("enter", "Enter"),
        "return": ("enter", "Enter"),
        "tab": ("tab", "Tab"),
        "backspace": ("backspace", "Backspace"),
        "delete": ("delete", "Delete"),
        "insert": ("insert", "Insert"),
        "up": ("up", "Up"),
        "down": ("down", "Down"),
        "left": ("left", "Left"),
        "right": ("right", "Right"),
        "home": ("home", "Home"),
        "end": ("end", "End"),
        "page-up": ("pageup", "PgUp"),
        "pageup": ("pageup", "PgUp"),
        "pgup": ("pageup", "PgUp"),
        "page-down": ("pagedown", "PgDn"),
        "pagedown": ("pagedown", "PgDn"),
        "pgdn": ("pagedown", "PgDn"),
        "space": (" ", "Space"),
        "spacebar": (" ", "Space"),
        "minus": ("-", "-"),
    }

    if base in named:
        return named[base]
    if len(base) == 1:
        return base, base.upper() if base.isalpha() else base
    if base.startswith("f") and base[1:].isdigit():
        number = int(base[1:])
        if 1 <= number <= 24:
            return base, base.upper()

    raise ValueError(f"{path} has invalid key binding: {value}")


def _prompt_keys(
    keys: tuple[str, ...],
    *,
    path: str,
    value: str
) -> tuple[Keys | str, ...]:
    """使用 prompt_toolkit 校验并规范化按键序列。"""
    bindings = KeyBindings()

    try:
        bindings.add(*keys)(lambda event: None)
    except ValueError as error:
        raise ValueError(
            f"{path} has unsupported key binding: {value}"
        ) from error

    return tuple(bindings.bindings[0].keys)


def _validate_context_conflicts(
    context: str,
    keymap: TuiKeymapContext,
) -> None:
    """拒绝同一输入上下文中分配给多个动作的按键。"""
    owners: dict[tuple[Keys | str, ...], str] = {}
    for field_info in fields(keymap):
        action = field_info.name
        bindings = _bindings_for_action(keymap, action)
        for binding in bindings:
            for key_sequence in binding.key_sequences:
                previous = owners.get(key_sequence)
                if (
                    previous is not None
                    and previous != action
                    and not _same_context_overlap_allowed(
                        context,
                        previous,
                        action,
                    )
                ):
                    raise ValueError(
                        f"tui.keymap.{context}.{action} conflicts with "
                        f"tui.keymap.{context}.{previous}: {binding.label}"
                    )
                owners[key_sequence] = action


def _same_context_overlap_allowed(
    context: str,
    first: str,
    second: str,
) -> bool:
    """判断两个动作是否由互斥运行时条件明确分派。"""
    allowed = {
        "editor": frozenset({
            frozenset({"exit", "delete_forward"}),
            frozenset({"cancel_shell_mode", "delete_backward"}),
            frozenset({"cancel_shell_mode", "cancel_completion"}),
        }),
    }
    return frozenset({first, second}) in allowed.get(context, frozenset())


def _validate_overlapping_contexts(
    global_keys: TuiGlobalKeymap,
    chat: TuiChatKeymap,
    composer: TuiComposerKeymap,
    editor: TuiEditorKeymap,
) -> None:
    """拒绝会在主输入分派链中互相遮蔽的动作。"""
    contexts: tuple[tuple[str, TuiKeymapContext], ...] = (
        ("global", global_keys),
        ("chat", chat),
        ("composer", composer),
        ("editor", editor),
    )
    owners: dict[tuple[Keys | str, ...], tuple[str, str]] = {}
    for context, keymap in contexts:
        for action, bindings in _context_actions(keymap):
            for binding in bindings:
                for key_sequence in binding.key_sequences:
                    previous = owners.get(key_sequence)
                    if previous is None:
                        owners[key_sequence] = context, action
                        continue
                    previous_context, previous_action = previous
                    if previous_context == context:
                        continue
                    if _main_overlap_allowed(
                        previous_context,
                        previous_action,
                        context,
                        action,
                        binding,
                    ):
                        continue
                    raise ValueError(
                        f"tui.keymap.{context}.{action} conflicts with "
                        f"tui.keymap.{previous_context}.{previous_action}: "
                        f"{binding.label}"
                    )


def _main_overlap_allowed(
    first_context: str,
    first_action: str,
    second_context: str,
    second_action: str,
    binding: TuiKeyBinding,
) -> bool:
    """判断主输入上下文间是否存在显式的条件化优先级。"""
    identities = frozenset({
        f"{first_context}.{first_action}",
        f"{second_context}.{second_action}",
    })
    allowed = {
        frozenset({
            "chat.interrupt_turn",
            "editor.cancel_shell_mode",
        }),
        frozenset({
            "chat.interrupt_turn",
            "editor.cancel_completion",
        }),
        frozenset({
            "composer.submit",
            "editor.insert_newline",
        }),
    }
    if identities not in allowed:
        return False
    return binding.strokes[0].key_name in {"esc", "enter"}


def _validate_configured_binding_shapes(
    configs: typing.Mapping[str, TuiKeymapConfig],
) -> None:
    """校验可配置按键不会占用文本输入或固定安全入口。"""
    for context, config in configs.items():
        for action in config:
            effective_context = (
                "composer"
                if context == "global"
                and action in {"submit", "queue", "toggle_shortcuts"}
                else context
            )
            bindings = _resolve_bindings(
                config,
                action,
                defaults=(),
                path=f"tui.keymap.{context}.{action}",
            )
            for binding in bindings:
                _validate_binding_shape(
                    context=effective_context,
                    action=action,
                    path=f"tui.keymap.{context}.{action}",
                    binding=binding,
                )


def _validate_binding_shape(
    *,
    context: str,
    action: str,
    path: str,
    binding: TuiKeyBinding,
) -> None:
    """校验一个绑定在其输入上下文中的可达性与安全性。"""
    for stroke in binding.strokes:
        if (
            len(stroke.key_name) == 1
            and {"ctrl", "alt"}.issubset(stroke.modifiers)
        ):
            raise ValueError(
                f"{path}: Ctrl+Alt character keys may be AltGr text input"
            )

    if binding.is_chord:
        prefix = binding.strokes[0]
        if any(stroke.key_name == "esc" for stroke in binding.strokes):
            raise ValueError(
                f"{path}: Esc is reserved for cancelling an incomplete chord"
            )
        if (
            len(prefix.key_name) == 1
            and not {"ctrl", "alt"}.intersection(prefix.modifiers)
        ):
            raise ValueError(
                f"{path}: a chord prefix must use Ctrl, Alt, or a named key"
            )
        for stroke in binding.strokes:
            _validate_reserved_stroke(
                context=context,
                action=action,
                path=path,
                stroke=stroke,
                chord=True,
            )
        return None

    stroke = binding.strokes[0]
    if (
        context in {"global", "chat", "editor"}
        and len(stroke.key_name) == 1
        and not stroke.modifiers
    ):
        raise ValueError(f"{path}: printable keys are reserved for text input")
    if (
        context == "composer"
        and action not in {"enter_shell_mode", "toggle_shortcuts"}
        and len(stroke.key_name) == 1
        and not stroke.modifiers
    ):
        raise ValueError(f"{path}: printable keys are reserved for text input")
    _validate_reserved_stroke(
        context=context,
        action=action,
        path=path,
        stroke=stroke,
        chord=False,
    )


def _validate_reserved_stroke(
    *,
    context: str,
    action: str,
    path: str,
    stroke: TuiKeyStroke,
    chord: bool,
) -> None:
    """拒绝覆盖固定取消、退出和历史回溯按键。"""
    identity = f"{context}.{action}"
    token = stroke.key_name, stroke.modifiers
    allowed: dict[tuple[str, frozenset[str]], frozenset[str]] = {
        ("esc", frozenset()): frozenset({
            "chat.interrupt_turn",
            "editor.cancel_shell_mode",
            "editor.cancel_completion",
            "list.cancel",
            "approval.decline",
        }),
        ("c", frozenset({"ctrl"})): frozenset({
            "pager.close",
        }),
        ("d", frozenset({"ctrl"})): frozenset({
            "editor.delete_forward",
            "pager.half_page_down",
        }),
        ("z", frozenset({"ctrl"})): frozenset({
            "editor.undo",
        }),
    }
    owners = allowed.get(token)
    if owners is None:
        return None
    if chord or identity not in owners:
        raise ValueError(
            f"{path}: {stroke.label} is reserved by a fixed safety action"
        )


def _validate_chord_conflicts(
    global_keys: TuiGlobalKeymap,
    chat: TuiChatKeymap,
    composer: TuiComposerKeymap,
    editor: TuiEditorKeymap,
    pager: TuiPagerKeymap,
    list_keys: TuiListKeymap,
    approval: TuiApprovalKeymap,
) -> None:
    """拒绝两键 chord 的前缀遮蔽和跨重叠上下文歧义。"""
    contexts: tuple[tuple[str, TuiKeymapContext], ...] = (
        ("global", global_keys),
        ("chat", chat),
        ("composer", composer),
        ("editor", editor),
        ("pager", pager),
        ("list", list_keys),
        ("approval", approval),
    )
    actions = tuple(
        (context, action, binding)
        for context, keymap in contexts
        for action, bindings in _context_actions(keymap)
        for binding in bindings
    )
    for context, action, binding in actions:
        if not binding.is_chord:
            continue
        for prefix in binding.strokes[0].key_sequences:
            for other_context, other_action, other_binding in actions:
                if not _contexts_overlap(context, other_context):
                    continue
                if (
                    not other_binding.is_chord
                    and prefix in other_binding.key_sequences
                ):
                    raise ValueError(
                        f"tui.keymap.{context}.{action} chord prefix "
                        "conflicts with "
                        f"tui.keymap.{other_context}.{other_action}: "
                        f"{binding.strokes[0].label}"
                    )


def _contexts_overlap(first: str, second: str) -> bool:
    """返回两个运行时上下文是否共享同一输入路径。"""
    if first == second:
        return True
    return any(
        first in group and second in group
        for group in TUI_KEYMAP_OVERLAP_GROUPS
    )


def _context_actions(
    keymap: TuiKeymapContext,
) -> tuple[tuple[str, TuiActionBindings], ...]:
    """返回一个冻结上下文中的全部动作绑定。"""
    return tuple(
        (
            field_info.name,
            _bindings_for_action(keymap, field_info.name),
        )
        for field_info in fields(keymap)
    )


def _validate_reserved_pager_bindings(keymap: TuiPagerKeymap) -> None:
    """拒绝页面动作覆盖完整记录的固定编辑按键。"""
    reserved: dict[tuple[Keys | str, ...], str] = {}
    for key, action in (
        ("esc", "edit_previous"),
        ("left", "edit_previous"),
        ("right", "edit_next"),
        ("enter", "edit_confirm"),
    ):
        binding = _parse_binding(key, path="tui.keymap.pager")
        for key_sequence in binding.key_sequences:
            reserved[key_sequence] = action

    for field_info in fields(keymap):
        action = field_info.name
        bindings = _bindings_for_action(keymap, action)

        for binding in bindings:
            for key_sequence in binding.key_sequences:
                fixed = reserved.get(key_sequence)
                if fixed is not None:
                    raise ValueError(
                        f"tui.keymap.pager.{action} conflicts with fixed "
                        f"transcript {fixed}: {binding.label}"
                    )


def _bindings_for_action(
    keymap: TuiKeymapContext,
    action: str,
) -> tuple[TuiKeyBinding, ...]:
    """读取并校验按键映射动作的绑定集合。"""
    value = getattr(keymap, action, None)
    if not isinstance(value, tuple) or any(
        not isinstance(binding, TuiKeyBinding)
        for binding in value
    ):
        raise ValueError(f"tui.keymap action {action} has invalid bindings")
    return value


def _primary_label(bindings: tuple[TuiKeyBinding, ...]) -> str:
    """返回首个按键标签或空字符串。"""
    return bindings[0].label if bindings else ""


if __name__ == '__main__':
    pass
