import unittest
from unittest.mock import Mock

from module.exception import GameTooManyClickError
from module.os.globe_camera import GlobeCamera


class _FakeZone:
    def __init__(self, zone_id=5, location=(2245.0, 990.0)):
        self.zone_id = zone_id
        self.location = location


class GlobeFocusToTest(unittest.TestCase):
    """globe_focus_to 点击未钉住时的居中自愈。

    背景：2026-10 月初，大世界重置后非碧蓝港口被塞壬占领，地球仪上占领
    塔楼的热区与 area_pos 标定点错位，原地重试 12 次全部落空触发
    GameTooManyClickError 并重启模拟器。自愈策略：连续点击未钉住后把
    海域拉向视野中心再点，居中尝试限次，次数用尽回退原行为由设备层兜底。
    """

    # 与 globe_focus_to 中定义一致，此处仅用于断言
    CORE_SIGHT = (150, 260, 850, 580)

    def setUp(self):
        self.cam = GlobeCamera.__new__(GlobeCamera)
        self.zone = _FakeZone(5, location=(2245.0, 990.0))
        self.cam.name_to_zone = Mock(return_value=self.zone)
        self.cam.handle_zone_pinned = Mock(return_value=False)
        self.cam.globe_update = Mock()
        self.cam.zone_to_button = Mock(return_value=Mock(name='ZONE_5'))
        self.cam.device = Mock()
        self.cam.globe_camera = (1865.0, 1050.0)
        self.cam.config = Mock(OS_GLOBE_SWIPE_MULTIPLY=(1.91, 2.21))

        # 目标投影贴在视野右缘 (978, 314)，复现月初 Taranto 的卡死形态
        self.projection = [978.0, 314.0]
        self.cam.globe2screen = Mock(side_effect=lambda points: [self.projection])

        self.swipes = []
        self.click_count = 0

        # 默认平移不改投影，仅记录调用；需要模拟平移生效的测试自行覆盖
        self.cam.globe_swipe = Mock(side_effect=lambda vector: self.swipes.append(vector))

        def fake_click(button):
            self.click_count += 1

        self.cam.device.click.side_effect = fake_click

    def _bind_in_sight(self):
        """globe_in_sight 保留真实实现，外层包 Mock 以便断言调用参数。"""
        self.cam.globe_in_sight = Mock(
            side_effect=lambda *args, **kwargs:
                GlobeCamera.globe_in_sight(self.cam, *args, **kwargs))

    def _pin_after(self, nth_click):
        def fake_wait(zone, skip_first_screenshot=True):
            return self.click_count >= nth_click

        self.cam.globe_wait_until_zone_pinned = Mock(side_effect=fake_wait)

    def test_success_on_first_click_keeps_original_behavior(self):
        """一次点中：不触发居中，不额外平移，原有路径不受影响。"""
        self._bind_in_sight()
        self._pin_after(1)

        self.cam.globe_focus_to(5)

        self.assertEqual(self.click_count, 1)
        self.assertEqual(self.swipes, [])
        core_calls = [c for c in self.cam.globe_in_sight.call_args_list
                      if c.kwargs.get('max_swipe')]
        self.assertEqual(core_calls, [])

    def test_center_retry_after_two_failed_clicks(self):
        """前两次点击落空：第三次点击前把海域拉向视野中心，一次平移到位。"""
        self._bind_in_sight()
        self._pin_after(3)

        def fake_swipe(vector):
            self.swipes.append(vector)
            # 平移后目标进入视野中心
            self.projection = [500.0, 420.0]

        self.cam.globe_swipe = Mock(side_effect=fake_swipe)

        self.cam.globe_focus_to(5)

        self.assertEqual(self.click_count, 3)
        self.assertEqual(len(self.swipes), 1)
        core_calls = [c for c in self.cam.globe_in_sight.call_args_list
                      if c.kwargs.get('max_swipe')]
        self.assertEqual(len(core_calls), 1)
        self.assertEqual(core_calls[0].kwargs.get('sight'), self.CORE_SIGHT)
        self.assertEqual(core_calls[0].kwargs.get('max_swipe'), 1)

    def test_center_retry_stops_once_zone_reaches_core(self):
        """居中后海域已在核心区：即使仍未钉住也不再平移，原地重试到设备层兜底。"""
        self._bind_in_sight()

        def fake_swipe(vector):
            self.swipes.append(vector)
            self.projection = [500.0, 420.0]

        self.cam.globe_swipe = Mock(side_effect=fake_swipe)

        def fake_wait(zone, skip_first_screenshot=True):
            if self.click_count >= 12:
                raise GameTooManyClickError('test stop')
            return False

        self.cam.globe_wait_until_zone_pinned = Mock(side_effect=fake_wait)

        with self.assertRaises(GameTooManyClickError):
            self.cam.globe_focus_to(5)

        self.assertEqual(len(self.swipes), 1)
        self.assertLessEqual(self.click_count, 12)

    def test_center_retry_capped_when_zone_never_reaches_core(self):
        """海域贴地图边缘怎么滑都进不了核心区：居中最多 2 次后放弃，不再无限平移。"""
        self._bind_in_sight()

        def fake_swipe(vector):
            self.swipes.append(vector)
            # 平移后仍在核心区外（模拟镜头顶死地图边缘）

        self.cam.globe_swipe = Mock(side_effect=fake_swipe)

        def fake_wait(zone, skip_first_screenshot=True):
            if self.click_count >= 12:
                raise GameTooManyClickError('test stop')
            return False

        self.cam.globe_wait_until_zone_pinned = Mock(side_effect=fake_wait)

        with self.assertRaises(GameTooManyClickError):
            self.cam.globe_focus_to(5)

        self.assertEqual(len(self.swipes), 2)
        self.assertLessEqual(self.click_count, 12)

    def test_globe_in_sight_max_swipe_prevents_infinite_swipe(self):
        """max_swipe 用尽后必须返回 False，不得在边缘死循环平移。"""
        result = GlobeCamera.globe_in_sight(
            self.cam, 5, sight=self.CORE_SIGHT, max_swipe=2)

        self.assertFalse(result)
        self.assertEqual(len(self.swipes), 2)

    def test_globe_in_sight_already_in_sight(self):
        """目标已在视野内：不平移并返回 True。"""
        self.projection = [500.0, 420.0]

        result = GlobeCamera.globe_in_sight(self.cam, 5)

        self.assertTrue(result)
        self.assertEqual(self.swipes, [])


if __name__ == '__main__':
    unittest.main()
