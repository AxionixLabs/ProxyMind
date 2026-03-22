#  __  __ _     _     _ _
# |  \/  (_) __| | __| | | _____      ____ _ _ __ ___  ___
# | |\/| | |/ _` |/ _` | |/ _ \ \ /\ / / _` | '__/ _ \/ __|
# | |  | | | (_| | (_| | |  __/\ V  V / (_| | | |  __/\__ \
# |_|  |_|_|\__,_|\__,_|_|\___| \_/\_/ \__,_|_|  \___||___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from fastapi import FastAPI

from .mid_touch import touch_middleware


def register_middlewares(app: FastAPI) -> None:
    app.middleware("http")(touch_middleware)


if __name__ == '__main__':
    pass
