"""服务器状态轮询日志降噪的回归测试。

背景：维护期间调度器每 60 秒查一次服务器状态，旧实现每次都打
`[服务器检查] 服务器 "X" 维护中。` 和 `服务器检查er will retry after Ns`。
一次维护几个小时，几百行同样的日志把日志区淹了，真正有用的信息看不见。

现在：只有「首次查询」和「状态发生变化」打 INFO，其余降级为 DEBUG
（默认级别 INFO，不显示）。开服那一刻状态翻转，仍然会有一条 INFO。
"""

import inspect
import unittest
from collections import deque
from unittest.mock import patch

from module.server_checker import ServerChecker


def make_checker():
    """构造一个不联网的 ServerChecker，只保留状态机需要的字段。"""
    checker = ServerChecker.__new__(ServerChecker)
    checker._state = deque(maxlen=2)
    checker._server = '奥林匹克行动'
    return checker


class TestPushStateLoglevel(unittest.TestCase):
    """状态没变用 DEBUG，变了用 INFO。"""

    def test_first_check_is_info(self):
        checker = make_checker()
        with patch('module.server_checker.logger') as log:
            checker._push_state(False, '维护中')
        log.info.assert_called_once()
        log.debug.assert_not_called()

    def test_repeated_same_state_is_debug(self):
        checker = make_checker()
        with patch('module.server_checker.logger') as log:
            checker._push_state(False, '维护中')
            checker._push_state(False, '维护中')
            checker._push_state(False, '维护中')
        self.assertEqual(log.info.call_count, 1, '重复轮询只能有一条 INFO')
        self.assertEqual(log.debug.call_count, 2)

    def test_recovery_is_info(self):
        """开服那一刻状态翻转，必须留下一条 INFO。"""
        checker = make_checker()
        with patch('module.server_checker.logger') as log:
            checker._push_state(False, '维护中')
            checker._push_state(True, '可用')
        self.assertEqual(log.info.call_count, 2)

    def test_state_still_recorded(self):
        """降级日志不影响状态记录本身。"""
        checker = make_checker()
        with patch('module.server_checker.logger'):
            checker._push_state(False, '维护中')
            checker._push_state(False, '维护中')
        self.assertFalse(checker._state[-1])


class TestNoInfoSpamInSource(unittest.TestCase):
    """源码锁：轮询路径不许再出现每轮必打的 INFO。"""

    def test_retry_line_is_debug(self):
        source = inspect.getsource(ServerChecker.check_now)
        self.assertNotIn('logger.info', source, 'check_now 每轮都会跑，不能打 INFO')

    def test_load_server_uses_push_state(self):
        source = inspect.getsource(ServerChecker._load_server)
        self.assertIn('_push_state', source)
        # 直接 append + info 的组合是旧的刷屏写法
        self.assertNotIn("self._state.append(True)\n                    logger.info", source)


if __name__ == '__main__':
    unittest.main()
