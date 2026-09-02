"""余烬信标收集状态与信标任务调用确认状态的单元测试。

覆盖两组行为约定：
- ash_collect_status 的返回值与 _ash_fully_collected 标记：
  每日收集满/持有满时仍返回真实持有量（供调用判定使用），
  同时置位标记供侵蚀 1、耄耋相接判断"信标数据已收集满"。
- 信标任务空转后的确认状态：
  OpsiAshBeacon 空转（无可攻击信标）结束时把本次调用时的持有量
  读数与日期持久化；同一天内相同读数不再重复调用，防止持有量
  OCR 误读（如 70 被读成 170）造成每轮空转打断调度任务的死循环。
  日期或读数变化后状态自动失效，不影响正常召唤流程。
"""

import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from module.config.time_source import now as current_time
from module.os_ash.ash import CONFIG_PATH_ASH_NOTHING_TO_DO, OSAsh


class _AshStatusStub:
    """仅提供 ash_collect_status 所需接口的最小桩对象。"""

    _ash_fully_collected = False
    ash_collect_status = OSAsh.ash_collect_status
    device = SimpleNamespace(image=None)

    @staticmethod
    def image_color_count(*args, **kwargs):
        return True


class _FakeOcr:
    """代替 DigitCounter/DailyDigitCounter 的固定值 OCR 桩。"""

    def __init__(self, result):
        self._result = result

    def ocr(self, image):
        return self._result


def _ocr_patches(collect, daily):
    return (
        patch('module.os_ash.ash.DigitCounter', return_value=_FakeOcr(collect)),
        patch('module.os_ash.ash.DailyDigitCounter', return_value=_FakeOcr(daily)),
    )


class TestAshCollectStatus(unittest.TestCase):
    def test_daily_full_sets_flag_and_returns_holding(self):
        """今日数据 200/200 时置位标记，但仍返回真实持有量。"""
        stub = _AshStatusStub()
        collect_patch, daily_patch = _ocr_patches((170, 30, 200), (200, 0, 200))
        with collect_patch, daily_patch:
            status = stub.ash_collect_status()

        self.assertEqual(status, 170)
        self.assertTrue(stub._ash_fully_collected)

    def test_reach_hold_limit_sets_flag_and_returns_holding(self):
        """持有量达到 200 上限时置位标记，但仍返回真实持有量。"""
        stub = _AshStatusStub()
        collect_patch, daily_patch = _ocr_patches((200, 0, 200), (100, 100, 200))
        with collect_patch, daily_patch:
            status = stub.ash_collect_status()

        self.assertEqual(status, 200)
        self.assertTrue(stub._ash_fully_collected)

    def test_returns_progress_when_not_full(self):
        """未收集满时正常返回收集进度。"""
        stub = _AshStatusStub()
        collect_patch, daily_patch = _ocr_patches((170, 30, 200), (100, 100, 200))
        with collect_patch, daily_patch:
            status = stub.ash_collect_status()

        self.assertEqual(status, 170)
        self.assertFalse(stub._ash_fully_collected)

    def test_cached_full_returns_zero_before_ocr(self):
        """已标记收集满后直接返回 0，不再重复 OCR。"""
        stub = _AshStatusStub()
        stub._ash_fully_collected = True
        collect_patch, daily_patch = _ocr_patches((170, 30, 200), (200, 0, 200))
        with collect_patch as mock_collect, daily_patch:
            status = stub.ash_collect_status()

        self.assertEqual(status, 0)
        mock_collect.assert_not_called()


class _AttackConfig:
    """handle_ash_beacon_attack 所需的最小配置桩。"""

    def __init__(self, next_run=None, nothing_to_do=None):
        self._next_run = next_run if next_run is not None else current_time() + timedelta(hours=8)
        self._nothing_to_do = nothing_to_do
        self.modified = {}
        self.save_count = 0
        self.task_call_args = []
        self._ash_beacon_call_value = None

    def is_task_enabled(self, task):
        return True

    def cross_get(self, keys, default=None):
        if keys == 'OpsiAshBeacon.Scheduler.NextRun':
            return self._next_run
        if keys == CONFIG_PATH_ASH_NOTHING_TO_DO:
            return self._nothing_to_do
        return default

    def save(self):
        self.save_count += 1

    def task_call(self, task=None, force_call=True):
        self.task_call_args.append(task)


class _AttackStub:
    """仅提供 handle_ash_beacon_attack 调用链所需接口的最小桩对象。"""

    ash_collect_status = lambda self: 170
    handle_ash_beacon_attack = OSAsh.handle_ash_beacon_attack
    _support_call_ash_beacon_task = OSAsh._support_call_ash_beacon_task
    _ash_beacon_confirmed_nothing_to_do = OSAsh._ash_beacon_confirmed_nothing_to_do

    def __init__(self, config):
        self.config = config


class TestHandleAshBeaconAttack(unittest.TestCase):
    def test_calls_task_and_records_call_value(self):
        """无确认状态时正常调用，并记录本次读数供任务写入确认状态。"""
        config = _AttackConfig()
        stub = _AttackStub(config)

        self.assertTrue(stub.handle_ash_beacon_attack())
        self.assertEqual(config.task_call_args, ['OpsiAshBeacon'])
        self.assertEqual(config._ash_beacon_call_value, 170)

    def test_skips_when_confirmed_today_with_same_value(self):
        """今天已用相同读数确认过无可做内容时跳过调用。"""
        today = current_time().strftime('%Y-%m-%d')
        config = _AttackConfig(nothing_to_do={'NothingToDoDate': today, 'NothingToDoValue': 170})
        stub = _AttackStub(config)

        self.assertFalse(stub.handle_ash_beacon_attack())
        self.assertEqual(config.task_call_args, [])

    def test_calls_when_value_changed_today(self):
        """同一天读数变化（新数据或误读消失）时恢复调用。"""
        today = current_time().strftime('%Y-%m-%d')
        config = _AttackConfig(nothing_to_do={'NothingToDoDate': today, 'NothingToDoValue': 160})
        stub = _AttackStub(config)

        self.assertTrue(stub.handle_ash_beacon_attack())
        self.assertEqual(config.task_call_args, ['OpsiAshBeacon'])

    def test_calls_when_date_changed(self):
        """跨天后确认状态自动失效。"""
        yesterday = (current_time() - timedelta(days=1)).strftime('%Y-%m-%d')
        config = _AttackConfig(nothing_to_do={'NothingToDoDate': yesterday, 'NothingToDoValue': 170})
        stub = _AttackStub(config)

        self.assertTrue(stub.handle_ash_beacon_attack())
        self.assertEqual(config.task_call_args, ['OpsiAshBeacon'])

    def test_skips_when_state_invalid(self):
        """持久化状态不是 dict 时视为无状态，正常调用。"""
        config = _AttackConfig(nothing_to_do='invalid')
        stub = _AttackStub(config)

        self.assertTrue(stub.handle_ash_beacon_attack())
        self.assertEqual(config.task_call_args, ['OpsiAshBeacon'])

    def test_skips_when_next_run_is_close(self):
        """信标任务即将由调度器执行（30 分钟内）时不代为调用。"""
        config = _AttackConfig(next_run=current_time() + timedelta(minutes=10))
        stub = _AttackStub(config)

        self.assertFalse(stub.handle_ash_beacon_attack())
        self.assertEqual(config.task_call_args, [])

    def test_skips_when_status_below_threshold(self):
        """持有量低于 100 时不调用信标任务。"""
        config = _AttackConfig()

        class _LowStatusStub(_AttackStub):
            ash_collect_status = lambda self: 70

        stub = _LowStatusStub(config)

        self.assertFalse(stub.handle_ash_beacon_attack())
        self.assertEqual(config.task_call_args, [])


if __name__ == '__main__':
    unittest.main()
