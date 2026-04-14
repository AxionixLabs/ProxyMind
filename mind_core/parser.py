# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import argparse
import textwrap
from mind_nova import const


class Parser(object):
    """Parser class."""

    __parse_engine: typing.Optional["argparse.ArgumentParser"] = None

    def __init__(self):
        custom_made_usage = f"""\
        --------------------------------------------
        \033[1;35m{const.APP_NAME}\033[0m --chat "Unlock the device"
        \033[1;35m{const.APP_NAME}\033[0m --fast "Extract keyframes from /path/to/demo.mp4 and return evidence"
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
            ''')
        )
        major_group = mutually_exclusive.add_mutually_exclusive_group()

        # Workflow: ======================== 参数互斥 ========================

        major_group.add_argument(
            "--hello", action="store_true",
            help=textwrap.dedent(f'''\
                \033[1;34m^* 中枢协议 *^\033[0m
                -------------------------
                - 拉起后台管理中心面板，统一管理模型配置、日志与服务状态。

            ''')
        )

        # major_group.add_argument(
        #     "--apply", type=str,
        #     help=textwrap.dedent(f'''\
        #         \033[1;34m^* 原点协议 *^\033[0m
        #         -------------------------
        #         - 使用激活码向授权中心申请并写入 LIC 授权文件。
        #
        #     ''')
        # )

        # major_group.add_argument(
        #     "--pref", action="store_true",
        #     help=textwrap.dedent(f'''\
        #         \033[1;34m^* 基线协议 *^\033[0m
        #         -------------------------
        #         - 指定/加载模型偏好或配置。
        #
        #     ''')
        # )

        major_group.add_argument(
            "--upgrade", action="store_true",
            help=textwrap.dedent(f'''\
                \033[1;34m^* 奇点协议 *^\033[0m
                -------------------------
                - 更新/同步 MCP 服务（服务端组件），一键拉取并覆盖安装。

            ''')
        )

        major_group.add_argument(
            "--chat", nargs="?", const="", default=None,
            help=textwrap.dedent(f'''\
                \033[1;34m^* 潮汐协议 *^\033[0m
                -------------------------
                - 启用流式下发通道，持续输出对话内容。

            ''')
        )

        major_group.add_argument(
            "--fast", nargs="?", const="", default=None,
            help=textwrap.dedent(f'''\
                \033[1;34m^* 边界协议 *^\033[0m
                -------------------------
                - 快速执行通道，适合接口、文本与媒体类短链路任务。

            ''')
        )

        major_group.add_argument(
            "--plan", nargs="?", const="", default=None,
            help=textwrap.dedent(f'''\
                \033[1;34m^* 推演协议 *^\033[0m
                -------------------------
                - 启用行动规划通道，生成可执行步骤轨迹。

            ''')
        )

        major_group.add_argument(
            "--agent", action="store_true",
            help=textwrap.dedent(f'''\
                \033[1;34m^* 折跃协议 *^\033[0m
                -------------------------
                - 启动订阅模式，连接 /agents/open 与 /agents/ws。

            ''')
        )

        # Workflow: ======================== 参数兼容 ========================

        minor_group = self.__parse_engine.add_argument_group(
            title="\033[1m^* Σ / SIGMA :: 协议矩阵 *^\033[0m",
            description=textwrap.dedent(f'''\
                \033[1;32m兼容: P1 :: Context Injection\033[0m
            ''')
        )

        minor_group.add_argument(
            "--gravity", type=str, default=None,
            help=textwrap.dedent(f'''\
                \033[1;36m^* 引力协议 *^\033[0m
                -------------------------
                - 设置报告“引力标签”，用于确定本次运行的日志/报告落盘根目录（同标签聚合到同一命名空间）。

            ''')
        )

        minor_group.add_argument(
            "--reflection", action="store_true",
            help=textwrap.dedent(f'''\
                \033[1;36m^* 反射协议 *^\033[0m
                -------------------------
                - 开启详细调试视角输出运行轨迹与关键决策信息。

            ''')
        )

        minor_group.add_argument(
            "--code", nargs="+", type=str, default=None,
            help=textwrap.dedent(f'''\
                \033[1;36m^* 星图协议 *^\033[0m
                -------------------------
                - 装载批量执行蓝本（.md/.txt）
                - 支持 cfg、case、前后置、循环、规则后置等编排结构
                - 必须与 --chat/--fast/--plan 叠加：选择批跑协议

            ''')
        )

        minor_group.add_argument(
            "--attach", action="append", default=None, metavar="PATH",
            help=textwrap.dedent(f'''\
                \033[1;36m^* 共振协议 *^\033[0m
                -------------------------
                - 为本次命令行请求挂载本地附件
                - 可重复传入：--attach a.png --attach "./docs/**/*.md"
                - 当前仅用于单次 `--chat` / `--fast` 请求

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
