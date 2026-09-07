# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import (
    dataclass,
    fields,
)

from prompt_toolkit.filters import FilterOrBool
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.keys import Keys


@dataclass(frozen=True, slots=True)
class TuiKeyBinding(object):
    """保存一个已解析的按键序列及其展示标签。"""
    keys: tuple[Keys | str, ...]
    label: str


TuiActionBindings = tuple[TuiKeyBinding, ...]


@dataclass(frozen=True, slots=True)
class TuiResolvedKeyAction(object):
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
        """从有效配置中解析完整记录相关按键。"""
        root = config if isinstance(config, dict) else {}
        tui = root.get("tui") if isinstance(root.get("tui"), dict) else {}

        keymap = (
            tui.get("keymap")
            if isinstance(tui.get("keymap"), dict)
            else {}
        )

        global_config = (
            keymap.get("global")
            if isinstance(keymap.get("global"), dict)
            else {}
        )

        pager_config = (
            keymap.get("pager")
            if isinstance(keymap.get("pager"), dict)
            else {}
        )

        global_keys = TuiGlobalKeymap(
            open_transcript=_resolve_bindings(
                global_config,
                "open_transcript",
                defaults=("ctrl-t",),
                path="tui.keymap.global.open_transcript",
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
        chat = _default_chat_keymap()
        composer = _default_composer_keymap()
        editor = _default_editor_keymap()
        list_keys = _default_list_keymap()
        approval = _default_approval_keymap()
        pager = _resolve_pager_keymap(pager_config)

        for context_name, context in (
            ("chat", chat),
            ("composer", composer),
            ("list", list_keys),
            ("approval", approval),
            ("pager", pager),
        ):
            _validate_context_conflicts(context_name, context)
        _validate_reserved_pager_bindings(pager)

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
        contexts: tuple[tuple[str, TuiKeymapContext], ...] = (
            ("global", self.global_keys),
            ("chat", self.chat),
            ("composer", self.composer),
            ("editor", self.editor),
            ("pager", self.pager),
            ("list", self.list),
            ("approval", self.approval),
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
            previous = owners.get(binding.keys)
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
            if save_before is None:
                bindings.add(
                    *binding.keys,
                    eager=eager,
                    filter=binding_filter,
                )(handler)
            else:
                bindings.add(
                    *binding.keys,
                    eager=eager,
                    filter=binding_filter,
                    save_before=save_before,
                )(handler)
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
    return any(binding.keys == resolved for binding in configured)


def _default_bindings(action: str, *values: str) -> TuiActionBindings:
    """解析一个内置动作的默认按键。"""
    return tuple(
        _parse_binding(value, path=f"tui.keymap.{action}")
        for value in values
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
        ),
        delete_forward=_default_bindings(
            "editor.delete_forward",
            "delete",
            "ctrl-d",
        ),
        delete_word_backward=_default_bindings(
            "editor.delete_word_backward",
            "ctrl-w",
            "alt-backspace",
        ),
        undo=_default_bindings("editor.undo", "ctrl-z"),
        move_left=_default_bindings("editor.move_left", "left", "ctrl-b"),
        move_right=_default_bindings("editor.move_right", "right", "ctrl-f"),
        move_up=_default_bindings("editor.move_up", "up"),
        move_down=_default_bindings("editor.move_down", "down"),
        insert_newline=_default_bindings(
            "editor.insert_newline",
            "ctrl-j",
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
        move_left=_default_bindings("list.move_left", "left"),
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


def _resolve_bindings(
    config: dict[str, typing.Any],
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
    seen: set[tuple[Keys | str, ...]] = set()

    for value in values:
        binding = _parse_binding(value, path=path)
        if binding.keys in seen:
            continue
        seen.add(binding.keys)
        out.append(binding)

    return tuple(out)


def _resolve_pager_keymap(config: dict[str, typing.Any]) -> TuiPagerKeymap:
    """解析页面按键，并让显式覆盖优先于其他动作的默认键。"""
    defaults: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("scroll_up", ("up", "k")),
        ("scroll_down", ("down", "j")),
        ("page_up", ("page-up", "ctrl-b")),
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
            previous = owners.get(binding.keys)
            if previous is None:
                owners[binding.keys] = action, explicit
                accepted.append(binding)
                continue

            previous_action, previous_explicit = previous
            if previous_explicit and not explicit:
                continue
            if explicit and not previous_explicit:
                resolved[previous_action] = tuple(
                    item
                    for item in resolved[previous_action]
                    if item.keys != binding.keys
                )
                owners[binding.keys] = action, True
                accepted.append(binding)
                continue
            raise ValueError(
                f"tui.keymap.pager.{action} conflicts with "
                f"tui.keymap.pager.{previous_action}: {binding.label}"
            )

        resolved[action] = tuple(accepted)

    return TuiPagerKeymap(**resolved)


def _parse_binding(value: str, *, path: str) -> TuiKeyBinding:
    """把规范按键字符串转换为 prompt_toolkit 按键序列。"""
    text = str(value or "").strip().lower().replace("control-", "ctrl-")
    if not text:
        raise ValueError(f"{path} contains an empty key binding")

    modifier: str = ""

    base = text

    for candidate in ("ctrl", "alt", "shift"):
        prefix = f"{candidate}-"
        if text.startswith(prefix):
            modifier = candidate
            base = text[len(prefix):]
            break
    if not base or any(base.startswith(f"{item}-") for item in (
            "ctrl",
            "alt",
            "shift",
    )):
        raise ValueError(f"{path} has unsupported key binding: {value}")

    prompt_key, label = _plain_key(base, path=path, value=value)

    if modifier == "ctrl":
        if len(prompt_key) == 1:
            keys = (f"c-{prompt_key}",)
        elif prompt_key in {
            "delete",
            "down",
            "end",
            "home",
            "insert",
            "left",
            "pagedown",
            "pageup",
            "right",
            "up",
        }:
            keys = (f"c-{prompt_key}",)
        else:
            raise ValueError(f"{path} has unsupported key binding: {value}")
        label = f"Ctrl+{label}"

    elif modifier == "alt":
        keys = ("escape", prompt_key)
        label = f"Alt+{label}"

    elif modifier == "shift":
        if prompt_key == "tab":
            keys = ("s-tab",)
            label = "Shift+Tab"
        elif prompt_key in {"left", "right", "up", "down"}:
            keys = (f"s-{prompt_key}",)
            label = f"Shift+{label}"
        elif len(prompt_key) == 1 and prompt_key.isalpha():
            keys = (prompt_key.upper(),)
            label = f"Shift+{label}"
        else:
            raise ValueError(f"{path} has unsupported key binding: {value}")
    else:
        keys = (prompt_key,)

    parsed = _prompt_keys(keys, path=path, value=value)
    return TuiKeyBinding(keys=parsed, label=label)


def _plain_key(base: str, *, path: str, value: str) -> tuple[str, str]:
    """解析无修饰键并返回 prompt 名称和展示标签。"""
    named = {
        "esc": ("escape", "Esc"),
        "escape": ("escape", "Esc"),
        "enter": ("enter", "Enter"),
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
        "page-down": ("pagedown", "PgDn"),
        "pagedown": ("pagedown", "PgDn"),
        "space": (" ", "Space"),
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
            previous = owners.get(binding.keys)
            if previous is not None:
                raise ValueError(
                    f"tui.keymap.{context}.{action} conflicts with "
                    f"tui.keymap.{context}.{previous}: {binding.label}"
                )
            owners[binding.keys] = action


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
    reserved = {
        _parse_binding(key, path="tui.keymap.pager").keys: action
        for key, action in (
            ("esc", "edit_previous"),
            ("left", "edit_previous"),
            ("right", "edit_next"),
            ("enter", "edit_confirm"),
        )
    }

    for field_info in fields(keymap):
        action = field_info.name
        bindings = _bindings_for_action(keymap, action)

        for binding in bindings:
            fixed = reserved.get(binding.keys)
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
