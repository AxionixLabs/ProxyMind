# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing


def consume_builtin_done(event: dict[str, typing.Any], tracker: typing.Any) -> None:
    """
    消费 builtin done 事件。

    `SegmentTracker` 目前只关心 `sources / source_count` 这类通用元数据，
    因此这里统一下沉给 tracker 自己判断。
    """
    tracker.on_builtin_done(event)


if __name__ == '__main__':
    pass
