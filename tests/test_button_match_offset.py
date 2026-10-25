"""模板匹配偏移写入的行为锁测试。

背景（2026-10 逆迴十六夜运行日志）：大世界黄币读取（os_status）对共享按钮
GET_SHIP 做 offset 模板匹配，旧版 match() 在相似度不足时仍把最佳匹配点写入
_button_offset，残留偏移把战斗结算点击掉落卡片的坐标整体平移（日志出现
x>1055 的越界点击），偶尔点开掉落舰船的档案页且无人处理，约 200 秒后触发
GameStuckError 重启游戏。

因此约定：_button_offset 只能由成功的模板匹配写入，失败匹配不得改动。
"""

import unittest

import numpy as np

from module.combat.assets import GET_SHIP


class ButtonMatchOffsetTest(unittest.TestCase):
    """GET_SHIP 为跨模块共享按钮，测试前后保存/恢复其偏移状态。"""

    def setUp(self):
        self._saved_offset = GET_SHIP._button_offset
        GET_SHIP.clear_offset()

    def tearDown(self):
        GET_SHIP._button_offset = self._saved_offset

    def test_match_failure_leaves_offset_clean(self):
        blank = np.zeros((720, 1280, 3), np.uint8)
        result = GET_SHIP.match(blank, offset=(20, 20))
        self.assertFalse(result)
        self.assertIsNone(GET_SHIP._button_offset)

    def test_match_success_records_offset(self):
        GET_SHIP.ensure_template()
        canvas = np.zeros((720, 1280, 3), np.uint8)
        x1, y1, x2, y2 = GET_SHIP.area
        canvas[y1:y2, x1:x2] = GET_SHIP.image
        result = GET_SHIP.match(canvas, offset=(20, 20))
        self.assertTrue(result)
        self.assertIsNotNone(GET_SHIP._button_offset)
        # 模板位于定义位置时，记录的偏移应为零位移
        self.assertEqual(tuple(GET_SHIP.button), tuple(GET_SHIP._button))

    def test_clear_offset_resets_click_area(self):
        GET_SHIP._button_offset = (990, 621, 1045, 679)
        GET_SHIP.clear_offset()
        self.assertEqual(tuple(GET_SHIP.button), tuple(GET_SHIP._button))


if __name__ == '__main__':
    unittest.main()
