"""自动搜索开关两帧确认的回归测试。

锁定 handle_auto_search_map_option 的行为：单帧 OFF 只记录确认不点击，
两张不同截图均确认 OFF 后才按原有节流点击；非 OFF 画面清空确认。

历史问题：战斗结算退场后约 1.8 秒会误点一次 OFF 开关，开关翻面但舰队
不再移动，最终由看门狗判定卡死（上游日志 1625 次战斗 19 次卡死）。
"""

import unittest
from types import SimpleNamespace

import numpy as np

from module.handler.auto_search import AutoSearchHandler


class _TwoFrameStub:
    """只挂载 handle_auto_search_map_option 的最小桩。

    每次 match_template_color 模拟一次新截图后的识别结果，
    以 frame 序列控制每帧画面与识别 outcome。
    """

    handle_auto_search_map_option = AutoSearchHandler.handle_auto_search_map_option
    _auto_search_offset = AutoSearchHandler._auto_search_offset
    _auto_search_off_frame = None

    def __init__(self, images, results):
        self.device = SimpleNamespace(image=images[0])
        self._images = list(images)
        self._results = iter(results)
        self.clicks = 0

    def match_template_color(self, button, offset=(20, 20), interval=0,
                             similarity=0.85, threshold=30):
        # 模拟截图：每次识别都来自一张（可能不同的）新截图
        self.device.image = self._images.pop(0) if self._images else self.device.image
        return next(self._results)

    def appear_then_click(self, button, offset=(20, 20), interval=0):
        self.clicks += 1
        return True


def _frame(seed):
    rng = np.random.RandomState(seed)
    return rng.randint(0, 255, (720, 1280, 3), dtype=np.uint8)


class TestAutoSearchTwoFrameConfirm(unittest.TestCase):

    def test_single_off_frame_does_not_click(self):
        """第一帧 OFF 只记录确认，不点击。"""
        image = _frame(1)
        stub = _TwoFrameStub([image], [True])
        self.assertFalse(stub.handle_auto_search_map_option())
        self.assertEqual(stub.clicks, 0)
        self.assertIs(stub._auto_search_off_frame, image, '第一帧应被记录为确认')

    def test_same_frame_twice_does_not_click(self):
        """同一张截图重复确认为 OFF 不得凑成两帧。"""
        image = _frame(1)
        stub = _TwoFrameStub([image], [True, True])
        stub.handle_auto_search_map_option()
        self.assertFalse(stub.handle_auto_search_map_option())
        self.assertEqual(stub.clicks, 0, '同一帧不能当作第二帧确认')

    def test_two_different_frames_click(self):
        """两张不同截图均确认为 OFF 后才点击。"""
        stub = _TwoFrameStub([_frame(1), _frame(2)], [True, True])
        self.assertFalse(stub.handle_auto_search_map_option())
        self.assertTrue(stub.handle_auto_search_map_option())
        self.assertEqual(stub.clicks, 1)

    def test_non_off_frame_clears_confirmation(self):
        """中间出现非 OFF 画面时清空确认，重新积累两帧。"""
        stub = _TwoFrameStub([_frame(1), _frame(2), _frame(3)],
                             [True, False, True])
        stub.handle_auto_search_map_option()   # 记录第一帧
        stub.handle_auto_search_map_option()   # 非 OFF，清空确认
        self.assertIsNone(stub._auto_search_off_frame)
        stub.handle_auto_search_map_option()   # 重新记录，仍不应点击
        self.assertEqual(stub.clicks, 0)

    def test_confirmation_survives_across_click(self):
        """点击后确认帧被清空，后续流程重新积累。"""
        stub = _TwoFrameStub([_frame(1), _frame(2), _frame(3), _frame(4)],
                             [True, True, True, True])
        stub.handle_auto_search_map_option()
        self.assertTrue(stub.handle_auto_search_map_option())
        # 点击后 _off_frame 已清空，同流程内第三帧不应立即再点
        self.assertFalse(stub.handle_auto_search_map_option())
        self.assertEqual(stub.clicks, 1)


if __name__ == '__main__':
    unittest.main()
