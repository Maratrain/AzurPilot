"""大世界每日委托延期机制的回归测试。

移植自上游 #1117 的延期用例子集（tests/test_opsi_daily.py），
仅覆盖本地已移植的\"延期进不了海域的委托并继续其他委托\"半边；
迷雾侦察半边依赖上游 smart_explore 基座，本地未移植、不测试。
"""
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.base.utils import load_image
from module.config.deep import deep_get, deep_set
from module.config.config import TaskEnd
from module.exception import GameStuckError
from module.os.assets import ZONE_LOCKED
from module.os.globe_operation import OSExploreError
from module.os.operation_siren import OperationSiren
from module.os_handler.action_point import ActionPointLimit

RESET = datetime(2026, 11, 1)
NOW = datetime(2026, 10, 3, 9)
UPDATE = datetime(2026, 10, 4)


class TestOpsiDaily(unittest.TestCase):
    def setUp(self):
        for name, value in (('current_time', NOW), ('get_server_next_update', UPDATE)):
            clock = patch(f'module.os_handler.mission.{name}', return_value=value)
            clock.start()
            self.addCleanup(clock.stop)
        self.data = {}
        self.runner = OperationSiren.__new__(OperationSiren)
        self.runner.config = SimpleNamespace(
            task=SimpleNamespace(command='OpsiDaily'),
            SERVER='cn',
            OpsiDaily_UseTuningSample=False,
            OpsiGeneral_UseLogger=False,
            OpsiDaily_SkipSirenResearchMission=False,
            OpsiDaily_KeepMissionZone=False,
            OpsiDaily_CollectTargetReward=False,
            OpsiFleet_Fleet=4,
            OpsiFleet_Submarine=True,
            Scheduler_ServerUpdate='00:00',
            cross_get=lambda keys, default=None: deep_get(self.data, keys, default),
            cross_set=lambda keys, value: deep_set(self.data, keys, value),
            task_delay=Mock(),
            task_stop=Mock(side_effect=TaskEnd),
            check_task_switch=Mock(),
        )
        self.runner.zone = self.runner.name_to_zone(44)
        self.runner.is_zone_name_hidden = False
        for name in ('zone_init', 'globe_goto', 'fleet_set', 'os_order_execute', 'handle_after_auto_search',
                     'ensure_no_zone_pinned', 'os_globe_goto_map', 'os_port_mission', 'os_daily_clear_all_mission_zones'):
            setattr(self.runner, name, Mock())
        self.runner.is_in_opsi_explore = Mock(return_value=False)
        self.runner.os_mission_overview_accept = Mock(return_value=True)
        self.runner.run_auto_search = Mock(return_value=1)

    def set_next_missions(self, results):
        queue = iter(results)

        def get_next(**kwargs):
            self.runner._os_mission_index = kwargs.get('mission_index', 0)
            return next(queue, False)

        self.runner.os_get_next_mission = Mock(side_effect=get_next)

    def prepare_mission_entry(self, zone=44):
        image = load_image(ZONE_LOCKED.file)
        self.runner.device = SimpleNamespace(image=image, click=Mock())
        self.runner.loop = lambda: iter((None,))
        self.runner.is_in_map = Mock(return_value=False)
        self.runner.is_zone_pinned = Mock(return_value=True)
        self.runner.get_zone_pinned_name = Mock(return_value='DANGEROUS')
        self.runner.appear = lambda button, offset=0: button.match(image, offset=offset)
        self.runner.os_mission_enter = Mock(return_value=(-20, -20, 20, 20))
        self.runner._os_find_checkout_offset_skip_monthly_boss = Mock(return_value=(-20, -20, 20, 20))
        self.runner.globe_update = Mock()
        self.runner.get_globe_pinned_zone = Mock(return_value=self.runner.name_to_zone(zone))

    def test_unavailable_mission_continues_other_daily_missions(self):
        for accepted in (False, True):
            for skip_siren in (False, True):
                with self.subTest(accepted=accepted, skip_siren=skip_siren):
                    self.runner.os_mission_overview_accept = Mock(side_effect=[accepted, True])
                    self.runner.config.OpsiDaily_SkipSirenResearchMission = skip_siren
                    self.runner.config.task_delay.reset_mock()
                    self.runner.run_auto_search.reset_mock()
                    self.set_next_missions(['mission_zone_unavailable', 'pinned_at_mission_zone', False])
                    self.runner.os_daily()
                    self.runner.run_auto_search.assert_called_once()
                    self.assertEqual(self.runner.os_get_next_mission.call_args_list[1].kwargs,
                                     dict(skip_siren_mission=skip_siren, skip_unavailable=True, mission_index=1))
                    self.runner.config.task_delay.assert_called_once_with(server_update=True)
                    self.runner.config.task_stop.assert_not_called()

    def test_lock_between_two_available_missions_does_not_abort(self):
        self.set_next_missions(['pinned_at_mission_zone', 'mission_zone_unavailable',
                                'pinned_at_mission_zone', False])
        self.assertEqual(self.runner.os_finish_daily_mission(), 2)
        self.assertEqual(self.runner.run_auto_search.call_count, 2)
        self.runner.config.task_delay.assert_not_called()

    def test_locked_template_defers_only_the_actual_target_zone(self):
        self.prepare_mission_entry()
        self.assertEqual(self.runner.os_get_next_mission(skip_unavailable=True), 'mission_zone_unavailable')
        self.runner.device.click.assert_not_called()
        self.runner.ensure_no_zone_pinned.assert_called_once_with()
        self.runner.os_globe_goto_map.assert_called_once_with()
        self.assertEqual(deep_get(self.data, 'OpsiDaily.OpsiDaily.DeferredMissions'),
                         {'until': UPDATE.isoformat(), 'zones': [44]})
        self.assertIsNone(deep_get(self.data, 'OpsiDaily.Scheduler.NextRun'))
        self.runner.config.task_delay.assert_not_called()

    def test_same_day_retry_skips_entering_only_the_deferred_zone(self):
        self.prepare_mission_entry()
        self.runner._os_defer_mission_zone(self.runner.name_to_zone(44))
        self.runner.globe_enter = Mock()
        self.assertEqual(self.runner.os_get_next_mission(skip_unavailable=True), 'mission_zone_unavailable')
        self.runner.globe_enter.assert_not_called()
        self.runner.get_globe_pinned_zone.return_value = self.runner.name_to_zone(42)
        self.assertEqual(self.runner.os_get_next_mission(skip_unavailable=True), 'pinned_at_mission_zone')
        self.runner.globe_enter.assert_called_once_with(zone=self.runner.name_to_zone(42))

    def test_next_day_retries_and_defers_only_the_still_locked_zone(self):
        self.prepare_mission_entry()
        self.runner._os_defer_mission_zone(self.runner.name_to_zone(44))
        tomorrow = datetime(2026, 10, 5)
        with (patch('module.os_handler.mission.current_time', return_value=UPDATE),
              patch('module.os_handler.mission.get_server_next_update', return_value=tomorrow),
              patch.object(self.runner, 'globe_enter', side_effect=OSExploreError) as enter):
            self.assertEqual(self.runner.os_get_next_mission(skip_unavailable=True), 'mission_zone_unavailable')
            enter.assert_called_once_with(zone=self.runner.name_to_zone(44))
        self.assertEqual(deep_get(self.data, 'OpsiDaily.OpsiDaily.DeferredMissions'),
                         {'until': tomorrow.isoformat(), 'zones': [44]})
        self.assertIsNone(deep_get(self.data, 'OpsiDaily.Scheduler.NextRun'))

    def test_submitting_a_completed_task_resets_the_skipped_row_index(self):
        self.prepare_mission_entry()
        self.runner._os_mission_submitted = True
        self.runner.globe_enter = Mock()
        self.assertEqual(self.runner.os_get_next_mission(skip_unavailable=True, mission_index=2),
                         'pinned_at_mission_zone')
        self.runner._os_find_checkout_offset_skip_monthly_boss.assert_called_once_with(
            (-20, -20, 20, 20), skip=0)
        self.assertEqual(self.runner._os_mission_index, 0)

    def test_full_queue_with_only_deferred_missions_does_not_reaccept_forever(self):
        self.runner.os_mission_overview_accept.return_value = False
        self.set_next_missions(['mission_zone_unavailable', 'mission_zone_unavailable', False])
        self.runner.os_daily()
        self.runner.os_mission_overview_accept.assert_called_once()
        self.runner.run_auto_search.assert_not_called()
        self.runner.config.task_stop.assert_not_called()

    def test_refresh_failure_skips_one_mission_and_continues(self):
        self.set_next_missions(['already_at_mission_zone', 'pinned_at_mission_zone', False])
        self.runner.globe_goto.side_effect = OSExploreError
        self.assertEqual(self.runner.os_finish_daily_mission(), 1)
        self.runner.run_auto_search.assert_called_once()
        self.assertEqual(deep_get(self.data, 'OpsiDaily.OpsiDaily.DeferredMissions')['zones'], [44])

    def test_other_tasks_do_not_defer_unavailable_missions(self):
        self.prepare_mission_entry()
        self.runner.config.task.command = 'OpsiArchive'
        self.runner.globe_enter = Mock(side_effect=OSExploreError)
        with self.assertRaises(OSExploreError):
            self.runner.os_get_next_mission(skip_unavailable=True)
        self.assertIsNone(deep_get(self.data, 'OpsiDaily.OpsiDaily.DeferredMissions'))

    def test_corrupt_or_expired_records_do_not_skip_missions(self):
        for state in (None, [], {}, {'until': 'bad'}, {'until': UPDATE.isoformat(), 'zones': None},
                      {'until': UPDATE.isoformat() + '+08:00', 'zones': [44]},
                      {'until': NOW.isoformat(), 'zones': [44]}):
            with self.subTest(state=state):
                deep_set(self.data, 'OpsiDaily.OpsiDaily.DeferredMissions', state)
                self.assertEqual(self.runner._os_deferred_mission_zones(), set())

    def test_cleanup_skips_the_locked_zone_and_cleans_the_other_zone(self):
        self.runner.zone = self.runner.name_to_zone(0)
        deep_set(self.data, 'OpsiDaily.OpsiDaily.MissionZones', '44 42')

        def enter(zone, **kwargs):
            if zone.zone_id == 44:
                raise OSExploreError

        self.runner.globe_goto.side_effect = enter
        with patch('module.os.tasks.daily.get_os_reset_remain', return_value=0):
            # 调用真实清理流程，屏蔽设备交互。
            del self.runner.os_daily_clear_all_mission_zones
            self.runner.os_daily_clear_all_mission_zones()
        self.runner.run_auto_search.assert_called_once_with()
        self.assertEqual(deep_get(self.data, 'OpsiDaily.OpsiDaily.MissionZones'), '44')
        self.runner.config.task_delay.assert_not_called()

    def test_unexpected_errors_and_action_point_limits_are_not_suppressed(self):
        for error in (GameStuckError(), RuntimeError('识别失败'), ActionPointLimit()):
            with self.subTest(error=type(error).__name__):
                self.runner.os_finish_daily_mission = Mock(side_effect=error)
                with self.assertRaises(type(error)) as raised:
                    self.runner.os_daily()
                self.assertIs(raised.exception, error)


if __name__ == '__main__':
    unittest.main()
