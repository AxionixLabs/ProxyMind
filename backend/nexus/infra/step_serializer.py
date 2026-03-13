#  ____  _               ____            _       _ _
# / ___|| |_ ___ _ __   / ___|  ___ _ __(_) __ _| (_)_______ _ __
# \___ \| __/ _ \ '_ \  \___ \ / _ \ '__| |/ _` | | |_  / _ \ '__|
#  ___) | ||  __/ |_) |  ___) |  __/ |  | | (_| | | |/ /  __/ |
# |____/ \__\___| .__/  |____/ \___|_|  |_|\__,_|_|_/___\___|_|
#               |_|
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.nexus.domain.models import StepResult


class StepSerializer(object):

    @staticmethod
    def to_dict(step: StepResult) -> dict[str, typing.Any]:
        """把 StepResult 转成稳定的字典结构用于回传。"""
        return {
            "name"       : step.name,
            "type"       : step.type,
            "ok"         : step.ok,
            "elapsed_ms" : step.elapsed_ms,
            "detail"     : step.detail
        }


if __name__ == '__main__':
    pass
