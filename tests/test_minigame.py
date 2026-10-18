"""小游戏列表识别与滚动恢复回归，只使用资源裁剪和模拟设备。"""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from module.exception import GameStuckError, RequestHumanTakeover
from module.minigame.assets import (
    BACK,
    GAME_LIST_CHECK_LEFT,
    GAME_LIST_CHECK_RIGHT,
    GOTO_CHOOSE_GAME,
    MINIGAME_SCROLL_AREA,
    MINIGAME_SCROLL_KNOB,
    NEW_YEAR_CHALLENGE_ENTRANCE,
    NEW_YEAR_CHALLENGE_START,
)
from module.minigame.minigame import MINIGAME_SCROLL, Minigame, MinigameRun, MinigameScroll, MinigameUI
from module.minigame.new_year_challenge import NewYearChallenge
from module.ui.scroll import Scroll


ROOT = Path(__file__).resolve().parents[1]


def paste_asset(image, button, dy=0):
    """保留资源的原生像素，只移动待测试的识别区域。"""
    x1, y1, x2, y2 = button.area
    with Image.open(ROOT / button.file) as asset:
        image[y1 + dy:y2 + dy, x1:x2] = np.array(asset.convert('RGB').crop(button.area))


def list_frame(marker=GAME_LIST_CHECK_LEFT, dy=0, scrollbar=True, start=True):
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    if scrollbar:
        paste_asset(image, MINIGAME_SCROLL_AREA)
    if marker is not None:
        paste_asset(image, marker, dy=dy)
    if start:
        paste_asset(image, GOTO_CHOOSE_GAME)
        # 模拟残留按钮横跨滚动条检测区域的白色边框。
        image[590:655, 1223:1230] = 247
    return image


def runner(cls=MinigameUI, image=None):
    """跳过配置、设备初始化，避免连接模拟器或保存用户配置。"""
    main = cls.__new__(cls)
    main.config = SimpleNamespace()
    main.interval_timer = {}
    main.device = SimpleNamespace(image=image, stuck_record_add=Mock())
    return main


class ListDetectionTests(unittest.TestCase):
    def tearDown(self):
        for button in (GAME_LIST_CHECK_LEFT, GAME_LIST_CHECK_RIGHT, MINIGAME_SCROLL_KNOB, GOTO_CHOOSE_GAME):
            button.clear_offset()

    def test_list_with_persistent_start_is_recognized_in_either_column_after_scrolling(self):
        for marker in (GAME_LIST_CHECK_LEFT, GAME_LIST_CHECK_RIGHT):
            for dy in (-110, 0, 300, 400):
                with self.subTest(marker=marker.name, dy=dy):
                    main = runner(image=list_frame(marker, dy=dy))
                    self.assertTrue(main.appear(GOTO_CHOOSE_GAME, offset=(5, 5)))
                    self.assertTrue(main.minigame_list_appear())

    def test_scrollbar_or_card_alone_does_not_prove_list(self):
        for frame in (list_frame(marker=None), list_frame(scrollbar=False, start=False)):
            with self.subTest(has_white_border=bool(frame[590, 1223, 0])):
                self.assertFalse(runner(image=frame).minigame_list_appear())

    def test_knob_template_locates_top_middle_and_bottom(self):
        scroll = MinigameScroll(MINIGAME_SCROLL_AREA, color=(247, 247, 247))
        for dy, expected in ((0, 0), (106, 0.25), (211, 0.5), (422, 1)):
            with self.subTest(dy=dy):
                image = list_frame()
                paste_asset(image, MINIGAME_SCROLL_KNOB, dy=dy)
                main = runner(image=image)
                self.assertEqual(main.minigame_scroll_y(), 164 + dy)
                self.assertAlmostEqual(scroll.cal_position(main), expected, delta=0.003)

    def test_start_border_biases_old_color_position_but_not_template_position(self):
        image = list_frame()
        paste_asset(image, MINIGAME_SCROLL_KNOB)
        main = runner(image=image)
        old_scroll = Scroll(MINIGAME_SCROLL_AREA, color=(247, 247, 247))
        new_scroll = MinigameScroll(MINIGAME_SCROLL_AREA, color=(247, 247, 247))
        self.assertGreater(old_scroll.cal_position(main), 0.1)
        self.assertEqual(new_scroll.cal_position(main), 0)

    def test_white_border_cannot_replace_covered_knob(self):
        main = runner(image=list_frame(scrollbar=False))
        self.assertIsNone(main.minigame_scroll_y())
        self.assertIsNone(MinigameScroll(MINIGAME_SCROLL_AREA, color=(247, 247, 247)).cal_position(main))

    def test_new_assets_use_native_game_resolution(self):
        for button in (GAME_LIST_CHECK_LEFT, GAME_LIST_CHECK_RIGHT, MINIGAME_SCROLL_KNOB):
            with self.subTest(button=button.name), Image.open(ROOT / button.file) as asset:
                self.assertEqual(asset.size, (1280, 720))


class NavigationTests(unittest.TestCase):
    def test_return_to_main_and_collect_leave_list_before_accepting_start_button(self):
        for method in ('go_to_main_page', 'collect_coin'):
            with self.subTest(method=method):
                main = runner(Minigame, list_frame())
                main.ui_additional = Mock(return_value=False)
                main.device.screenshot = Mock(side_effect=lambda: setattr(
                    main.device, 'image', list_frame(marker=None, scrollbar=False)))
                main.appear_then_click = Mock(side_effect=lambda button, **kwargs: button == BACK)
                getattr(main, method)()
                back_calls = [call for call in main.appear_then_click.call_args_list if call.args[0] == BACK]
                self.assertEqual(len(back_calls), 1)
                main.device.screenshot.assert_called_once()

    def test_run_accepts_list_without_clicking_persistent_start(self):
        main = runner(MinigameRun, list_frame())
        main.appear_then_click = Mock(side_effect=AssertionError('列表内不应点击残留开始按钮'))
        main.choose_game = Mock()
        main.use_coin = Mock(return_value=False)
        main.play_game = Mock()
        main.exit_game = Mock()
        self.assertFalse(main.minigame_run())
        main.choose_game.assert_called_once()
        main.exit_game.assert_called_once()
        main.play_game.assert_not_called()

    def test_choose_game_prioritizes_target_card_over_persistent_start(self):
        main = runner(NewYearChallenge)
        entered = [False]
        main.deal_popup = Mock(return_value=False)
        main.appear = Mock(side_effect=lambda button, **kwargs: (
            entered[0] if button == NEW_YEAR_CHALLENGE_START else button == NEW_YEAR_CHALLENGE_ENTRANCE))
        main.appear_then_click = Mock(side_effect=AssertionError('应先选择目标卡片'))
        main.interval_clear = Mock()
        main.interval_reset = Mock()
        main.device.click = Mock()
        main.device.screenshot = Mock(side_effect=lambda: entered.__setitem__(0, True))
        main.choose_game()
        main.device.click.assert_called_once_with(NEW_YEAR_CHALLENGE_ENTRANCE)

    def test_choose_game_scrolls_list_before_clicking_persistent_start(self):
        main = runner(NewYearChallenge)
        entered = [False]
        main.deal_popup = Mock(return_value=False)
        main.appear = Mock(side_effect=lambda button, **kwargs: entered[0] and button == NEW_YEAR_CHALLENGE_START)
        main.minigame_list_appear = Mock(return_value=True)
        main.ui_page_appear = Mock(return_value=True)
        main.appear_then_click = Mock(side_effect=AssertionError('列表内应继续滚动'))
        main.interval_clear = Mock()
        main.device.screenshot = Mock(side_effect=lambda: entered.__setitem__(0, True))
        with patch.object(MINIGAME_SCROLL, 'appear', return_value=True), \
                patch.object(MINIGAME_SCROLL, 'set', return_value=1) as scroll:
            main.choose_game()
        scroll.assert_called_once_with(main=main, position=0.25, distance_check=False)

    def test_exit_accepts_list_even_when_start_button_is_absent(self):
        main = runner(NewYearChallenge, list_frame(start=False))
        main.deal_popup = Mock(return_value=False)
        main.appear_then_click = Mock(side_effect=AssertionError('已回列表时不应继续点击'))
        main.exit_game()
        main.appear_then_click.assert_not_called()


class ScrollRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.clock = [100.0]
        self.time_patch = patch('module.base.timer.time', side_effect=lambda: self.clock[0])
        self.time_patch.start()
        self.addCleanup(self.time_patch.stop)
        self.scroll = MinigameScroll(MINIGAME_SCROLL_AREA, color=(247, 247, 247))

    def simulate(self, positions, cards=(0,), step_seconds=1.1):
        index = [0]

        def screenshot():
            index[0] += 1
            self.clock[0] += step_seconds

        return SimpleNamespace(
            minigame_scroll_y=lambda: positions[min(index[0], len(positions) - 1)],
            image_crop=lambda area: np.full((8, 8, 3), cards[min(index[0], len(cards) - 1)], dtype=np.uint8),
            device=SimpleNamespace(screenshot=Mock(side_effect=screenshot), swipe=Mock()),
        )

    def test_already_positioned_needs_no_swipe_or_new_screenshot(self):
        main = self.simulate([270])
        self.assertEqual(self.scroll.set(0.25, main), 0)
        main.device.swipe.assert_not_called()
        main.device.screenshot.assert_not_called()

    def test_visible_knob_drag_is_confirmed_by_next_screenshot(self):
        main = self.simulate([164, 270])
        self.assertEqual(self.scroll.set(0.25, main, distance_check=False), 1)
        swipe = main.device.swipe.call_args
        self.assertLess(swipe.args[0][1], swipe.args[1][1])
        self.assertFalse(swipe.kwargs['distance_check'])
        main.device.screenshot.assert_called_once()

    def test_covered_knob_recovers_through_cards_then_positions(self):
        main = self.simulate([None, 164, 270])
        self.assertEqual(self.scroll.set(0.25, main), 2)
        self.assertEqual(main.device.swipe.call_args_list[0].args, ((600, 250), (600, 550)))

    def test_bottom_requires_two_consecutive_stationary_comparisons(self):
        main = self.simulate([None], cards=(0, 80, 80, 80))
        self.assertEqual(self.scroll.set_bottom(main), 3)
        self.assertEqual(main.device.swipe.call_count, 3)
        for swipe in main.device.swipe.call_args_list:
            self.assertEqual(swipe.args, ((600, 550), (600, 250)))

    def test_card_motion_resets_bottom_confirmation(self):
        main = self.simulate([None], cards=(0, 0, 80, 80, 80))
        self.assertEqual(self.scroll.set_bottom(main), 4)

    def test_unreachable_position_requests_takeover_after_eight_swipes(self):
        main = self.simulate([164])
        with self.assertRaises(RequestHumanTakeover):
            self.scroll.set(0.25, main)
        self.assertEqual(main.device.swipe.call_count, 8)

    def test_timeout_requests_takeover_before_swipe_limit(self):
        main = self.simulate([164], step_seconds=11)
        with self.assertRaises(RequestHumanTakeover):
            self.scroll.set(0.25, main)
        self.assertEqual(main.device.swipe.call_count, 2)

    def test_device_stuck_error_propagates_to_existing_recovery(self):
        main = self.simulate([164])
        main.device.screenshot.side_effect = GameStuckError('模拟截图卡住')
        with self.assertRaises(GameStuckError):
            self.scroll.set(0.25, main)


if __name__ == '__main__':
    unittest.main()
