"""战役章节索引上限校验的回归测试（2026-11-11 点击洪水事故）。

背景日志（用户 2026-11-11 那份 alas 日志，12:05 段）：

    12:05:39.004  Mode_switch_1 set to hard
    12:05:39.434  WARNING [战役] 数字与文本之间未找到间隔。
    12:05:39.890  [campaign 0.411s] ['2', '31009', '1', '']
    12:05:39.897  [章节] 3100
    12:05:39.907  [当前索引] 3100
    12:05:40.152  点击 (46,367) @ CHAPTER_PREV      # 3098 次从这里开始
    ...（连续 12 分 55 秒，189041~192053 行全是 CHAPTER_PREV，中间零截图）
    12:18:35.749  点击 (55,373) @ CHAPTER_PREV      # 第 3098 次，本串结束

转场动画期间 OCR 把 "2-2" 读成 "31009"，`_campaign_separate_name()` 命中
`name[-1].isdigit()` 分支切成章节 '3100'；目标是第 2 章，
`diff = 2 - 3100 = -3098` 直接进了 `device.multi_click()`。那个函数是同步
阻塞循环、全程不截图、循环内 `control_check=False` 不计点击记录，
所以 13 分钟盲点既没被 OCR 发现、也没被 GameTooManyClickError 兜住。

现在的处置：数字章节超过 `MAX_CHAPTER_INDEX` 直接抛 CampaignNameError，
由 `ensure_campaign_ui()` 的 Timer(5, count=20) 接住重新截图重试。
"""

import re
import unittest
from pathlib import Path
from unittest.mock import Mock

from module.campaign.campaign_ocr import MAX_CHAPTER_INDEX, CampaignOcr
from module.exception import CampaignNameError

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_MAIN = ROOT / 'campaign' / 'campaign_main'
# 主线地图文件名形如 campaign_7_2.py / campaign_1_1_f.py
STAGE_FILE_PATTERN = re.compile(r'^campaign_(\d+)_(\d+)(?:_\w+)?$')


class ChapterIndexGuardTests(unittest.TestCase):
    """数字章节越界必须按识别失败处理，而不是返回一个假索引去算 diff。"""

    def setUp(self):
        self.get_index = CampaignOcr._campaign_get_chapter_index

    def test_incident_value_is_rejected(self):
        """事故现场值 '3100' 必须抛异常——这是本次修复的直接目标。"""
        with self.assertRaises(CampaignNameError):
            self.get_index('3100')

    def test_boundary(self):
        """上限本身放行，上限 +1 拒绝。"""
        self.assertEqual(self.get_index(str(MAX_CHAPTER_INDEX)), MAX_CHAPTER_INDEX)
        with self.assertRaises(CampaignNameError):
            self.get_index(str(MAX_CHAPTER_INDEX + 1))

    def test_int_argument_is_also_guarded(self):
        """子类/文案走 int 分支时同样要校验，不能只挡字符串那条路。"""
        self.assertEqual(self.get_index(MAX_CHAPTER_INDEX), MAX_CHAPTER_INDEX)
        with self.assertRaises(CampaignNameError):
            self.get_index(MAX_CHAPTER_INDEX + 1)
        with self.assertRaises(CampaignNameError):
            self.get_index(3100)

    def test_real_chapters_still_work(self):
        """正常章节号不受影响。"""
        for chapter in range(1, MAX_CHAPTER_INDEX + 1):
            with self.subTest(chapter=chapter):
                self.assertEqual(self.get_index(str(chapter)), chapter)

    def test_alpha_chapters_unaffected(self):
        """字母章节（活动/SP/档案）不走上限校验，映射保持原样。"""
        expected = {
            'a': 1, 'c': 1, 'as': 1, 'cs': 1, 't': 1, 'ht': 1,
            'ts': 1, 'hts': 1, 'sp': 1, 'ex_sp': 1,
            'b': 2, 'd': 2, 'bs': 2, 'ds': 2, 'ex_ex': 2,
        }
        for name, index in expected.items():
            with self.subTest(name=name):
                self.assertEqual(self.get_index(name), index)

    def test_unknown_name_still_raises(self):
        """原有未知名称分支不能被改动破坏。"""
        with self.assertRaises(CampaignNameError):
            self.get_index('zzz')


class ChapterIndexChainTests(unittest.TestCase):
    """确认校验点在唯一的算差值入口上，不会被绕过。"""

    def test_subclass_overrides_cannot_bypass_guard(self):
        """覆写 _campaign_get_chapter_index() 的子类也必须被挡住（行为测试）。

        有 7 个活动/档案的 CampaignBase 整段覆写了这个方法、不调 super()
        （event_20200917_cn / event_20201029_cn / event_20201126_cn /
        event_20210121_cn / event_20210722_cn / war_archives_20190911_cn /
        war_archives_20200917_cn），只改基类那一份会被它们绕过。
        get_chapter_index() 是唯一出口且没有子类覆写，兜底必须在那里生效。

        这里直接构造一个覆写返回 3100 的子类实例，跑真正的调用链。
        """
        class RogueCampaign(CampaignOcr):
            """模拟覆写映射表、不做校验的老活动子类。"""

            def __init__(self):
                self.device = Mock()
                self.campaign_chapter = '3100'

            @staticmethod
            def _campaign_get_chapter_index(name):
                return 3100

            def _get_stage_name(self, image):
                return None

            def handle_get_chapter_additional(self):
                return False

        campaign = RogueCampaign()
        with self.assertRaises(CampaignNameError):
            campaign.get_chapter_index()

    def test_normal_chapter_passes_through_subclass(self):
        """子类返回合法章节时不能被误拦。"""
        class NormalCampaign(CampaignOcr):
            def __init__(self):
                self.device = Mock()
                self.campaign_chapter = '2'

            @staticmethod
            def _campaign_get_chapter_index(name):
                return 2

            def _get_stage_name(self, image):
                return None

            def handle_get_chapter_additional(self):
                return False

        self.assertEqual(NormalCampaign().get_chapter_index(), 2)

    def test_ensure_chapter_does_not_swallow_name_error(self):
        """campaign_ensure_chapter() 不能捕获 CampaignNameError。

        抛异常是这套防护的出口：必须冒泡到 ensure_campaign_ui() 的
        Timer(5, count=20) 重试。若在这一层 except 掉再 continue，
        画面一直不变时会变成每秒空转的死循环。
        """
        source = (ROOT / 'module' / 'campaign' / 'campaign_ui.py').read_text(encoding='utf-8')
        start = source.index('def campaign_ensure_chapter')
        end = source.index('def handle_chapter_additional', start)
        body = source[start:end]
        self.assertNotIn('except CampaignNameError', body)
        self.assertNotIn('except (', body)


class MaxChapterConstantTests(unittest.TestCase):
    """上限常量必须覆盖游戏实际章节，游戏扩章时这个测试要第一时间变红。"""

    def test_constant_matches_game_content(self):
        """扫 campaign_main 的真实章节号，最大章节不能超过上限。

        游戏更新到 21 章而常量还是 20 时，这里会失败——提醒调大
        MAX_CHAPTER_INDEX，别让新章节被当成 OCR 误识别拒绝掉。
        """
        chapters = set()
        for path in CAMPAIGN_MAIN.glob('campaign_*.py'):
            match = STAGE_FILE_PATTERN.match(path.stem)
            if match:
                chapters.add(int(match.group(1)))

        # 扫描点数下限：防止 glob 写错/目录改名导致本测试形同虚设
        self.assertGreaterEqual(len(chapters), 10,
                                f'扫描到的章节数异常偏少: {sorted(chapters)}')
        self.assertLessEqual(
            max(chapters), MAX_CHAPTER_INDEX,
            f'游戏已有第 {max(chapters)} 章，但 MAX_CHAPTER_INDEX={MAX_CHAPTER_INDEX}，'
            f'请调大 module/campaign/campaign_ocr.py 中的常量（并更新事故注释里的章数）')

    def test_constant_is_sane(self):
        """上限不能小到把现行主线章节挡在门外。"""
        self.assertGreaterEqual(MAX_CHAPTER_INDEX, 16)


if __name__ == '__main__':
    unittest.main()
