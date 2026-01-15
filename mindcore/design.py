#  ____            _
# |  _ \  ___  ___(_) __ _ _ __
# | | | |/ _ \/ __| |/ _` | '_ \
# | |_| |  __/\__ \ | (_| | | | |
# |____/ \___||___/_|\__, |_| |_|
#                    |___/
#

import random
import typing
import textwrap
from rich.console import Console
from mindnova import const


class Design(object):

    console: typing.Optional["Console"] = Console()

    def __init__(self, design_level: str = const.SHOW_LEVEL):
        self.design_level = design_level

    class Doc(object):

        @classmethod
        def log(cls, text: typing.Any) -> None:
            Design.console.print(const.PRINT_HEAD, f"[bold]{text}")

        @classmethod
        def suc(cls, text: typing.Any) -> None:
            Design.console.print(const.PRINT_HEAD, f"{const.SUC}{text}")

        @classmethod
        def wrn(cls, text: typing.Any) -> None:
            Design.console.print(const.PRINT_HEAD, f"{const.WRN}{text}")

        @classmethod
        def err(cls, text: typing.Any) -> None:
            Design.console.print(const.PRINT_HEAD, f"{const.ERR}{text}")

    @staticmethod
    def startup_logo() -> None:
        color = random.choice([
            "#7C3AED",
            "#A855F7",
            "#6366F1",
            "#3B82F6",
            "#22D3EE",
            "#06B6D4",
            "#10B981",
            "#2DD4BF",
            "#F472B6",
            "#FB7185",
            "#FBBF24",
            "#A3E635",
            "#7C3AED",
            "#A855F7",
            "#6366F1",
            "#3B82F6",
            "#60A5FA",
            "#22D3EE",
            "#06B6D4",
            "#2DD4BF",
            "#10B981",
            "#34D399",
            "#0EA5E9",
            "#38BDF8",
        ])

        banner_standard = textwrap.dedent(f"""\
             __  __ _           _
            |  \/  (_)_ __   __| |
            | |\/| | | '_ \ / _` |
            | |  | | | | | | (_| |
            |_|  |_|_|_| |_|\__,_|
        """)
        banner_speed = textwrap.dedent(f"""\
            ______  _______       _________
            ___   |/  /__(_)____________  /
            __  /|_/ /__  /__  __ \  __  /
            _  /  / / _  / _  / / / /_/ /
            /_/  /_/  /_/  /_/ /_/\__,_/
        """)
        banner = random.choice([banner_standard, banner_speed])

        Design.console.print(f"[bold {color}]{banner}")
        Design.console.print(const.DECLARE)

    @staticmethod
    def show_done() -> None:
        task_done = textwrap.dedent(f"""\
            [bold #00FF88]
            ╭────────────────────────────────────────╮
            │             {const.APP_DESC} Task Done             │
            ╰────────────────────────────────────────╯
        """)
        Design.console.print(task_done)

    @staticmethod
    def show_exit() -> None:
        task_exit = textwrap.dedent(f"""\
            [bold #FFEE55]
            ╭────────────────────────────────────────╮
            │             {const.APP_DESC} Task Exit             │
            ╰────────────────────────────────────────╯
        """)
        Design.console.print(task_exit)

    @staticmethod
    def show_fail() -> None:
        task_fail = textwrap.dedent(f"""\
            [bold #FF4444]
            ╭────────────────────────────────────────╮
            │             {const.APP_DESC} Task Fail             │
            ╰────────────────────────────────────────╯
        """)
        Design.console.print(task_fail)

    @staticmethod
    async def compile_animation() -> None:
        pass


if __name__ == '__main__':
    pass
