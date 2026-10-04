"""验证 save() 从磁盘重读配置后 datetime 类型不退化。

save() 会重读磁盘上的配置文件以避免覆盖 WebUI 的修改，而磁盘上的
Scheduler.NextRun / Record 等字段是 ISO 字符串。若重读结果不经过
config_update 类型还原，self.data 中的 datetime 会退化为 str，
后续 cross_get 或重新 bind 的调用方拿 str 与 datetime 比较就会抛
TypeError（大世界行动力推送、侵蚀1练级均因此崩溃过）。
"""

import os
import unittest
from datetime import datetime, timedelta

from module.config.config import AzurLaneConfig
from module.config.utils import filepath_config


CONFIG_NAME = 'test_save_datetime'


class TestConfigSaveKeepsDatetime(unittest.TestCase):
    def setUp(self):
        self.path = filepath_config(CONFIG_NAME)
        self.addCleanup(self._remove_config_file)
        self.config = AzurLaneConfig(CONFIG_NAME)

    def _remove_config_file(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_save_keeps_datetime_types_after_disk_reread(self):
        """保存重读磁盘后，cross_get 与重新 bind 读到的 NextRun 仍是 datetime。"""
        next_run = datetime(2026, 11, 1, 0, 0)

        # 第一次保存：写入 NextRun，落盘后成为 ISO 字符串
        # （直接写 modified 以绕开任务绑定，与生产中 multi_set 等路径一致）
        self.config.modified['OpsiExplore.Scheduler.NextRun'] = next_run
        self.config.save()

        # 第二次保存：修改其他任务的字段，触发 save() 重读磁盘
        self.config.modified['Restart.Scheduler.Enable'] = True
        self.config.save()

        # 重读后内存中的值必须保持 datetime，而不是磁盘上的字符串
        value = self.config.cross_get('OpsiExplore.Scheduler.NextRun')
        self.assertIsInstance(value, datetime)
        self.assertEqual(value, next_run)

        # 重新绑定其他任务（崩溃现场 _run_with_opsi_task_context 的用法）后同样保持。
        # bind 绑定的属性名是短名 Scheduler_NextRun（General/Alas/OpsiGeneral 无 Scheduler 组）
        self.config.bind('OpsiExplore')
        bound_value = self.config.Scheduler_NextRun
        self.assertIsInstance(bound_value, datetime)
        self.assertEqual(bound_value, next_run)

        # 崩溃现场的表达式：str 与 datetime 比较/运算不得抛 TypeError
        self.assertLess(bound_value, next_run + timedelta(hours=12))


if __name__ == '__main__':
    unittest.main()
