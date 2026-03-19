#  ____            _       _ _
# / ___|  ___ _ __(_) __ _| (_)_______ _ __
# \___ \ / _ \ '__| |/ _` | | |_  / _ \ '__|
#  ___) |  __/ |  | | (_| | | |/ /  __/ |
# |____/ \___|_|  |_|\__,_|_|_/___\___|_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.models.model_nexus import StepResult


class StepSerializer(object):

    @staticmethod
    def to_dict(step: StepResult) -> dict[str, typing.Any]:
        """把 StepResult 转成稳定的字典结构用于回传。"""
        return {
            "name"       : step.name,
            "type"       : step.type,
            "ok"         : step.ok,
            "elapsed_ms" : step.elapsed_ms,
            "detail"     : step.detail,
            "artifact"   : step.artifact
        }


if __name__ == '__main__':
    pass
