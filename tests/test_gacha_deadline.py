"""活动日期识别使用固定时间和模拟 OCR，避免访问模拟器与用户配置。"""

import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from module.gacha.gacha_reward import RewardGacha


class GachaDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.gacha = RewardGacha.__new__(RewardGacha)
        self.gacha.device = SimpleNamespace(image=object())
        self.enterContext(patch('module.config.server.server', 'cn'))
        self.ocr = self.enterContext(patch('module.gacha.gacha_reward.OCR_BUILD_EVENT_TIME'))

    def test_common_digit_confusions_are_corrected(self):
        for text, expected in [(' D ', 0), ('I', 1), ('己已', 22), ('乙Zz', 222), ('S', 5), ('B日曰', 888)]:
            with self.subTest(text=text):
                self.assertEqual(expected, self.gacha._process_ocr_digit(text))

    def test_date_range_accepts_ocr_confusions_spacing_and_different_separators(self):
        for text in [
            '活动时间:5月20日-6月12日维护',
            '活动时间:5月己0日-6月I己日维P',
            '活动时间：５月２０日～６月１２日维护',
            '5 月 20 日 — 6 月 12 日维护',
            '5月20日至6月12日维护',
        ]:
            with self.subTest(text=text):
                self.ocr.ocr.return_value = text
                self.assertEqual(date(2026, 6, 12), self.gacha._get_event_deadline(today=date(2026, 6, 1)))

    def test_day_misrecognized_as_sun_character_is_not_treated_as_delimiter(self):
        self.ocr.ocr.return_value = '7月20日-日月1日日维护'
        self.assertEqual(date(2026, 8, 18), self.gacha._get_event_deadline(today=date(2026, 8, 18)))

    def test_cross_year_event_is_resolved_on_both_sides_of_new_year(self):
        self.ocr.ocr.return_value = '12月20日-1月5日维护'
        for today, expected in [
            (date(2026, 12, 31), date(2027, 1, 5)),
            (date(2027, 1, 1), date(2027, 1, 5)),
            (date(2027, 1, 5), date(2027, 1, 5)),
            (date(2027, 1, 6), None),
        ]:
            with self.subTest(today=today):
                self.assertEqual(expected, self.gacha._get_event_deadline(today=today))

    def test_leap_day_is_validated_in_the_inferred_event_year(self):
        self.ocr.ocr.return_value = '2月20日-2月29日维护'
        self.assertEqual(date(2024, 2, 29), self.gacha._get_event_deadline(today=date(2024, 2, 29)))
        self.assertIsNone(self.gacha._get_event_deadline(today=date(2026, 2, 20)))

    def test_invalid_incomplete_or_inactive_ranges_do_not_supply_a_deadline(self):
        for text in [
            '', '无法识别', '6月12日维护', '5月20日-6月12维护',
            '5月20日-13月12日维护', '5月20日-6月31日维护',
            '5月20日-106月12日维护', '105月20日-6月12日维护',
            '5月32日-6月12日维护', '5月20日-5月12日维护',
            '7月20日-8月12日维护', '4月20日-5月12日维护',
            '5月20日-6月12日-6月13日维护',
        ]:
            with self.subTest(text=text):
                self.ocr.ocr.return_value = text
                self.assertIsNone(self.gacha._get_event_deadline(today=date(2026, 6, 1)))

    def test_current_day_uses_calibrated_server_timezone(self):
        self.ocr.ocr.return_value = '5月20日-6月12日维护'
        for utc_now, expected in [
            (datetime(2026, 6, 11, 16, 30, tzinfo=timezone.utc), date(2026, 6, 12)),
            (datetime(2026, 6, 12, 16, 30, tzinfo=timezone.utc), None),
        ]:
            with self.subTest(utc_now=utc_now), patch(
                'module.gacha.gacha_reward.current_time', side_effect=lambda tz: utc_now.astimezone(tz)
            ) as clock:
                self.assertEqual(expected, self.gacha._get_event_deadline())
                clock.assert_called_once_with(timezone(timedelta(hours=8)))

    def test_unverified_servers_do_not_use_chinese_fallback_asset_to_clear_tickets(self):
        for region in ['en', 'jp', 'tw']:
            with self.subTest(region=region), patch('module.config.server.server', region):
                self.assertIsNone(self.gacha._get_event_deadline(today=date(2026, 6, 12)))
        self.ocr.ocr.assert_not_called()


if __name__ == '__main__':
    unittest.main()
