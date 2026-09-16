"""模拟器启停并发保护测试。

回归自 2026-09-16 的实机日志：两条恢复线程同时在跑 emulator_start，
一个刚发出启动命令、另一个随即 shutdown，MuMu 窗口永远卡在加载态。
这里锁定三件事：

1. 启停操作互斥——已有操作在跑时，新的启停请求抛 EmulatorOpBusy 且不动手；
2. 锁覆盖操作的**全过程**（含 3 次重试与启动监视），不是只包住发命令；
3. 启动监视超时按 180/300/480 递增，给冷启动留出时间。
"""

import inspect
import json
import threading
import unittest
from unittest.mock import Mock, call, patch

from alas import RESTART_EMULATOR_OP_TIMEOUT, AzurLaneAutoScript
from module.device.platform import platform_windows
from module.device.platform.platform_windows import (
    EMULATOR_START_WATCH_TIMEOUTS,
    MUMU12_STOP_WAIT_TIMEOUT,
    PlatformWindows,
)
from module.exception import EmulatorNotRunningError, EmulatorOpBusy


def make_platform():
    """构造一个不连接设备、不碰真实模拟器的 PlatformWindows。

    用 __new__ 跳过 __init__（避免 ADB 连接与配置读取），只替换掉真正
    会去操作模拟器的那几个方法。
    """
    platform = PlatformWindows.__new__(PlatformWindows)
    platform.config = Mock()
    platform._emulator_function_wrapper = Mock(return_value=True)
    platform.emulator_start_watch = Mock(return_value=True)
    platform._emulator_stop = Mock()
    platform._emulator_start = Mock()
    return platform


class TestEmulatorOpExclusive(unittest.TestCase):
    def tearDown(self):
        # 兜底：任何测试把锁漏掉都会让后续测试全红，这里主动回收
        if PlatformWindows._emulator_op_lock.locked():
            PlatformWindows._emulator_op_lock.release()

    def test_stop_is_skipped_while_another_op_holds_the_lock(self):
        platform = make_platform()
        with PlatformWindows._emulator_op_lock:
            with self.assertRaises(EmulatorOpBusy):
                platform.emulator_stop()

        # 关键：被跳过时不能真的去动模拟器
        platform._emulator_stop.assert_not_called()
        platform._emulator_start.assert_not_called()

    def test_start_is_skipped_while_another_op_holds_the_lock(self):
        platform = make_platform()
        with PlatformWindows._emulator_op_lock:
            with self.assertRaises(EmulatorOpBusy):
                platform.emulator_start()

        platform._emulator_stop.assert_not_called()
        platform._emulator_start.assert_not_called()

    def test_lock_is_released_after_success(self):
        platform = make_platform()
        self.assertTrue(platform.emulator_stop())
        self.assertTrue(platform.emulator_start())

    def test_lock_is_released_when_operation_raises(self):
        platform = make_platform()
        platform._emulator_function_wrapper = Mock(side_effect=RuntimeError('boom'))

        with self.assertRaises(RuntimeError):
            platform.emulator_stop()

        # 异常路径也必须释放，否则后续恢复会被永久跳过
        self.assertTrue(PlatformWindows._emulator_op_lock.acquire(blocking=False))
        PlatformWindows._emulator_op_lock.release()

    def test_lock_is_held_for_the_whole_start_attempt(self):
        """启动监视进行中时，另一个线程不能停模拟器。

        这是本次事故的直接成因：监视（最长数分钟的冷启动等待）期间
        被另一个线程 shutdown 打断。
        """
        platform = make_platform()
        watching = threading.Event()
        release = threading.Event()

        def watch(timeout=None):
            watching.set()
            release.wait(5)
            return True

        platform.emulator_start_watch = watch
        thread = threading.Thread(target=platform.emulator_start)
        thread.start()
        try:
            self.assertTrue(watching.wait(5), '启动监视未进入')
            with self.assertRaises(EmulatorOpBusy):
                platform.emulator_stop()
        finally:
            release.set()
            thread.join(5)

        self.assertFalse(thread.is_alive())

    def test_watch_timeout_grows_across_retries(self):
        """冷启动可能耗时数分钟：重试的等待时间必须递增，而不是固定 180 秒。"""
        platform = make_platform()
        platform.emulator_start_watch = Mock(return_value=False)

        self.assertFalse(platform.emulator_start())

        self.assertEqual(
            platform.emulator_start_watch.call_args_list,
            [call(timeout=t) for t in EMULATOR_START_WATCH_TIMEOUTS],
        )
        self.assertGreater(len(EMULATOR_START_WATCH_TIMEOUTS), 1)
        self.assertEqual(
            list(EMULATOR_START_WATCH_TIMEOUTS),
            sorted(EMULATOR_START_WATCH_TIMEOUTS),
        )


def mumu_info(*players):
    """构造 MuMuManager info -v all 的 JSON 输出。"""
    data = {str(i): p for i, p in enumerate(players)}
    return json.dumps(data, ensure_ascii=False)


class TestMumu12StateQuery(unittest.TestCase):
    """MuMuManager info 查询：按实例精确判断状态，替代靠进程名猜测。"""

    def make_platform(self, stdout):
        platform = make_platform()
        platform.emulator_instance = Mock()
        platform.emulator_instance.emulator.path = 'F:/mumu/shell/MuMuPlayer.exe'
        platform.emulator_instance.MuMuPlayer12_id = 0
        return platform, patch.object(platform_windows, 'run_mumu_manager', return_value=stdout)

    def test_parses_instance_states(self):
        platform, patched = self.make_platform(
            mumu_info({'is_process_started': True, 'player_state': 'start_finished'}, {'is_process_started': False})
        )
        with patched:
            info = platform._mumu12_instances('F:/mumu/shell/MuMuPlayer.exe')

        self.assertEqual({'0', '1'}, set(info))
        self.assertTrue(info['0']['is_process_started'])
        self.assertFalse(info['1']['is_process_started'])

    def test_returns_none_when_output_is_not_json(self):
        platform, patched = self.make_platform('unknown cmd: info')
        with patched:
            self.assertIsNone(platform._mumu12_instances('F:/mumu/shell/MuMuPlayer.exe'))

    def test_other_instance_running_detects_multi_open(self):
        cases = [
            (mumu_info({'is_process_started': True}, {'is_process_started': False}), False),
            (mumu_info({'is_process_started': True}, {'is_process_started': True}), True),
            (mumu_info({'is_process_started': False}, {'is_process_started': False}), False),
        ]
        for stdout, expected in cases:
            platform, patched = self.make_platform(stdout)
            with patched:
                self.assertIs(expected, platform._mumu12_other_instance_running(
                    'F:/mumu/shell/MuMuPlayer.exe', 0))

    def test_other_instance_running_is_unknown_when_query_fails(self):
        platform, patched = self.make_platform('')
        with patched:
            self.assertIsNone(platform._mumu12_other_instance_running(
                'F:/mumu/shell/MuMuPlayer.exe', 0))

    def test_residue_cleanup_skipped_for_multi_open(self):
        """多开时不按进程名清理，避免误伤其它实例。"""
        platform, patched = self.make_platform(
            mumu_info({'is_process_started': True}, {'is_process_started': True})
        )
        with patched, patch.object(platform_windows.psutil, 'process_iter') as process_iter:
            platform._clean_mumu12_residue('F:/mumu/shell/MuMuPlayer.exe', 0)

        process_iter.assert_not_called()

    def test_residue_cleanup_skipped_when_state_unknown(self):
        """查不到状态时不清理——宁可少清理，不可误杀。"""
        platform, patched = self.make_platform('')
        with patched, patch.object(platform_windows.psutil, 'process_iter') as process_iter:
            platform._clean_mumu12_residue('F:/mumu/shell/MuMuPlayer.exe', 0)

        process_iter.assert_not_called()

    def test_wait_stopped_returns_when_instance_is_down(self):
        platform, patched = self.make_platform(mumu_info({'is_process_started': False}))
        with patched:
            self.assertTrue(platform._mumu12_wait_stopped('F:/mumu/shell/MuMuPlayer.exe', 0))

    def test_wait_stopped_keeps_polling_while_instance_is_up(self):
        """实例还在关闭过程中必须继续等。

        MuMu 的 shutdown 是异步的：命令秒回、实例还要 2~3 秒才停。
        ALAS 原来只盲等 2 秒就 launch，请求会被吞掉（实机复现）。
        """
        platform = make_platform()
        platform.emulator_instance = Mock()
        platform.emulator_instance.MuMuPlayer12_id = 0
        started = mumu_info({'is_process_started': True})
        stopped = mumu_info({'is_process_started': False})
        responses = [started, started, stopped]

        with (
            patch.object(platform_windows, 'run_mumu_manager', side_effect=responses),
            patch.object(platform_windows, 'MUMU12_STATE_POLL_INTERVAL', 0),
        ):
            self.assertTrue(platform._mumu12_wait_stopped('F:/mumu/shell/MuMuPlayer.exe', 0))

    def test_wait_stopped_gives_up_after_timeout(self):
        platform = make_platform()
        with (
            patch.object(platform_windows, 'run_mumu_manager',
                         return_value=mumu_info({'is_process_started': True})),
            patch.object(platform_windows, 'MUMU12_STATE_POLL_INTERVAL', 0),
            patch.object(platform_windows, 'MUMU12_STOP_WAIT_TIMEOUT', 0),
        ):
            self.assertFalse(platform._mumu12_wait_stopped('F:/mumu/shell/MuMuPlayer.exe', 0))

    def test_wait_stopped_falls_back_when_query_unavailable(self):
        """旧版 MuMu 没有 info 子命令时不能卡住，回退到旧的短暂等待。"""
        platform = make_platform()
        with (
            patch.object(platform_windows, 'run_mumu_manager', return_value=''),
            patch.object(platform_windows, 'MUMU12_STATE_POLL_INTERVAL', 0),
        ):
            self.assertTrue(platform._mumu12_wait_stopped('F:/mumu/shell/MuMuPlayer.exe', 0))


class TestDeepRestart(unittest.TestCase):
    """深度重启：结束 MuMu 全部进程，仅由「连续重启都失败」触发。

    定位是最後一招逃生口，不是省内存的常规手段——实测普通重启已经会替换
    虚拟机进程，全杀并不更省内存。
    """

    def make_platform(self):
        return make_platform()

    @staticmethod
    def fake_process(name, pid=1234):
        proc = Mock()
        proc.info = {'name': name}
        proc.pid = pid
        return proc

    def test_deep_clean_kills_only_listed_processes(self):
        platform = self.make_platform()
        platform.execute = Mock()
        mumu_player = self.fake_process('MuMuPlayer.exe')
        mumu_vm = self.fake_process('MuMuVMMHeadless.exe')
        unrelated = self.fake_process('notepad.exe')

        with patch.object(platform_windows.psutil, 'process_iter',
                          side_effect=[[mumu_player, mumu_vm, unrelated], []]):
            self.assertTrue(platform._deep_clean_mumu12('F:/mumu/shell/MuMuPlayer.exe'))

        mumu_player.kill.assert_called_once()
        mumu_vm.kill.assert_called_once()
        unrelated.kill.assert_not_called()

    def test_deep_clean_asks_mumu_to_shutdown_all_instances(self):
        platform = self.make_platform()
        platform.execute = Mock()
        with patch.object(platform_windows.psutil, 'process_iter', side_effect=[[], []]):
            platform._deep_clean_mumu12('F:/mumu/shell/MuMuPlayer.exe')

        self.assertTrue(platform.execute.called)
        self.assertIn('control -v all shutdown', platform.execute.call_args.args[0])

    def test_deep_clean_ignores_multi_open(self):
        """多开不阻止深度重启——用户明确选了「一律全杀」。"""
        platform = self.make_platform()
        platform.execute = Mock()
        multi_open = mumu_info({'is_process_started': True}, {'is_process_started': True})

        with (
            patch.object(platform_windows, 'run_mumu_manager', return_value=multi_open) as query,
            patch.object(platform_windows.psutil, 'process_iter', side_effect=[[], []]),
        ):
            platform._deep_clean_mumu12('F:/mumu/shell/MuMuPlayer.exe')

        # 关键：根本没有去查其它实例的状态
        query.assert_not_called()
        self.assertTrue(platform.execute.called)

    def test_deep_clean_gives_up_waiting_after_timeout(self):
        platform = self.make_platform()
        platform.execute = Mock()
        alive = [self.fake_process('MuMuVMMHeadless.exe')]
        with (
            patch.object(platform_windows.psutil, 'process_iter', return_value=alive),
            patch.object(platform_windows, 'MUMU12_DEEP_WAIT_TIMEOUT', 0),
        ):
            self.assertFalse(platform._deep_clean_mumu12('F:/mumu/shell/MuMuPlayer.exe'))

    def test_start_with_deep_uses_deep_clean(self):
        platform = self.make_platform()
        platform.emulator_instance = Mock()
        platform.emulator_instance.emulator.path = 'F:/mumu/shell/MuMuPlayer.exe'
        platform.emulator_instance.MuMuPlayer12_id = 0
        platform.config.EmulatorInfo_Emulator = 'MuMuPlayer12'
        platform._clean_mumu12_residue = Mock()
        platform._deep_clean_mumu12 = Mock()
        platform._mumu12_wait_stopped = Mock()

        self.assertTrue(platform.emulator_start(deep=True))

        platform._deep_clean_mumu12.assert_called_once()
        platform._clean_mumu12_residue.assert_not_called()

    def test_start_without_deep_keeps_old_cleanup(self):
        """默认仍是普通清理——深度重启必须显式开启。"""
        platform = self.make_platform()
        platform.emulator_instance = Mock()
        platform.emulator_instance.emulator.path = 'F:/mumu/shell/MuMuPlayer.exe'
        platform.emulator_instance.MuMuPlayer12_id = 0
        platform.config.EmulatorInfo_Emulator = 'MuMuPlayer12'
        platform._clean_mumu12_residue = Mock()
        platform._deep_clean_mumu12 = Mock()
        platform._mumu12_wait_stopped = Mock()

        self.assertTrue(platform.emulator_start())

        platform._clean_mumu12_residue.assert_called_once()
        platform._deep_clean_mumu12.assert_not_called()


class TestDeepFlagPortability(unittest.TestCase):
    """`deep` 必须是所有平台都能接的关键字参数。

    alas.py 调 emulator_start 时不区分平台，任何平台少了这个参数都会在
    运行时抛 TypeError。
    """

    def test_all_platforms_accept_deep(self):
        from module.device.platform.platform_base import PlatformBase
        from module.device.platform.platform_mac import PlatformMac
        from module.device.platform.platform_windows import PlatformWindows

        for cls in (PlatformBase, PlatformMac, PlatformWindows):
            with self.subTest(platform=cls.__name__):
                # inspect.signature 会自动跟随 functools.wraps 的 __wrapped__
                parameters = inspect.signature(cls.emulator_start).parameters
                self.assertIn('deep', parameters)
                self.assertIs(False, parameters['deep'].default)


class TestDeepRestartThreshold(unittest.TestCase):
    """EmulatorManagement.DeepRestartAfterFailures 的触发判定。"""

    def make_script(self, threshold, consecutive):
        script = AzurLaneAutoScript.__new__(AzurLaneAutoScript)
        script.consecutive_adb_offline = consecutive
        script.config = Mock()
        script.config.EmulatorManagement_DeepRestartAfterFailures = threshold
        return script

    def test_zero_disables_deep_restart(self):
        self.assertFalse(self.make_script(0, 99)._deep_restart_enabled())

    def test_below_threshold_keeps_normal_restart(self):
        self.assertFalse(self.make_script(3, 2)._deep_restart_enabled())

    def test_at_threshold_switches_to_deep_restart(self):
        self.assertTrue(self.make_script(3, 3)._deep_restart_enabled())

    def test_stays_deep_after_threshold(self):
        self.assertTrue(self.make_script(3, 7)._deep_restart_enabled())

    def test_invalid_config_falls_back_to_normal_restart(self):
        script = AzurLaneAutoScript.__new__(AzurLaneAutoScript)
        script.consecutive_adb_offline = 99
        script.config = Mock()  # 读出来是 Mock，int() 会抛 TypeError
        self.assertFalse(script._deep_restart_enabled())


class TestRestartTimeoutBudget(unittest.TestCase):
    def test_outer_timeout_covers_the_whole_platform_budget(self):
        """外层硬超时必须 ≥ 平台层 emulator_start() 的完整预算。

        这是本次事故的核心不变量：只要外层超时短于内层预算，超时就必然发生，
        被放弃的线程会残留下来继续操作模拟器，进而与下一轮恢复互相踩踏。
        平台侧一旦新增耗时步骤（如"等实例真正关闭"），这条断言就会失败，
        提醒同步调大外层超时。
        """
        # 每次尝试：_emulator_stop(subprocess timeout=30) + 等确认关闭 + 监视 + 再关一次
        stop_budget = 30
        per_attempt = stop_budget * 2 + MUMU12_STOP_WAIT_TIMEOUT
        platform_budget = (
            sum(EMULATOR_START_WATCH_TIMEOUTS)
            + per_attempt * len(EMULATOR_START_WATCH_TIMEOUTS)
        )

        self.assertGreaterEqual(RESTART_EMULATOR_OP_TIMEOUT, platform_budget)


class TestDeviceAutoStartBusyHandling(unittest.TestCase):
    """Device 初始化时撞上并发启停：要退化成"模拟器暂不可用"，不能停掉调度器。"""

    def test_busy_becomes_emulator_not_running(self):
        from module.device.device import Device

        with (
            patch('module.device.connection.Connection.__init__',
                  side_effect=EmulatorNotRunningError('offline')),
            patch.object(Device, 'emulator_start', side_effect=EmulatorOpBusy('busy')),
            patch.object(Device, 'emulator_instance', Mock()),
        ):
            # 关键：抛 EmulatorNotRunningError（调度器会安排 Restart 后继续），
            # 而不是 RequestHumanTakeover（那会把整个调度器停掉）
            with self.assertRaises(EmulatorNotRunningError):
                Device(Mock())


class TestRestartEmulatorBusyHandling(unittest.TestCase):
    def make_script(self):
        script = AzurLaneAutoScript.__new__(AzurLaneAutoScript)
        script.consecutive_adb_offline = 0
        script.config = Mock()
        script.config.Error_AdbOfflineThreshold = 3
        return script

    def test_gives_up_round_when_stop_is_busy(self):
        """上一轮启动仍在进行时，本轮不能去 stop——那正是打断启动的元凶。"""
        script = self.make_script()
        device = Mock()
        device.emulator_stop.side_effect = EmulatorOpBusy('busy')
        script.__dict__['device'] = device

        self.assertFalse(script._try_restart_emulator())
        device.emulator_start.assert_not_called()

    def test_gives_up_round_when_start_is_busy(self):
        script = self.make_script()
        device = Mock()
        device.emulator_start.side_effect = EmulatorOpBusy('busy')
        script.__dict__['device'] = device

        self.assertFalse(script._try_restart_emulator())
        device.emulator_stop.assert_called_once()

    def test_returns_true_and_resets_counter_when_restart_succeeds(self):
        script = self.make_script()
        script.consecutive_adb_offline = 2
        script.__dict__['device'] = Mock()

        self.assertTrue(script._try_restart_emulator())
        self.assertEqual(0, script.consecutive_adb_offline)


if __name__ == '__main__':
    unittest.main()
