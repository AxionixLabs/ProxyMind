# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from .geometry import (
    ActiveViewLayout,
    AuxiliaryPaneLayout,
    ComposerLayout,
    OverlayLayout,
)


def measure_composer_layout(
    *,
    available_height: int,
    input_content_height: int,
    popup_visible: bool,
    completion_hint_height: int,
    completion_candidate_count: int,
    input_surface_padding_height: int,
    completion_max_height: int,
) -> ComposerLayout:
    """在给定底部预算内计算输入区、补全和 footer 高度。"""
    available = max(0, int(available_height))
    padding = max(0, int(input_surface_padding_height))
    minimum_surface_height = padding * 2 + 1
    hint_height = max(0, int(completion_hint_height))

    footer_height = int(
        not popup_visible
        and available >= minimum_surface_height + 1
    )
    input_available_height = max(
        1,
        available - padding * 2 - footer_height,
    )
    input_height = min(
        max(1, int(input_content_height)),
        input_available_height,
    )
    popup_available_height = max(
        0,
        available - padding * 2 - input_height,
    )
    popup_height = (
        min(
            max(0, int(completion_max_height)) + hint_height,
            max(0, int(completion_candidate_count)) + hint_height,
            popup_available_height,
        )
        if popup_visible
        else 0
    )

    return ComposerLayout(
        input_top_padding_height=padding,
        input_height=input_height,
        input_bottom_padding_height=padding,
        popup_height=popup_height,
        footer_height=footer_height,
    )


def allocate_auxiliary_pane_layout(
    *,
    available_height: int,
    composer_height: int,
    natural_status_height: int,
    natural_process_status_height: int,
    natural_queued_height: int,
) -> AuxiliaryPaneLayout:
    """按剩余高度分配状态、进程、排队消息和交互间距。"""
    remaining_height = max(
        0,
        int(available_height) - max(0, int(composer_height)),
    )
    natural_status = max(0, int(natural_status_height))
    natural_process = max(0, int(natural_process_status_height))
    natural_queued = max(0, int(natural_queued_height))

    status_height = min(natural_status, remaining_height)
    remaining_height -= status_height
    process_status_height = min(natural_process, remaining_height)
    remaining_height -= process_status_height
    queued_height = min(natural_queued, remaining_height)
    remaining_height -= queued_height
    interaction_gap_height = int(
        natural_queued == 0
        and bool(natural_status or natural_process)
        and remaining_height > 0
    )
    return AuxiliaryPaneLayout(
        status_height=status_height,
        process_status_height=process_status_height,
        queued_height=queued_height,
        interaction_gap_height=interaction_gap_height,
    )


def allocate_approval_view_layout(
    *,
    available_height: int,
    natural_content_height: int,
    natural_footer_height: int,
) -> ActiveViewLayout:
    """在底部预算内分配审批内容和 footer 高度。"""
    available = max(0, int(available_height))
    footer_height = min(available, max(0, int(natural_footer_height)))
    content_height = min(
        max(0, int(natural_content_height)),
        max(0, available - footer_height),
    )
    return ActiveViewLayout(
        surface="approval",
        available_height=available,
        top_padding_height=0,
        content_height=content_height,
        bottom_padding_height=0,
        footer_height=footer_height,
    )


def allocate_menu_view_layout(
    *,
    available_height: int,
    natural_content_height: int,
    natural_footer_height: int,
    vertical_inset: int,
) -> ActiveViewLayout:
    """在底部预算内分配菜单内容、留白和 footer 高度。"""
    available = max(0, int(available_height))
    inset = min(
        max(0, int(vertical_inset)),
        max(0, (available - 1) // 2),
    )
    footer_height = min(available, max(0, int(natural_footer_height)))
    content_height = min(
        max(0, int(natural_content_height)),
        max(0, available - inset * 2 - footer_height),
    )
    return ActiveViewLayout(
        surface="menu",
        available_height=available,
        top_padding_height=inset,
        content_height=content_height,
        bottom_padding_height=inset,
        footer_height=footer_height,
    )


def allocate_process_viewer_layout(
    *,
    available_height: int,
    natural_content_height: int,
    top_padding: int,
) -> ActiveViewLayout:
    """在底部预算内分配进程查看器内容和顶部留白。"""
    available = max(0, int(available_height))
    padding = min(
        max(0, int(top_padding)),
        max(0, available - 1),
    )
    content_height = min(
        max(0, int(natural_content_height)),
        max(0, available - padding),
    )
    return ActiveViewLayout(
        surface="process_viewer",
        available_height=available,
        top_padding_height=padding,
        content_height=content_height,
        bottom_padding_height=0,
        footer_height=0,
    )


def measure_overlay_layout(
    *,
    total_height: int,
    footer_max_height: int,
) -> OverlayLayout:
    """计算单行标题 overlay 的正文和 footer 高度。"""
    height = max(0, int(total_height))
    header_height = min(1, height)
    footer_height = min(
        max(0, int(footer_max_height)),
        max(0, height - header_height),
    )
    return OverlayLayout(
        header_height=header_height,
        content_height=max(0, height - header_height - footer_height),
        footer_height=footer_height,
    )


if __name__ == '__main__':
    pass
