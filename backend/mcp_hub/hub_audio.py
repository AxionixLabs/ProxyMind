# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import asyncio
import typing
import contextlib
from loguru import logger
from backend.utilities.validation import marked

try:
    os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "hide"
    import pygame
except ImportError:
    raise ImportError("AudioPlayer requires pygame. install it first.")


class AudioPlayer(object):
    """提供本地音频播放能力。"""

    def __init__(self):
        self.agent_id = "player"

    @staticmethod
    async def audio_play(
        audio_file: str,
        volume: float = 1.0,
        *_,
        **__,
    ) -> dict[str, typing.Any]:
        """播放本地音频文件并等待播放结束。"""
        marked.ensure_f(audio_file, "audio_file")

        volume_update = min(1.0, max(0.1, volume))
        try:
            pygame.mixer.init()
            pygame.mixer.music.load(audio_file)
            pygame.mixer.music.set_volume(volume_update)
            pygame.mixer.music.play()

            while pygame.mixer.music.get_busy():
                await asyncio.sleep(0.1)

            return {
                "ok"          : True,
                "text"        : f"audio_play ok file={audio_file}",
                "attachments" : [],
                "data": {
                    "audio_file" : audio_file,
                    "volume"     : volume_update
                },
                "logs": []
            }

        except (pygame.error, OSError, ValueError) as error:
            logger.error(error)
            raise marked.except_tip(
                "Audio playback failed. Verify the audio format, file integrity, and available audio output device.",
                error,
            )
        finally:
            with contextlib.suppress(Exception):
                pygame.mixer.music.stop()
            with contextlib.suppress(Exception):
                pygame.mixer.quit()


if __name__ == '__main__':
    pass
