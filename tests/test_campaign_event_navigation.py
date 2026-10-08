"""用活动截图控件和内存设备验证首发／复刻导航及同名关卡隔离。"""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from campaign.event_20240912_cn.a1 import Campaign as CrimsonCampaign, Config as CrimsonConfig
from campaign.event_20240912_cn.sp import Config as CrimsonSpConfig
from campaign.event_20250227_cn.a1 import Campaign as LightCampaign, Config as LightConfig
from module.base.utils import load_image
from module.campaign.assets import CHAPTER_20241219_EX, SWITCH_1_HARD, SWITCH_20241219_COMBAT, SWITCH_20241219_STORY
from module.campaign.campaign_ui import ASIDE_SWITCH_20241219, MODE_SWITCH_1, MODE_SWITCH_20241219
from module.campaign.run import CampaignRun
from module.config.config import AzurLaneConfig
from module.exception import CampaignNameError


FIXTURES = Path(__file__).parent / 'fixtures' / 'campaign_event_navigation'


class NavigationConfig(AzurLaneConfig):
    # 仅隔离配置中的服务器名；截图测试始终使用国服原图和国服资源。
    SERVER = 'cn'


def make_config(map_config=None, server='cn'):
    """模板模式不读写用户实例，地图配置仅合并到测试副本。"""
    config = NavigationConfig('template')
    config.override(SERVER=server)
    if map_config is not None:
        config.merge(map_config)
    return config


def legacy_image():
    """旧版普通／困难按钮加中间的作战选择器，复用原始资源像素。"""
    return np.maximum(
        load_image(SWITCH_1_HARD.file),
        np.roll(load_image(SWITCH_20241219_COMBAT.file), 444, axis=1),
    )


def make_campaign(campaign_type=CrimsonCampaign, map_config=None, image='crimson.png', server='cn'):
    if map_config is None:
        map_config = CrimsonConfig()
    if isinstance(image, str):
        image = load_image(str(FIXTURES / image))
    device = Mock(image=image)
    campaign = campaign_type(config=make_config(map_config, server=server), device=device)
    campaign.ui_goto_event = Mock()

    def read_chapter():
        # 本测试验证模板和导航，关卡 OCR 单独用完整原图回放。
        if campaign.config.MAP_CHAPTER_SWITCH_20241219:
            assert MODE_SWITCH_20241219.get(main=campaign) == 'combat', '读关卡前须选中作战模式'
            assert ASIDE_SWITCH_20241219.get(main=campaign) == 'part1', '读 A1 前须选中上篇'
        campaign.campaign_chapter = 'a'
        campaign.stage_entrance = {'a1': SimpleNamespace(name='a1')}
        return 1

    campaign.get_chapter_index = Mock(side_effect=read_chapter)
    return campaign


class CampaignEventNavigationTests(unittest.TestCase):
    def test_both_screenshots_recognize_new_controls(self):
        for image in ('crimson.png', 'light.png'):
            with self.subTest(image=image):
                campaign = make_campaign(image=image)
                self.assertEqual(MODE_SWITCH_20241219.get(main=campaign), 'combat')
                self.assertEqual(ASIDE_SWITCH_20241219.get(main=campaign), 'part1')
                self.assertEqual(MODE_SWITCH_1.get(main=campaign), 'unknown')

    def test_crimson_rerun_uses_sidebar_without_old_mode_clicks(self):
        campaign = make_campaign()
        with patch.object(MODE_SWITCH_1, 'set') as old_mode:
            self.assertTrue(campaign.ensure_campaign_ui('a1'))
        old_mode.assert_not_called()
        campaign.device.click.assert_not_called()
        self.assertEqual(campaign.ENTRANCE.name, 'a1')
        self.assertTrue(campaign.config.MAP_CHAPTER_SWITCH_20241219)
        self.assertTrue(campaign.config.MAP_HAS_MODE_SWITCH)
        self.assertEqual(campaign.config.modified, {})

    def test_other_activity_keeps_shared_sidebar_navigation(self):
        campaign = make_campaign(LightCampaign, LightConfig(), image='light.png')
        with patch.object(MODE_SWITCH_1, 'set') as old_mode:
            self.assertTrue(campaign.ensure_campaign_ui('a1'))
        old_mode.assert_not_called()
        campaign.device.click.assert_not_called()
        self.assertEqual(campaign.ENTRANCE.name, 'a1')

    def test_story_and_ex_switch_to_combat_and_part1_before_stage_read(self):
        for campaign_type, map_config, fixture in (
            (CrimsonCampaign, CrimsonConfig(), 'crimson.png'),
            (LightCampaign, LightConfig(), 'light.png'),
        ):
            with self.subTest(fixture=fixture):
                selected = load_image(str(FIXTURES / fixture))
                image = selected.copy()
                image[:, :110] = 0
                image[635:715, :400] = 0
                image = np.maximum(image, load_image(CHAPTER_20241219_EX.file))
                image = np.maximum(image, load_image(SWITCH_20241219_STORY.file))
                campaign = make_campaign(campaign_type, map_config, image=image)
                self.assertEqual(MODE_SWITCH_20241219.get(main=campaign), 'story')
                self.assertEqual(ASIDE_SWITCH_20241219.get(main=campaign), 'ex')
                clicks = []

                def click(button):
                    clicks.append(button.name)
                    if button.name == 'SWITCH_20241219_COMBAT':
                        campaign.device.image[635:715, :400] = selected[635:715, :400]
                    elif button.name == 'CHAPTER_20241219_PART1':
                        self.assertEqual(MODE_SWITCH_20241219.get(main=campaign), 'combat')
                        campaign.device.image[:, :110] = selected[:, :110]
                    else:
                        self.fail(f'出现非预期导航点击：{button.name}')

                campaign.device.click.side_effect = click
                self.assertTrue(campaign.ensure_campaign_ui('a1'))
                self.assertEqual(clicks, ['SWITCH_20241219_COMBAT', 'CHAPTER_20241219_PART1'])
                campaign.get_chapter_index.assert_called_once_with()
                self.assertEqual(campaign.ENTRANCE.name, 'a1')

    def test_original_layout_keeps_classic_navigation(self):
        for server in ('cn', 'en', 'jp', 'tw'):
            with self.subTest(server=server):
                campaign = make_campaign(image=legacy_image(), server=server)
                with patch.object(MODE_SWITCH_1, 'set', wraps=MODE_SWITCH_1.set) as old_mode:
                    self.assertTrue(campaign.ensure_campaign_ui('a1'))
                old_mode.assert_called_once_with('hard', main=campaign)
                self.assertFalse(campaign.config.MAP_CHAPTER_SWITCH_20241219)
                self.assertFalse(campaign.config.MAP_HAS_MODE_SWITCH)
                campaign.device.click.assert_not_called()

    def test_new_layout_does_not_depend_on_server_name(self):
        for server in ('cn', 'en', 'jp', 'tw'):
            with self.subTest(server=server):
                campaign = make_campaign(server=server)
                self.assertTrue(campaign.ensure_campaign_ui('a1'))
                self.assertTrue(campaign.config.MAP_CHAPTER_SWITCH_20241219)
                campaign.device.click.assert_not_called()

    def test_unknown_layout_does_not_guess_or_click(self):
        campaign = make_campaign(image=np.zeros((720, 1280, 3), dtype=np.uint8))
        with self.assertRaises(CampaignNameError):
            campaign.campaign_set_chapter_20241219('a', '1')
        campaign.device.click.assert_not_called()
        self.assertTrue(campaign.config.MAP_CHAPTER_SWITCH_20241219)

    def test_loading_frame_retries_with_new_screenshot(self):
        campaign = make_campaign(image=np.zeros((720, 1280, 3), dtype=np.uint8))

        def next_frame():
            campaign.device.image = load_image(str(FIXTURES / 'crimson.png'))

        campaign.device.screenshot.side_effect = next_frame
        self.assertTrue(campaign.ensure_campaign_ui('a1'))
        campaign.device.screenshot.assert_called_once_with()
        campaign.device.click.assert_not_called()
        self.assertEqual(campaign.ENTRANCE.name, 'a1')

    def test_layout_is_rechecked_and_flags_restore_on_same_instance(self):
        campaign = make_campaign()
        for image, modern in ((load_image(str(FIXTURES / 'crimson.png')), True),
                              (legacy_image(), False),
                              (load_image(str(FIXTURES / 'crimson.png')), True)):
            with self.subTest(modern=modern):
                campaign.device.image = image
                self.assertTrue(campaign.ensure_campaign_ui('a1'))
                self.assertEqual(campaign.config.MAP_CHAPTER_SWITCH_20241219, modern)
                self.assertEqual(campaign.config.MAP_HAS_MODE_SWITCH, modern)

    def test_hard_stage_uses_map_preparation_mode(self):
        campaign = make_campaign()
        with patch.object(MODE_SWITCH_1, 'set') as old_mode:
            self.assertTrue(campaign.ensure_campaign_ui('c1'))
        old_mode.assert_not_called()
        self.assertEqual(campaign.config.Campaign_Mode, 'hard')
        self.assertTrue(campaign.config.MAP_HAS_MODE_SWITCH)
        self.assertEqual(campaign.ENTRANCE.name, 'c1')

    def test_sp_does_not_enable_map_preparation_mode(self):
        campaign = make_campaign(map_config=CrimsonSpConfig())
        campaign.campaign_ensure_aside_20241219 = Mock()
        campaign.campaign_ensure_chapter = Mock()
        self.assertTrue(campaign.campaign_set_chapter_20241219('ex_sp', '1'))
        campaign.campaign_ensure_aside_20241219.assert_called_once_with('sp')
        self.assertFalse(campaign.config.MAP_HAS_MODE_SWITCH)

    def test_story_enters_event_page_before_switching(self):
        campaign = make_campaign()
        trace = []
        campaign.ui_goto_event.side_effect = lambda: trace.append('event')
        campaign.campaign_ensure_mode_20241219 = Mock(side_effect=lambda mode: trace.append(mode))
        self.assertTrue(campaign.campaign_set_chapter_20241219('a', '1', mode='story'))
        self.assertEqual(trace, ['event', 'story'])
        campaign.device.click.assert_not_called()


class CampaignLoadIdentityTests(unittest.TestCase):
    def test_same_stage_in_different_activities_loads_each_map(self):
        config = make_config()
        runner = CampaignRun(config=config, device=Mock())
        previous = None
        for folder in ('event_20240912_cn', 'event_20250227_cn', 'event_20240912_cn'):
            with self.subTest(folder=folder):
                self.assertTrue(runner.load_campaign('a1', folder=folder))
                self.assertEqual(runner.module.__name__, f'campaign.{folder}.a1')
                self.assertIsNot(runner.campaign, previous)
                self.assertIs(runner.campaign.MAP, runner.module.MAP)
                self.assertEqual(runner.folder, folder)
                previous = runner.campaign
                self.assertFalse(runner.load_campaign('a1', folder=folder))
                self.assertIs(runner.campaign, previous)
        self.assertEqual(config.modified, {})

    def test_navigation_overrides_stay_in_campaign_copy(self):
        config = make_config()
        runner = CampaignRun(config=config, device=Mock(image=load_image(str(FIXTURES / 'crimson.png'))))
        runner.load_campaign('a1', folder='event_20240912_cn')
        runner.campaign.ui_goto_event = Mock()
        runner.campaign.campaign_ensure_chapter = Mock()
        self.assertTrue(runner.campaign.campaign_set_chapter_20241219('a', '1'))
        self.assertTrue(runner.campaign.config.MAP_CHAPTER_SWITCH_20241219)
        self.assertFalse(config.MAP_CHAPTER_SWITCH_20241219)
        self.assertNotIn('MAP_CHAPTER_SWITCH_20241219', config.overridden)


if __name__ == '__main__':
    unittest.main()
