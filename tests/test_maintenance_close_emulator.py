"""游戏维护时关闭模拟器的回归测试。

背景：维护期间游戏登录不了，旧逻辑让模拟器一直开着、用 HTTP 轮询服务器状态，
开服后再 task_call('Restart')——6 小时的白白占用内存和 CPU。

新增开关 Optimization.CloseEmulatorDuringMaintenance（保留在本地 Alas 任务的
Optimization 组，未迁移分组）后，维护中先关模拟器，再关着轮询（HTTP 不需要
模拟器），开服后交给 Restart 拉起并登录。

本测试锁定：
1. 各种不该关的场景（开关关闭、服务器已可用、设备未初始化、远程实例）
2. 关闭失败（抛异常 / 返回 False）时不谎报成功
3. 正常路径：关一次、轮询到可用、丢弃设备缓存、返回 True
4. 中断与超时的兜底
5. 卡死分支与两处等待入口都真的接上了这个方法（源码锁）
"""

import inspect
import unittest
from unittest.mock import Mock, patch

from alas import AzurLaneAutoScript


def make_script(enabled=True, instance=object()):
    """构造一个只带必要属性的调度器实例。

    Args:
        enabled (bool): 维护时关闭模拟器开关。
        instance (object): self.device.emulator_instance 的返回值，
            None 表示远程设备（没有本地实例可关）。

    Returns:
        tuple[AzurLaneAutoScript, Mock]: (调度器, 模拟的 device)。
    """
    script = AzurLaneAutoScript.__new__(AzurLaneAutoScript)
    script.config = Mock()
    script.config.Optimization_CloseEmulatorDuringMaintenance = enabled
    script.stop_event = None
    device = Mock()
    device.emulator_instance = instance
    device.emulator_stop = Mock(return_value=True)
    device.release_during_wait = Mock()
    script.__dict__['device'] = device
    return script, device


def make_checker(sequence):
    """构造服务器状态检查器，is_available 按给定序列依次返回。"""
    checker = Mock()
    checker.is_available = Mock(side_effect=list(sequence))
    checker.check_now = Mock()
    return checker


class TestSkipCases(unittest.TestCase):
    """这些情况都不该关模拟器。"""

    def test_switch_disabled(self):
        """开关关着时一行都不能执行。"""
        script, device = make_script(enabled=False)
        checker = make_checker([False])
        self.assertFalse(script._close_emulator_for_maintenance(checker))
        device.emulator_stop.assert_not_called()

    def test_server_already_available(self):
        """服务器已经可用，没有维护可等。"""
        script, device = make_script()
        checker = make_checker([True])
        self.assertFalse(script._close_emulator_for_maintenance(checker))
        device.emulator_stop.assert_not_called()

    def test_device_not_initialized(self):
        """模拟器本来就没在跑（设备从未初始化），没有可关的东西。"""
        script, device = make_script()
        script.__dict__.pop('device')
        checker = make_checker([False])
        self.assertFalse(script._close_emulator_for_maintenance(checker))

    def test_remote_device(self):
        """远程设备（无线 ADB / SSH）没有本地实例，不能关。"""
        script, device = make_script(instance=None)
        checker = make_checker([False])
        self.assertFalse(script._close_emulator_for_maintenance(checker))
        device.emulator_stop.assert_not_called()


class TestStopFailure(unittest.TestCase):
    """关闭失败不能谎报成功，否则调用方不会拉起模拟器。"""

    def test_stop_returns_false(self):
        script, device = make_script()
        device.emulator_stop = Mock(return_value=False)
        checker = make_checker([False])
        with patch('module.base.resource.release_resources'):
            self.assertFalse(script._close_emulator_for_maintenance(checker))
        self.assertNotIn('device', script.__dict__, '关失败也要丢掉设备缓存')

    def test_stop_raises(self):
        script, device = make_script()
        device.emulator_stop = Mock(side_effect=RuntimeError('adb 挂了'))
        checker = make_checker([False])
        with patch('module.base.resource.release_resources'):
            self.assertFalse(script._close_emulator_for_maintenance(checker))
        self.assertNotIn('device', script.__dict__)


class TestSuccessPath(unittest.TestCase):
    """正常路径：关一次、轮询到开服、交出设备缓存。"""

    def test_closes_and_waits_until_available(self):
        script, device = make_script()
        # 第一次（入口判断）不可用 → 循环里第一次不可用 → 第二次可用
        checker = make_checker([False, False, True])
        with patch('module.base.resource.release_resources') as release, \
                patch('alas.time.sleep') as sleep:
            self.assertTrue(script._close_emulator_for_maintenance(checker))

        release.assert_called_once()
        device.release_during_wait.assert_called_once()
        device.emulator_stop.assert_called_once()
        self.assertNotIn('device', script.__dict__, '关掉后必须丢弃设备缓存')
        self.assertEqual(checker.check_now.call_count, 1)
        self.assertEqual(sleep.call_count, 1)

    def test_stop_event_interrupts(self):
        """更新事件要能打断等待，且返回 True（交给 Restart 拉起）。"""
        script, device = make_script()
        script.stop_event = Mock()
        script.stop_event.is_set = Mock(return_value=True)
        checker = make_checker([False, False])
        with patch('module.base.resource.release_resources'):
            self.assertTrue(script._close_emulator_for_maintenance(checker))
        script.stop_event.wait.assert_not_called()

    def test_max_wait_gives_up(self):
        """等太久（接口异常等）要放弃，不能永远不启动模拟器。"""
        script, device = make_script()
        checker = make_checker([False, False])
        # started_at = 0，循环里已过 100000 秒 > 24 小时
        with patch('module.base.resource.release_resources'), \
                patch('alas.time.monotonic', side_effect=[0, 100000]), \
                patch('alas.time.sleep'):
            self.assertTrue(script._close_emulator_for_maintenance(checker))


class TestStuckBranch(unittest.TestCase):
    """维护期间"画面静止"被判卡死的修复。

    背景：2026-11-05 10:00 国服维护，脚本 10:01 检测到账号登出，随后画面持续
    静止，10:04 判 GameStuckError、推了一条"游戏卡住"告警并重启游戏——实际
    只是维护。卡死分支必须先确认服务器状态，是维护就按维护处理。
    """

    def setUp(self):
        import alas as alas_module

        self.source = inspect.getsource(alas_module.AzurLaneAutoScript.run)

    def test_consults_server_before_stuck_verdict(self):
        stuck_at = self.source.index('except (GameStuckError, GameTooManyClickError)')
        count_at = self.source.index('consecutive_game_stuck += 1')
        check_at = self.source.index('check_now()', stuck_at)
        self.assertLess(check_at, count_at, '必须先查服务器状态，再计入卡死次数')

    def test_maintenance_returns_before_stuck_alarm(self):
        stuck_at = self.source.index('except (GameStuckError, GameTooManyClickError)')
        block = self.source[stuck_at:stuck_at + 3000]
        maintenance_at = block.index('_close_emulator_for_maintenance')
        alarm_at = block.index('游戏卡住 - 将自动重启游戏')
        self.assertLess(maintenance_at, alarm_at, '维护分支必须早于"游戏卡住"告警')

    def test_keeps_legacy_flow_when_server_available(self):
        """服务器可用时卡死流程不能变：仍然累计次数、仍然重启。"""
        stuck_at = self.source.index('except (GameStuckError, GameTooManyClickError)')
        block = self.source[stuck_at:stuck_at + 3000]
        self.assertIn('consecutive_game_stuck += 1', block)
        self.assertIn("self.config.task_call('Restart')", block)

    def test_no_false_failure_count_on_maintenance(self):
        """维护走 'recoverable'，不累计失败次数。"""
        stuck_at = self.source.index('except (GameStuckError, GameTooManyClickError)')
        block = self.source[stuck_at:stuck_at + 1500]
        self.assertIn("return 'recoverable'", block)


class TestPollRobustness(unittest.TestCase):
    """轮询期间的异常不能把已经关掉的模拟器丢在外面。

    2026-11-05 10:21 的实例：关掉模拟器等了 16 分钟后，等待里抛出 EOFError，
    直接落到调度器全局异常处理，整轮调度重启（模拟器虽然仍是关着的，但
    状态机被外部打断）。现在轮询体整段兜底。
    """

    def test_survives_poll_exception(self):
        script, device = make_script()
        checker = Mock()
        # 入口判可用一次 → 循环里第一次抛异常 → 之后可用
        checker.is_available = Mock(side_effect=[False, RuntimeError('EOFError 模拟'), True])
        checker.check_now = Mock()
        with patch('module.base.resource.release_resources'), \
                patch('alas.time.sleep'):
            self.assertTrue(script._close_emulator_for_maintenance(checker))
        device.emulator_stop.assert_called_once()

    def test_poll_error_logged_once(self):
        script, _ = make_script()
        script._poll_error_reported = False
        with patch('alas.logger') as log:
            script._log_poll_error_once(RuntimeError('boom'))
            script._log_poll_error_once(RuntimeError('boom'))
        self.assertEqual(log.warning.call_count, 1)
        self.assertEqual(log.debug.call_count, 1)


class TestCallSites(unittest.TestCase):
    """卡死分支与两处等待入口都必须接上新方法，否则功能等于没开。"""

    def test_get_next_task_loop_wired(self):
        import alas as alas_module

        source = inspect.getsource(alas_module.AzurLaneAutoScript.loop)
        self.assertIn('_close_emulator_for_maintenance', source)
        # 关掉之后再 continue，必须交给 Restart 拉起
        self.assertIn("task_call('Restart')", source)

    def test_task_failure_branch_wired(self):
        import alas as alas_module

        source = inspect.getsource(alas_module.AzurLaneAutoScript.run)
        self.assertIn('_close_emulator_for_maintenance', source)
        # 维护属于可自动恢复，不能计入任务失败次数
        idx = source.index('_close_emulator_for_maintenance')
        self.assertIn("return 'recoverable'", source[idx:idx + 600])

    def test_error_scene_flag_reset_each_round(self):
        """错误现场标记必须每轮重置，否则等待期间的下一个异常会丢堆栈。"""
        import alas as alas_module

        source = inspect.getsource(alas_module.AzurLaneAutoScript.loop)
        while_at = source.index('while 1:')
        try_at = source.index('try:', while_at)
        head = source[try_at:try_at + 800]
        self.assertIn('_error_scene_saved = False', head,
                      '每轮循环开头要清标记，否则任务级异常保存过现场后会一直残留')

    def test_default_is_disabled(self):
        from module.config.config_generated import GeneratedConfig

        self.assertFalse(GeneratedConfig.Optimization_CloseEmulatorDuringMaintenance)


if __name__ == '__main__':
    unittest.main()
