"""大世界任务跳过塞壬研究的回归测试。

锁定「不做塞壬研究任务」开关在任务列表（os_mission_enter）的行为：
开启开关时必须跳过所有塞壬研究行，不得误领；同时不得因行距变化而漏判。

历史问题：旧实现依赖「命中塞壬行后偏移 +110 像素」逐行推进，
当塞壬研究不在首行、或行距不是 110 时会直接走 else 分支领取，
导致塞壬研究被做掉（2026-10-26 同类事故）。

本测试直接调用 MissionHandler 的真实扫描方法（挂载到最小桩上），
用合成截图验证扫描与跳过判定，不依赖真机截图。
"""

import unittest
from types import SimpleNamespace

import numpy as np

from module.base.template import Template
from module.os_handler.assets import MISSION_FINISH, MISSION_SIREN_RESEARCH
from module.os_handler.mission import MissionHandler


def _synthetic_template(button, arr):
    """以真实按钮为底构建扫描模板，再把图片替换成合成图。"""
    button.ensure_template()
    template = Template(button.file)
    template.image = arr
    return template


class _DeviceStub:
    """记录点击的最小设备桩。"""

    def __init__(self, canvas):
        self.image = canvas
        self.clicked = []

    def click(self, button):
        self.clicked.append(button)


class _ScanStub:
    """只挂载 MissionHandler 扫描相关方法的最小桩，屏蔽 GlobeOperation 基类依赖。"""

    _os_mission_scan_rows = MissionHandler._os_mission_scan_rows
    _os_mission_claim_skip_siren = MissionHandler._os_mission_claim_skip_siren

    def __init__(self, canvas, siren_tpl, finish_tpl):
        self.device = _DeviceStub(canvas)
        self._os_mission_submitted = False
        self._os_mission_scan_templates = {
            MISSION_SIREN_RESEARCH.name: _synthetic_template(MISSION_SIREN_RESEARCH, siren_tpl),
            MISSION_FINISH.name: _synthetic_template(MISSION_FINISH, finish_tpl),
        }

    @property
    def clicked(self):
        return self.device.clicked


class TestOpsiMissionSkipSiren(unittest.TestCase):
    """测试扫描定位与跳过判定不依赖固定行距。"""

    def setUp(self):
        # 模板必须有非零方差，否则 TM_CCOEFF_NORMED 会因零方差产生 NaN
        rng = np.random.RandomState(42)
        self.siren_tpl = rng.randint(30, 225, (22, 41, 3)).astype(np.uint8)
        self.finish_tpl = rng.randint(30, 225, (27, 84, 3)).astype(np.uint8)

    def _build(self, rows):
        """按 (类型, y) 列表合成 720p 截图。"""
        canvas = np.zeros((720, 1280, 3), dtype=np.uint8)
        for kind, y in rows:
            if kind == 'siren':
                canvas[y:y + 22, 608:649] = self.siren_tpl
            else:
                canvas[y:y + 27, 1028:1112] = self.finish_tpl
        return canvas

    def _make_handler(self, rows):
        return _ScanStub(self._build(rows), self.siren_tpl, self.finish_tpl)

    def test_scan_rows_finds_all_positions(self):
        """扫描应按 y 升序返回所有匹配行。"""
        handler = self._make_handler([('finish', 186), ('finish', 322)])
        rows = handler._os_mission_scan_rows(MISSION_FINISH)
        ys = [y for y, _ in rows]
        self.assertEqual(len(rows), 2, f'应找到 2 个领取按钮，实际 {rows}')
        self.assertEqual(ys, sorted(ys), '扫描结果必须按 y 升序')

    def test_skip_all_siren_rows(self):
        """三行全是塞壬研究时，一个都不领取。"""
        handler = self._make_handler(
            [('siren', 186), ('siren', 296), ('siren', 406)])
        self.assertFalse(handler._os_mission_claim_skip_siren())
        self.assertEqual(handler.clicked, [])

    def test_skip_siren_row_but_claim_other(self):
        """塞壬研究行有领取按钮时跳过，其它行正常领取。"""
        handler = self._make_handler([
            ('siren', 186), ('finish', 186),
            ('finish', 322),
        ])
        self.assertTrue(handler._os_mission_claim_skip_siren())
        y = handler.clicked[0].area[1]
        # 应点击 y=322 那一行，而不是塞壬研究所在的 y=186
        self.assertLess(abs(y - 322), 120)
        self.assertGreater(abs(y - 186), 100)

    def test_skip_siren_not_on_first_row(self):
        """塞壬研究不在首行时也要正确跳过（旧实现在此漏判）。"""
        handler = self._make_handler([
            ('finish', 186),
            ('siren', 296), ('finish', 296),
        ])
        self.assertTrue(handler._os_mission_claim_skip_siren())
        y = handler.clicked[0].area[1]
        self.assertLess(abs(y - 186), 100)

    def test_skip_works_with_varied_row_gap(self):
        """行距不是 110 时依然正确（旧实现硬编码 110 会漏判）。"""
        handler = self._make_handler([
            ('siren', 186), ('finish', 186),
            ('finish', 336),  # 行距 150，不是 110
        ])
        self.assertTrue(handler._os_mission_claim_skip_siren())
        y = handler.clicked[0].area[1]
        # 应点击 y=336 那一行，而不是塞壬研究所在的 y=186
        self.assertLess(abs(y - 336), 120)
        self.assertGreater(abs(y - 186), 100)

    def test_no_siren_still_claims(self):
        """没有塞壬研究时，开关不应影响正常领取。"""
        handler = self._make_handler([('finish', 186), ('finish', 296)])
        self.assertTrue(handler._os_mission_claim_skip_siren())
        y = handler.clicked[0].area[1]
        self.assertLess(abs(y - 186), 100)

    def test_claim_sets_submitted_flag(self):
        """领取成功后必须置位 _os_mission_submitted（本地委托延期语义依赖它）。"""
        handler = self._make_handler([('finish', 186)])
        self.assertTrue(handler._os_mission_claim_skip_siren())
        self.assertTrue(handler._os_mission_submitted)


class TestSkipSirenSourceLock(unittest.TestCase):
    """源码锁：确认跳过分支不再使用硬编码行距累加。"""

    def test_no_hardcoded_110_step(self):
        """os_mission_enter 不得再用 area_offset(..., (0, 110)) 推进跳过。"""
        with open('module/os_handler/mission.py', encoding='utf-8') as f:
            source = f.read()

        # 取出 os_mission_enter 函数体
        start = source.index('def os_mission_enter(')
        end = source.index('def _os_mission_scan_templates(', start)
        body = source[start:end]

        self.assertNotIn(
            '(0, 110)', body,
            '跳过塞壬研究不得依赖硬编码行距 110')
        self.assertIn(
            '_os_mission_claim_skip_siren', body,
            '跳过逻辑应走全屏扫描实现')

    def test_scan_rows_method_exists(self):
        """扫描方法必须存在且基于多目标模板匹配。"""
        with open('module/os_handler/mission.py', encoding='utf-8') as f:
            source = f.read()
        self.assertIn('def _os_mission_scan_rows(', source)
        self.assertIn('match_multi', source)


if __name__ == '__main__':
    unittest.main()
