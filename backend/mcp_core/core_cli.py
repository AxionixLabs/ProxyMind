#   ____                  ____ _ _
#  / ___|___  _ __ ___   / ___| (_)
# | |   / _ \| '__/ _ \ | |   | | |
# | |__| (_) | | |  __/ | |___| | |
#  \____\___/|_|  \___|  \____|_|_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
import argparse
import textwrap
from backend.utilities import const


class Cli(object):
    """Cli class."""

    __parse_engine: typing.Optional["argparse.ArgumentParser"] = None

    def __init__(self):
        custom_made_usage = f"""\
        --------------------------------------------
        \033[1;35m{const.APP_NAME}\033[0m --level DEBUG / INFO / WARN / ERROR / CRITICAL
        """
        self.__parse_engine = argparse.ArgumentParser(
            const.APP_NAME,
            usage=f" \033[1;35m{const.APP_NAME}\033[0m [-h] [--help] View help documentation\n" + custom_made_usage,
            description=textwrap.dedent(f'''\
                \033[1;32m{const.APP_DESC} · {const.APP_CN}\033[0m
                \033[1m-----------------------------\033[0m
                \033[1;32mCommand Line Arguments {const.APP_DESC}\033[0m
            '''),
            formatter_class=argparse.RawTextHelpFormatter
        )

        self.__parse_engine.add_argument(
            "--level", default="INFO", type=str,
            choices=["DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"],
            help="level: DEBUG / INFO / WARN / ERROR / CRITICAL"
        )

    @property
    def parse_cmd(self) -> "argparse.Namespace":
        return self.__parse_engine.parse_args()

    @property
    def parse_engine(self) -> typing.Optional["argparse.ArgumentParser"]:
        return self.__parse_engine


if __name__ == '__main__':
    pass
