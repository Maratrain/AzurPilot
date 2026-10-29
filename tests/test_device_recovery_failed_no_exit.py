"""设备恢复失败不得杀死调度进程的回归测试（对齐 cq81 2026-10-26 事故）。

本地对应异常为 EmulatorNotRunningError（Device 构造中模拟器启动失败或
EmulatorOpBusy 冒泡而来），而非 cq81 的 RecoveryFailed。

缺陷链：device 属性的兜底 except Exception 把「设备侧恢复失败」当成
「配置/初始化错误」走 exit(1)，一次恢复失败就杀死整个调度进程，
7x24 场景下会停机数小时直到人工重启。

修复两层：
1. device 属性在 except Exception 之前单独放行 EmulatorNotRunningError
2. loop() 增加 EmulatorNotRunningError 分支：退避重试，不按脚本缺陷
   累计到全局失败上限而退出。
"""

import ast
import unittest
from pathlib import Path

SOURCE_PATH = Path(__file__).resolve().parent.parent / 'alas.py'


class DeviceRecoverySourceLock(unittest.TestCase):
    """AST 源码锁：防止未来改动悄悄恢复「恢复失败就退出进程」。"""

    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE_PATH.read_text(encoding='utf-8')
        cls.tree = ast.parse(cls.source)
        cls.lines = cls.source.splitlines()

    def _func(self, name):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        self.fail(f'alas.py 中找不到函数 {name}')

    def _segment(self, node):
        """返回函数体源码文本。"""
        return '\n'.join(self.lines[node.lineno - 1:node.end_lineno])

    def test_device_property_reraises_emulator_not_running(self):
        """device 属性必须在 except Exception 之前放行 EmulatorNotRunningError。"""
        body = self._segment(self._func('device'))
        self.assertIn('except EmulatorNotRunningError:', body,
                      'device 属性缺少放行分支：恢复失败会再次 exit(1)')
        self.assertLess(
            body.index('except EmulatorNotRunningError:'),
            body.index('except Exception as e:'),
            '放行分支必须写在 except Exception 之前，否则永远轮不到它',
        )
        tail = body[body.index('except EmulatorNotRunningError:'):]
        tail = tail[:tail.index('except Exception as e:')]
        code_lines = [ln.split('#', 1)[0].strip() for ln in tail.split('\n')]
        self.assertIn('raise', code_lines,
                      '放行分支必须是裸 raise 重新抛出，不能吞掉')

    def test_loop_recovery_branch_does_not_exit(self):
        """loop 的 EmulatorNotRunningError 分支不得 exit(1)，必须 continue。"""
        body = self._segment(self._func('loop'))
        self.assertIn('except EmulatorNotRunningError as e:', body,
                      'loop 缺少设备恢复失败的专门分支')
        seg = body[body.index('except EmulatorNotRunningError as e:'):]
        seg = seg[:seg.index('except Exception as e:')]
        self.assertNotIn('exit(1)', seg, '设备恢复分支不得终止进程')
        self.assertIn('continue', seg, '设备恢复分支必须退避后继续循环')


if __name__ == '__main__':
    unittest.main()
