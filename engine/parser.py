#   ____
#  |  _ \ __ _ _ __ ___  ___ _ __
#  | |_) / _` | '__/ __|/ _ \ '__|
#  |  __/ (_| | |  \__ \  __/ |
#  |_|   \__,_|_|  |___/\___|_|
#

import typing
import argparse
import textwrap
from utils import const


class Parser(object):

    __parse_engine: typing.Optional["argparse.ArgumentParser"] = None

    def __init__(self):
        custom_made_usage = f"""\
        --------------------------------------------
        \033[1;35m{const.APP_NAME}\033[0m exec "example"
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
            title="\033[1m^* 核心操控 *^\033[0m",
            description=textwrap.dedent(f'''\
                \033[1;33m参数互斥\033[0m
            '''),
        )
        major_group = mutually_exclusive.add_mutually_exclusive_group()

        # Workflow: ======================== 参数互斥 ========================

        major_group.add_argument(
            "--apply", type=str,
            help=textwrap.dedent(f'''\
                \033[1;34m^*思维凭证*^\033[0m
                -------------------------
                - 使用激活码向远程授权中心发起请求，获取签名后的授权数据。
                - 授权数据将绑定当前设备指纹，并以 LIC 文件形式存储在本地。

            ''')
        )

        # Workflow: ======================== 参数兼容 ========================

        minor_group = self.__parse_engine.add_argument_group(
            title="\033[1m^* 环境桥接 *^\033[0m",
            description=textwrap.dedent(f'''\
                \033[1;32m参数兼容\033[0m
            '''),
        )
        minor_group.add_argument(
            "--focus", type=str, default=None,
            help=textwrap.dedent(f'''\
                \033[1;36m^*数据魔方*^\033[0m
                -------------------------
                - 传递提示词。

            ''')
        )

        # Workflow: ======================== 参数兼容 ========================

        extra_group = self.__parse_engine.add_argument_group(
            title="\033[1m^* 观象引擎 *^\033[0m",
            description=textwrap.dedent(f'''\
                \033[1;32m参数兼容\033[0m
            '''),
        )
        extra_group.add_argument(
            "--watch", action="store_true",
            help=textwrap.dedent(f'''\
                \033[1;36m^*洞察之镜*^\033[0m
                -------------------------
                - 启动调试反射视角，用于观察系统运行轨迹与隐藏信息。
                - 展示最详细的调试输出，追踪函数调用与变量变化。

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
