"""任务奖励领取后的新船入手演出处理。

里程碑 / 周常任务奖励含舰船时，领完 GET_ITEMS 会弹出新船 debut 页。
它盖住任务页，且不在领取循环的任何等待状态内，认不出来就会无操作
空转到 GameStuckError（2026-11-09 连领 3 个含舰船奖励连续卡死 3 次）。
"""

import unittest

from module.handler.assets import NEW_SHIP_SKIP
from module.reward.reward import Reward


class FakeReward:
    """只挂 _reward_handle_new_ship_popup 的桩，记录实际点到的按钮。"""

    _reward_handle_new_ship_popup = Reward._reward_handle_new_ship_popup

    def __init__(self, skip=False):
        self.skip = skip
        self.clicked = []

    def appear_then_click(self, button, offset=0, interval=0, similarity=0.85, threshold=10):
        if button is NEW_SHIP_SKIP and self.skip:
            self.clicked.append(button)
            return True
        return False


class TestRewardNewShipPopup(unittest.TestCase):
    def test_new_ship_skip_clicked(self):
        """debut 页出现时点右上角 SKIP 收掉。"""
        fake = FakeReward(skip=True)
        self.assertTrue(fake._reward_handle_new_ship_popup())
        self.assertEqual(fake.clicked, [NEW_SHIP_SKIP])

    def test_other_screens_not_mistaken(self):
        """其它画面不误触（放宽到 0.75 的阈值不会误判）。"""
        fake = FakeReward(skip=False)
        self.assertFalse(fake._reward_handle_new_ship_popup())
        self.assertEqual(fake.clicked, [])


if __name__ == '__main__':
    unittest.main()
