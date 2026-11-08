"""大世界结算界面残留导致 GameStuckError 的回归测试。

背景（2026-11-07/11-08「逆迴十六夜」运行日志）：
进入策略搜索时，上一个已清除海域的「合计获得奖励」弹窗（AUTO_SEARCH_REWARD）
延迟弹出并遮挡界面。`strategy_search_enter()` 原先只做 `appear()` 检测后
`continue`，从不点击关闭，状态循环 60 秒无操作后被判为 GameStuckError。
另有战斗 VICTORY 结算页残留卡在 `action_point_enter()` 的情况。

本测试锁住两处修复：
- `MapEventHandler.handle_os_battle_settlement()` 会点掉结算界面且带点击限速；
- 策略搜索与行动力两个进入页面的循环都调用了该兜底，且不再有只检测不点击的写法。
"""

import ast
import unittest
from pathlib import Path

from module.combat.assets import BATTLE_STATUS_S, EXP_INFO_S
from module.os_handler.assets import AUTO_SEARCH_REWARD
from module.os_handler.map_event import MapEventHandler

ROOT = Path(__file__).resolve().parents[1]


class _FakeHandler:
    """只实现兜底方法依赖的 appear_then_click，避免构造真实设备。"""

    def __init__(self, visible):
        self.visible = set(visible)
        self.clicked = []
        self.intervals = {}

    def appear_then_click(self, button, **kwargs):
        self.intervals[button.name] = kwargs.get('interval', 0)
        if button.name in self.visible:
            self.clicked.append(button.name)
            return True
        return False


class TestOsBattleSettlement(unittest.TestCase):
    def test_reward_popup_clicked_with_interval(self):
        handler = _FakeHandler([AUTO_SEARCH_REWARD.name])
        self.assertTrue(MapEventHandler.handle_os_battle_settlement(handler))
        self.assertEqual(handler.clicked, [AUTO_SEARCH_REWARD.name])
        self.assertGreaterEqual(handler.intervals[AUTO_SEARCH_REWARD.name], 2)

    def test_victory_page_clicked(self):
        handler = _FakeHandler([BATTLE_STATUS_S.name])
        self.assertTrue(MapEventHandler.handle_os_battle_settlement(handler))
        self.assertEqual(handler.clicked, [BATTLE_STATUS_S.name])

    def test_exp_info_page_clicked(self):
        handler = _FakeHandler([EXP_INFO_S.name])
        self.assertTrue(MapEventHandler.handle_os_battle_settlement(handler))
        self.assertEqual(handler.clicked, [EXP_INFO_S.name])

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
