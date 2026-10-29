"""桥接 config 属性异常不得 exit(1) 的回归测试。

alas.py 的 config / device / checker 曾共用「except Exception → exit(1)」
模板，device 那个已造成 2026-10-26 停机 3.5 小时，checker 已降级修复。
本轮把同样的模板在两个桥接的 config 属性里也找了出来：

- submodule/AlasFpyBridge/fpy.py  config
- submodule/AlasMaaBridge/maa.py  config

桥接跑在 WebUI 独立子进程，exit(1) 不会杀主调度，但会静默结束该子进程且
不会自动拉起——表现为「Fpy/Maa 无故停止，需手动点启动」。
修复：RequestHumanTakeover（真正需要人工介入）保留 exit(1)；
其余 Exception 改为 raise，交给上层重试。cached_property 异常路径不写缓存，
下一次访问会重新构造，符合重试语义。
"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = {
    'AlasFpyBridge': ROOT / 'submodule' / 'AlasFpyBridge' / 'fpy.py',
    'AlasMaaBridge': ROOT / 'submodule' / 'AlasMaaBridge' / 'maa.py',
}


class BridgeConfigSourceLock(unittest.TestCase):
    """AST 源码锁：桥接 config 的通用异常分支不得 exit。"""

    def _config_func(self, path):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == 'config':
                for deco in node.decorator_list:
                    if 'cached_property' in ast.unparse(deco):
                        return node
        self.fail(f'{path} 中找不到 config 属性')

    def _handler_body(self, func, exc_name):
        for node in ast.walk(func):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    if handler.type is not None and ast.unparse(handler.type) == exc_name:
                        return handler.body
        return None

    def test_general_exception_reraises(self):
        """通用异常分支必须上抛，不得 exit(1)。"""
        for name, path in TARGETS.items():
            with self.subTest(bridge=name):
                func = self._config_func(path)
                body = self._handler_body(func, 'Exception')
                self.assertIsNotNone(body, f'{name} 缺少 except Exception 分支')
                source = '\n'.join(ast.unparse(stmt) for stmt in body)
                self.assertNotIn('exit', source, f'{name} 通用异常分支不得 exit')
                self.assertIn('raise', source, f'{name} 通用异常分支必须上抛')

    def test_human_takeover_still_exits(self):
        """RequestHumanTakeover 分支必须保留 exit（不可恢复语义）。"""
        for name, path in TARGETS.items():
            with self.subTest(bridge=name):
                func = self._config_func(path)
                body = self._handler_body(func, 'RequestHumanTakeover')
                self.assertIsNotNone(body, f'{name} 缺少 RequestHumanTakeover 分支')
                source = '\n'.join(ast.unparse(stmt) for stmt in body)
                self.assertIn('exit', source)


if __name__ == '__main__':
    unittest.main()
