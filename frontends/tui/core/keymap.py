# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import (
    dataclass,
    fields,
)
from prompt_toolkit.key_binding import KeyBindings


@dataclass(frozen=True, slots=True)
class TuiKeyBinding(object):
    """保存一个已解析的按键序列及其展示标签。"""
    keys: tuple[typing.Any, ...]
    label: str


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
    open_transcript: tuple[TuiKeyBinding, ...]
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

        open_transcript = _resolve_bindings(
            global_config,
            "open_transcript",
            defaults=("ctrl-t",),
            path="tui.keymap.global.open_transcript",
        )

        pager = _resolve_pager_keymap(pager_config)

        _validate_context_conflicts("pager", pager)
        _validate_reserved_pager_bindings(pager)

        return cls(open_transcript=open_transcript, pager=pager)

    @property
    def open_transcript_label(self) -> str:
        """返回打开完整记录的首选按键标签。"""
        return _primary_label(self.open_transcript)

    def validate_main_conflicts(
        self,
        reserved: typing.Iterable[
            tuple[str, tuple[typing.Any, ...]]
        ]
    ) -> None:
        """拒绝打开完整记录的按键覆盖现有主输入动作。"""
        owners = {keys: action for action, keys in reserved}
        for binding in self.open_transcript:
            previous = owners.get(binding.keys)
            if previous is not None:
                raise ValueError(
                    "tui.keymap.global.open_transcript conflicts with "
                    f"{previous}: {binding.label}"
                )


def binding_labels(bindings: tuple[TuiKeyBinding, ...]) -> str:
    """把一组按键转换为斜线分隔的展示标签。"""
    return "/".join(binding.label for binding in bindings)


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
    seen: set[tuple[typing.Any, ...]] = set()

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
    owners: dict[tuple[typing.Any, ...], tuple[str, bool]] = {}

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
        if len(prompt_key) != 1:
            raise ValueError(f"{path} has unsupported key binding: {value}")

        keys = (f"c-{prompt_key}",)
        label = f"Ctrl+{label}"

    elif modifier == "alt":
        keys = ("escape", prompt_key)
        label = f"Alt+{label}"

    elif modifier == "shift":
        if prompt_key == "tab":
            keys = ("s-tab",)
            label = "Shift+Tab"
        elif len(prompt_key) == 1 and prompt_key.isalpha():
            keys  = (prompt_key.upper(),)
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
) -> tuple[typing.Any, ...]:
    """使用 prompt_toolkit 校验并规范化按键序列。"""
    bindings = KeyBindings()

    try:
        bindings.add(*keys)(lambda event: None)
    except ValueError as error:
        raise ValueError(
            f"{path} has unsupported key binding: {value}"
        ) from error

    return tuple(bindings.bindings[0].keys)


def _validate_context_conflicts(context: str, keymap: TuiPagerKeymap) -> None:
    """拒绝同一输入上下文中分配给多个动作的按键。"""
    owners: dict[tuple[typing.Any, ...], str] = {}
    for field in fields(keymap):
        action = field.name
        bindings = typing.cast(tuple[TuiKeyBinding, ...], getattr(keymap, action))
        for binding in bindings:
            previous = owners.get(binding.keys)
            if previous is not None:
                raise ValueError(
                    f"tui.keymap.{context}.{action} conflicts with "
                    f"tui.keymap.{context}.{previous}: {binding.label}"
                )
            owners[binding.keys] = action


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

    for field in fields(keymap):
        action = field.name
        bindings = typing.cast(tuple[TuiKeyBinding, ...], getattr(keymap, action))

        for binding in bindings:
            fixed = reserved.get(binding.keys)
            if fixed is not None:
                raise ValueError(
                    f"tui.keymap.pager.{action} conflicts with fixed "
                    f"transcript {fixed}: {binding.label}"
                )


def _primary_label(bindings: tuple[TuiKeyBinding, ...]) -> str:
    """返回首个按键标签或空字符串。"""
    return bindings[0].label if bindings else ""


if __name__ == '__main__':
    pass
