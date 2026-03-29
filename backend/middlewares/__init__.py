# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from fastapi import FastAPI

from .mid_touch import touch_middleware


def register_middlewares(app: FastAPI) -> None:
    app.middleware("http")(touch_middleware)


if __name__ == '__main__':
    pass
