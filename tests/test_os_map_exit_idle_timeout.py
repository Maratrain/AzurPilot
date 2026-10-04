import unittest
from unittest.mock import Mock, patch

from module.os.map_operation import OSMapOperation


class FakeTimer:
    """可手动触发的 Timer 替身。

    limit <= 1 的（confirm_timer）视为已到达；空闲兜底计时器由测试用
    :meth:`expire_idle` 显式触发，避免测试真的等 40 秒。
    """

    instances = []

    def __init__(self, limit, count=0):
        self.limit = limit
        self.count = count
        self.expired = False
        self.reset_calls = 0
        self.started = False
        FakeTimer.instances.append(self)

    def start(self):
        self.started = True
        return self

    def reset(self):
        self.reset_calls += 1
        self.expired = False
        return self

    def clear(self):
        return self

    def reached(self):
        return self.limit <= 1 or self.expired

    @classmethod
    def expire_idle(cls):
        for timer in cls.instances:
            if timer.limit == OSMapOperation.map_exit_idle_timeout:
                timer.expired = True

    @classmethod
    def idle_timer(cls):
        for timer in cls.instances:
            if timer.limit == OSMapOperation.map_exit_idle_timeout:
                return timer
        return None

    @classmethod
    def reset_registry(cls):
        cls.instances = []


class MapExitIdleTimeoutTest(unittest.TestCase):
    """map_exit 空闲兜底：卡住时要主动收尾，不能干等到设备层判卡死。"""

    loop_yields = 50

    def setUp(self):
        FakeTimer.reset_registry()
        self.expire_after = None
        self.frames = 0

        self.task = OSMapOperation.__new__(OSMapOperation)
        self.task.appear = Mock(return_value=False)
        self.task.appear_then_click = Mock(return_value=False)
        self.task.handle_popup_confirm = Mock(return_value=False)
        self.task.handle_map_event = Mock(return_value='')
        self.task.is_in_map = Mock(return_value=True)
        self.task.zone_init = Mock()
        self.task.interval_reset = Mock()
        self.task.device = Mock()
        self.task.loop = self.loop

        patcher = patch('module.os.map_operation.Timer', FakeTimer)
        patcher.start()
        self.addCleanup(patcher.stop)

    def loop(self, *args, **kwargs):
        for index in range(self.loop_yields):
            if self.expire_after is not None and index >= self.expire_after:
                FakeTimer.expire_idle()
            self.frames += 1
            yield None
        raise AssertionError('map_exit 在给定帧数内没有结束（疑似死循环）')

    def test_idle_timeout_ends_map_exit(self):
        """整轮无任何可点内容：空闲计时器到点即结束，不盲点击。"""
        self.expire_after = 3

        self.task.map_exit()

        self.assertEqual(self.frames, 4)
        self.task.zone_init.assert_called_once()
        self.task.device.click.assert_not_called()

    def test_map_exit_click_keeps_idle_timer_alive(self):
        """有点击进展时不得因为空闲计时器结束。"""
        clicks = {'count': 0}

        def click_once(*args, **kwargs):
            clicks['count'] += 1
            return clicks['count'] == 1

        self.task.appear_then_click = Mock(side_effect=click_once)

        with self.assertRaises(AssertionError):
            self.task.map_exit()

        idle_timer = FakeTimer.idle_timer()
        self.assertIsNotNone(idle_timer)
        self.assertGreaterEqual(idle_timer.reset_calls, 1)

    def test_normal_exit_still_breaks_on_changed(self):
        """正常路径不受影响：处理到地图事件后按原条件结束。"""
        self.task.handle_map_event = Mock(return_value='map_get_items')

        self.task.map_exit()

        self.assertEqual(self.frames, 2)
        self.task.zone_init.assert_called_once()

    def test_timeout_is_below_device_stuck_threshold(self):
        """兜底超时必须小于设备层的 60 秒卡死判定，否则等于没有兜底。"""
        self.assertGreater(OSMapOperation.map_exit_idle_timeout, 0)
        self.assertLess(OSMapOperation.map_exit_idle_timeout, 60)


if __name__ == '__main__':
    unittest.main()
