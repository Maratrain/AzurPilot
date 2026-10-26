"""checker 属性网络异常不得杀死调度进程的回归测试。

背景：alas.py 的 checker 属性曾与 config/device 属性共用同一模板——
except Exception 后 exit(1)。ServerChecker.__init__ 内部会立即执行一次
check_now() 发起 HTTP 请求，一次 API 超时、DNS 失败或代理不可达都会让
构造抛异常，进而整个调度进程静默退出。

修复分两层：
1. server_checker._load_server 的异常分支扩展为 (ConnectionError, Timeout)，
   最常见的慢接口读超时（ReadTimeout）在检查器内部按「API 不可达」处理，
   走快速重试，检查器保持可用；
2. checker 改为 property + 显式缓存，构造失败返回 None 且不写缓存，
   调用方全部按「未知」处理。不用 @cached_property 是因为它会连 None
   一起缓存，网络恢复后不再重建，降级会变成永久失效。
"""

import ast
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

ROOT = Path(__file__).resolve().parent.parent
ALAS_SOURCE = ROOT / 'alas.py'

sys.path.insert(0, str(ROOT))


class SourceLocks(unittest.TestCase):
    """AST 源码锁：防止 checker 退回 exit(1) 模板。"""

    @classmethod
    def setUpClass(cls):
        cls.source = ALAS_SOURCE.read_text(encoding='utf-8')
        cls.tree = ast.parse(cls.source)
        cls.lines = cls.source.splitlines()

    def test_checker_has_no_exit(self):
        """checker 属性体内不得出现 exit()/sys.exit()。"""
        node = None
        for cls_node in ast.walk(self.tree):
            if isinstance(cls_node, ast.ClassDef) and cls_node.name == 'AzurLaneAutoScript':
                for item in cls_node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == 'checker':
                        node = item
        self.assertIsNotNone(node, 'alas.py 中找不到 AzurLaneAutoScript.checker')

        # 用 AST 判断真实的 exit 调用，而不是字符串匹配——
        # 文档字符串里说明历史缺陷时也会出现 "exit(1)" 字样，会误伤。
        exits = []
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                src = ast.unparse(child)
                if src.startswith('exit(') or src.startswith('sys.exit(') \
                        or src.startswith('os._exit('):
                    exits.append((child.lineno, src))
        self.assertEqual(exits, [],
                         f'checker 出现 exit 调用：网络抖动会再次杀死调度进程 {exits}')

    def test_checker_is_plain_property(self):
        """checker 必须是普通 property，不能是 cached_property。

        cached_property 会在返回后无条件写缓存（含 None），失败降级会变永久失效。
        """
        for cls_node in ast.walk(self.tree):
            if isinstance(cls_node, ast.ClassDef) and cls_node.name == 'AzurLaneAutoScript':
                for item in cls_node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == 'checker':
                        decos = [ast.unparse(d) for d in item.decorator_list]
                        self.assertNotIn('cached_property', decos,
                                         'checker 不能用 cached_property：None 会被永久缓存')
                        return
        self.fail('找不到 checker 定义')

    def test_no_bare_self_checker_attribute_use(self):
        """所有 self.checker 使用点必须先赋值给局部变量再判空。"""
        offenders = []
        for i, line in enumerate(self.lines, 1):
            stripped = line.split('#', 1)[0]
            if 'self.checker' not in stripped:
                continue
            # 允许 checker = self.checker
            if stripped.strip().startswith('checker = self.checker'):
                continue
            offenders.append((i, stripped.strip()[:80]))
        self.assertEqual(offenders, [],
                         f'self.checker 未经判空直接使用：{offenders}')


class CtorBehaviourTest(unittest.TestCase):
    """实测 ServerChecker 构造期异常的传播行为（证明修复前提成立）。"""

    def test_read_timeout_absorbed_by_load_server(self):
        """ReadTimeout 应被 (ConnectionError, Timeout) 分支吸收：检查器保持可用。

        快速重试依赖百度连通性探测，一并打桩离线化，走「网络不可用」分支。
        """
        from module.server_checker import ServerChecker
        with patch('requests.Session.post',
                   side_effect=requests.exceptions.ReadTimeout('read timed out')), \
             patch('requests.Session.get',
                   side_effect=requests.exceptions.ConnectionError('offline')):
            checker = ServerChecker('cn_android-12')  # 不应抛出
        self.assertFalse(checker.is_available(), '读超时后应标记为不可用而非崩溃')

    def test_too_many_redirects_escapes_ctor(self):
        """不属于 (ConnectionError, Timeout) 的请求异常会逸出构造函数。

        这是 checker 属性必须兜底返回 None 的前提。
        """
        from module.server_checker import ServerChecker
        with patch('requests.Session.post',
                   side_effect=requests.exceptions.TooManyRedirects('redirect loop')):
            with self.assertRaises(requests.exceptions.TooManyRedirects):
                ServerChecker('cn_android-12')

    def test_read_timeout_not_connection_error(self):
        """固化前提：ReadTimeout 不是 ConnectionError 子类。"""
        self.assertFalse(
            issubclass(requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError),
            '若 ReadTimeout 变成 ConnectionError 子类，本修复的前提需重新评估',
        )


class DegradeTest(unittest.TestCase):
    """checker 降级语义：失败返回 None，恢复后能重建。"""

    def _make_alas(self):
        """构造最小 alas 实例，只保留 checker 逻辑所需字段。"""
        from alas import AzurLaneAutoScript
        alas = AzurLaneAutoScript.__new__(AzurLaneAutoScript)

        # checker 内部读取 self.config.Emulator_ServerName
        class _Cfg:
            Emulator_ServerName = 'cn_android-12'
        alas.__dict__['config'] = _Cfg()
        return alas

    def test_failure_returns_none_without_exit(self):
        """构造期异常时 checker 返回 None，不抛 SystemExit。"""
        alas = self._make_alas()
        with patch('requests.Session.post',
                   side_effect=requests.exceptions.TooManyRedirects('boom')):
            result = alas.checker
        self.assertIsNone(result, '构造失败应降级为 None')

    def test_failure_does_not_cache(self):
        """失败不得写入缓存：下一次访问要真正重试。"""
        alas = self._make_alas()
        with patch('requests.Session.post',
                   side_effect=requests.exceptions.TooManyRedirects('boom')):
            self.assertIsNone(alas.checker)
        self.assertNotIn('_checker_instance', alas.__dict__,
                         '失败路径写了缓存，网络恢复后也不会重建')

    def test_recovers_after_network_back(self):
        """网络恢复后能重建出真实检查器（不被上次失败卡死）。"""
        alas = self._make_alas()
        with patch('requests.Session.post',
                   side_effect=requests.exceptions.TooManyRedirects('boom')):
            self.assertIsNone(alas.checker)

        # 模拟网络恢复：API 返回正常结构
        class _Resp:
            status_code = 200
            text = '{}'

            def json(self):
                return {'state': 0, 'last_update': 1}

        with patch('requests.Session.post', return_value=_Resp()):
            checker = alas.checker
        self.assertIsNotNone(checker, '网络恢复后 checker 仍为 None：降级变成了死锁')
        self.assertIn('_checker_instance', alas.__dict__, '成功后应写入缓存')

    def test_success_is_cached(self):
        """成功构造后应复用同一实例，不重复查询。"""
        alas = self._make_alas()

        class _Resp:
            status_code = 200
            text = '{}'

            def json(self):
                return {'state': 0, 'last_update': 1}

        with patch('requests.Session.post', return_value=_Resp()):
            first = alas.checker
            second = alas.checker
        self.assertIs(first, second, '成功路径必须缓存同一个实例')


if __name__ == '__main__':
    unittest.main()
