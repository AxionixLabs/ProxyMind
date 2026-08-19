# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_core.skills import SkillSpec
from ..core.models import (
    MenuDescriptionLayout,
    MenuOption,
    MenuRequest,
    STANDARD_MENU_FOOTER_HINT
)
from ..prompting.skills import skill_description_text

if typing.TYPE_CHECKING:
    from ..core.runtime import TuiRuntime


async def choose_skill(runtime: "TuiRuntime") -> SkillSpec | None:
    """选择一项可用 skill 并把引用写回主输入。"""
    skills = runtime.input_model.skills

    selected = await runtime.select_menu(MenuRequest(
        title="Skills",
        view_id="skills:root",
        status=f"items={len(skills)}",
        body=("No skills available.",) if not skills else (),
        help_text="",
        footer_hint=STANDARD_MENU_FOOTER_HINT,
        description_layout=MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW,
        options=tuple(
            MenuOption(
                value=skill,
                label=skill.name,
                detail=skill_description_text(skill.description),
            )
            for skill in skills
        ),
    ))
    if not isinstance(selected, SkillSpec):
        return None

    runtime.replace_input_text(
        f"${selected.name} ",
        selected_skill=True,
    )
    return selected


if __name__ == '__main__':
    pass
