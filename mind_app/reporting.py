# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import time
import typing
from pathlib import Path
from loguru import logger
from mind_nova import const

DEBUG_LOG_FILE = f"{const.APP_NAME}.debug.log"

DEBUG_LOG_FORMAT = (
    f"{const.APP_DESC} :: "
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {message}"
)


class RunReport(object):
    """管理单次 Mind 进程的报告和日志目录。"""

    def __init__(self, total_path: str, label: typing.Optional[str] = None) -> None:
        self.total_path = total_path

        tag: str = f"{time.strftime('%Y%m%d%H%M%S')}_{os.getpid()}"
        tender: str = const.R_TOTAL_TAG + "_" + (label or tag)

        self.total_path: str = os.path.join(total_path, tender, const.R_COLLECTION)
        if not (total_path := Path(self.total_path)).exists():
            total_path.mkdir(parents=True, exist_ok=True)

        self.reset_path: str = os.path.join(os.path.dirname(self.total_path), const.R_RECOVERY)
        if not (reset_path := Path(self.reset_path)).exists():
            reset_path.mkdir(parents=True, exist_ok=True)

        self.__log_papers: str = os.path.join(self.reset_path, const.R_LOG_FILE)

        self.__debug_log: str = os.path.join(
            self.reset_path,
            DEBUG_LOG_FILE,
        )

        self.__log_sink_id: int | None = logger.add(
            self.__debug_log,
            level=const.NOTE_LEVEL,
            format=DEBUG_LOG_FORMAT,
            encoding=const.CHARSET,
            colorize=False,
            enqueue=True,
            backtrace=False,
            diagnose=False,
        )

        # 创建分类文件夹：截图
        self.__cap_path: str = os.path.join(self.total_path, "caps")
        if not (cap_dir := Path(self.__cap_path)).exists():
            cap_dir.mkdir(parents=True, exist_ok=True)

        # 创建分类文件夹：视频
        self.__rec_path: str = os.path.join(self.total_path, "recs")
        if not (rec_dir := Path(self.__rec_path)).exists():
            rec_dir.mkdir(parents=True, exist_ok=True)

        # 创建分类文件夹：日志
        self.__log_path: str = os.path.join(self.total_path, "logs")
        if not (log_dir := Path(self.__log_path)).exists():
            log_dir.mkdir(parents=True, exist_ok=True)

        # 自研：native
        self.__native_path: str = os.path.join(self.total_path, "native")
        if not (native_dir := Path(self.__native_path)).exists():
            native_dir.mkdir(parents=True, exist_ok=True)

        # 创建分类文件夹：toolkit
        self.__toolkit_path: str = os.path.join(self.total_path, "toolkit")
        if not (toolkit_dir := Path(self.__toolkit_path)).exists():
            toolkit_dir.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        """刷新并关闭当前运行持有的诊断日志。"""
        sink_id = self.__log_sink_id
        if sink_id is None:
            return None
        self.__log_sink_id = None
        logger.remove(sink_id)

    @property
    def log_papers(self) -> str:
        return self.__log_papers

    @property
    def debug_log(self) -> str:
        """返回当前运行使用的诊断日志路径。"""
        return self.__debug_log

    @property
    def cap_path(self) -> str:
        """获取截图文件夹路径"""
        return self.__cap_path

    @property
    def rec_path(self) -> str:
        """获取视频文件夹路径"""
        return self.__rec_path

    @property
    def log_path(self) -> str:
        """获取日志文件夹路径"""
        return self.__log_path

    @property
    def native_path(self) -> str:
        """native 文件夹路径"""
        return self.__native_path

    @property
    def toolkit_path(self) -> str:
        """toolkit 文件夹路径"""
        return self.__toolkit_path


if __name__ == '__main__':
    pass
