"""长等待关闭模拟器：可配置阈值的回归测试。

背景：该功能原来写死在 alas.py 里（`wait_duration > timedelta(hours=3)`），
GUI 上只有一个开关，用户既改不了 3 小时，也找不到参数在哪。

本地改造（不迁移配置组，保留在 Alas 任务的 Optimization 组内）：

1. 阈值改为可配置的分钟数 `Optimization.CloseEmulatorLongWaitMinutes`
   （默认 180 分钟，升级前后行为一致）。
2. 新增维护关模拟器开关 `Optimization.CloseEmulatorDuringMaintenance`
   （默认关闭），由 test_maintenance_close_emulator.py 单独覆盖。

本测试锁定三件事：

1. 新配置项默认值正确，存量配置合并后用户的选择不丢（false 仍是 false）
2. 阈值读取的兜底：脏值一律回落到 180 分钟，避免关得太勤或永不关
3. alas.py 不再出现硬编码的 3 小时（源码锁，防止改回去）
"""

import inspect
import unittest
from unittest.mock import Mock

from alas import AzurLaneAutoScript

SWITCH = 'Alas.Optimization.CloseEmulatorDuringLongWait'
MINUTES = 'Alas.Optimization.CloseEmulatorLongWaitMinutes'


class TestDefaults(unittest.TestCase):
    """新配置项的模板默认值。"""

    def test_default_values(self):
        from module.config.config_generated import GeneratedConfig

        self.assertTrue(GeneratedConfig.Optimization_CloseEmulatorDuringLongWait)
        self.assertEqual(GeneratedConfig.Optimization_CloseEmulatorLongWaitMinutes, 180)
        self.assertFalse(GeneratedConfig.Optimization_CloseEmulatorDuringMaintenance)


class TestConfigPersistence(unittest.TestCase):
    """存量配置合并后用户的选择不丢。"""

    def setUp(self):
        from module.config.config_updater import ConfigUpdater
        from module.config.deep import deep_get

        self.cu = ConfigUpdater()
        self.deep_get = deep_get

    def _merge(self, old):
        """模拟配置加载流程：先合并默认值，再执行版本迁移。"""
        new = self.cu.config_update(old)
        return self.cu.config_redirect(old, new)

    def test_user_without_key_uses_default(self):
        """从未存过此键的存量用户，升级后取模板默认值。"""
        out = self._merge({'Alas': {'Optimization': {'WhenTaskQueueEmpty': 'goto_main'}}})
        self.assertEqual(self.deep_get(out, keys=MINUTES), 180)

    def test_user_with_true_keeps_true(self):
        """显式存了 true 的用户保持 true。"""
        out = self._merge({'Alas': {'Optimization': {'CloseEmulatorDuringLongWait': True}}})
        self.assertTrue(self.deep_get(out, keys=SWITCH))

    def test_user_with_false_respects_choice(self):
        """显式关掉的用户，合并后仍然是关闭——否则等于替他重新打开。"""
        out = self._merge({'Alas': {'Optimization': {'CloseEmulatorDuringLongWait': False}}})
        self.assertFalse(self.deep_get(out, keys=SWITCH))


class TestThresholdMinutes(unittest.TestCase):
    """阈值读取的兜底行为。"""

    def make_script(self, minutes):
        script = AzurLaneAutoScript.__new__(AzurLaneAutoScript)
        script.config = Mock()
        script.config.Optimization_CloseEmulatorLongWaitMinutes = minutes
        return script

    def test_reads_config_value(self):
        """正常数值直接用。"""
        self.assertEqual(self.make_script(45)._long_wait_close_emulator_minutes(), 45)
        self.assertEqual(self.make_script(180)._long_wait_close_emulator_minutes(), 180)
        self.assertEqual(self.make_script(10080)._long_wait_close_emulator_minutes(), 10080)

    def test_non_positive_falls_back(self):
        """0 和负数会变成"立刻关闭"，必须回落到默认 180。"""
        for bad in (0, -1):
            self.assertEqual(self.make_script(bad)._long_wait_close_emulator_minutes(), 180)

    def test_dirty_value_falls_back(self):
        """非数字（手改 JSON 写坏）回落到默认 180，不抛异常。"""
        for bad in ('abc', None, '', []):
            self.assertEqual(self.make_script(bad)._long_wait_close_emulator_minutes(), 180)

    def test_numeric_string_accepted(self):
        """JSON 里写成字符串的数字仍能识别，避免误回落到 180。"""
        self.assertEqual(self.make_script('45')._long_wait_close_emulator_minutes(), 45)


class TestNoHardcodedHours(unittest.TestCase):
    """源码锁：不允许退回硬编码 3 小时。"""

    def setUp(self):
        import alas as alas_module

        self.source = inspect.getsource(alas_module.AzurLaneAutoScript.get_next_task)

    def test_uses_configurable_minutes(self):
        self.assertIn('_long_wait_close_emulator_minutes()', self.source)

    def test_no_hardcoded_three_hours(self):
        self.assertNotIn(
            'timedelta(hours=3)',
            self.source,
            '阈值已改为可配置分钟数，不得再硬编码 3 小时',
        )

    def test_switch_kept_in_optimization_group(self):
        """本地不迁移配置组：长等待开关仍从 Optimization_* 属性读取。"""
        self.assertIn('Optimization_CloseEmulatorDuringLongWait', self.source)


if __name__ == '__main__':
    unittest.main()
