"""``module.meowfficer.scan`` 的测试：网格几何、去噪、遍历编排。

遍历部分用桩替换掉所有设备交互，只验证编排逻辑（去重、上限、到底判定、异常隔离），
真机行为由实机验证覆盖。
"""

import unittest
from contextlib import contextmanager

import numpy as np

from module.meowfficer.scan import MEOWFFICER_CATTERY_GRID, MeowfficerScanner
from module.meowfficer.scan_utils import _crop, _mean_diff, parse_level, pick_cat_name


class GridGeometryTests(unittest.TestCase):
    """网格坐标是实机量测值，改动必须是有意的。"""

    def test_grid_shape(self):
        self.assertEqual(len(MEOWFFICER_CATTERY_GRID.buttons), 12)

    def test_first_and_last_button(self):
        first = MEOWFFICER_CATTERY_GRID.buttons[0].area
        last = MEOWFFICER_CATTERY_GRID.buttons[-1].area
        self.assertEqual(tuple(int(v) for v in first), (784, 185, 864, 265))
        self.assertEqual(tuple(int(v) for v in last), (1174, 477, 1254, 557))

    def test_column_and_row_pitch(self):
        areas = [tuple(int(v) for v in b.area) for b in MEOWFFICER_CATTERY_GRID.buttons]
        # 列距 130：第 1 行相邻两张
        self.assertEqual(areas[1][0] - areas[0][0], 130)
        # 行距 146：第 1 列上下两张
        self.assertEqual(areas[4][1] - areas[0][1], 146)


class PickCatNameTests(unittest.TestCase):
    """猫名去噪：实机 OCR 会同时带出属性、状态等界面词。"""

    def test_prefers_longest_chinese_candidate(self):
        texts = ['R4', '空闲中', 'O', '风帆', 'WV30']
        self.assertEqual(pick_cat_name(texts), '风帆')

    def test_keeps_four_char_name(self):
        self.assertEqual(pick_cat_name(['潜艇参谋', '30']), '潜艇参谋')

    def test_drops_ui_words(self):
        self.assertEqual(pick_cat_name(['空闲中', '后勤', '加成一览', 'Lv30']), '')

    def test_drops_short_and_ascii_only(self):
        self.assertEqual(pick_cat_name(['R', 'SR', 'Lv30', '30']), '')

    def test_empty_input(self):
        self.assertEqual(pick_cat_name([]), '')
        self.assertEqual(pick_cat_name([None, '']), '')


class ParseLevelTests(unittest.TestCase):
    """等级用于区分同名猫，实机 OCR 结果有好几种形态。"""

    def test_reads_lv_prefixed(self):
        self.assertEqual(parse_level(['R', '空闲中', '风帆', 'LV30']), 30)

    def test_reads_colon_and_dot_variants(self):
        self.assertEqual(parse_level(['LV:30']), 30)
        self.assertEqual(parse_level(['Lv.28']), 28)

    def test_reads_misread_prefix(self):
        # 实机把 Lv 认成过 IV / WV
        self.assertEqual(parse_level(['IV:30']), 30)
        self.assertEqual(parse_level(['WV30']), 30)

    def test_rejects_resource_numbers(self):
        # 陪玩页的经验/资源数字不能被当成等级
        self.assertIsNone(parse_level(['22417/70000', '24741/74100']))
        self.assertIsNone(parse_level(['后勤 101', '指挥 194']))

    def test_none_when_no_digits(self):
        self.assertIsNone(parse_level(['空闲中', '风帆']))
        self.assertIsNone(parse_level([]))


class HelpersTests(unittest.TestCase):

    def test_crop_uses_xy_order(self):
        image = np.arange(10 * 10 * 3, dtype=np.uint8).reshape(10, 10, 3)
        cropped = _crop(image, (2, 3, 5, 7))
        self.assertEqual(cropped.shape, (4, 3, 3))
        self.assertTrue(np.array_equal(cropped, image[3:7, 2:5]))

    def test_mean_diff_identical_is_zero(self):
        image = np.zeros((8, 8, 3), dtype=np.uint8)
        self.assertEqual(_mean_diff(image, image.copy()), 0.0)

    def test_mean_diff_shape_mismatch_is_large(self):
        a = np.zeros((8, 8, 3), dtype=np.uint8)
        b = np.zeros((4, 4, 3), dtype=np.uint8)
        self.assertEqual(_mean_diff(a, b), 255.0)

    def test_mean_diff_detects_change(self):
        a = np.zeros((8, 8, 3), dtype=np.uint8)
        b = np.full((8, 8, 3), 10, dtype=np.uint8)
        self.assertAlmostEqual(_mean_diff(a, b), 10.0)

    def test_mean_diff_handles_none(self):
        self.assertEqual(_mean_diff(None, np.zeros((4, 4, 3))), 255.0)


class _StubScanner(MeowfficerScanner):
    """把设备交互全部替换成脚本化的返回值。"""

    def __init__(self, pages, talents_by_cat=None, fail_cats=()):
        # 刻意不调 super().__init__：不需要 config / device
        self.scanned = []
        self.pages = pages                    # 每屏 12 个位置的猫名（'' 表示读不到）
        self.talents_by_cat = talents_by_cat or {}
        self.fail_cats = set(fail_cats)
        self.page_index = 0
        self.swipes = []
        self.back_calls = 0
        self.reenter_calls = 0
        self.device = _StubDevice()
        self.talents_pool = []

    def _load_ocr(self):
        return object()

    def _ensure_cattery(self):
        return None

    def _reset_cattery_scroll(self):
        """编排测试不关心列表回顶部，桩成空操作以免消耗 page_index。"""
        return None

    def _reset_talent_scroll(self):
        return None

    def _read_current_cat(self, ocr):
        return '', None

    def _select_card(self, button, ocr, previous=''):
        index = list(MEOWFFICER_CATTERY_GRID.buttons).index(button)
        page = self.pages[min(self.page_index, len(self.pages) - 1)]
        return page[index] if index < len(page) else ''

    def _open_talent(self):
        return True

    def _read_talents(self, ocr):
        # talents_pool 按读取顺序逐个吐出，用于构造同名但天赋不同的卡片
        if self.talents_pool:
            return self.talents_pool.pop(0)
        return self.talents_by_cat.get(self.current_cat, [])

    def _back_to_cattery(self):
        self.back_calls += 1
        return True

    def _swipe_panel(self, area, distance=150, duration=0.9):
        self.swipes.append(distance)

    def _advance_cattery_screen(self):
        """模拟整屏前进：还有下一页就前进一屏，否则返回 0 表示到底。"""
        if self.page_index >= len(self.pages) - 1:
            return 0
        self.page_index += 1
        self.device.image = np.full((720, 1280, 3), 40 * (self.page_index + 1) % 250, dtype=np.uint8)
        return 438

    # 记录当前正在处理的卡片对应的猫名，供 _read_talents 查表
    def scan_all(self, limit=0, passes=12):
        self.current_cat = ''
        original = self._select_card

        def wrapped(button, ocr, previous=''):
            self.current_cat = original(button, ocr, previous)
            return self.current_cat

        self._select_card = wrapped
        return super().scan_all(limit=limit, passes=passes)


class _StubDevice:
    """只提供 scan 编排用到的设备接口。"""

    def __init__(self):
        self.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.stuck_cleared = 0

    def screenshot(self):
        """桩设备不需要真的截图，画面由 _swipe_panel 的桩更新。"""

    def stuck_record_clear(self):
        self.stuck_cleared += 1

    @contextmanager
    def stuck_timeout_override(self, image_stuck=None, long_wait=None):
        yield


def _talents(*names):
    """按天赋库解析成真实的 Talent（正确的天赋线与等级），而不是伪造数据。"""
    from module.meowfficer.score import resolve_talents
    return resolve_talents(list(names))


class ScanOrchestrationTests(unittest.TestCase):
    """遍历编排：逐屏访问、上限、到底判定。"""

    def _pages(self, *rows):
        """把若干「12 个位置的猫名」拼成页面列表。"""
        return [list(row) for row in rows]

    def test_every_page_is_recorded(self):
        """不做按内容去重：每屏整屏前进互不重叠，所以每张卡片都算一只。"""
        page1 = ['猫A', '猫B'] + [''] * 10
        page2 = ['猫C', '猫D'] + [''] * 10
        scanner = _StubScanner(self._pages(page1, page2),
                               talents_by_cat={k: _talents('天赋') for k in ('猫A', '猫B', '猫C', '猫D')})
        result = scanner.scan_all(passes=2)
        self.assertEqual([cat for cat, _ in result], ['猫A', '猫B', '猫C', '猫D'])

    def test_respects_limit(self):
        page1 = ['猫A', '猫B', '猫C'] + [''] * 9
        scanner = _StubScanner(self._pages(page1),
                               talents_by_cat={k: _talents('天赋') for k in ('猫A', '猫B', '猫C')})
        result = scanner.scan_all(limit=2)
        self.assertEqual(len(result), 2)

    def test_same_name_different_talents_are_both_kept(self):
        """同名猫实测存在（3 只潜艇参谋），天赋不同就必须都留下，不能按名字合并。"""
        page1 = ['潜艇参谋', '潜艇参谋'] + [''] * 10
        scanner = _StubScanner(self._pages(page1))
        scanner.talents_pool = [_talents('其徐如林'), _talents('不动如山')]
        result = scanner.scan_all(passes=1)
        self.assertEqual([cat for cat, _ in result], ['潜艇参谋', '潜艇参谋'])

    def test_same_name_identical_talents_are_both_kept(self):
        """完全相同的一对也不合并：指挥喵允许重名，也可能真的天赋一样。"""
        page1 = ['潜艇参谋', '潜艇参谋'] + [''] * 10
        scanner = _StubScanner(self._pages(page1))
        scanner.talents_pool = [_talents('其徐如林'), _talents('其徐如林')]
        result = scanner.scan_all(passes=1)
        self.assertEqual(len(result), 2)

    def test_renamed_cat_may_share_a_talent_name(self):
        """自定义名可能与天赋重名（用户有一只猫就叫「不动如山」），不能被过滤掉。"""
        page1 = ['不动如山'] + [''] * 11
        scanner = _StubScanner(self._pages(page1), talents_by_cat={'不动如山': _talents('其徐如林')})
        result = scanner.scan_all(passes=1)
        self.assertEqual([cat for cat, _ in result], ['不动如山'])

    def test_skips_cat_without_talents(self):
        page1 = ['猫A', '猫B'] + [''] * 10
        scanner = _StubScanner(self._pages(page1), talents_by_cat={'猫A': _talents('天赋')})
        result = scanner.scan_all(passes=1)
        self.assertEqual([cat for cat, _ in result], ['猫A'])

    def test_unreadable_card_is_skipped(self):
        page1 = ['', '猫A'] + [''] * 10
        scanner = _StubScanner(self._pages(page1), talents_by_cat={'猫A': _talents('天赋')})
        result = scanner.scan_all(passes=1)
        self.assertEqual([cat for cat, _ in result], ['猫A'])

    def test_open_talent_failure_does_not_abort(self):
        page1 = ['猫A', '猫B'] + [''] * 10
        scanner = _StubScanner(self._pages(page1), talents_by_cat={'猫B': _talents('天赋')})
        scanner._open_talent = lambda: False
        result = scanner.scan_all(passes=1)
        self.assertEqual(result, [])

    def test_stops_when_list_does_not_scroll(self):
        page1 = ['猫A'] + [''] * 11
        scanner = _StubScanner(self._pages(page1), talents_by_cat={'猫A': _talents('天赋')})
        # 面板不变化 -> 认为到底
        scanner._swipe_panel = lambda area, distance=150, duration=0.9: None
        result = scanner.scan_all(passes=5)
        self.assertEqual([cat for cat, _ in result], ['猫A'])

    def test_reenters_when_back_fails(self):
        page1 = ['猫A'] + [''] * 11
        scanner = _StubScanner(self._pages(page1), talents_by_cat={'猫A': _talents('天赋')})
        calls = []
        scanner._back_to_cattery = lambda: False
        scanner._ensure_cattery = lambda: calls.append(1)
        result = scanner.scan_all(passes=1)
        # 猫本身仍然入账；_ensure_cattery 被调用两次：进入时一次，返回失败后重进一次
        self.assertEqual([cat for cat, _ in result], ['猫A'])
        self.assertEqual(len(calls), 2)


class _FakeConfig:
    """只提供 ``_run_scan`` 用到的配置项。"""

    def __init__(self, limit=0, passes=12, report_path='./log/meowfficer_score.md'):
        self.MeowfficerScore_ScanLimit = limit
        self.MeowfficerScore_ScanPasses = passes
        self.MeowfficerScore_ReportPath = report_path


class _FakeScanner:
    """替身扫描器：返回预置的 (猫名, 天赋) 列表，并记录收到的参数。"""

    calls = []

    def __init__(self, config, device):
        self.config = config
        self.device = device

    def scan_all(self, limit=0, passes=12):
        _FakeScanner.calls.append((limit, passes))
        return list(self.results)


class ScanToReportTests(unittest.TestCase):
    """scan 结果 -> 评分 -> 报告 的整条链路（不连设备）。"""

    def setUp(self):
        _FakeScanner.calls = []

    def _task(self, results, report_path='./log/meowfficer_score.md', limit=0, passes=1):
        from module.meowfficer.score_task import MeowfficerScore

        task = object.__new__(MeowfficerScore)          # 跳过需要配置名的 __init__
        task.config = _FakeConfig(limit=limit, passes=passes, report_path=report_path)
        task.device = object()
        task.results = []

        class _Scanner(_FakeScanner):
            def __init__(self, config, device):
                super().__init__(config, device)
                self.results = results

        self.scanner_cls = _Scanner
        return task

    def test_scores_every_scanned_cat(self):
        from unittest.mock import patch

        results = [
            ('克雷喵', _talents('狼群之首', '雷击长·潜艇')),
            ('林德喵', _talents('其徐如林')),
        ]
        task = self._task(results, limit=2)
        with patch('module.meowfficer.scan.MeowfficerScanner', self.scanner_cls):
            task._run_scan()

        self.assertEqual([name for name, _ in task.results], ['克雷喵', '林德喵'])
        for _name, result in task.results:
            self.assertTrue(result.rubrics, '每只猫都应该走完评分引擎')
        self.assertEqual(_FakeScanner.calls, [(2, 1)], 'limit/passes 要透传给扫描器')

    def test_empty_scan_does_not_crash(self):
        from unittest.mock import patch

        task = self._task([])
        with patch('module.meowfficer.scan.MeowfficerScanner', self.scanner_cls):
            task._run_scan()
        self.assertEqual(task.results, [])

    def test_report_files_written_with_scan_cats(self):
        """核对报告：跑完扫描后三份产物都要落盘，且带上扫描到的猫名。"""
        import json
        import os
        import tempfile
        from unittest.mock import patch

        results = [
            ('克雷喵', _talents('狼群之首', '雷击长·潜艇')),
            ('林德喵', _talents('其徐如林', '既定的命运')),
        ]
        with tempfile.TemporaryDirectory() as folder:
            report = os.path.join(folder, 'meowfficer_score.md')
            task = self._task(results, report_path=report)
            with patch('module.meowfficer.scan.MeowfficerScanner', self.scanner_cls):
                task._run_scan()
            task._save_report()

            for path in (report,
                         os.path.join(folder, 'meowfficer_score.html'),
                         os.path.join(folder, 'meowfficer_score.json')):
                self.assertTrue(os.path.exists(path), f'缺少产物 {path}')

            with open(report, encoding='utf-8') as f:
                markdown = f.read()
            self.assertIn('克雷喵', markdown)
            self.assertIn('林德喵', markdown)

            with open(os.path.join(folder, 'meowfficer_score.json'), encoding='utf-8') as f:
                payload = json.load(f)
            self.assertEqual(payload['count'], 2)
            self.assertEqual([cat['cat'] for cat in payload['cats']], ['克雷喵', '林德喵'])
            for cat in payload['cats']:
                self.assertTrue(cat['rubrics'], '报告里每只猫都要有口径结果')


if __name__ == '__main__':
    unittest.main()
