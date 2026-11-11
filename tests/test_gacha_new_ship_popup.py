"""完工弃船时的新船入手演出处理。

完工首次获得新船时，收完 GET_ITEMS 会弹出全屏新船 debut 页。它盖住
建造页，且不在 gacha_flush_queue 的任何等待状态内，认不出来就会无操作
空转到 GameStuckError（2026-11-12 完工收富特卡死）。
"""

import unittest

from module.gacha.gacha_reward import RewardGacha
from module.handler.assets import NEW_SHIP_SKIP


class FakeGacha:
    """只挂 _gacha_handle_new_ship_popup 的桩，记录实际点到的按钮。"""

    _gacha_handle_new_ship_popup = RewardGacha._gacha_handle_new_ship_popup

    def __init__(self, skip=False):
        self.skip = skip
        self.clicked = []

    def appear_then_click(self, button, offset=0, interval=0, similarity=0.85, threshold=10):
        if button is NEW_SHIP_SKIP and self.skip:
            self.clicked.append(button)
            return True
        return False


class TestGachaNewShipPopup(unittest.TestCase):
    def test_new_ship_skip_clicked(self):
        """debut 页出现时点右上角 SKIP 收掉。"""
        fake = FakeGacha(skip=True)
        self.assertTrue(fake._gacha_handle_new_ship_popup())
        self.assertEqual(fake.clicked, [NEW_SHIP_SKIP])

    def test_other_screens_not_mistaken(self):
        """其它画面不误触（放宽到 0.75 的阈值不会误判）。"""
        fake = FakeGacha(skip=False)
        self.assertFalse(fake._gacha_handle_new_ship_popup())
        self.assertEqual(fake.clicked, [])


if __name__ == '__main__':
    unittest.main()
