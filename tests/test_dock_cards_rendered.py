
"""船坞卡片渲染等待的回归测试。

背景（长卿 2026-10-25）：换船反复报「没有普通稀有度航母」，
但船坞里明明有一堆合适的船。日志显示点击筛选确认后仅 0.25 秒
就开始 OCR，等级全部读成 0。

根因：handle_dock_cards_loading 只比对连续两帧哈希是否相同，
而「卡片还没渲染出来」的空骨架自身也是稳定的，于是刚点完确认
就立刻判定加载完成，OCR 拿到一整页空区域。

修复：哈希稳定的同时，还必须确认等级区域已经出现白色文字
（_dock_cards_rendered），否则继续等到超时。
"""

import unittest
from types import SimpleNamespace

import numpy as np

from module.retire.dock import CARD_LEVEL_GRIDS, Dock


def blank_image(value=150):
    """构造整屏纯色图，模拟未渲染的空骨架。"""
    return np.full((720, 1280, 3), value, dtype=np.uint8)


def rendered_image():
    """构造一张已渲染卡片的截图：第一格等级区域填白色。"""
    image = blank_image(120)
    area = [int(v) for v in CARD_LEVEL_GRIDS.buttons[0].area]
    image[area[1]:area[3], area[0]:area[2]] = 255
    return image


def make_dock(image):
    dock = Dock.__new__(Dock)
    dock.device = SimpleNamespace(image=image)
    return dock


class DockCardsRenderedTest(unittest.TestCase):
    def test_rendered_card_detected(self):
        self.assertTrue(make_dock(rendered_image())._dock_cards_rendered())

    def test_blank_skeleton_not_rendered(self):
        """空骨架亮度 150，必须判为未渲染。"""
        self.assertFalse(make_dock(blank_image(150))._dock_cards_rendered())

    def test_dark_skeleton_not_rendered(self):
        self.assertFalse(make_dock(blank_image(60))._dock_cards_rendered())

    def test_black_screen_not_rendered(self):
        self.assertFalse(make_dock(blank_image(0))._dock_cards_rendered())

    def test_missing_screenshot_not_rendered(self):
        self.assertFalse(make_dock(None)._dock_cards_rendered())

    def test_threshold_between_skeleton_and_text(self):
        """阈值必须卡在空骨架亮度与白色文字之间。"""
        threshold = Dock.DOCK_CARD_RENDERED_BRIGHTNESS
        self.assertGreater(threshold, 150, '空骨架亮度不能被判为已渲染')
        self.assertLessEqual(threshold, 255, '白色文字必须能被判为已渲染')

    def test_timeout_covers_slow_rendering(self):
        """超时不能比原来的 1.2s 更短，慢机器需要更长的渲染窗口。"""
        self.assertGreaterEqual(Dock.DOCK_CARDS_RENDER_TIMEOUT, 1.2)


if __name__ == '__main__':
    unittest.main()
