#  _____ _ _        ____             _                 _   ____                  _
# |  ___(_) | ___  |  _ \ __ _ _   _| | ___   __ _  __| | / ___|  ___ _ ____   _(_) ___ ___
# | |_  | | |/ _ \ | |_) / _` | | | | |/ _ \ / _` |/ _` | \___ \ / _ \ '__\ \ / / |/ __/ _ \
# |  _| | | |  __/ |  __/ (_| | |_| | | (_) | (_| | (_| |  ___) |  __/ |   \ V /| | (_|  __/
# |_|   |_|_|\___| |_|   \__,_|\__, |_|\___/ \__,_|\__,_| |____/ \___|_|    \_/ |_|\___\___|
#                              |___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pathlib import Path
from loguru import logger
from backend.utilities import const


class FilePayloadService(object):

    @staticmethod
    def files_payload(
        items: typing.Optional[list[dict[str, typing.Any]]]
    ) -> typing.Optional[list[tuple[str, typing.Any]]]:
        """把抽象文件描述转换成 multipart/form-data 的文件载荷。"""
        if not items: return None
        payload: list[tuple[str, typing.Any]] = []

        for item in items:
            if not isinstance(item, dict):
                continue

            field = str(item.get("field") or "file")
            filename = str(item.get("filename") or "upload.bin")
            content_type = str(item.get("content_type") or "application/octet-stream")

            if item.get("path"):
                path = Path(str(item["path"])).expanduser()
                try:
                    with path.open("rb") as fh:
                        payload.append((field, (filename or path.name, fh.read(), content_type)))
                except Exception as e:
                    logger.error(f"Error reading file {path}: {e}")
                    continue
            elif item.get("text") is not None:
                payload.append((field, (filename, str(item.get("text") or "").encode(const.CHARSET), content_type)))
            elif item.get("bytes") is not None:
                raw = item.get("bytes")
                if isinstance(raw, bytes):
                    payload.append((field, (filename, raw, content_type)))
                elif isinstance(raw, str):
                    payload.append((field, (filename, raw.encode(const.CHARSET), content_type)))

        return payload or None


if __name__ == '__main__':
    pass
