"""领奖单排网格定位，避免暗色道具被误当作两格之间的空隙。"""
import numpy as np

from module.base.utils import crop, rgb2gray
from module.statistics.assets import GET_ITEMS_ODD


def single_row_is_odd(image):
    """根据左右边框的连续竖直边缘区分奇数、偶数排布。

    只检查单排图标边框的几何位置，不依赖图标内容的亮度。背景和
    弧形图案通常只有短边缘；真正的道具框两侧都有长边缘。动画等
    无法确定边框的画面沿用原亮度检查，保持旧布局兼容。

    Args:
        image (np.ndarray): 1280×720 RGB 领奖画面。

    Returns:
        bool: True 表示 1/3/5 格，False 表示 2/4 格。
    """
    gray = rgb2gray(image[304:388, 320:960]).astype(np.int16)
    edges = np.abs(np.diff(gray, axis=1)) > 15

    def score(origin, count):
        total = 0.
        for index in range(count):
            x = origin + index * 128 - 320
            left = edges[:, x - 6:x + 6].mean(axis=0).max()
            right = edges[:, x + 90:x + 102].mean(axis=0).max()
            total += max(0., min(left, right) - .55)
        return total

    odd, even = score(336, 5), score(400, 4)
    if max(odd, even) >= .25 and abs(odd - even) >= .2:
        return odd > even
    return np.mean(rgb2gray(crop(image, GET_ITEMS_ODD.area, copy=False)) > 127) > .1
