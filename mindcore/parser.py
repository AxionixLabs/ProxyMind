#   ____
#  |  _ \ __ _ _ __ ___  ___ _ __
#  | |_) / _` | '__/ __|/ _ \ '__|
#  |  __/ (_| | |  \__ \  __/ |
#  |_|   \__,_|_|  |___/\___|_|
#

import typing
import argparse
import textwrap
from mindnova import const


class Parser(object):
    """Parser class."""

    __parse_engine: typing.Optional["argparse.ArgumentParser"] = None

    def __init__(self):
        custom_made_usage = f"""\
        --------------------------------------------
        \033[1;35m{const.APP_NAME}\033[0m --plan "Unlock the device"
        \033[1;35m{const.APP_NAME}\033[0m --plan "Wait 2 seconds and tap Music"
        \033[1;35m{const.APP_NAME}\033[0m --plan "Unlock, wait 1 second, then tap 500,1000"
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

        mutually_exclusive = self.__parse_engine.add_argument_group(
            title="\033[1m^* Ω / OMEGA :: 主序协议 *^\033[0m",
            description=textwrap.dedent(f'''\
                \033[1;33m互斥: P0 :: Primary Protocols\033[0m
            '''),
        )
        major_group = mutually_exclusive.add_mutually_exclusive_group()

        # Workflow: ======================== 参数互斥 ========================

        major_group.add_argument(
            "--apply", type=str,
            help=textwrap.dedent(f'''\
                \033[1;34m^*原点协议*^\033[0m
                -------------------------
                - 使用激活码向授权中心申请并写入 LIC 授权文件。

            ''')
        )

        major_group.add_argument(
            "--pref", action="store_true",
            help=textwrap.dedent(f'''\
                \033[1;34m^*基线协议*^\033[0m
                -------------------------
                - 指定/加载模型偏好或配置。

            ''')
        )

        major_group.add_argument(
            "--chat", type=str, default=None,
            help=textwrap.dedent(f'''\
                \033[1;34m^*潮汐协议*^\033[0m
                -------------------------
                - 启用流式下发通道，持续输出对话内容。

            ''')
        )

        major_group.add_argument(
            "--plan", type=str, default=None,
            help=textwrap.dedent(f'''\
                \033[1;34m^*推演协议*^\033[0m
                -------------------------
                - 启用行动规划通道，生成可执行步骤轨迹。

            ''')
        )

        major_group.add_argument(
            "--fast", type=str, default=None,
            help=textwrap.dedent(f'''\
                \033[1;34m^*边界协议*^\033[0m
                -------------------------
                - 启用性能压测与指标采集通道，用于探测系统性能边界。

            ''')
        )

        # Workflow: ======================== 参数兼容 ========================

        minor_group = self.__parse_engine.add_argument_group(
            title="\033[1m^* Σ / SIGMA :: 协议矩阵 *^\033[0m",
            description=textwrap.dedent(f'''\
                \033[1;32m兼容: P1 :: Context Injection\033[0m
            '''),
        )

        minor_group.add_argument(
            "--debug", action="store_true",
            help=textwrap.dedent(f'''\
                \033[1;36m^*反射协议*^\033[0m
                -------------------------
                - 开启详细调试视角输出运行轨迹与关键决策信息。

            ''')
        )

    @property
    def parse_cmd(self) -> "argparse.Namespace":
        return self.__parse_engine.parse_args()

    @property
    def parse_engine(self) -> typing.Optional["argparse.ArgumentParser"]:
        return self.__parse_engine


if __name__ == '__main__':
    pass
