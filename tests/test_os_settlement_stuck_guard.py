"""大世界结算界面残留导致 GameStuckError 的回归测试。

背景（2026-11-07/11-08「逆迴十六夜」运行日志）：
进入策略搜索时，上一个已清除海域的「合计获得奖励」弹窗（AUTO_SEARCH_REWARD）
延迟弹出并遮挡界面。`strategy_search_enter()` 原先只做 `appear()` 检测后
`continue`，从不点击关闭，状态循环 60 秒无操作后被判为 GameStuckError。
另有战斗 VICTORY 结算页残留卡在 `action_point_enter()` 的情况。

本测试锁住两处修复：
- `MapEventHandler.handle_os_battle_settlement()` 会点掉结算界面且带点击限速；
- 策略搜索与行动力两个进入页面的循环都调用了该兜底，且不再有只检测不点击的写法。

2026-11-15 补充：战斗评价页 / 经验结算页不接受任意点击关闭（顶部固定安全点
连点十余次页面纹丝不动，逆迴十六夜 18:28 / 18:59 两次 GameTooManyClickError），
只有右下「确定」响应点击。因确定按钮的点击区压在地图右下按钮排上，
点击前必须用新截图复核按钮仍在，旧帧残留时跳过，防止穿透。
"""

import ast
import unittest
from pathlib import Path

from module.combat.assets import BATTLE_STATUS_S, EXP_INFO_S
from module.os_handler.assets import AUTO_SEARCH_REWARD
from module.os_handler.map_event import MapEventHandler

ROOT = Path(__file__).resolve().parents[1]


class _FakeHandler:
    """只实现兜底方法依赖的 appear / appear_then_click / device，避免构造真实设备。"""

    def __init__(self, visible):
        self.visible = set(visible)
        # device.screenshot() 取到新帧后生效的画面；None 表示画面不变
        self.visible_after_refresh = None
        self.clicked = []
        self.intervals = {}
        self.screenshots = 0

    def appear(self, button, **kwargs):
        # 同一按钮可能被「检测 + 新帧复核」连续调用两次，只保留最大限速
        interval = kwargs.get('interval', 0)
        self.intervals[button.name] = max(self.intervals.get(button.name, 0), interval)
        return button.name in self.visible

    def appear_then_click(self, button, **kwargs):
        interval = kwargs.get('interval', 0)
        self.intervals[button.name] = max(self.intervals.get(button.name, 0), interval)
        if button.name in self.visible:
            self.clicked.append(button.name)
            return True
        return False

    def _refresh(self):
        self.screenshots += 1
        if self.visible_after_refresh is not None:
            self.visible = set(self.visible_after_refresh)

    def _close_os_battle_settlement(self, button):
        # 测试以假 handler 直调未绑定方法，补一层透传走真实实现
        return MapEventHandler._close_os_battle_settlement(self, button)

    class _Device:
        def __init__(self, owner):
            self._owner = owner

        def click(self, point):
            self._owner.clicked.append(point)

        def screenshot(self):
            self._owner._refresh()

    @property
    def device(self):
        return _FakeHandler._Device(self)


class TestOsBattleSettlement(unittest.TestCase):
    def test_reward_popup_clicked_with_interval(self):
        handler = _FakeHandler([AUTO_SEARCH_REWARD.name])
        self.assertTrue(MapEventHandler.handle_os_battle_settlement(handler))
        self.assertEqual(handler.clicked, [AUTO_SEARCH_REWARD.name])
        self.assertGreaterEqual(handler.intervals[AUTO_SEARCH_REWARD.name], 2)

    def test_victory_page_clicked_after_refresh(self):
        """战斗评价页只有右下「确定」响应点击：新截图复核按钮仍在后点按钮本身，
        且带点击限速。"""
        handler = _FakeHandler([BATTLE_STATUS_S.name])
        self.assertTrue(MapEventHandler.handle_os_battle_settlement(handler))
        self.assertEqual(handler.clicked, [BATTLE_STATUS_S])
        self.assertEqual(handler.screenshots, 1)
        self.assertGreaterEqual(handler.intervals[BATTLE_STATUS_S.name], 2)

    def test_exp_info_page_clicked_after_refresh(self):
        """经验结算页同样确认式点按钮本身，而非任意位置。"""
        handler = _FakeHandler([EXP_INFO_S.name])
        self.assertTrue(MapEventHandler.handle_os_battle_settlement(handler))
        self.assertEqual(handler.clicked, [EXP_INFO_S])
        self.assertEqual(handler.screenshots, 1)

    def test_stale_frame_skips_click(self):
        """窗口刚被上一击关闭、旧帧仍显示按钮时，新截图复核后不得点击，
        防止点击穿透到下层海域地图的右下按钮排（误开作战总览/编队页）。"""
        handler = _FakeHandler([BATTLE_STATUS_S.name])
        handler.visible_after_refresh = set()
        self.assertFalse(MapEventHandler.handle_os_battle_settlement(handler))
        self.assertEqual(handler.clicked, [])

    def test_get_items_still_uses_safe_point(self):
        """「获得道具」弹窗任意点击可关闭，接取任务循环仍用固定安全点，
        不受结算页改回点确定按钮的影响。"""
        node = None
        tree = ast.parse((ROOT / 'module/os_handler/mission.py').read_text(encoding='utf-8'))
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and n.name == 'os_get_next_mission':
                node = n
                break
        self.assertIsNotNone(node)
        clicked = [
            call.args[0].id
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and call.func.attr == 'click' and call.args
            and isinstance(call.args[0], ast.Name)
        ]
        self.assertIn('OS_SETTLEMENT_DISMISS_POINT', clicked)

    def test_no_settlement(self):
        handler = _FakeHandler([])
        self.assertFalse(MapEventHandler.handle_os_battle_settlement(handler))
        self.assertEqual(handler.clicked, [])


def _function_node(path, name):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f'{path} 中未找到函数 {name}')


def _called_attrs(func_node):
    return [
        call.func.attr
        for call in ast.walk(func_node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
    ]


class TestEnterLoopsUseGuard(unittest.TestCase):
    def test_strategy_search_enter(self):
        node = _function_node(ROOT / 'module/os_handler/strategic.py', 'strategy_search_enter')
        self.assertIn('handle_os_battle_settlement', _called_attrs(node))
        for call in ast.walk(node):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
                continue
            if call.args and isinstance(call.args[0], ast.Name) \
                    and call.args[0].id == 'AUTO_SEARCH_REWARD':
                self.assertEqual(
                    call.func.attr, 'appear_then_click',
                    '策略搜索循环中不允许只检测不点击 AUTO_SEARCH_REWARD',
                )

    def test_action_point_enter(self):
        node = _function_node(ROOT / 'module/os_handler/action_point.py', 'action_point_enter')
        self.assertIn('handle_os_battle_settlement', _called_attrs(node))


if __name__ == '__main__':
    unittest.main()
