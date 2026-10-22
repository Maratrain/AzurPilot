"""飓风与雾霭回廊（event_20261022_cn）的关卡接入与 SP 固定敌人回归。

用例借鉴自 wess09/AzurPilot 提交 83d0c31e3
（feat(campaign): 适配飓风与雾霭回廊并更新舰船资料），战役文件与上游逐字节一致，
接入方式参照 changqing81/Azurpilot-Auto 提交 8309f1dc2（本地同样没有
module/campaign/stage_name.py，T 章节列表改在 module/campaign/run.py 内联维护）。

与上游原测试的差异（本地化改写）：

- 上游用 `campaign.navigation` 聚合对象记录导航调用顺序，本地没有该属性，
  改为把四个导航方法逐个替换成 Mock 后分别检查调用记录；
- 上游通过 `config.override(SERVER=...)` 在 cn/en/jp 之间切换，本地 SERVER 是
  只读 property（module/config/server 全局决定），且本活动的导航分支
  （page_sp / ASIDE_SWITCH）与服务器无关，因此不逐服跑，只在
  test_registered_event_uses_shared_directory_for_cn_en_jp 校验四服活动候选；
- 配置沿用上游写法 `AzurLaneConfig('template')` + `merge(地图文件.Config())`：
  直接用 `Config()` 会缺少 ManualConfig 提供的 MAP_CHAPTER_SWITCH_20260326 等属性。
"""

import copy
import importlib
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

from campaign.event_20261022_cn import sp, t1
from module.config.config import AzurLaneConfig
from module.exception import CampaignNameError


EVENT = 'event_20261022_cn'
ROOT = Path(__file__).resolve().parents[1]

# 导航方法名：上游在 navigation 上聚合，本地逐个替换
NAV_METHODS = (
    'ui_goto_sp',
    'campaign_ensure_mode_20241219',
    'campaign_ensure_aside_20241219',
    'campaign_ensure_chapter',
)


def make_campaign(map_module=t1):
    """构造可测试的 Campaign：真实配置 + 真实地图，仅替换导航与设备交互。"""
    config = AzurLaneConfig('template')
    config.merge(map_module.Config())
    campaign = object.__new__(map_module.Campaign)
    campaign.config = config
    campaign.device = Mock()
    for name in NAV_METHODS:
        setattr(campaign, name, Mock(name=name))
    return campaign


def nav_calls(campaign):
    """按固定顺序取出导航调用记录，便于与上游断言对齐。"""
    return [getattr(campaign, name).call_args for name in NAV_METHODS]


class WindFogCorridorTests(unittest.TestCase):
    """验证 T1~T6 上下篇分区、SP 独立分区与非法关卡拒绝导航。"""

    def test_stage_routes_use_correct_part_for_t1_to_t6(self):
        for stage, aside in ((1, 'part1'), (2, 'part1'), (3, 'part1'),
                             (4, 'part2'), (5, 'part2'), (6, 'part2')):
            with self.subTest(stage=stage):
                module = importlib.import_module(f'campaign.{EVENT}.t{stage}')
                campaign = make_campaign(module)
                campaign.campaign_set_chapter(f't{stage}')
                calls = nav_calls(campaign)
                # 上篇 T1~T3 走 part1，下篇 T4~T6 走 part2
                self.assertEqual(calls[0], ((),))                       # ui_goto_sp()
                self.assertEqual(calls[1], (('combat',),))              # ensure_mode
                self.assertEqual(calls[2], ((aside,),))                 # ensure_aside
                self.assertEqual(calls[3], (('t',),))                   # ensure_chapter
                self.assertFalse(campaign.config.MAP_HAS_MODE_SWITCH)
                self.assertFalse(campaign.config.MAP_CHAPTER_SWITCH_20260326)
                campaign.device.click.assert_not_called()

    def test_daily_sp_uses_sp_partition_without_difficulty_switch(self):
        campaign = make_campaign(sp)
        campaign.campaign_set_chapter('sp')
        calls = nav_calls(campaign)
        self.assertEqual(calls[0], ((),))                               # ui_goto_sp()
        self.assertEqual(calls[1], (('combat',),))
        self.assertEqual(calls[2], (('sp',),))                          # SP 分区
        self.assertEqual(calls[3], (('ex_sp',),))
        self.assertTrue(campaign.config.MAP_IS_ONE_TIME_STAGE)
        self.assertFalse(campaign.config.MAP_HAS_MODE_SWITCH)

    def test_invalid_t_stage_does_not_navigate(self):
        campaign = make_campaign()
        with self.assertRaises(CampaignNameError):
            campaign.campaign_set_chapter('t7')
        for name in NAV_METHODS:
            getattr(campaign, name).assert_not_called()
        campaign.device.click.assert_not_called()

    def test_sp_initialization_restores_only_fixed_sirens_each_sortie(self):
        """SP 的 type=98 敌人没有精英图标，按 Lua 固定出生格初始化。"""
        campaign = make_campaign(sp)
        campaign.map_is_clear_mode = False
        map_ = copy.deepcopy(sp.MAP)
        expected = {(3, 1), (2, 2), (1, 3)}
        for _ in range(2):
            campaign.map_data_init(map_)
            self.assertEqual(
                {grid.location for grid in map_.select(is_siren=True)},
                expected,
            )
            for grid in map_.select(is_siren=True):
                grid.wipe_out()
        self.assertFalse(campaign.config.MAP_HAS_MOVABLE_ENEMY)
        self.assertFalse(campaign.config.MAP_SIREN_HAS_BOSS_ICON_SMALL)
        self.assertEqual(campaign.config.MAP_SIREN_TEMPLATE, [])

    def test_registered_event_uses_shared_directory_for_cn_en_jp(self):
        args = json.loads(
            (ROOT / 'module/config/argument/args.json').read_text(encoding='utf-8'))
        for task in ('Event', 'Event2', 'Event3', 'EventA', 'EventB',
                     'EventC', 'EventD', 'EventSp'):
            for server in ('cn', 'en', 'jp'):
                with self.subTest(task=task, server=server):
                    self.assertEqual(
                        args[task]['Campaign']['Event'][f'option_{server}'],
                        [EVENT],
                    )

    def test_localized_event_names_survive_config_generation(self):
        for language, expected in (
            ('zh-CN', '飓风与雾霭回廊'),
            ('en-US', 'Tempesta and the Corridor of Mist'),
            ('ja-JP', 'テンペスタと霧靄回廊'),
            ('zh-TW', '颶風與霧靄迴廊'),
        ):
            with self.subTest(language=language):
                path = ROOT / f'module/config/i18n/{language}.json'
                translated = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(translated['Campaign']['Event'][EVENT], expected)


if __name__ == '__main__':
    unittest.main()
