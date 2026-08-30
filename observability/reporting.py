# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import time
import typing
from pathlib import Path
from metadata import const
from observability import (
    add_file_sink,
    observe,
    remove_sink,
)

DEBUG_LOG_FILE = f"{const.APP_NAME}.debug.log"

DEBUG_LOG_FORMAT = (
    f"{const.APP_DESC} :: "
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {message}"
)


class RunReport(object):
    """管理单次应用进程的报告和日志目录。"""

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

        self.__output_record_path: str = os.path.join(
            self.reset_path,
            const.R_LOG_FILE,
        )

        self.__debug_log: str = os.path.join(
            self.reset_path,
            DEBUG_LOG_FILE,
        )

        self.__log_sink_id: int | None = add_file_sink(
            self.__debug_log,
            level=const.NOTE_LEVEL,
            output_format=DEBUG_LOG_FORMAT,
            encoding=const.CHARSET,
        )
        self.run_id = tender
        observe("report.open", run_id=self.run_id)

    def close(self) -> None:
        """刷新并关闭当前运行持有的诊断日志。"""
        sink_id = self.__log_sink_id
        if sink_id is None:
            return None
        observe("report.close", run_id=self.run_id)
        self.__log_sink_id = None
        remove_sink(sink_id)

    @property
    def output_record_path(self) -> str:
        """返回当前进程的终端展示记录路径。"""
        return self.__output_record_path

    @property
    def debug_log(self) -> str:
        """返回当前运行使用的诊断日志路径。"""
        return self.__debug_log


if __name__ == '__main__':
    pass
