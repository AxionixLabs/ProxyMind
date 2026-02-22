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

        log_papers: str = os.path.join(self.reset_path, const.R_LOG_FILE)
        logger.add(log_papers, level=const.NOTE_LEVEL, format=const.WRITE_FORMAT)


if __name__ == '__main__':
    pass
