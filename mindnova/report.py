#  ____                       _
# |  _ \ ___ _ __   ___  _ __| |_
# | |_) / _ \ '_ \ / _ \| '__| __|
# |  _ <  __/ |_) | (_) | |  | |_
# |_| \_\___| .__/ \___/|_|   \__|
#           |_|
#

import os
import time
import typing
from pathlib import Path
from loguru import logger
from mindnova import const


class Report(object):

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

        self.log_papers: str = os.path.join(self.reset_path, const.R_LOG_FILE)
        logger.add(self.log_papers, level=const.NOTE_LEVEL, format=const.WRITE_FORMAT)

        # 创建分类文件夹：截图、视频、日志等
        self.__cap_path = os.path.join(self.total_path, "caps")
        cap_dir = Path(self.cap_path)
        if not cap_dir.exists():
            cap_dir.mkdir(parents=True, exist_ok=True)

        self.__rec_path = os.path.join(self.total_path, "recs")
        rec_dir = Path(self.rec_path)
        if not rec_dir.exists():
            rec_dir.mkdir(parents=True, exist_ok=True)

        self.__log_path = os.path.join(self.total_path, "logs")
        log_dir = Path(self.log_path)
        if not log_dir.exists():
            log_dir.mkdir(parents=True, exist_ok=True)

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


if __name__ == '__main__':
    pass
