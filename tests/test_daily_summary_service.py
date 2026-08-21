import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from alas import AzurLaneAutoScript
import module.statistics.daily_summary as daily_summary
from module.statistics.daily_summary import DAILY_SUMMARY_TITLE, DailySummaryService
from module.statistics.daily_summary_store import DailySummaryStore
from tests.test_daily_summary import sample_facts, summary_config, valid_report_text


class TestDailySummaryService(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = DailySummaryStore(
            Path(self.temporary_directory.name) / 'daily_summary.db'
        )
        self.service = DailySummaryService('alpha', store=self.store)
        self.start = datetime(2026, 8, 20, 20)
        self.end = datetime(2026, 8, 21, 20)
        self.key = 'cn:2026-08-21:2000'

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_due_period_is_deduplicated_and_missed_period_is_not_backfilled(self):
        config = summary_config()
        with (
            patch.object(daily_summary, 'server_time_offset_for', return_value=timedelta()),
            patch.object(daily_summary.threading, 'Thread') as thread,
        ):
            self.assertTrue(
                self.service.check_due(config, now=datetime(2026, 8, 21, 20, 2))
            )
            self.assertFalse(
                self.service.check_due(config, now=datetime(2026, 8, 21, 20, 3))
            )

        thread.assert_called_once()
        thread.return_value.start.assert_called_once_with()
        self.assertEqual('generating', self.store.get_period('alpha', self.key)['status'])

        missed = DailySummaryService('beta', store=self.store)
        with (
            patch.object(daily_summary, 'server_time_offset_for', return_value=timedelta()),
            patch.object(daily_summary.threading, 'Thread') as thread,
        ):
            self.assertFalse(
                missed.check_due(config, now=datetime(2026, 8, 21, 20, 6))
            )

        thread.assert_not_called()
        self.assertEqual('skipped', self.store.get_period('beta', self.key)['status'])

    def test_unresolved_automatic_package_does_not_claim_a_period(self):
        config = summary_config(
            Emulator_PackageName='auto', Emulator_ServerName='disabled'
        )
        with (
            patch.object(daily_summary, 'server_time_offset_for', return_value=timedelta()),
            patch.object(daily_summary.threading, 'Thread') as thread,
        ):
            self.assertFalse(
                self.service.check_due(config, now=datetime(2026, 8, 21, 20, 2))
            )

        thread.assert_not_called()
        self.assertIsNone(self.store.get_period('alpha', self.key))

    def test_missed_check_also_cleans_expired_periods(self):
        old_start = self.start - timedelta(days=36)
        old_end = self.end - timedelta(days=36)
        old_key = 'cn:2026-07-16:2000'
        self.assertTrue(
            self.store.claim_period('alpha', old_key, 'cn', old_start, old_end)
        )

        with (
            patch.object(daily_summary, 'server_time_offset_for', return_value=timedelta()),
            patch.object(daily_summary.threading, 'Thread') as thread,
        ):
            self.assertFalse(
                self.service.check_due(summary_config(), now=datetime(2026, 8, 21, 12))
            )

        thread.assert_not_called()
        self.assertIsNone(self.store.get_period('alpha', old_key))

    def test_build_facts_uses_only_aggregated_statistics(self):
        resource_result = {
            'resources': {
                'Oil': {
                    'start': 100,
                    'end': 120,
                    'delta': 20,
                    'baseline_known': True,
                    'end_known': True,
                }
            }
        }
        commission_result = {
            'available': True,
            'settled_count': 3,
            'items': {'Gem': {'total': 1}},
        }
        cl1_result = {'available': True, 'battles': 4, 'estimated_exp': 1248}
        with (
            patch(
                'module.statistics.resource_stats.get_resource_interval_summary',
                return_value=resource_result,
            ),
            patch(
                'module.statistics.commission_income_stats.get_commission_income_interval_summary',
                return_value=commission_result,
            ),
            patch(
                'module.statistics.ship_exp_stats.get_cl1_interval_summary',
                return_value=cl1_result,
            ),
        ):
            facts = self.service.build_facts(
                server='cn', window_start=self.start, window_end=self.end
            )

        self.assertEqual('石油', facts['resources'][0]['label'])
        self.assertEqual(20, facts['resources'][0]['delta'])
        self.assertTrue(facts['commission']['available'])
        self.assertNotIn('alpha', str(facts))
        self.assertNotIn('Error_LlmApiKey', str(facts))
        self.assertNotIn('OnePush', str(facts))

    def test_facts_display_the_game_server_window(self):
        with (
            patch.object(
                daily_summary, 'server_time_offset_for', return_value=timedelta(hours=-1)
            ),
            patch.object(self.store, 'get_task_summary', return_value={'available': False}),
            patch(
                'module.statistics.resource_stats.get_resource_interval_summary',
                return_value={'resources': {}},
            ),
            patch(
                'module.statistics.commission_income_stats.get_commission_income_interval_summary',
                return_value={'available': False, 'items': {}},
            ),
            patch(
                'module.statistics.ship_exp_stats.get_cl1_interval_summary',
                return_value={'available': False},
            ),
        ):
            facts = self.service.build_facts(
                server='jp',
                window_start=datetime(2026, 8, 20, 19),
                window_end=datetime(2026, 8, 21, 19),
            )

        self.assertEqual('2026-08-20 20:00:00', facts['window']['start'])
        self.assertEqual('2026-08-21 20:00:00', facts['window']['end'])

    def test_llm_retries_until_valid_text_and_push_reuses_it(self):
        client = Mock()

        def response(content):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

        client.chat.completions.create.side_effect = [
            response('# 不合规'),
            response('1. 仍然不合规'),
            response(valid_report_text()),
        ]
        openai_module = types.ModuleType('openai')
        openai_module.OpenAI = Mock(return_value=client)
        request = {
            'llm_api_key': 'test-key',
            'llm_api_base': 'https://example.invalid/v1',
            'llm_model': 'test-model',
        }
        with patch.dict(sys.modules, {'openai': openai_module}):
            report, attempts = self.service._generate_report(request, sample_facts())

        self.assertEqual(valid_report_text(), report)
        self.assertEqual(3, attempts)
        self.assertEqual(3, client.chat.completions.create.call_count)

        with patch(
            'module.notify.handle_notify',
            side_effect=[False, RuntimeError('temporary'), True],
        ) as notify:
            sent, send_attempts = self.service._send_report(
                'provider: json', report
            )

        self.assertTrue(sent)
        self.assertEqual(3, send_attempts)
        self.assertEqual(3, notify.call_count)
        for call in notify.call_args_list:
            self.assertEqual(
                DAILY_SUMMARY_TITLE.format(config_name='alpha'),
                call.kwargs['title'],
            )
            self.assertEqual(report, call.kwargs['content'])

    def test_llm_failure_does_not_attempt_onepush(self):
        self.assertTrue(
            self.store.claim_period('alpha', self.key, 'cn', self.start, self.end)
        )
        request = {
            'period_key': self.key,
            'server': 'cn',
            'window_start': self.start,
            'window_end': self.end,
            'llm_api_key': 'test-key',
            'llm_api_base': 'https://example.invalid/v1',
            'llm_model': 'test-model',
            'onepush_config': 'provider: json',
        }
        client = Mock()
        client.chat.completions.create.side_effect = [
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='# 无效'))]),
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='# 仍然无效'))]),
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='# 最后无效'))]),
        ]
        openai_module = types.ModuleType('openai')
        openai_module.OpenAI = Mock(return_value=client)
        with (
            patch.dict(sys.modules, {'openai': openai_module}),
            patch('module.base.async_executor.async_executor.flush'),
            patch.object(self.service, 'build_facts', return_value=sample_facts()),
            patch('module.notify.handle_notify') as notify,
        ):
            self.service._generate_and_send(request)

        period = self.store.get_period('alpha', self.key)
        self.assertEqual('failed', period['status'])
        self.assertEqual('llm', period['error_kind'])
        self.assertEqual(3, client.chat.completions.create.call_count)
        notify.assert_not_called()

    def test_missing_configuration_records_failure_without_fallback(self):
        self.assertTrue(
            self.store.claim_period('alpha', self.key, 'cn', self.start, self.end)
        )
        request = {
            'period_key': self.key,
            'server': 'cn',
            'window_start': self.start,
            'window_end': self.end,
            'llm_api_key': '',
            'llm_api_base': 'https://example.invalid/v1',
            'llm_model': 'test-model',
            'onepush_config': 'provider: json',
        }
        with (
            patch.object(self.service, 'build_facts') as facts,
            patch('module.notify.handle_notify') as notify,
        ):
            self.service._generate_and_send(request)

        period = self.store.get_period('alpha', self.key)
        self.assertEqual('failed', period['status'])
        self.assertEqual('configuration', period['error_kind'])
        facts.assert_not_called()
        notify.assert_not_called()

    def test_scheduler_check_never_initializes_device_or_restart_flow(self):
        script = AzurLaneAutoScript.__new__(AzurLaneAutoScript)
        script.config_name = 'alpha'
        script.failure_record = {'Commission': 2}
        script.__dict__['config'] = summary_config()
        service = Mock()
        script.__dict__['_daily_summary_service'] = service

        with patch('alas.current_time', return_value=datetime(2026, 8, 21, 20, 2)):
            script._check_daily_summary()
            service.check_due.side_effect = RuntimeError('日报故障')
            script._check_daily_summary()

        self.assertEqual(2, service.check_due.call_count)
        self.assertNotIn('device', script.__dict__)
        self.assertEqual({'Commission': 2}, script.failure_record)

        script.__dict__['config'] = summary_config(DailySummary_Enable=False)
        script.__dict__['_daily_summary_service'] = None
        script._check_daily_summary()
        self.assertIsNone(script._daily_summary_service)


if __name__ == '__main__':
    unittest.main()
