"""验证 3 选项剧情的选项选择逻辑。

塞壬装置识别把 3 选项剧情一律当作塞壬信息收集装置 / 探测装置产物柱子，
固定点中间项，绕过了 STORY_OPTION。深渊 / 隐秘 / 要塞 / 跨月 用
STORY_OPTION=0 指定点第一项（解除封锁的强制确认），中间项是「查阅作战说明」，
误点会打开引导系统。这些用例锁定：显式 STORY_OPTION 优先，自动选择时才按柱子处理。
"""

import unittest
from types import SimpleNamespace

from module.handler.info_handler import InfoHandler


def make_cross_get(values=None):
    """构造按 key 取值的 cross_get。"""
    values = values or {}

    def cross_get(keys, default=None):
        return values.get(keys, default)

    return cross_get


class StoryOptionStub:
    """只提供 _identify_siren_device_option 需要的属性。"""

    def __init__(self, story_option, task='OpsiAbyssal', cross_get_values=None):
        self.config = SimpleNamespace(
            STORY_OPTION=story_option,
            task=SimpleNamespace(command=task),
            cross_get=make_cross_get(cross_get_values),
        )
        self.siren_device_mode = None


class TestStoryOptionSelection(unittest.TestCase):
    def select(self, story_option, count=3, task='OpsiAbyssal', cross_get_values=None):
        stub = StoryOptionStub(story_option, task=task, cross_get_values=cross_get_values)
        options = [object() for _ in range(count)]
        select = InfoHandler._identify_siren_device_option(stub, options)
        return stub, options, select

    def test_special_zone_story_option_zero_chooses_first(self):
        """深渊 / 隐秘 / 要塞 / 跨月：STORY_OPTION=0 时点第一项，不能抢占为中间项。"""
        stub, options, select = self.select(0)
        self.assertIs(select, options[0])
        self.assertIsNone(stub.siren_device_mode)

    def test_auto_story_option_keeps_siren_device_behavior(self):
        """大世界默认 STORY_OPTION=-2（自动选择）：3 选项仍是柱子的中间项。"""
        stub, options, select = self.select(-2, task='OpsiHazard1Leveling')
        self.assertIs(select, options[1])
        self.assertEqual(stub.siren_device_mode, 'collected')

    def test_explicit_middle_option_is_honoured(self):
        """显式指定第 2 项时同样按 STORY_OPTION 选择。"""
        stub, options, select = self.select(1)
        self.assertIs(select, options[1])
        self.assertIsNone(stub.siren_device_mode)

    def test_out_of_range_story_option_falls_back_to_siren_device(self):
        """STORY_OPTION 越界时不抛 IndexError，按柱子处理。"""
        stub, options, select = self.select(5)
        self.assertIs(select, options[1])
        self.assertEqual(stub.siren_device_mode, 'collected')

    def test_non_three_option_story_is_not_siren_device(self):
        """2 / 4 选项剧情保持不识别为塞壬装置。"""
        for count in (2, 4):
            with self.subTest(count=count):
                stub, options, select = self.select(-2, count=count)
                self.assertIsNone(select)
                self.assertIsNone(stub.siren_device_mode)

    def test_five_option_siren_device_still_uses_configured_mode(self):
        """5 选项塞壬探测装置的行为不受本次改动影响。"""
        cases = (
            (True, 'enemy', 2),
            (True, 'resource', 3),
            (False, 'resource', 4),
        )
        for enabled, mode, index in cases:
            with self.subTest(enabled=enabled, mode=mode):
                stub, options, select = self.select(
                    -2, count=5, task='OpsiHazard1Leveling',
                    cross_get_values={
                        'OpsiHazard1Leveling.OpsiSirenBug.SirenResearch_Enable': enabled,
                        'OpsiHazard1Leveling.OpsiSirenBug.Siren_Mode': mode,
                    },
                )
                self.assertIs(select, options[index])


if __name__ == '__main__':
    unittest.main()
