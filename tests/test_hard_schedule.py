"""困难图每日调度的时间边界与旧配置兼容性。

移植自上游 wess09/AzurPilot 提交 c0b4de1b4
（feat(hard): 支持可选的每日18点运行 #1303）的 tests/test_hard_schedule.py，
按本地测试惯例改写：

- 上游断言 `config.modified['Hard.Scheduler.NextRun']`，但本地 task_delay
  会落盘并清空 modified，无法沿用；改为按本地惯例 mock config，
  断言 task_delay / task_stop 的调用参数（同 tests/test_gacha_ticket.py 写法）；
- `'18:00'` 字符串到具体服务器时间的换算由 get_server_next_update 的
  既有用例覆盖，此处不重复验证时区运算；
- 配置默认值与翻译保留上游的文件级断言（只读 args.json / template.json / i18n）。
"""

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.config.config import AzurLaneConfig, TaskEnd
from module.config.utils import read_file
from module.hard.hard import CampaignHard


class HardScheduleTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 22, 12)
        self.offset = timedelta()
        for target, value in (
            ('module.hard.hard.current_time', lambda: self.now),
            ('module.hard.hard.server_time_offset', lambda: self.offset),
        ):
            self.enterContext(patch(target, side_effect=value))
        self.enterContext(patch('module.hard.hard.logger'))
        self.ocr = self.enterContext(patch('module.hard.hard.OCR_HARD_REMAIN'))

    def runner(self, run_at18=False):
        config = SimpleNamespace(
            Hard_RunAt18=run_at18,
            Hard_HardStage='11-4',
            Hard_HardFleet=1,
            override=Mock(),
            task_delay=Mock(),
            task_stop=Mock(side_effect=TaskEnd),
            task_call=Mock(),
        )
        runner = object.__new__(CampaignHard)
        runner.config = config
        runner.device = Mock()
        runner.campaign = Mock()
        runner.load_campaign = Mock()
        # 模拟每次出击用时
        runner.campaign.run.side_effect = lambda: setattr(self, 'now', self.now + timedelta(seconds=1))
        return runner

    def test_config_default_and_saved_choice(self):
        config = AzurLaneConfig('template')
        config.data = config.config_update({'Hard': {'Hard': {}}})
        config.bind('Hard')
        self.assertFalse(config.Hard_RunAt18)
        definition = read_file('module/config/argument/args.json')['Hard']['Hard']['RunAt18']
        self.assertEqual(definition, {'type': 'checkbox', 'value': False})
        self.assertFalse(read_file('config/template.json')['Hard']['Hard']['RunAt18'])
        config = AzurLaneConfig('template')
        config.data = config.config_update({'Hard': {'Hard': {'RunAt18': True}}})
        config.bind('Hard')
        self.assertTrue(config.Hard_RunAt18)
        for lang in ('zh-CN', 'zh-MIAO', 'en-US', 'ja-JP', 'zh-TW'):
            translation = read_file(f'module/config/i18n/{lang}.json')['Hard']['RunAt18']
            self.assertTrue(all(value and not value.startswith('Hard.RunAt18.') for value in translation.values()))

    def test_before_18_defers_without_game_access(self):
        for server_now in (datetime(2026, 10, 22), datetime(2026, 10, 22, 17, 59, 59)):
            for hours in (0, -1, 16):
                with self.subTest(server_now=server_now, offset=hours):
                    self.offset = timedelta(hours=hours)
                    self.now = server_now + self.offset
                    runner = self.runner(run_at18=True)
                    with self.assertRaises(TaskEnd):
                        runner.run()
                    runner.config.task_delay.assert_called_once_with(server_update='18:00')
                    runner.config.task_stop.assert_called_once_with()
                    runner.load_campaign.assert_not_called()
                    self.assertEqual(runner.device.mock_calls, [])
                    self.assertEqual(runner.campaign.mock_calls, [])
                    runner.config.task_call.assert_not_called()

    def test_at_or_after_18_runs_and_schedules_next_evening(self):
        for hour in (18, 23):
            for hours in (0, -1, 16):
                with self.subTest(hour=hour, offset=hours):
                    self.offset = timedelta(hours=hours)
                    self.now = datetime(2026, 10, 22, hour) + self.offset
                    self.ocr.ocr.return_value = 3
                    runner = self.runner(run_at18=True)
                    runner.run()
                    self.assertEqual(runner.campaign.run.call_count, 3)
                    runner.campaign.ensure_auto_search_exit.assert_called_once_with()
                    runner.config.task_delay.assert_called_once_with(server_update='18:00')
                    runner.config.task_call.assert_called_once_with('Reward', force_call=False)

    def test_default_still_runs_before_18_and_schedules_midnight(self):
        self.ocr.ocr.return_value = 3
        runner = self.runner()
        runner.run()
        self.assertEqual(runner.campaign.run.call_count, 3)
        runner.config.task_delay.assert_called_once_with(server_update=True)
        runner.config.task_call.assert_called_once_with('Reward', force_call=False)

    def test_no_remaining_attempts_still_schedules_next_evening(self):
        self.now = datetime(2026, 10, 22, 18, 1)
        self.ocr.ocr.return_value = 0
        runner = self.runner(run_at18=True)
        runner.run()
        runner.campaign.run.assert_not_called()
        runner.config.task_delay.assert_called_once_with(server_update='18:00')


if __name__ == '__main__':
    unittest.main()
