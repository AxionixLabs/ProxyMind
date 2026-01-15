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

    __parse_engine: typing.Optional["argparse.ArgumentParser"] = None

    def __init__(self):
        custom_made_usage = f"""\
        --------------------------------------------
        \033[1;35m{const.APP_NAME}\033[0m --exec "Unlock the device"
        \033[1;35m{const.APP_NAME}\033[0m --exec "Wait 2 seconds and tap Music"
        \033[1;35m{const.APP_NAME}\033[0m --exec "Unlock, wait 1 second, then tap 500,1000"
        \033[1;35m{const.APP_NAME}\033[0m --horizon
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
                原点协议用于启动系统授权初始化流程。

                - 通过激活码向远程授权中心发送协议请求。
                - 授权中心返回经签名校验的授权数据。
                - 授权数据将与当前设备指纹进行绑定。
                - 最终以 LIC 授权文件形式写入本地存储。

                该协议仅在首次授权或授权重建场景中触发。
                授权完成后，系统将自动进入能力解锁状态。

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
            "--exec", type=str, default=None,
            help=textwrap.dedent(f'''\
                \033[1;36m^*提示注入*^\033[0m
                -------------------------
                - 注入提示词 / 上下文，引导引擎生成更稳定的执行轨迹。
                - 适用于：复现特定场景、约束输出风格、固定策略偏好。

            ''')
        )

        # Workflow: ======================== 参数兼容 ========================

        extra_group = self.__parse_engine.add_argument_group(
            title="\033[1m^* Φ / PHI :: 反射协议 *^\033[0m",
            description=textwrap.dedent(f'''\
                \033[1;32m观测: P2 :: Trace & Telemetry\033[0m
            '''),
        )
        extra_group.add_argument(
            "--horizon", action="store_true",
            help=textwrap.dedent(f'''\
                \033[1;36m^*轨迹观测*^\033[0m
                -------------------------
                - 开启反射视角，输出系统运行轨迹与隐藏信息。
                - 展示最详细的调试信息：关键分支选择、函数调用链、变量变化。

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
