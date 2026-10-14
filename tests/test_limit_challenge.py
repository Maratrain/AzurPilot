"""限界挑战截图识别、页面链接与月度进度回归，不连接模拟器。

移植自上游 wess09/AzurPilot dev 的 test_limit_challenge.py，去除
module/api、module/combat/result 等本地不存在的依赖；月度状态机部分
按本地 combat_result 适配后的实现重写。
"""

import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

import module.config.server as server_module
import module.limit_challenge.limit_challenge as source
from module.base.base import ModuleBase
from module.base.utils import load_image
from module.combat.assets import GET_ITEMS_1
from module.exception import RequestHumanTakeover
from module.limit_challenge.assets import CHALLENGE_START, TEMPLATE_LEVEL_LOCKED
from module.limit_challenge.limit_challenge import LEVELS, LimitChallenge, challenge_month
from module.ui.assets import CAMPAIGN_MENU_GOTO_LIMIT_CHALLENGE, LIMIT_CHALLENGE_CHECK
from module.ui.page import Page, page_campaign_menu, page_limit_challenge, page_limit_challenge_preparation

FIXTURES = Path(__file__).parent / 'fixtures' / 'limit_challenge'

SCREEN_NAMES = ('menu', 'easy', 'preparation', 'normal', 'hard', 'rewards')


class ChallengeRecognitionTests(unittest.TestCase):
    """原始截图识别回归：每个画面只命中自己的页面状态。"""

    def setUp(self):
        self.task = LimitChallenge.__new__(LimitChallenge)
        self.task.device = Mock()
        self.task.config = SimpleNamespace(BUTTON_OFFSET=(30, 30))
        self.task.interval_timer = {}
        self.images = {name: load_image(str(FIXTURES / f'{name}.png'))
                       for name in SCREEN_NAMES}

    def test_original_screens_identify_only_their_own_page(self):
        for name, image in self.images.items():
            with self.subTest(screen=name):
                self.task.device.image = image
                self.assertEqual(self.task.is_in_challenge(), name in ('easy', 'normal', 'hard'))
                self.assertEqual(self.task.is_in_challenge_preparation(), name == 'preparation')
                self.assertEqual(self.task.appear(CAMPAIGN_MENU_GOTO_LIMIT_CHALLENGE, offset=(3, 3)), name == 'menu')
                self.assertEqual(image.shape, (720, 1280, 3))

    def test_easy_selected_and_later_levels_locked_in_original_image(self):
        self.task.device.image = self.images['easy']
        self.assertEqual(self.task.selected_level(), 1)
        for index, row in enumerate(LEVELS, 1):
            self.assertEqual(TEMPLATE_LEVEL_LOCKED.match(self.task.image_crop(row.area)), index > 1)
        self.assertTrue(self.task.appear(CHALLENGE_START, offset=(3, 3)))

    def test_normal_and_hard_selected_in_original_images(self):
        for name, level in (('normal', 2), ('hard', 3)):
            with self.subTest(level=name):
                self.task.device.image = self.images[name]
                self.assertEqual(self.task.selected_level(), level)
                self.assertTrue(self.task.appear(CHALLENGE_START, offset=(3, 3)))
                self.assertFalse(TEMPLATE_LEVEL_LOCKED.match(self.task.image_crop(LEVELS[level - 1].area)))

    def test_reward_popup_is_recognized_and_dismissed(self):
        self.task.device.image = self.images['rewards']
        self.assertFalse(self.task.is_in_challenge())
        self.assertTrue(self.task.handle_challenge_rewards())
        self.task.device.click.assert_called_once_with(GET_ITEMS_1)

    def test_selection_marker_is_unique_and_position_based(self):
        # 只模拟共用菱形的行位置，不把合成帧当作审视/洞察真实截图。
        original = self.images['easy']
        marker = original[438:461, 30:52].copy()
        for selected in (None, 1, 2, 3):
            with self.subTest(selected=selected):
                frame = original.copy()
                frame[420:680, 27:54] = 0
                if selected is not None:
                    y = 438 + (selected - 1) * 100
                    frame[y:y + 23, 30:52] = marker
                self.task.device.image = frame
                self.assertEqual(self.task.selected_level(), selected)
        frame[438:461, 30:52] = marker
        self.assertIsNone(self.task.selected_level())


class NavigationTests(unittest.TestCase):
    """页面链接：从作战菜单进入，返回不误用每日出击入口。"""

    def test_navigation_has_entrance_and_return_links(self):
        self.assertIs(page_campaign_menu.links[page_limit_challenge], CAMPAIGN_MENU_GOTO_LIMIT_CHALLENGE)
        self.assertIs(page_limit_challenge.check_button, LIMIT_CHALLENGE_CHECK)
        self.assertIn(page_campaign_menu, page_limit_challenge.links)
        self.assertIn(page_limit_challenge, page_limit_challenge_preparation.links)
        Page.init_connection(page_limit_challenge)
        self.addCleanup(Page.clear_connection)
        self.assertIs(page_limit_challenge_preparation.parent, page_limit_challenge)


class ChallengeMonthTests(unittest.TestCase):
    """服务器月份换算：跨月边界不得沿用旧进度。"""

    def test_converts_local_time_to_server_month(self):
        with patch.object(source, 'current_time', return_value=datetime(2026, 10, 14, 20, 0)), \
                patch.object(source, 'server_time_offset', return_value=timedelta(hours=8)):
            self.assertEqual(challenge_month(), 202610)

    def test_month_rollover_uses_server_time(self):
        # 本地 10 月 1 日清晨，服务器仍在 9 月，必须记入 9 月。
        with patch.object(source, 'current_time', return_value=datetime(2026, 10, 1, 6, 0)), \
                patch.object(source, 'server_time_offset', return_value=timedelta(hours=8)):
            self.assertEqual(challenge_month(), 202609)


class RunGuardTests(unittest.TestCase):
    """run() 入口守卫：服务器校验、跨月重置、失败/完成后等待下月。"""

    def setUp(self):
        class StopRun(Exception):
            pass

        self.StopRun = StopRun
        self.task = LimitChallenge.__new__(LimitChallenge)
        self.task.device = Mock()
        self.task.config = Mock()
        self.task.config.multi_set.return_value = nullcontext()
        self.task.ui_ensure = Mock(side_effect=StopRun)

    def test_non_cn_server_requests_human_takeover(self):
        self.task.config.LimitChallenge_TargetLevel = 'easy'
        with patch.object(server_module, 'server', 'en'), \
                self.assertRaises(RequestHumanTakeover):
            self.task.run()

    def test_invalid_target_level_requests_human_takeover(self):
        self.task.config.LimitChallenge_TargetLevel = 'nightmare'
        with patch.object(server_module, 'server', 'cn'), \
                self.assertRaises(RequestHumanTakeover):
            self.task.run()

    def test_failed_month_waits_for_next_month(self):
        self.task.config.LimitChallenge_TargetLevel = 'easy'
        self.task.config.LimitChallenge_Month = 202610
        self.task.config.LimitChallenge_Failed = True
        with patch.object(server_module, 'server', 'cn'), \
                patch.object(source, 'challenge_month', return_value=202610):
            self.task.run()
        self.task.ui_ensure.assert_not_called()
        self.task.config.task_delay.assert_called_once()
        _, kwargs = self.task.config.task_delay.call_args
        self.assertIn('target', kwargs)

    def test_completed_target_waits_for_next_month(self):
        self.task.config.LimitChallenge_TargetLevel = 'easy'
        self.task.config.LimitChallenge_Month = 202610
        self.task.config.LimitChallenge_Failed = False
        self.task.config.LimitChallenge_CompletedLevel = 1
        with patch.object(server_module, 'server', 'cn'), \
                patch.object(source, 'challenge_month', return_value=202610):
            self.task.run()
        self.task.ui_ensure.assert_not_called()
        self.task.config.task_delay.assert_called_once()

    def test_new_month_resets_progress_before_running(self):
        self.task.config.LimitChallenge_TargetLevel = 'easy'
        self.task.config.LimitChallenge_Month = 202609
        self.task.config.LimitChallenge_CompletedLevel = 0
        self.task.config.LimitChallenge_AttemptedLevel = 0
        self.task.config.LimitChallenge_Failed = False
        with patch.object(server_module, 'server', 'cn'), \
                patch.object(source, 'challenge_month', return_value=202610), \
                self.assertRaises(self.StopRun):
            self.task.run()
        # 跨月重置发生在进入页面之前。
        self.assertEqual(self.task.config.LimitChallenge_Month, 202610)
        self.assertEqual(self.task.config.LimitChallenge_CompletedLevel, 0)
        self.assertEqual(self.task.config.LimitChallenge_Failed, False)
        self.task.ui_ensure.assert_called_once()


if __name__ == '__main__':
    unittest.main()
