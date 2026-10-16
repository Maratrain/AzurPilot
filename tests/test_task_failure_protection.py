import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from module.handler.task_failure_protection import (
    PROTECTION_EXEMPT_TASKS,
    TaskFailureTracker,
)


class TestTaskFailureTracker(unittest.TestCase):
    """任务失败保护跟踪器的单元测试"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_name = 'test_tfp'
        self.filepath = os.path.join(self.tmp.name, f'{self.config_name}.task_failure.json')
        # 保留原始 _make_filepath，供 log 目录测试恢复
        self._original_make_filepath = TaskFailureTracker._make_filepath
        # 将数据文件重定向到临时目录，避免污染仓库 log/ 目录
        patcher = patch.object(
            TaskFailureTracker, '_make_filepath', staticmethod(lambda name: self.filepath)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _tracker(self):
        return TaskFailureTracker(self.config_name)

    def _read_data(self):
        with open(self.filepath, encoding='utf-8') as f:
            return json.load(f)

    def test_file_created_under_log_dir(self):
        """数据文件应存放在 log/ 目录下，与配置文件分离"""
        # setUp 的补丁会拦截 _make_filepath，先恢复原始实现
        with patch.object(
            TaskFailureTracker,
            '_make_filepath',
            staticmethod(self._original_make_filepath),
        ):
            with tempfile.TemporaryDirectory() as cwd:
                old = os.getcwd()
                os.chdir(cwd)
                try:
                    path = TaskFailureTracker._make_filepath('alas')
                finally:
                    os.chdir(old)
        self.assertEqual(
            os.path.normpath(path),
            os.path.normpath(os.path.join(cwd, 'log', 'alas.task_failure.json')),
        )

    def test_record_failure_counts(self):
        """记录失败后累计次数递增"""
        tracker = self._tracker()
        self.assertEqual(1, tracker.record_failure('Main', 'GameStuckError'))
        self.assertEqual(2, tracker.record_failure('Main', 'GameStuckError'))
        # 不同错误原因独立计数
        self.assertEqual(1, tracker.record_failure('Main', 'GameBugError'))
        # 不同任务独立计数
        self.assertEqual(1, tracker.record_failure('Commission', 'GameStuckError'))
        # 持久化到磁盘
        data = self._read_data()
        self.assertEqual(2, len(data['failures']['Main']['GameStuckError']))
        self.assertEqual(1, len(data['failures']['Main']['GameBugError']))
        self.assertEqual(1, len(data['failures']['Commission']['GameStuckError']))

    def test_get_failure_count_respects_time_window(self):
        """超出时间窗口的失败不计入"""
        tracker = self._tracker()
        tracker.record_failure('Main', 'GameStuckError')
        # 手工注入一条 25 小时前的旧记录
        old_time = (datetime.now() - timedelta(hours=25)).strftime('%Y-%m-%dT%H:%M:%S')
        data = self._read_data()
        data['failures']['Main']['GameStuckError'].insert(0, old_time)
        with open(self.filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f)

        # 24 小时窗口只看到最近一条
        self.assertEqual(1, tracker.get_failure_count('Main', 'GameStuckError', 24))
        # 48 小时窗口能看到两条
        self.assertEqual(2, tracker.get_failure_count('Main', 'GameStuckError', 48))

    def test_should_disable_task_threshold(self):
        """达到阈值才返回 True"""
        tracker = self._tracker()
        tracker.record_failure('Main', 'GameStuckError')
        tracker.record_failure('Main', 'GameStuckError')
        self.assertFalse(
            tracker.should_disable_task('Main', 'GameStuckError', 3, 24)
        )
        tracker.record_failure('Main', 'GameStuckError')
        self.assertTrue(
            tracker.should_disable_task('Main', 'GameStuckError', 3, 24)
        )

    def test_notifications_lifecycle(self):
        """通知的添加、未读查询与已读标记"""
        tracker = self._tracker()
        tracker.add_notification('Main', 'GameStuckError', 3)
        tracker.add_notification('Commission', 'GameBugError', 3)

        unread = tracker.get_unread_notifications()
        self.assertEqual(2, len(unread))
        self.assertEqual('Main', unread[0]['task'])
        self.assertEqual(3, unread[0]['count'])
        self.assertFalse(unread[0]['read'])

        tracker.mark_notification_read('Main')
        unread = tracker.get_unread_notifications()
        self.assertEqual(1, len(unread))
        self.assertEqual('Commission', unread[0]['task'])

    def test_clear_and_reset_task(self):
        """清除失败记录与完全重置"""
        tracker = self._tracker()
        tracker.record_failure('Main', 'GameStuckError')
        tracker.add_notification('Main', 'GameStuckError', 3)

        tracker.clear_task_failures('Main')
        self.assertEqual(0, tracker.get_failure_count('Main', 'GameStuckError', 24))
        # 通知不受 clear_task_failures 影响
        self.assertEqual(1, len(tracker.get_unread_notifications()))

        tracker.reset_task('Main')
        self.assertEqual(0, len(tracker.get_unread_notifications()))

    def test_cleanup_old_failures(self):
        """过期失败记录被清理，空任务项被删除"""
        tracker = self._tracker()
        tracker.record_failure('Main', 'GameStuckError')
        # 注入过期记录
        old_time = (datetime.now() - timedelta(hours=48)).strftime('%Y-%m-%dT%H:%M:%S')
        data = self._read_data()
        data['failures']['Main']['GameStuckError'].insert(0, old_time)
        data['failures']['Main']['GameBugError'] = [old_time]  # 整条过期
        with open(self.filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f)

        tracker.cleanup_old_failures(24)
        data = self._read_data()
        # GameStuckError 只保留窗口内的一条
        self.assertEqual(1, len(data['failures']['Main']['GameStuckError']))
        # GameBugError 整条过期被删除
        self.assertNotIn('GameBugError', data['failures']['Main'])

    def test_cleanup_caps_read_notifications(self):
        """已读通知超过上限时只保留最近的部分"""
        tracker = self._tracker()
        for i in range(60):
            tracker.add_notification(f'Task{i}', 'GameStuckError', 3)
        # 全部标记已读
        for i in range(60):
            tracker.mark_notification_read(f'Task{i}')

        tracker.cleanup_old_failures(24)
        data = self._read_data()
        self.assertLessEqual(len(data['notifications']), 50)
        # 保留的是最后 50 条（Task10 ~ Task59）
        self.assertEqual('Task59', data['notifications'][-1]['task'])

    def test_corrupt_file_resets(self):
        """文件损坏时重置为空结构而不崩溃"""
        with open(self.filepath, 'w', encoding='utf-8') as f:
            f.write('{invalid json')
        tracker = self._tracker()
        self.assertEqual(0, tracker.get_failure_count('Main', 'GameStuckError', 24))
        self.assertEqual([], tracker.get_unread_notifications())

    def test_reload_absorbs_external_changes(self):
        """写前重载：WebUI 跨进程清除后不会被调度器旧副本覆盖"""
        tracker = self._tracker()
        tracker.record_failure('Main', 'GameStuckError')

        # 模拟 WebUI 进程清除失败记录（跨进程修改）
        with open(self.filepath, 'w', encoding='utf-8') as f:
            json.dump({'failures': {}, 'notifications': []}, f)

        # 调度器进程再次写操作前会先重载，不会把旧副本写回
        tracker.record_failure('Commission', 'GameBugError')
        data = self._read_data()
        self.assertNotIn('Main', data['failures'])
        self.assertIn('Commission', data['failures'])

    def test_protection_exempt_tasks(self):
        """Restart 为豁免任务"""
        self.assertIn('Restart', PROTECTION_EXEMPT_TASKS)


if __name__ == '__main__':
    unittest.main()
