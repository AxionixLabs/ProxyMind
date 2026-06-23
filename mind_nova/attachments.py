# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing


def upload_response_attachment(
    response: dict[str, typing.Any],
    *,
    context: str = "upload"
) -> dict[str, typing.Any]:
    """从上传响应中提取服务端生成的标准附件对象。"""
    attachment = response.get("attachment") if isinstance(response, dict) else None
    if not isinstance(attachment, dict):
        raise ValueError(f"{context} returned no attachment")
    return dict(attachment)


if __name__ == '__main__':
    pass
