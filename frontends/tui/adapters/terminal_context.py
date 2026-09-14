# -*- coding: utf-8 -*-
"""隔离 prompt-toolkit 终端交接的取消语义及私有状态访问。

沿用 Application 的交接队列，由每次调用负责完成自己的信号；上游在等待前驱和
CPR 时也保证取消清理、且通过相同回归测试后，可删除此适配并恢复上游入口。
"""

# Adapted from prompt_toolkit.application.run_in_terminal (3.0.52).
# Copyright (c) 2014, Jonathan Slenders
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
# * Redistributions in binary form must reproduce the above copyright notice, this
#   list of conditions and the following disclaimer in the documentation and/or
#   other materials provided with the distribution.
# * Neither the name of the {organization} nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR
# ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
# ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import asyncio
import typing
from contextlib import asynccontextmanager

from prompt_toolkit.application.current import get_app_or_none


@asynccontextmanager
async def in_terminal(
    render_cli_done: bool = False,
) -> typing.AsyncIterator[None]:
    """串行交接终端，并在等待、绘制及正文取消时释放所属完成信号。"""
    application = get_app_or_none()
    if application is None or not application.is_running:
        yield
        return

    previous = application._running_in_terminal_f
    completed: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    application._running_in_terminal_f = completed
    restore_renderer = False

    def complete() -> None:
        """完成当前交接拥有的信号。"""
        if not completed.done():
            completed.set_result(None)

    def complete_after_previous(_previous: asyncio.Future[None]) -> None:
        """在前驱结束后释放已取消的排队项。"""
        complete()

    try:
        if previous is not None:
            await asyncio.shield(previous)
        if application.output.responds_to_cpr:
            await application.renderer.wait_for_cpr_responses()

        restore_renderer = True
        if render_cli_done:
            application._redraw(render_as_done=True)
        else:
            application.renderer.erase()
        application._running_in_terminal = True
        with application.input.detach(), application.input.cooked_mode():
            yield
    finally:
        try:
            if restore_renderer:
                application._running_in_terminal = False
                application.renderer.reset()
                application._request_absolute_cursor_position()
                application._redraw()
        finally:
            # 取消排队项不得取消前驱，也不得让后继越过仍占用终端的前驱。
            if previous is not None and not previous.done():
                previous.add_done_callback(complete_after_previous)
            else:
                complete()
