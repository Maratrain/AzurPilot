"""作战委托：一键消耗委托书的本周记录与放弃逻辑。

记录值 `2026W37` 会被配置系统按 ISO 周日期解析成 datetime
（`datetime.fromisoformat('2026W37')` → 该周周一），比较时必须还原回周 key，
否则同一周会被反复当成「还没触发过」。
"""

import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from module.config.deep import deep_get
from module.config.utils import filepath_args, parse_value
from module.handover.handover import OperationHandover

# 2026-09-12 是周六，ISO 第 37 周的第一天（周一）是 2026-09-07
NOW = datetime(2026, 9, 12, 11, 30)
NOW_WEEK = '2026W37'
# 配置系统写完盘再读回来时，`2026W37` 的实际形态
PARSED_RECORD = datetime(2026, 9, 7)


class FakeHandover:
    """只挂纯逻辑方法的桩，避免构造需要 config/device 的 ModuleBase。"""

    handover_week_key = staticmethod(OperationHandover.handover_week_key)
    handover_consume_all_book_record_key = OperationHandover.handover_consume_all_book_record_key
    handover_consume_all_book_state = OperationHandover.handover_consume_all_book_state
    handover_consume_all_book_waiting = OperationHandover.handover_consume_all_book_waiting
    handover_consume_all_book_trigger = OperationHandover.handover_consume_all_book_trigger
    handover_consume_all_book = OperationHandover.handover_consume_all_book
    handover_consume_all_book_record = OperationHandover.handover_consume_all_book_record
    handover_consume_all_book_give_up = OperationHandover.handover_consume_all_book_give_up

    def __init__(self, record=None, weekday='sat', time='11:13'):
        self.config = SimpleNamespace(
            OperationHandover_ConsumeAllBook=True,
            OperationHandover_ConsumeAllBookWeekday=weekday,
            OperationHandover_ConsumeAllBookTime=time,
            OperationHandover_ConsumeAllBookRecord=record,
        )


class TestConsumeAllBookRecord(unittest.TestCase):
    def patch_now(self):
        return patch('module.handover.handover.current_time', return_value=NOW)

    def test_week_key_of_parsed_record(self):
        # 记录被解析成周一那天，仍要还原成同一周
        self.assertEqual(OperationHandover.handover_week_key(PARSED_RECORD), NOW_WEEK)

    def test_config_round_trip(self):
        # 走真实配置系统：写下去的是 `2026W37`，读回来的是 datetime，
        # 两边都要能还原成同一个周 key
        with open(filepath_args(), encoding='utf-8') as f:
            args = json.load(f)
        data = deep_get(args, keys='OperationHandover.OperationHandover.ConsumeAllBookRecord')
        parsed = parse_value(NOW_WEEK, data=data)
        # 配置系统一旦不再把它解析成日期，这条断言会先失败，提醒同步调整实现
        self.assertEqual(parsed, PARSED_RECORD)

        fake = FakeHandover(record=parsed)
        self.assertEqual(fake.handover_consume_all_book_record_key(), NOW_WEEK)
        with self.patch_now():
            self.assertEqual(fake.handover_consume_all_book_state(), (False, '本周已处理过'))

    def test_record_key_accepts_both_forms(self):
        for record in [NOW_WEEK, PARSED_RECORD]:
            with self.subTest(record=record):
                fake = FakeHandover(record=record)
                self.assertEqual(fake.handover_consume_all_book_record_key(), NOW_WEEK)

    def test_used_week_does_not_trigger(self):
        # 关键回归：写盘后被解析成 datetime 的记录，必须仍然算「本周已处理过」
        for record in [NOW_WEEK, PARSED_RECORD]:
            with self.subTest(record=record):
                fake = FakeHandover(record=record)
                with self.patch_now():
                    self.assertEqual(fake.handover_consume_all_book_state(), (False, '本周已处理过'))
                    self.assertFalse(fake.handover_consume_all_book_waiting())

    def test_fresh_week_triggers(self):
        fake = FakeHandover(record=None)
        with self.patch_now():
            self.assertEqual(fake.handover_consume_all_book_state(), (True, ''))
            self.assertTrue(fake.handover_consume_all_book_waiting())


class TestConsumeAllBookFailure(unittest.TestCase):
    def test_no_book_returns_zero(self):
        fake = FakeHandover()
        fake.handover_count_max = lambda: 140
        fake.handover_click_until_stable = lambda *args, **kwargs: 0
        fake.handover_input_count = lambda count: self.fail('没有委托书时不该改成这个次数')
        self.assertEqual(OperationHandover.handover_consume_all_book(fake), 0)

    def test_ui_failure_returns_minus_one(self):
        fake = FakeHandover()
        fake.handover_count_max = lambda: -1
        self.assertEqual(OperationHandover.handover_consume_all_book(fake), -1)

    def test_input_failure_returns_minus_one(self):
        fake = FakeHandover()
        fake.handover_count_max = lambda: 140
        fake.handover_click_until_stable = lambda *args, **kwargs: 3
        fake.handover_input_count = lambda count: False
        self.assertEqual(OperationHandover.handover_consume_all_book(fake), -1)

    def test_book_count_returned_on_success(self):
        fake = FakeHandover()
        fake.handover_count_max = lambda: 140
        fake.handover_click_until_stable = lambda *args, **kwargs: 3
        fake.handover_input_count = lambda count: count == 3
        self.assertEqual(OperationHandover.handover_consume_all_book(fake), 3)


class TestConsumeAllBookGiveUp(unittest.TestCase):
    def test_give_up_records_week_and_idles(self):
        fake = FakeHandover(record=None)
        calls = []
        fake.handover_commission_clear = lambda: calls.append('clear')
        fake.handover_idle_delay = lambda maintain: calls.append(('idle', maintain))

        with patch('module.handover.handover.current_time', return_value=NOW):
            fake.handover_consume_all_book_give_up('没有可投入的作战全权委托书', None)

        self.assertEqual(fake.config.OperationHandover_ConsumeAllBookRecord, NOW_WEEK)
        # 放弃本周前要先清掉旧的委托结束时间，否则下一次还会白进一次游戏
        self.assertEqual(calls, ['clear', ('idle', None)])

        # 放弃之后本周不再触发，下一次重试要等到下周
        with patch('module.handover.handover.current_time', return_value=NOW):
            self.assertEqual(fake.handover_consume_all_book_state(), (False, '本周已处理过'))
            self.assertFalse(fake.handover_consume_all_book_waiting())


if __name__ == '__main__':
    unittest.main()
