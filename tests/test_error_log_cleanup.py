"""验证错误日志现场按天数过期，并按所选方式处理。

只跑纯文件系统逻辑：临时目录里造出现场目录并改写修改时间，
不构造完整调度器、不碰真实日志目录。
"""

import os
import shutil
import tempfile
import time
import unittest
import zipfile
from types import SimpleNamespace

from alas import AzurLaneAutoScript
from module.base import archive


def make_error_folder(root, name, age_seconds, files=('log.txt', '1.png')):
    """在 root 下造一个现场目录，并按年龄改写其修改时间。"""
    folder = os.path.join(root, name)
    os.makedirs(folder, exist_ok=True)
    for file in files:
        with open(os.path.join(folder, file), 'w', encoding='utf-8') as f:
            f.write('x')
    mtime = time.time() - age_seconds
    os.utime(folder, (mtime, mtime))
    return folder


class ErrorLogCleanupTestCase(unittest.TestCase):
    """准备临时错误日志根目录，并隔离出清理方法的调用入口。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='error_log_test_')
        self.config_folder = os.path.join(self.root, 'alas')
        os.makedirs(self.config_folder)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def config(self, days=30, method='zip', zip_method='zip'):
        """构造只带错误日志清理相关字段的配置对象。"""
        return SimpleNamespace(
            Error_SaveErrorRetentionDays=days,
            Error_SaveErrorBackUpMethod=method,
            Error_SaveErrorZipMethod=zip_method,
        )

    def cleanup(self, config):
        """绕过 __init__ 直接调用清理方法（与既有 alas 测试同一手法）。"""
        script = AzurLaneAutoScript.__new__(AzurLaneAutoScript)
        script.config_name = 'alas'
        script.__dict__['config'] = config
        return script.cleanup_error_logs(self.config_folder)

    def bak_files(self):
        bak = os.path.join(self.config_folder, 'bak')
        return sorted(os.listdir(bak)) if os.path.isdir(bak) else []


class TestErrorLogCleanup(ErrorLogCleanupTestCase):
    def test_expired_folders_are_archived(self):
        old = make_error_folder(self.config_folder, '1704067200000', 40 * 86400)
        fresh = make_error_folder(self.config_folder, '1799999999999', 60)

        self.assertEqual(self.cleanup(self.config()), 1)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(fresh))

        names = self.bak_files()
        self.assertEqual(len(names), 1)
        self.assertTrue(names[0].endswith('_alas.zip'), names[0])
        with zipfile.ZipFile(
                os.path.join(self.config_folder, 'bak', names[0])) as z:
            self.assertEqual(
                sorted(z.namelist()), ['1704067200000/1.png', '1704067200000/log.txt'])

    def test_nothing_expired_is_noop(self):
        fresh = make_error_folder(self.config_folder, '1799999999999', 60)

        self.assertEqual(self.cleanup(self.config()), 0)
        self.assertTrue(os.path.exists(fresh))
        self.assertEqual(self.bak_files(), [])

    def test_zero_days_keeps_everything(self):
        old = make_error_folder(self.config_folder, '1704067200000', 400 * 86400)

        self.assertEqual(self.cleanup(self.config(days=0)), 0)
        self.assertTrue(os.path.exists(old))
        self.assertEqual(self.bak_files(), [])

    def test_delete_method_removes_without_backup(self):
        old = make_error_folder(self.config_folder, '1704067200000', 40 * 86400)

        self.assertEqual(self.cleanup(self.config(method='delete')), 1)
        self.assertFalse(os.path.exists(old))
        self.assertFalse(os.path.exists(os.path.join(self.config_folder, 'bak')))

    def test_copy_method_keeps_folder_copy(self):
        old = make_error_folder(self.config_folder, '1704067200000', 40 * 86400)

        self.assertEqual(self.cleanup(self.config(method='copy')), 1)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.isfile(os.path.join(
            self.config_folder, 'bak', '1704067200000', 'log.txt')))

    def test_backup_folder_is_untouched(self):
        """bak 里的备份不能被当成过期现场重复处理。"""
        bak = os.path.join(self.config_folder, 'bak')
        os.makedirs(bak)
        kept = make_error_folder(bak, '1704067200000', 400 * 86400)

        self.assertEqual(self.cleanup(self.config(days=1)), 0)
        self.assertTrue(os.path.exists(kept))

    def test_files_in_root_are_ignored(self):
        """根目录下的文件不是现场目录，不参与清理。"""
        note = os.path.join(self.config_folder, 'readme.txt')
        with open(note, 'w', encoding='utf-8') as f:
            f.write('x')
        old = time.time() - 400 * 86400
        os.utime(note, (old, old))

        self.assertEqual(self.cleanup(self.config(days=1)), 0)
        self.assertTrue(os.path.exists(note))

    def test_missing_folder_is_safe(self):
        script = AzurLaneAutoScript.__new__(AzurLaneAutoScript)
        script.config_name = 'alas'
        script.__dict__['config'] = self.config()

        missing = os.path.join(self.root, 'not_created_yet')
        self.assertEqual(script.cleanup_error_logs(missing), 0)

    def test_invalid_config_values_fall_back(self):
        """天数非法按不清理，处理方式非法按压缩备份。"""
        old = make_error_folder(self.config_folder, '1704067200000', 40 * 86400)

        self.assertEqual(self.cleanup(self.config(days='abc')), 0)
        self.assertTrue(os.path.exists(old))

        self.assertEqual(
            self.cleanup(self.config(method='shred', zip_method='rar')), 1)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(self.bak_files()[0].endswith('.zip'))


if __name__ == '__main__':
    unittest.main()
