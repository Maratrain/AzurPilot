"""验证剧情选项可见期间抑制安全区 / 跳过点击。

story_skip 的安全区分支在 STORY_SKIP_3 出现 0.5 秒后即可触发，而剧情选项
的点击需要 0.3 秒稳定确认；2 选项对话（如隐秘海域解除封锁确认）会在确认
窗口走完前被安全区点击放掉，战斗不再触发，舰队停在塞壬精英身上无操作
空转直到自律寻敌卡死。用例锁定：选项可见期间不产生任何空白区/跳过点击，
确认完成后按 STORY_OPTION 点选项；无选项时安全区行为保持不变。
"""

import unittest
from types import SimpleNamespace

from module.base.timer import Timer
from module.handler.assets import STORY_SKIP, STORY_SKIP_3
from module.handler.info_handler import InfoHandler
from module.os_handler.assets import CLICK_SAFE_AREA as OS_CLICK_SAFE_AREA


class FakeConfirmTimer:
    """可编程确认计时器：reached() 返回预设值，reset() 关闭触发。"""

    def __init__(self, reached=False):
        self._reached = reached

    def reached(self):
        return self._reached

    def reset(self):
        self._reached = False


class FakeDevice:
    def __init__(self):
        self.clicks = []

    def click(self, button, *args, **kwargs):
        self.clicks.append(button)


class FakeStoryHandler:
    """只提供 story_skip 与 _identify_siren_device_option 所需的最小桩。"""

    story_skip = InfoHandler.story_skip
    _identify_siren_device_option = InfoHandler._identify_siren_device_option

    def __init__(self, options, story_option=0, option_confirm=False,
                 allow_skip=False, story_confirm=True):
        self.story_popup_timeout = Timer(10, count=20)  # 未启动，started() 为 False
        self._story_option_timer = Timer(2)  # 未启动，reached() 为 True（首次快速尝试）
        self._story_option_record = 0
        self._story_option_confirm = FakeConfirmTimer(option_confirm)
        self._story_confirm = FakeConfirmTimer(story_confirm)
        self.config = SimpleNamespace(STORY_OPTION=story_option, STORY_ALLOW_SKIP=allow_skip)
        self.device = FakeDevice()
        self.siren_device_mode = None
        self._options = options

    def _is_story_black(self):
        return False

    def _story_option_buttons_2(self):
        return self._options

    def appear(self, button, offset=0, interval=0, similarity=0.85, threshold=10):
        return button is STORY_SKIP_3

    def appear_then_click(self, button, offset=0, interval=0, similarity=0.85, threshold=10):
        return False

    def interval_reset(self, *buttons):
        pass

    def interval_clear(self, *buttons):
        pass


def make_options(count):
    """构造带 button 属性的选项对象（story_skip 会记录 option.button）。"""
    return [SimpleNamespace(button=f'STORY_OPTION_{n + 1}_OF_{count}') for n in range(count)]


class TestStorySkipOptionGuard(unittest.TestCase):
    def test_first_detection_frame_blocks_safe_area(self):
        """选项首次检出帧（确认计时刚清零）不得触发安全区点击。

        复现 2026-11-10 隐秘海域卡死：选项 0.3 秒稳定确认还差一帧时，
        安全区分支抢先触发把对话放掉。
        """
        options = make_options(2)
        fake = FakeStoryHandler(options, story_option=0, option_confirm=True)
        self.assertFalse(fake.story_skip())
        self.assertEqual(fake.device.clicks, [])

    def test_confirm_pending_frame_blocks_safe_area(self):
        """选项已记录但确认窗口未走完的帧，同样不得点空白区。"""
        options = make_options(2)
        fake = FakeStoryHandler(options, story_option=0, option_confirm=False)
        fake._story_option_record = 2  # 模拟上一帧已记录 2 个选项
        self.assertFalse(fake.story_skip())
        self.assertEqual(fake.device.clicks, [])

    def test_confirmed_options_click_configured_option(self):
        """确认窗口走完后按 STORY_OPTION 点选项，不再走安全区。"""
        options = make_options(2)
        fake = FakeStoryHandler(options, story_option=0, option_confirm=True)
        fake._story_option_record = 2
        self.assertTrue(fake.story_skip())
        self.assertEqual(fake.device.clicks, [options[0]])

    def test_three_options_respect_story_option_end_to_end(self):
        """3 选项对话走 story_skip 全流程时同样按 STORY_OPTION 选第一项。"""
        options = make_options(3)
        fake = FakeStoryHandler(options, story_option=0, option_confirm=True)
        fake._story_option_record = 3
        self.assertTrue(fake.story_skip())
        self.assertEqual(fake.device.clicks, [options[0]])
        self.assertIsNone(fake.siren_device_mode)

    def test_no_options_keeps_safe_area_behavior(self):
        """无选项的普通剧情保持原有安全区推进行为。"""
        fake = FakeStoryHandler([], allow_skip=False)
        self.assertTrue(fake.story_skip())
        self.assertEqual(fake.device.clicks, [OS_CLICK_SAFE_AREA])

    def test_no_options_keeps_skip_behavior(self):
        """无选项且允许跳过时保持原有右上角跳过行为。"""
        fake = FakeStoryHandler([], allow_skip=True)
        self.assertTrue(fake.story_skip())
        self.assertEqual(fake.device.clicks, [STORY_SKIP])


if __name__ == '__main__':
    unittest.main()
