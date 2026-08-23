# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_core.config_session import ConfigSession
from mind_core.skills import (
    SkillSpec,
    available_skills,
    configured_skills
)
from ..core.models import (
    MenuColumnWidthMode,
    MenuDescriptionLayout,
    MenuEmptyAcceptAction,
    MenuOption,
    MenuRequest,
    STANDARD_MENU_FOOTER_HINT
)
from ..prompting.skills import (
    skill_description_text,
    skill_match_score
)
from ..runtime.ports import SkillRuntimePort

_LIST_ACTION: typing.Final[str] = "list"

_MANAGE_ACTION: typing.Final[str] = "manage"

_SKILL_MANAGE_FOOTER: typing.Final[str] = (
    "Press space or enter to toggle; esc to close"
)


async def choose_skill(
    runtime: SkillRuntimePort,
    config_session: ConfigSession
) -> None:
    """打开 skills 动作菜单并进入原生输入或管理菜单。"""
    all_skills = _sorted_skills(available_skills())

    selected_action = await runtime.select_menu(_root_request())
    if selected_action == _LIST_ACTION:
        runtime.open_skill_search()
        return None

    if selected_action == _MANAGE_ACTION:
        await _manage_skills(runtime, config_session, all_skills)
    return None


def _root_request() -> MenuRequest:
    """构造 `/skills` 一级动作菜单。"""
    return MenuRequest(
        title="Skills",
        view_id="skills:root",
        help_text="Choose an action",
        footer_hint=STANDARD_MENU_FOOTER_HINT,
        options=(
            MenuOption(
                value=_LIST_ACTION,
                label="List skills",
                detail="Tip: press @ to open this list directly.",
            ),
            MenuOption(
                value=_MANAGE_ACTION,
                label="Enable/Disable Skills",
                detail="Enable or disable skills.",
            ),
        ),
    )


async def _manage_skills(
    runtime: SkillRuntimePort,
    config_session: ConfigSession,
    all_skills: tuple[SkillSpec, ...]
) -> None:
    """打开可搜索的启用状态菜单并自动保存每次切换。"""
    config = config_session.load()

    enabled_names  = _configured_names(config, "enabled")
    disabled_names = _configured_names(config, "disabled")

    enabled_state = {
        skill.name.casefold(): (
            skill.name.casefold() not in disabled_names
            and (not enabled_names or skill.name.casefold() in enabled_names)
        )
        for skill in all_skills
    }

    def toggle(skill: SkillSpec) -> None:
        key = skill.name.casefold()
        enabled = not enabled_state.get(key, False)
        enabled_state[key] = enabled

        saved = _save_skill_enabled(
            config_session,
            skill,
            enabled=enabled,
        )

        runtime.input_model.set_skills(configured_skills(saved))
        runtime.update_menu(_manage_request(all_skills, enabled_state, toggle))

    await runtime.select_menu(
        _manage_request(all_skills, enabled_state, toggle)
    )


def _manage_request(
    skills: tuple[SkillSpec, ...],
    enabled_state: dict[str, bool],
    toggle: typing.Callable[[SkillSpec], None]
) -> MenuRequest:
    """根据最新启用状态生成管理菜单请求。"""
    return MenuRequest(
        title="Enable/Disable Skills",
        view_id="skills:manage",
        help_text=(
            "Turn skills on or off. Your changes are saved automatically."
        ),
        searchable=True,
        search_placeholder="",
        search_ranker=_skill_search_rank,
        search_prompt_prefix="> ",
        search_prompt_style="class:tui-menu.search.placeholder",
        search_help_text="Type to search skills",
        search_empty_text="no matches",
        empty_accept_action=MenuEmptyAcceptAction.IGNORE,
        footer_hint=_SKILL_MANAGE_FOOTER,
        description_layout=MenuDescriptionLayout.COLUMNS,
        column_width_mode=MenuColumnWidthMode.AUTO_VISIBLE,
        separate_options=False,
        options=tuple(
            MenuOption(
                value=skill,
                label=(
                    f"[{'x' if enabled_state.get(skill.name.casefold(), False) else ' '}] "
                    f"{skill.name}"
                ),
                detail=skill_description_text(skill.description),
                search_value=skill.name,
                on_select=lambda skill=skill: toggle(skill),
                dismiss_on_select=False,
            )
            for skill in skills
        ),
        body=("No skills available.",) if not skills else (),
    )


def _configured_names(config: dict[str, typing.Any], field: str) -> set[str]:
    """读取配置中的 skill 名称并统一大小写。"""
    skills = config.get("skills") if isinstance(config, dict) else {}
    values = skills.get(field) if isinstance(skills, dict) else ()

    return {
        str(value).strip().casefold()
        for value in values or ()
        if str(value).strip()
    }


def _sorted_skills(skills: typing.Iterable[SkillSpec]) -> tuple[SkillSpec, ...]:
    """按用户可见名称排序菜单技能。"""
    return tuple(sorted(
        skills,
        key=lambda skill: (skill.name.casefold(), skill.name),
    ))


def _skill_search_rank(
    query: str,
    option: MenuOption
) -> tuple[int, str] | None:
    """对 skill 名称执行模糊匹配并生成排序键。"""
    name  = option.search_value or option.label
    score = skill_match_score(name, query)

    return (score, name) if score is not None else None


def _save_skill_enabled(
    config_session: ConfigSession,
    skill: SkillSpec,
    *,
    enabled: bool
) -> dict[str, typing.Any]:
    """更新单个 skill 的启用状态并返回保存后的配置。"""
    config = config_session.load()
    skills = config.get("skills") if isinstance(config, dict) else {}
    skills = skills if isinstance(skills, dict) else {}

    enabled_values  = [str(value) for value in skills.get("enabled") or []]
    disabled_values = [str(value) for value in skills.get("disabled") or []]

    key = skill.name.casefold()

    enabled_values = [
        value for value in enabled_values
        if value.strip().casefold() != key
    ] if enabled else enabled_values
    disabled_values = [
        value for value in disabled_values
        if value.strip().casefold() != key
    ] if enabled else disabled_values

    if enabled and enabled_values:
        enabled_values.append(skill.name)
    if not enabled:
        disabled_values.append(skill.name)

    return config_session.update_user({
        ("skills", "enabled"): enabled_values,
        ("skills", "disabled"): disabled_values,
    })


if __name__ == '__main__':
    pass
