"""用生成的边框和暗图标覆盖排布，不存玩家截图。"""
import unittest

import cv2
import numpy as np

from module.statistics.get_items import GetItemsStatistics
from module.azur_stats.image.get_items import has_odd_items
from module.statistics.item_layout import single_row_is_odd


class GetItemsLayoutTest(unittest.TestCase):
    def test_one_to_five_dark_items_and_bright_even_gap(self):
        for count in range(1, 6):
            for bright_gap in (False, True):
                with self.subTest(count=count, bright_gap=bright_gap):
                    screen = np.full((720, 1280, 3), 35, dtype=np.uint8)
                    if bright_gap:
                        screen[294:397, 628:653] = 180
                    start = 592 - (count - 1) * 64
                    for index in range(count):
                        x = start + index * 128
                        screen[298:394, x:x + 96] = 12
                        cv2.rectangle(screen, (x, 298), (x + 95, 393), (185, 185, 185), 2)
                    expected = count % 2 == 1
                    self.assertEqual(bool(single_row_is_odd(screen)), expected)
                    self.assertEqual(bool(GetItemsStatistics._stats_get_items_is_odd(screen)), expected)
                    self.assertEqual(bool(has_odd_items(screen)), expected)

    def test_inconclusive_border_keeps_legacy_fallback(self):
        screen = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.assertFalse(single_row_is_odd(screen))
        screen[294:397, 628:653] = 255
        self.assertTrue(single_row_is_odd(screen))


if __name__ == '__main__':
    unittest.main()
