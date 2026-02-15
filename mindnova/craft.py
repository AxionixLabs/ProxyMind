#   ____            __ _
#  / ___|_ __ __ _ / _| |_
# | |   | '__/ _` | |_| __|
# | |___| | | (_| |  _| |_
#  \____|_|  \__,_|_|  \__|
#

import time
import uuid


def b36(n: int) -> str:
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    if n <= 0: return "0"
    s = []
    while n:
        n, r = divmod(n, 36)
        s.append(chars[r])
    return "".join(reversed(s))


def new_cid(prefix: str = "cid") -> str:
    # 秒级时间 + 8 位随机：cid_kr3f2n_1a2b3c4d
    ts   = b36(int(time.time()))
    rand = uuid.uuid4().hex[:8]
    return f"{prefix}_{ts}_{rand}"


def new_sid(cid: str, prefix: str = "sid") -> str:
    # 用 cid 做关联，sid 带毫秒 + 6 位随机：sid_kr3f2n_m3ab9e_7f2c1a
    ts_ms = b36(int(time.time() * 1000))
    rand = uuid.uuid4().hex[:6]
    return f"{prefix}_{cid.split('_', 2)[1]}_{ts_ms}_{rand}"


if __name__ == '__main__':
    pass
