# __     ___     _
# \ \   / (_)___(_) ___  _ __
#  \ \ / /| / __| |/ _ \| '_ \
#   \ V / | \__ \ | (_) | | | |
#    \_/  |_|___/_|\___/|_| |_|
#

import numpy as np
from PIL import Image


def prepare(
    path: str,
    crop_top: float,
    crop_bottom: float,
    crop_left: float,
    crop_right: float,
    size: tuple[int, int]
) -> np.ndarray:
    with Image.open(path) as im:
        im = im.convert("L")
        width, height = im.size

        left = int(width * crop_left)
        right = int(width * (1.0 - crop_right))
        top = int(height * crop_top)
        bottom = int(height * (1.0 - crop_bottom))

        if (right - left) >= 4 and (bottom - top) >= 4:
            im = im.crop((left, top, right, bottom))

        im = im.resize(size)
        return np.asarray(im, dtype=np.float32)


def similarity(
    image_first: str,
    image_last: str
) -> float:
    """0~1：越大越相似（基于灰度 + downscale + 加权MSE，含轻微裁剪增强）。"""
    crop_top: float    = 0.15
    crop_bottom: float = 0.12
    crop_left: float   = 0.00
    crop_right: float  = 0.00

    size: tuple[int, int] = (96, 96)

    center_weight: float = 1.8

    a1 = prepare(image_first, crop_top, crop_bottom, crop_left, crop_right, size)
    a2 = prepare(image_last, crop_top, crop_bottom, crop_left, crop_right, size)

    h, w = a1.shape
    yy, xx = np.mgrid[0:h, 0:w]
    cy, cx = max(1e-6, (h - 1) / 2.0), max(1e-6, (w - 1) / 2.0)

    r = np.sqrt(((yy - cy) / cy) ** 2 + ((xx - cx) / cx) ** 2)
    r = np.clip(r, 0.0, 1.0)

    weights = 1.0 + (center_weight - 1.0) * (1.0 - r) ** 2

    diff = a1 - a2
    mse = float(np.sum((diff * diff) * weights)) / float(np.sum(weights))

    sim = 1.0 - min(1.0, mse / (255.0 * 255.0))
    return float(sim)


if __name__ == '__main__':
    pass
