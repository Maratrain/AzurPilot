"""
科研系列编号识别。

本模块通过模板匹配从截图中识别科研项目的系列编号（S1-S9）。

科研系列编号以罗马数字形式显示在项目卡片上，由于透视效果，
不同位置的项目卡片存在缩放差异，因此使用 RESEARCH_SCALING
对各位置的模板匹配进行缩放补偿。

主要功能：
- 列表中的系列识别：通过裁剪各项目卡片的系列标识区域，
  使用灰度图模板匹配逐个识别 5 个项目的系列编号
- 详情页的系列识别：从科研详情页中识别系列编号

术语对照：
    系列(Series): 科研系列编号 S1-S9，对应不同的科研舰船池
"""
from module.base.utils import area_pad, crop, rgb2gray
from module.research.assets import *

RESEARCH_SERIES = (SERIES_1, SERIES_2, SERIES_3, SERIES_4, SERIES_5)
RESEARCH_SCALING = [
    424 / 558,
    491 / 558,
    1.0,
    491 / 558,
    424 / 558,
]


def match_series(image, scaling):
    """
    通过模板匹配识别单个科研项目的系列编号。

    按 S9 -> S1 的顺序依次匹配模板，优先匹配高编号系列
    以避免低编号模板的误匹配。

    Args:
        image (np.ndarray): 裁剪后的系列标识区域灰度图像。
        scaling (float): 模板匹配的缩放因子，用于补偿透视效果。

    Returns:
        int: 系列编号（1-9），匹配失败返回 0。
    """
    image = rgb2gray(image)

    if TEMPLATE_S9.match(image, scaling=scaling):
        return 9
    if TEMPLATE_S8.match(image, scaling=scaling):
        return 8
    if TEMPLATE_S7.match(image, scaling=scaling):
        return 7
    if TEMPLATE_S6.match(image, scaling=scaling):
        return 6
    if TEMPLATE_S4_2.match(image, scaling=scaling):
        return 4
    if TEMPLATE_S4.match(image, scaling=scaling):
        return 4
    if TEMPLATE_S5.match(image, scaling=scaling):
        return 5
    if TEMPLATE_S3.match(image, scaling=scaling):
        return 3
    if TEMPLATE_S2.match(image, scaling=scaling):
        return 2
    if TEMPLATE_S1.match(image, scaling=scaling):
        return 1
    return 0


def get_research_series_3(image, series_button=RESEARCH_SERIES):
    """
    从科研列表截图中批量识别 5 个项目的系列编号。

    通过裁剪各项目卡片的系列标识区域，使用带缩放补偿的
    模板匹配逐一识别系列编号。不同位置的项目因透视效果
    需要不同的缩放因子（由 RESEARCH_SCALING 定义）。

    Args:
        image (np.ndarray): 科研列表页面的完整截图。
        series_button (list[Button]): 5 个系列标识区域的按钮定义。

    Returns:
        list[int]: 5 个项目的系列编号列表，如 [1, 3, 5, 4, 2]。
    """
    return [
        match_series(crop(image, area_pad(button.area, pad=-10), copy=False), scaling)
        for scaling, button in zip(RESEARCH_SCALING, series_button)
    ]


def get_detail_series(image):
    """
    从科研详情页截图中识别系列编号。

    裁剪详情页的系列标识区域，使用模板匹配（缩放因子 1.0）
    识别当前详情页显示的项目所属系列。

    Args:
        image (np.ndarray): 科研详情页的截图。

    Returns:
        int: 系列编号（1-9），匹配失败返回 0。
    """
    return match_series(crop(image, area_pad(SERIES_DETAIL.area, pad=-30), copy=False), scaling=1.0)
