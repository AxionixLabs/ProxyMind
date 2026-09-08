# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from agent.ports.presentation import (
    ApplicationView,
    StyledBlock,
    TextSpan,
    TextStyle,
)
from protocol.schema.review import ReviewOutput

_PRIMARY = TextStyle()
_BOLD = TextStyle(bold=True)
_SECONDARY = TextStyle(dim=True)


def build_review_started_view(hint: str) -> ApplicationView:
    """构建不暴露隐藏 prompt 或请求载荷的 Review 启动展示。"""
    text = f"Reviewing {hint}"
    return ApplicationView(
        type="review.started",
        renderable=StyledBlock(
            plain_text=f"• {text}",
            spans=(
                TextSpan("• ", _SECONDARY),
                TextSpan(text, _PRIMARY),
            ),
            preserve_spans=True,
        ),
    )


def build_review_completed_view(output: ReviewOutput) -> ApplicationView:
    """按稳定 Review 文本协议构建 explanation 和 findings 展示。"""
    text = review_output_text(output)
    return ApplicationView(
        type="review.completed",
        renderable=StyledBlock(
            plain_text=text,
            spans=(TextSpan(text, _PRIMARY),),
            preserve_spans=True,
        ),
    )


def build_review_failed_view(message: str) -> ApplicationView:
    """构建 Review 失败终态展示。"""
    text = str(message or "Review failed.").strip() or "Review failed."
    return ApplicationView(
        type="review.failed",
        renderable=StyledBlock(
            plain_text=f"■ {text}",
            spans=(
                TextSpan("■ ", TextStyle(bold=True)),
                TextSpan(text, _PRIMARY),
            ),
            preserve_spans=True,
        ),
    )


def build_review_cancelled_view(reason: str) -> ApplicationView:
    """构建 Review 已由权威终态取消的展示。"""
    detail = str(reason or "").strip()
    text = "Review cancelled" + (f": {detail}" if detail else "")
    return ApplicationView(
        type="review.cancelled",
        renderable=StyledBlock(
            plain_text=text,
            spans=(TextSpan(text, _SECONDARY),),
            preserve_spans=True,
        ),
    )


def build_review_reconciliation_view(
    message: str,
    *,
    effect_id: str = "",
) -> ApplicationView:
    """构建需要显式对账且不得伪装成普通失败的展示。"""
    detail = str(message or "Review requires reconciliation.").strip()
    suffix = f" ({effect_id})" if effect_id else ""
    text = f"Review requires reconciliation{suffix}: {detail}"
    return ApplicationView(
        type="review.reconciliation_required",
        renderable=StyledBlock(
            plain_text=text,
            spans=(TextSpan(text, _BOLD),),
            preserve_spans=True,
        ),
    )


def review_output_text(output: ReviewOutput) -> str:
    """把类型化 ReviewOutput 转换为稳定的 assistant 正文。"""
    sections = [output.overall_explanation]
    findings = output.findings
    if findings:
        sections.append(
            "Review comment:"
            if len(findings) == 1
            else "Full review comments:"
        )
        finding_lines: list[str] = []
        for finding in findings:
            location = finding.code_location
            line_range = location.line_range
            finding_lines.append(
                f"- {finding.title} — {location.path}:"
                f"{line_range.start}-{line_range.end}"
            )
            finding_lines.extend(
                f"  {line}"
                for line in finding.body.split("\n")
            )
        sections.append("\n".join(finding_lines))
    return "\n\n".join(sections)


if __name__ == '__main__':
    pass
