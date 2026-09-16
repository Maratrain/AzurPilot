"""验证掉落记录截图的保留天数清理策略。

只跑纯文件系统逻辑：临时目录里造出截图文件并改写修改时间，
`DropRecord_SaveFolder` 与委托收益截图目录都指向临时目录，
不依赖模拟器、也不碰真实截图。
"""

import os
import shutil
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.statistics import drop_cleanup


class DropCleanupTestCase(unittest.TestCase):
    """准备临时目录，并隔离模块级节流缓存与截图目录常量。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='drop_cleanup_test_')
        self.save_folder = os.path.join(self.root, 'screenshots')
        self.commission_folder = os.path.join(self.root, 'commission_rewards')
        os.makedirs(self.save_folder)
        os.makedirs(self.commission_folder)

        self._saved_cleanup = drop_cleanup._LAST_CLEANUP
        # 避免测试过程中真的去扫日志目录
        drop_cleanup._LAST_CLEANUP = float('inf')
        self._patch = patch.object(
            drop_cleanup, 'COMMISSION_REWARD_FOLDER', self.commission_folder)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        drop_cleanup._LAST_CLEANUP = self._saved_cleanup
        shutil.rmtree(self.root, ignore_errors=True)

    def config(self, days=0, instance='alas', folder=None):
        """构造只带掉落记录相关字段的配置对象。"""
        return SimpleNamespace(
            DropRecord_SaveFolder=self.save_folder if folder is None else folder,
            DropRecord_RetentionDays=days,
            config_name=instance,
        )

    def make_file(self, path, age_seconds):
        """按给定年龄写出文件，返回其路径。"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(b'x')
        mtime = time.time() - age_seconds
        os.utime(path, (mtime, mtime))
        return path

    def drop_image(self, name, age_seconds):
        """造一个掉落截图：文件名是 13 位毫秒时间戳。"""
        return self.make_file(
            os.path.join(self.save_folder, 'commission', name), age_seconds)

    def reward_image(self, name, age_seconds, instance='alas'):
        """造一个委托收益截图：文件名是保存时间戳，落在月份目录下。"""
        return self.make_file(
            os.path.join(
                self.commission_folder, instance, '2020-01', name),
            age_seconds)


class TestRetentionSetting(DropCleanupTestCase):
    def test_days_is_read_from_config(self):
        self.assertEqual(
            drop_cleanup.drop_screenshot_retention_days(
                self.config(7)), 7)

    def test_numeric_string_is_accepted(self):
        self.assertEqual(
            drop_cleanup.drop_screenshot_retention_days(
                self.config('30')), 30)

    def test_missing_setting_keeps_everything(self):
        # 老配置里没有这一项时按「不清理」处理
        self.assertEqual(
            drop_cleanup.drop_screenshot_retention_days(SimpleNamespace()), 0)

    def test_invalid_setting_keeps_everything(self):
        self.assertEqual(
            drop_cleanup.drop_screenshot_retention_days(
                self.config('abc')), 0)


class TestCleanupDropScreenshots(DropCleanupTestCase):
    def test_zero_retention_keeps_screenshots(self):
        old = self.drop_image('1704067200000.png', 400 * 86400)
        self.assertEqual(
            drop_cleanup.cleanup_drop_screenshots(self.config(0), 0), 0)
        self.assertTrue(os.path.exists(old))

    def test_removes_expired_drop_screenshots(self):
        old = self.drop_image('1704067200000.png', 8 * 86400)
        old_with_info = self.drop_image('1704067200001_13-4.png', 8 * 86400)
        fresh = self.drop_image('1799999999999.png', 60)

        self.assertEqual(
            drop_cleanup.cleanup_drop_screenshots(self.config(7), 7), 2)
        self.assertFalse(os.path.exists(old))
        self.assertFalse(os.path.exists(old_with_info))
        self.assertTrue(os.path.exists(fresh))

    def test_keeps_files_that_are_not_ours(self):
        """模板、说明文件等非掉落截图不能被误删。"""
        template = self.make_file(
            os.path.join(self.save_folder, 'item_templates', '主炮.png'),
            400 * 86400)
        note = self.make_file(
            os.path.join(self.save_folder, 'readme.txt'), 400 * 86400)
        # 名字像截图但扩展名不对的也要留着
        other = self.make_file(
            os.path.join(self.save_folder, 'commission', '1704067200000.txt'),
            400 * 86400)

        self.assertEqual(
            drop_cleanup.cleanup_drop_screenshots(self.config(1), 1), 0)
        self.assertTrue(os.path.exists(template))
        self.assertTrue(os.path.exists(note))
        self.assertTrue(os.path.exists(other))

    def test_only_current_instance_rewards_are_cleaned(self):
        mine = self.reward_image('20200101_000000_000000_0.png', 8 * 86400)
        others = self.reward_image(
            '20200101_000000_000000_0.png', 8 * 86400, instance='other')

        self.assertEqual(
            drop_cleanup.cleanup_drop_screenshots(self.config(7), 7), 1)
        self.assertFalse(os.path.exists(mine))
        # 另一个实例的保留天数可能不同，交给它自己清理
        self.assertTrue(os.path.exists(others))

    def test_empty_month_folder_is_removed(self):
        self.reward_image('20200101_000000_000000_0.png', 8 * 86400)
        month = os.path.join(self.commission_folder, 'alas', '2020-01')
        self.assertTrue(os.path.isdir(month))

        drop_cleanup.cleanup_drop_screenshots(self.config(7), 7)
        self.assertFalse(os.path.exists(month))
        # 实例根目录要留着，下次保存还要往里写
        self.assertTrue(
            os.path.isdir(os.path.join(self.commission_folder, 'alas')))

    def test_missing_directories_are_safe(self):
        config = self.config(
            7, folder=os.path.join(self.root, 'not_created_yet'))
        self.assertEqual(drop_cleanup.cleanup_drop_screenshots(config, 7), 0)

    def test_missing_instance_name_is_safe(self):
        config = self.config(7)
        del config.config_name
        self.assertEqual(drop_cleanup.cleanup_drop_screenshots(config, 7), 0)


class TestCleanupIfDue(DropCleanupTestCase):
    def setUp(self):
        super().setUp()
        drop_cleanup._LAST_CLEANUP = 0.0  # 让首次调用一定执行清理

    def test_uses_configured_retention_days(self):
        old = self.drop_image('1704067200000.png', 3 * 86400)
        self.assertEqual(
            drop_cleanup.cleanup_drop_screenshots_if_due(self.config(1)), 1)
        self.assertFalse(os.path.exists(old))

    def test_disabled_setting_does_not_scan(self):
        kept = self.drop_image('1704067200000.png', 400 * 86400)
        self.assertEqual(
            drop_cleanup.cleanup_drop_screenshots_if_due(self.config(0)), 0)
        self.assertTrue(os.path.exists(kept))

    def test_second_call_is_throttled(self):
        self.drop_image('1704067200000.png', 3 * 86400)
        self.assertEqual(
            drop_cleanup.cleanup_drop_screenshots_if_due(self.config(1)), 1)

        still_there = self.drop_image('1704067200001.png', 3 * 86400)
        # 节流期内不再扫描，文件仍然留着
        self.assertEqual(
            drop_cleanup.cleanup_drop_screenshots_if_due(self.config(1)), 0)
        self.assertTrue(os.path.exists(still_there))

    def test_invalid_setting_does_not_delete(self):
        kept = self.drop_image('1704067200000.png', 400 * 86400)
        self.assertEqual(
            drop_cleanup.cleanup_drop_screenshots_if_due(self.config('abc')), 0)
        self.assertTrue(os.path.exists(kept))


class TestDropRecordWiring(unittest.TestCase):
    """掉落记录提交时会顺带触发清理。"""

    def test_new_triggers_cleanup(self):
        from module.statistics import azurstats

        stat = azurstats.AzurStats(
            config=SimpleNamespace(DropRecord_RetentionDays=7))
        with patch.object(
            azurstats, 'cleanup_drop_screenshots_if_due'
        ) as cleanup:
            stat.new('commission', method='do_not')

        cleanup.assert_called_once_with(stat.config)


class TestCommissionScreenshotSwitch(unittest.TestCase):
    """委托收益截图开关与保留天数的联动（直接驱动 RewardCommission 的方法）。"""

    def make_commission(self, method, retention=0):
        return SimpleNamespace(
            config=SimpleNamespace(
                DropRecord_CommissionIncomeScreenshot=method,
                DropRecord_RetentionDays=retention,
            ),
            # 张数上限的裁剪方法，用假对象记录是否被调用
            _prune_commission_reward_screenshots=Mock(),
        )

    def save(self, fake):
        """调用保存方法（重定向掉落盘）：返回路径列表与 save_image 的假实现。"""
        from module.commission.commission import RewardCommission

        with patch('os.makedirs'), \
                patch('module.commission.commission.save_image') as save:
            paths = RewardCommission._save_commission_reward_screenshots(
                fake, [object()], 'alas')
        return paths, save

    def test_disabled_switch_skips_saving(self):
        fake = self.make_commission('do_not')
        paths, save = self.save(fake)

        self.assertEqual(paths, [])
        save.assert_not_called()
        fake._prune_commission_reward_screenshots.assert_not_called()

    def test_retention_days_replaces_count_cap(self):
        fake = self.make_commission('save', retention=7)
        paths, _ = self.save(fake)

        self.assertEqual(len(paths), 1)
        fake._prune_commission_reward_screenshots.assert_not_called()

    def test_default_keeps_count_cap(self):
        fake = self.make_commission('save', retention=0)
        self.save(fake)

        fake._prune_commission_reward_screenshots.assert_called_once_with('alas')


if __name__ == '__main__':
    unittest.main()
