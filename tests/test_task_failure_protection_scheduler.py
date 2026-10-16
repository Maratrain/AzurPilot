import unittest
from unittest.mock import Mock, patch

from alas import AzurLaneAutoScript
from module.handler.task_failure_protection import TaskFailureTracker


def _make_script(**config_attrs):
    """构造绕过 __init__ 的调度器实例，仅装配失败保护依赖"""
    script = AzurLaneAutoScript.__new__(AzurLaneAutoScript)
    script.config_name = 'test'
    script.__dict__['failure_record'] = {}
    script.__dict__['_last_task_error'] = None
    script.__dict__['_disabled_tasks_by_protection'] = set()
    script.__dict__['failure_tracker'] = Mock(spec=TaskFailureTracker)
    config = Mock()
    config.modified = {}
    for k, v in config_attrs.items():
        setattr(config, k, v)
    script.__dict__['config'] = config
    return script


class TestCheckTaskFailureProtection(unittest.TestCase):
    """调度器任务失败保护逻辑的单元测试"""

    def test_disabled_returns_false(self):
        """未启用失败保护时直接返回 False，不记录失败"""
        script = _make_script(TaskFailureProtection_Enable=False)
        self.assertFalse(script._check_task_failure_protection('Main', False))
        script.failure_tracker.record_failure.assert_not_called()

    def test_success_not_recorded(self):
        """任务成功不记录失败"""
        script = _make_script(TaskFailureProtection_Enable=True)
        self.assertFalse(script._check_task_failure_protection('Main', True))
        script.failure_tracker.record_failure.assert_not_called()

    def test_below_threshold_records_only(self):
        """未达阈值时只记录失败，不关闭任务"""
        script = _make_script(
            TaskFailureProtection_Enable=True,
            TaskFailureProtection_MaxFailures=3,
            TaskFailureProtection_TimeWindowHours=24,
        )
        script._last_task_error = 'GameStuckError'
        script.failure_tracker.should_disable_task.return_value = False
        script.failure_tracker.record_failure.return_value = 2

        self.assertFalse(script._check_task_failure_protection('Main', False))
        script.failure_tracker.record_failure.assert_called_once_with(
            'Main', 'GameStuckError'
        )
        script.config.save.assert_not_called()

    def test_threshold_disables_task(self):
        """达到阈值时自动关闭任务并生成通知"""
        script = _make_script(
            TaskFailureProtection_Enable=True,
            TaskFailureProtection_MaxFailures=3,
            TaskFailureProtection_TimeWindowHours=24,
            TaskFailureProtection_PushNotify=False,
            Error_OnePushConfig='',
        )
        script._last_task_error = 'GameStuckError'
        script.failure_tracker.should_disable_task.return_value = True
        script.failure_tracker.record_failure.return_value = 3
        script.failure_tracker.get_unread_notifications.return_value = [
            {'task': 'Main', 'reason': 'GameStuckError', 'count': 3}
        ]

        with (
            patch('alas.handle_notify') as notify,
            patch('alas.notify_webui') as webui,
            patch('alas._get_task_display_name', return_value='主线'),
        ):
            self.assertTrue(script._check_task_failure_protection('Main', False))

        # 任务被关闭：Scheduler.Enable = False 并保存配置
        self.assertFalse(script.config.modified['Main.Scheduler.Enable'])
        script.config.save.assert_called_once()
        # 加入运行时禁用列表
        self.assertIn('Main', script._disabled_tasks_by_protection)
        # 生成通知
        script.failure_tracker.add_notification.assert_called_once_with(
            'Main', 'GameStuckError', 3
        )
        # 重置调度器失败计数
        self.assertEqual(0, script.failure_record['Main'])
        # WebUI 通知已发送（PushNotify 关闭时不走 OnePush）
        webui.assert_called_once()
        notify.assert_not_called()

    def test_threshold_with_push_notify(self):
        """开启 PushNotify 时同时发送 OnePush 推送"""
        script = _make_script(
            TaskFailureProtection_Enable=True,
            TaskFailureProtection_MaxFailures=3,
            TaskFailureProtection_TimeWindowHours=24,
            TaskFailureProtection_PushNotify=True,
            Error_OnePushConfig='provider: null',
        )
        script._last_task_error = 'GameBugError'
        script.failure_tracker.should_disable_task.return_value = True
        script.failure_tracker.record_failure.return_value = 3
        script.failure_tracker.get_unread_notifications.return_value = [
            {'task': 'Main', 'reason': 'GameBugError', 'count': 3}
        ]

        with (
            patch('alas.handle_notify') as notify,
            patch('alas.notify_webui'),
            patch('alas._get_task_display_name', return_value='主线'),
        ):
            self.assertTrue(script._check_task_failure_protection('Main', False))

        notify.assert_called_once()
        self.assertIn('任务已自动关闭', notify.call_args.kwargs['title'])

    def test_restart_task_exempt(self):
        """Restart 为豁免任务，达到阈值也不会被自动关闭"""
        script = _make_script(
            TaskFailureProtection_Enable=True,
            TaskFailureProtection_MaxFailures=3,
            TaskFailureProtection_TimeWindowHours=24,
            TaskFailureProtection_PushNotify=False,
            Error_OnePushConfig='',
        )
        script._last_task_error = 'EmulatorNotRunningError'
        script.failure_tracker.record_failure.return_value = 5

        self.assertFalse(script._check_task_failure_protection('Restart', False))
        # 记录了失败但未关闭任务
        script.failure_tracker.record_failure.assert_called_once_with(
            'Restart', 'EmulatorNotRunningError'
        )
        script.config.save.assert_not_called()
        self.assertNotIn('Restart', script._disabled_tasks_by_protection)
        script.failure_tracker.add_notification.assert_not_called()


if __name__ == '__main__':
    unittest.main()
