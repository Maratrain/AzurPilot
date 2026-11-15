"""任务奖励领取后的新船入手演出处理。

里程碑 / 周常任务奖励含舰船时，领完 GET_ITEMS 会弹出新船 debut 页。
它盖住任务页，且不在领取循环的任何等待状态内，认不出来就会无操作
空转到 GameStuckError（2026-11-09 连领 3 个含舰船奖励连续卡死 3 次）。

2026-11-15 补充：部分 debut 页没有右上角 SKIP（绫波 SR 实测，与建造完工
7b160023f 同款），改用右下角「重复获得提示」开关识别后点击页面中央关闭，
与 gacha 的实现保持一致。
"""

import unittest

from module.gacha.assets import NEW_SHIP_LAUNCHED_CHECK
from module.handler.assets import NEW_SHIP_DISMISS_POINT, NEW_SHIP_SKIP
from module.reward.reward import Reward


class FakeReward:
    """只挂 _reward_handle_new_ship_popup 的桩，记录实际点到的按钮。"""

    _reward_handle_new_ship_popup = Reward._reward_handle_new_ship_popup

    def __init__(self, skip=False, launched=False):
        self.skip = skip
        self.launched = launched
        self.clicked = []

    def appear_then_click(self, button, offset=0, interval=0, similarity=0.85, threshold=10):
        if button is NEW_SHIP_SKIP and self.skip:
            self.clicked.append(button)
            return True
        return False

    def appear(self, button, offset=0, interval=0):
        return button is NEW_SHIP_LAUNCHED_CHECK and self.launched

    @property
    def device(self):
        owner = self

        class _Device:
            def click(self, button):
                owner.clicked.append(button)

        return _Device()


class TestRewardNewShipPopup(unittest.TestCase):
    def test_new_ship_skip_clicked(self):
        """debut 页出现时点右上角 SKIP 收掉。"""
        fake = FakeReward(skip=True)
        self.assertTrue(fake._reward_handle_new_ship_popup())
        self.assertEqual(fake.clicked, [NEW_SHIP_SKIP])

    def test_no_skip_debut_dismissed_via_center(self):
        """无 SKIP 的 debut 页用「重复获得提示」开关识别，点中央关闭。"""
        fake = FakeReward(launched=True)
        self.assertTrue(fake._reward_handle_new_ship_popup())
        self.assertEqual(fake.clicked, [NEW_SHIP_DISMISS_POINT])

    def test_other_screens_not_mistaken(self):
        """其它画面不误触（放宽到 0.75 的阈值不会误判）。"""
        fake = FakeReward(skip=False)
        self.assertFalse(fake._reward_handle_new_ship_popup())
        self.assertEqual(fake.clicked, [])


if __name__ == '__main__':
    unittest.main()
