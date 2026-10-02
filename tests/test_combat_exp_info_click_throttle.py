"""经验结算（EXP_INFO_*）点击必须带节流的源码锁测试。

背景（2026-09-29 两份用户日志）：大世界自动搜索结算时游戏转场/加载较慢，
点击被游戏吞掉，而 `handle_exp_info()` 每帧都点一次（约 0.75s），
9 秒内同一按钮攒满 12 次，触发 `GameTooManyClickError` 误判卡死：

    [自动搜索-结算] 战斗结算
    点击 (1197, 642) @ EXP_INFO_S   # x12
    GameTooManyClickError: [设备-点击] 按钮点击次数过多: EXP_INFO_S

因此所有 EXP_INFO_*/OPTS_INFO_* 的 `appear_then_click` 调用都必须传 `interval`，
把点击间隔拉开（默认 2s，攒满 12 次需 24s），避免慢加载场景被误判。
"""

import ast
import unittest
from pathlib import Path

# 经验结算界面相关按钮：EXP_INFO_S/A/B/C/D、EXP_INFO_CF，以及其后的 OPTS_INFO_D 弹窗
BUTTON_PREFIXES = ('EXP_INFO', 'OPTS_INFO')
# 源码扫描范围：module/ 与 campaign/ 下的全部 Python 文件
SCAN_DIRS = ('module', 'campaign')


class _ExpInfoClickVisitor(ast.NodeVisitor):
    """收集对 EXP_INFO_*/OPTS_INFO_* 的 appear_then_click 调用。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls = []

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == 'appear_then_click'
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id.startswith(BUTTON_PREFIXES)
        ):
            self.calls.append(node)
        self.generic_visit(node)


def _iter_exp_info_clicks():
    root = Path(__file__).resolve().parent.parent
    for dirname in SCAN_DIRS:
        for path in sorted((root / dirname).rglob('*.py')):
            # 个别模块带 BOM，utf-8-sig 兜住
            source = path.read_text(encoding='utf-8-sig')
            # 先做廉价文本过滤，避免解析全部模块拖慢测试
            if 'EXP_INFO' not in source and 'OPTS_INFO' not in source:
                continue
            tree = ast.parse(source)
            visitor = _ExpInfoClickVisitor(path)
            visitor.visit(tree)
            for call in visitor.calls:
                yield path, call


class TestExpInfoClickThrottle(unittest.TestCase):
    def test_scan_finds_exp_info_clicks(self):
        """扫描必须真的扫到点击点，否则本测试形同虚设。"""
        found = list(_iter_exp_info_clicks())
        self.assertGreaterEqual(
            len(found), 15,
            f'只扫到 {len(found)} 处经验结算点击，扫描逻辑可能已失效'
        )

    def test_all_exp_info_clicks_pass_interval(self):
        offenders = []
        for path, call in _iter_exp_info_clicks():
            if not any(keyword.arg == 'interval' for keyword in call.keywords):
                relative = path.name
                offenders.append(f'{relative}:{call.lineno} {ast.unparse(call)}')
        self.assertEqual(
            offenders, [],
            '经验结算点击缺少 interval 节流，慢加载时会被 GameTooManyClickError 误判卡死:\n'
            + '\n'.join(offenders)
        )

    def test_exp_info_interval_is_not_degenerate(self):
        """interval 必须是正数：0/None 等于没有节流（0 在 appear() 里表示不启用）。"""
        offenders = []
        for path, call in _iter_exp_info_clicks():
            for keyword in call.keywords:
                if keyword.arg != 'interval':
                    continue
                value = keyword.value
                if isinstance(value, ast.Constant):
                    if not isinstance(value.value, (int, float)) or value.value <= 0:
                        offenders.append(f'{path.name}:{call.lineno} interval={value.value!r}')
                elif isinstance(value, ast.Attribute):
                    # 允许 self.exp_info_click_interval / self.battle_status_click_interval
                    # 这类类属性（由下一条测试锁住默认值）
                    if value.attr not in ('exp_info_click_interval', 'battle_status_click_interval'):
                        offenders.append(f'{path.name}:{call.lineno} interval={ast.unparse(value)}')
                else:
                    offenders.append(f'{path.name}:{call.lineno} interval={ast.unparse(value)}')
        self.assertEqual(offenders, [], '经验结算点击的 interval 取值不合法:\n' + '\n'.join(offenders))


class TestExpInfoIntervalDefault(unittest.TestCase):
    """锁住 Combat.exp_info_click_interval 的默认值。"""

    def _combat_class(self) -> ast.ClassDef:
        path = Path(__file__).resolve().parent.parent / 'module' / 'combat' / 'combat.py'
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == 'Combat':
                return node
        self.fail('未找到 module/combat/combat.py 中的 Combat 类')

    def _class_int_attr(self, name: str):
        for statement in self._combat_class().body:
            if not isinstance(statement, ast.Assign):
                continue
            for target in statement.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    if isinstance(statement.value, ast.Constant) and isinstance(
                        statement.value.value, int
                    ):
                        return statement.value.value
        return None

    def test_exp_info_click_interval_defined(self):
        value = self._class_int_attr('exp_info_click_interval')
        self.assertIsNotNone(
            value,
            'Combat.exp_info_click_interval 必须在 module/combat/combat.py 中定义为整数'
        )
        self.assertGreaterEqual(
            value, 2,
            f'exp_info_click_interval={value} 过小：慢加载场景下 12 次点击窗口不足 24s，'
            f'仍会被 GameTooManyClickError 误判'
        )


if __name__ == '__main__':
    unittest.main()
