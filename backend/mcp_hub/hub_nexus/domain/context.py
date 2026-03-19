#   ____            _            _
#  / ___|___  _ __ | |_ _____  _| |_
# | |   / _ \| '_ \| __/ _ \ \/ / __|
# | |__| (_) | | | | ||  __/>  <| |_
#  \____\___/|_| |_|\__\___/_/\_\\__|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing


class ContextMergeService(object):

    @staticmethod
    def merge_step_extract(
        pack_data: typing.Any,
        ctx: dict[str, typing.Any],
        allow_ctx_merge: bool
    ) -> None:
        """按开关把单步 extract 结果合并回运行上下文。"""
        if not allow_ctx_merge:
            return None
        if not isinstance(pack_data, dict):
            return None

        step_extract = pack_data.get("extract")
        if isinstance(step_extract, dict) and step_extract:
            ctx.update(step_extract)


if __name__ == '__main__':
    pass
