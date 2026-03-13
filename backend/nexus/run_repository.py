#  ____                ____                      _ _
# |  _ \ _   _ _ __   |  _ \ ___ _ __   ___  ___(_) |_ ___  _ __ _   _
# | |_) | | | | '_ \  | |_) / _ \ '_ \ / _ \/ __| | __/ _ \| '__| | | |
# |  _ <| |_| | | | | |  _ <  __/ |_) | (_) \__ \ | || (_) | |  | |_| |
# |_| \_\\__,_|_| |_| |_| \_\___| .__/ \___/|___/_|\__\___/|_|   \__, |
#                               |_|                              |___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.nexus.domain.models import RunRecord


class MemoryRunRepository(object):
    """In-memory repository for nexus mission runs."""

    def __init__(self) -> None:
        """初始化内存态运行记录字典。"""
        self._runs: dict[str, RunRecord] = {}

    @property
    def runs(self) -> dict[str, RunRecord]:
        """返回全部运行记录。"""
        return self._runs

    def get(self, mission_id: str) -> typing.Optional[RunRecord]:
        """按 mission_id 获取单条运行记录。"""
        return self._runs.get(mission_id)

    def list(self) -> list[RunRecord]:
        """按插入顺序返回全部运行记录列表。"""
        return list(self._runs.values())

    def save(self, record: RunRecord) -> None:
        """保存或覆盖单次运行记录。"""
        self._runs[record.mission_id] = record
