"""资源提速的状态边界回归；不连接设备，不读写真实账号配置或统计库。

基于上游 tests/test_opsi_resource_speed.py 适配本地分支：本地没有
module.statistics.resource_tracking 与 tests.test_opsi_cl1_speed，
事件收尾的虚拟场景桩改为内联实现。
"""

import json
import os
import tempfile
import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock, patch

import numpy as np

from module.base.timer import Timer
from module.log_res import LogRes
from module.ocr.ocr import Digit, Ocr, ocr_batch
from module.os.fleet import OSFleet
from module.os.map import OSMap
from module.os.operation_siren import OperationSiren
from module.os_handler.action_point import ActionPointHandler
from module.os_handler.assets import STRATEGIC_SEARCH_MAP_OPTION_OFF
from module.os_handler.os_status import OSStatus
from module.os_handler.resource_state import ActionPointSnapshot, invalidate_action_point_inventory
from module.os_shop.assets import PORT_SUPPLY_CHECK
from module.statistics.cl1_database import Cl1Database
from module.statistics.item import AmountOcr


class VirtualClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class WalkStateStub(OSFleet):
    """真实移动等待循环的依赖桩，提供连续地图画面及指定时刻的事件。"""

    def __init__(self, clock, *, command='OpsiHazard1Leveling', shop=False, events=None):
        self.clock = clock
        self.config = SimpleNamespace(task=SimpleNamespace(command=command))
        self.device = Mock()
        self.view = SimpleNamespace(backend=SimpleNamespace(homo_loca=(52, 58)))
        self.frame = -1
        self.shop = shop
        self.events = events or {}
        for name in (
            'handle_retirement', 'handle_walk_out_of_step', 'handle_popup_confirm',
            'handle_manjuu', 'is_in_globe', 'is_in_storage', 'is_in_os_mission',
            'handle_os_game_tips', 'is_in_map_order', 'combat_appear',
            'appear_then_click', 'enemy_searching_appear',
        ):
            setattr(self, name, Mock(return_value=False))
        self.update_os = Mock()
        self.interval_clear = Mock()
        self.handle_akashi_supply_buy = Mock()
        self.enemy_searching_color_initial = Mock()
        self.is_in_map = Mock(return_value=True)
        self.match_template_color = Mock(return_value=True)

    def loop(self, **kwargs):
        for frame in range(40):
            self.frame = frame
            self.clock.now = 100 + 0.35 * frame
            yield None
        raise AssertionError('移动等待没有在连续稳定地图上结束')

    def handle_map_event(self, **kwargs):
        return self.events.get(self.frame, '')

    def appear(self, button, **kwargs):
        return button is PORT_SUPPLY_CHECK and self.shop and self.frame == 0


class TestActionPointPurchaseCache(unittest.TestCase):
    def setUp(self):
        self.handler = ActionPointHandler.__new__(ActionPointHandler)
        self.handler.config = SimpleNamespace(OpsiGeneral_BuyActionPointLimit=2)
        self.handler.device = SimpleNamespace(image=None)
        self.handler._action_point_box = [30000, 0, 0, 0]
        self.handler._is_in_month_end_purchase_block_week = Mock(return_value=False)
        self.handler.action_point_set_button = Mock(return_value=True)
        self.handler.action_point_get_buy_remain = Mock(return_value=3)
        self.handler.action_point_use = Mock()
        self.now = datetime(2026, 10, 15, 12)
        for name, value in (('current_time', self.now), ('server_time_offset', timedelta())):
            patcher = patch(f'module.os_handler.action_point.{name}', return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_reached_limit_skips_oil_in_later_replenishments(self):
        self.assertFalse(self.handler.action_point_buy())
        self.assertFalse(self.handler.action_point_buy())
        self.handler.action_point_set_button.assert_called_once_with(0)
        self.handler.action_point_get_buy_remain.assert_called_once()
        self.handler.action_point_use.assert_not_called()

    def test_raise_lower_disable_and_reenable_use_actual_purchased_count(self):
        self.assertFalse(self.handler.action_point_buy())  # 已购2次。
        self.handler.config.OpsiGeneral_BuyActionPointLimit = 5
        self.handler.action_point_get_buy_remain.side_effect = [3, 2]
        self.assertTrue(self.handler.action_point_buy())
        self.assertEqual(self.handler._ap_purchase_count[1], 3)
        self.handler.config.OpsiGeneral_BuyActionPointLimit = 1
        self.assertFalse(self.handler.action_point_buy())
        self.handler.config.OpsiGeneral_BuyActionPointLimit = 0
        self.assertFalse(self.handler.action_point_buy())
        self.handler.config.OpsiGeneral_BuyActionPointLimit = 3
        self.assertFalse(self.handler.action_point_buy())
        self.handler.action_point_use.assert_called_once()
        self.assertEqual(self.handler.action_point_set_button.call_count, 2)

    def test_changed_limit_while_reading_prevents_purchase(self):
        self.handler.config.OpsiGeneral_BuyActionPointLimit = 5

        def changed():
            self.handler.config.OpsiGeneral_BuyActionPointLimit = 0
            return 3

        self.handler.action_point_get_buy_remain.side_effect = changed
        self.assertFalse(self.handler.action_point_buy())
        self.handler.action_point_use.assert_not_called()

    def test_new_server_week_discards_old_exhaustion(self):
        self.assertFalse(self.handler.action_point_buy())
        self.handler.action_point_get_buy_remain.side_effect = [5, 4]
        with patch('module.os_handler.action_point.current_time', return_value=self.now + timedelta(days=7)):
            self.assertTrue(self.handler.action_point_buy())
        self.assertEqual(self.handler._ap_purchase_count[1], 1)

    def test_instance_change_does_not_share_exhaustion(self):
        self.assertFalse(self.handler.action_point_buy())
        self.handler.config.config_name = 'temporary-other-instance'
        self.handler.action_point_get_buy_remain.side_effect = [5, 4]
        with patch.object(self.handler, '_action_point_purchase_limit', return_value=2):
            self.assertTrue(self.handler.action_point_buy())

    def test_unknown_counter_does_not_cache_exhaustion(self):
        self.handler.action_point_get_buy_remain.return_value = None
        self.assertFalse(self.handler.action_point_buy())
        self.assertNotIn('_ap_purchase_count', self.handler.__dict__)
        self.handler.action_point_use.assert_not_called()

    def test_unconfirmed_purchase_invalidates_old_counter(self):
        self.handler.config.OpsiGeneral_BuyActionPointLimit = 5
        self.handler.action_point_get_buy_remain.side_effect = [3, None]
        self.assertTrue(self.handler.action_point_buy())
        self.assertNotIn('_ap_purchase_count', self.handler.__dict__)

    def test_purchase_failure_propagates_and_invalidates_cache(self):
        self.handler.config.OpsiGeneral_BuyActionPointLimit = 5
        self.handler.action_point_use.side_effect = RuntimeError('测试失败')
        with self.assertRaisesRegex(RuntimeError, '测试失败'):
            self.handler.action_point_buy()
        self.assertNotIn('_ap_purchase_count', self.handler.__dict__)

    def test_month_end_block_is_checked_before_oil_click(self):
        self.handler._is_in_month_end_purchase_block_week.return_value = True
        self.assertFalse(self.handler.action_point_buy())
        self.handler.action_point_set_button.assert_not_called()

    def test_latest_file_limit_is_read_without_overwriting_other_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'isolated.json'
            data = {'OpsiGeneral': {'OpsiGeneral': {'BuyActionPointLimit': 5}}, 'Other': {'keep': 42}}
            path.write_text(json.dumps(data), encoding='utf-8')
            self.handler.config.config_name = 'isolated'
            self.handler.config.bound = {}
            with patch('module.os_handler.action_point.filepath_config', return_value=str(path)):
                self.assertEqual(self.handler._action_point_purchase_limit(), 5)
                data['OpsiGeneral']['OpsiGeneral']['BuyActionPointLimit'] = 0
                path.write_text(json.dumps(data), encoding='utf-8')
                stamp = path.stat()
                os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000_000))
                self.assertEqual(self.handler._action_point_purchase_limit(), 0)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), data)

    def test_invalid_denominator_and_timeout_are_unknown(self):
        self.handler.loop = lambda **kwargs: iter(range(2))
        with patch('module.os_handler.action_point.OCR_ACTION_POINT_BUY_REMAIN') as reader:
            reader.ocr.return_value = (0, 0, 0)
            self.assertIsNone(ActionPointHandler.action_point_get_buy_remain(self.handler))
            reader.ocr.side_effect = [(2, 4, 6), (2, 3, 5)]
            self.assertEqual(ActionPointHandler.action_point_get_buy_remain(self.handler), 2)


class TestActionPointCheckInterval(unittest.TestCase):
    def setUp(self):
        self.handler = OperationSiren.__new__(OperationSiren)
        self.handler.config = SimpleNamespace(
            config_name='isolated', task=SimpleNamespace(command='OpsiHazard1Leveling'),
            OpsiGeneral_ActionPointCheckInterval=10, OS_ACTION_POINT_BOX_USE=True,
            OS_ACTION_POINT_PRESERVE=200,
        )
        self.handler.device = Mock(image=None)
        self.handler.appear = Mock(return_value=True)
        self.handler.handle_map_event = Mock(return_value=False)
        self.handler._ap_snapshot = ActionPointSnapshot(self.handler._action_point_identity(), 100, 0,
                                                       (10000, 1, 2, 3))
        self.monotonic = patch('module.os_handler.action_point.monotonic', return_value=110)
        self.monotonic.start()
        self.addCleanup(self.monotonic.stop)
        self.reader_patch = patch('module.os_handler.action_point.OCR_ACTION_POINT_REMAIN_OS')
        self.reader = self.reader_patch.start()
        self.addCleanup(self.reader_patch.stop)
        self.reader.ocr.return_value = 130
        self.reader.last_valid = True

    def test_cache_uses_current_map_value_and_does_not_extend_full_check_time(self):
        self.assertEqual(self.handler.action_point_cached_read(), (550, 130))
        self.assertEqual(self.handler._ap_snapshot.checked_at, 100)
        self.reader.ocr.return_value = 125
        self.assertEqual(self.handler.action_point_cached_read(), (545, 125))
        self.assertEqual(self.handler._ap_snapshot.checked_at, 100)

    def test_interval_boundary_forces_full_check(self):
        with patch('module.os_handler.action_point.monotonic', return_value=700):
            self.assertIsNone(self.handler.action_point_cached_read())

    def test_unknown_box_count_cannot_start_full_check_interval(self):
        self.handler._action_point_current = 100
        self.handler._action_point_box = [30000, 0, 1, 0]
        self.handler._ap_inventory_valid = False
        with patch('module.os_handler.action_point.OCR_ACTION_POINT_REMAIN') as reader:
            reader.last_valid = True
            self.handler._remember_action_point_snapshot()
            self.assertNotIn('_ap_snapshot', self.handler.__dict__)
            self.handler._ap_inventory_valid = True
            self.handler._remember_action_point_snapshot()
        self.assertIn('_ap_snapshot', self.handler.__dict__)
        self.reader.ocr.assert_not_called()

    def test_modified_interval_takes_effect_at_next_check(self):
        self.handler.config.OpsiGeneral_ActionPointCheckInterval = 1
        with patch('module.os_handler.action_point.monotonic', return_value=160):
            self.assertIsNone(self.handler.action_point_cached_read())

    def test_file_interval_change_takes_effect_without_full_panel_reload(self):
        self.handler.config.OpsiGeneral_ActionPointCheckInterval = 0
        self.handler.config.bound = {}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'isolated.json'
            path.write_text(json.dumps({'OpsiGeneral': {'OpsiGeneral': {'ActionPointCheckInterval': 10}}}),
                            encoding='utf-8')
            with patch('module.os_handler.action_point.filepath_config', return_value=str(path)):
                self.assertEqual(self.handler.action_point_cached_read(), (550, 130))

    def test_zero_and_other_tasks_keep_old_path(self):
        for command, interval in (('OpsiHazard1Leveling', 0), ('OpsiAbyssal', 10),
                                  ('OpsiStronghold', 10), ('OpsiDaily', 10)):
            with self.subTest(command=command, interval=interval):
                self.handler.config.task.command = command
                self.handler.config.OpsiGeneral_ActionPointCheckInterval = interval
                self.assertIsNone(self.handler.action_point_cached_read())
        self.handler.device.screenshot.assert_not_called()

    def test_smart_and_meow_enable_light_read(self):
        for command in ('OpsiScheduling', 'OpsiMeowfficerFarming'):
            self.handler.config.task.command = command
            self.assertEqual(self.handler.action_point_cached_read(), (550, 130))

    def test_box_use_override_preserves_full_inventory_total(self):
        self.handler.config.OS_ACTION_POINT_BOX_USE = False
        self.assertEqual(self.handler.action_point_cached_read(), (130, 130))
        self.assertEqual(self.handler._action_point_total_with_box, 550)

    def test_inventory_revision_and_instance_change_invalidate_snapshot(self):
        for invalidate in (lambda: invalidate_action_point_inventory(self.handler.config),
                           lambda: setattr(self.handler.config, 'config_name', 'other')):
            snapshot = self.handler._ap_snapshot
            invalidate()
            self.assertIsNone(self.handler.action_point_cached_read())
            self.handler._ap_snapshot = snapshot

    def test_invalid_map_digit_does_not_refresh_or_use_snapshot(self):
        self.reader.last_valid = False
        self.assertIsNone(self.handler.action_point_cached_read())
        self.assertNotIn('_ap_snapshot', self.handler.__dict__)

    def test_routine_sufficient_read_skips_panel(self):
        self.handler.action_point_enter = Mock()
        self.assertTrue(self.handler.action_point_set(cost=120, routine=True))
        self.handler.action_point_enter.assert_not_called()

    def test_low_current_ap_forces_replenishment_even_before_interval(self):
        self.reader.ocr.return_value = 36
        self.handler.action_point_enter = Mock()
        self.handler.handle_action_point = Mock(return_value=True)
        self.handler.loop = lambda **kwargs: iter(range(1))
        self.assertTrue(self.handler.action_point_set(cost=120, routine=True))
        self.handler.action_point_enter.assert_called_once()
        self.handler.handle_action_point.assert_called_once()

    def test_mandatory_check_never_uses_cache(self):
        self.handler.action_point_cached_read = Mock(return_value=(9999, 300))
        self.handler.action_point_enter = Mock()
        self.handler.handle_action_point = Mock(return_value=True)
        self.handler.loop = lambda **kwargs: iter(range(1))
        self.assertTrue(self.handler.action_point_set(cost=100))
        self.handler.action_point_cached_read.assert_not_called()
        self.handler.action_point_enter.assert_called_once()

    def test_smart_decision_cached_read_does_not_open_panel(self):
        self.handler.check_and_notify_action_point_threshold = Mock()
        self.handler.action_point_enter = Mock()
        self.assertEqual(self.handler._get_scheduling_action_point(allow_cached=True), (550, 130))
        self.handler.action_point_enter.assert_not_called()


class TestCompletedEventFastExit(unittest.TestCase):
    def run_event(self, *, command='OpsiHazard1Leveling', allow=True, shop=False, events=None, entry=True,
                  configure=None):
        clock = VirtualClock()
        handler = WalkStateStub(clock, command=command, shop=shop, events=events)
        handler.match_template_color = Mock(side_effect=lambda button, **kwargs:
                                           entry if button is STRATEGIC_SEARCH_MAP_OPTION_OFF else True)
        if configure is not None:
            configure(handler)
        with patch('module.base.timer.time', clock):
            result = OSFleet.wait_until_walk_stable(handler, confirm_timer=Timer(3.9, count=4),
                                                   allow_event_finish=allow)
        return handler, clock, result

    def test_akashi_exit_needs_no_homography_in_farming_tasks(self):
        for command in ('OpsiHazard1Leveling', 'OpsiMeowfficerFarming'):
            with self.subTest(command=command):
                handler, clock, result = self.run_event(command=command, shop=True)
                self.assertIn('akashi', result)
                handler.update_os.assert_not_called()
                self.assertEqual(handler.frame, 1)
                self.assertAlmostEqual(clock.now, 100.35)

    def test_final_reward_and_archives_exit_on_next_confirmed_frame(self):
        for event in ('map_get_items', 'map_archives'):
            handler, _, result = self.run_event(events={0: event})
            self.assertIn('event', result)
            handler.update_os.assert_not_called()

    def test_intermediate_dialogue_does_not_skip_final_reward(self):
        handler, _, _ = self.run_event(events={0: 'story_skip', 3: 'story_skip', 9: 'map_get_items'})
        self.assertEqual(handler.frame, 10)

    def test_reward_followed_by_another_option_does_not_complete_the_new_event(self):
        handler, _, _ = self.run_event(events={0: 'map_get_items', 1: 'story_skip'})
        self.assertTrue(handler.update_os.called)

    def test_followup_combat_finishes_before_fast_exit(self):
        def configure(handler):
            handler.combat_appear.side_effect = lambda: handler.frame == 2
            handler.combat = Mock()
            handler.fleet_show_index = 1

        handler, _, _ = self.run_event(events={0: 'map_get_items', 1: 'story_skip'}, configure=configure)
        handler.combat.assert_called_once()
        handler.update_os.assert_not_called()
        self.assertEqual(handler.frame, 3)

    def test_globe_recovery_after_reward_still_requires_position_confirmation(self):
        def configure(handler):
            handler.is_in_globe.side_effect = lambda: handler.frame == 1
            handler.os_globe_goto_map = Mock()

        handler, _, _ = self.run_event(events={0: 'map_get_items'}, configure=configure)
        handler.os_globe_goto_map.assert_called_once()
        self.assertTrue(handler.update_os.called)

    def test_smart_proxy_uses_actual_farming_task(self):
        def configure(handler):
            handler.config._bind_task_override = 'OpsiMeowfficerFarming'

        handler, _, _ = self.run_event(command='OpsiScheduling', shop=True, configure=configure)
        handler.update_os.assert_not_called()

    def test_initial_travel_and_option_only_keep_stability_checks(self):
        for events in (None, {0: 'story_skip'}):
            handler, clock, _ = self.run_event(events=events)
            self.assertTrue(handler.update_os.called)
            self.assertGreater(clock.now, 103.9)

    def test_recovery_other_task_and_hidden_plan_entry_keep_stability(self):
        for values in ({'allow': False}, {'command': 'OpsiExplore'}, {'entry': False}):
            handler, _, _ = self.run_event(shop=True, **values)
            self.assertTrue(handler.update_os.called)

    def test_virtual_scene_baseline_and_fast_path(self):
        before, old_clock, _ = self.run_event(shop=True, allow=False)
        after, new_clock, _ = self.run_event(shop=True)
        self.assertGreater(old_clock.now, new_clock.now)
        self.assertGreater(before.update_os.call_count, after.update_os.call_count)


class TestBatchedResourceOcr(unittest.TestCase):
    image = np.full((30, 80, 3), 255, np.uint8)

    def test_currency_batch_keeps_preprocessing_and_validity_per_field(self):
        readers = [Digit((0, 0, 40, 20)), Digit((40, 0, 80, 20), letter=(239, 239, 239))]
        model = Mock()
        model.atomic_ocr_for_single_lines.return_value = ['75696', '']
        with patch.object(Ocr, 'cnocr', new_callable=PropertyMock, return_value=model):
            self.assertEqual(ocr_batch(self.image, readers), [75696, 0])
        self.assertTrue(readers[0].last_valid)
        self.assertFalse(readers[1].last_valid)
        model.atomic_ocr_for_single_lines.assert_called_once()

    def test_amount_batch_preserves_overflow_retry(self):
        reader = AmountOcr([], threshold=96)
        model = Mock()
        model.atomic_ocr_for_single_lines.side_effect = [['999', '2'], ['3']]
        with patch.object(Ocr, 'cnocr', new_callable=PropertyMock, return_value=model):
            # 物品名走前缀上限表，读数为 999 时仍触发超限复识别
            result = reader.ocr_batch_with_validation([self.image, self.image],
                                                      item_names=['Cat', 'Cat'], batch_first=True)
        self.assertEqual(result, [3, 2])
        self.assertEqual(model.atomic_ocr_for_single_lines.call_count, 2)

    def test_amount_batch_preserves_untrimmed_zero_retry(self):
        reader = AmountOcr([], threshold=96)
        model = Mock()
        model.atomic_ocr_for_single_lines.side_effect = [['', '2'], ['1']]
        with patch.object(Ocr, 'cnocr', new_callable=PropertyMock, return_value=model):
            self.assertEqual(reader.ocr_batch_with_validation([self.image, self.image], batch_first=True), [1, 2])

    def test_empty_quantity_is_not_treated_as_verified_zero_inventory(self):
        reader = AmountOcr([], threshold=96)
        model = Mock()
        model.atomic_ocr_for_single_lines.side_effect = [['', '0'], [''], ['0']]
        with patch.object(Ocr, 'cnocr', new_callable=PropertyMock, return_value=model):
            self.assertEqual(reader.ocr_batch_with_validation([self.image] * 2, batch_first=True), [0, 0])
        self.assertEqual(reader.last_batch_validity, (False, True))

    def test_same_crops_reduce_queue_submissions_without_changing_values(self):
        images = [self.image] * 4
        reader = AmountOcr([], threshold=96)
        model = Mock()
        model.atomic_ocr_for_single_lines.side_effect = lambda crops, *_: ['12'] * len(crops)
        with patch.object(Ocr, 'cnocr', new_callable=PropertyMock, return_value=model):
            baseline = reader.ocr_batch_with_validation(images)
            before = model.atomic_ocr_for_single_lines.call_count
            model.reset_mock()
            result = reader.ocr_batch_with_validation(images, batch_first=True)
        self.assertEqual(result, baseline)
        self.assertEqual(before, 4)
        self.assertEqual(model.atomic_ocr_for_single_lines.call_count, 1)

    def test_successful_batch_does_not_repeat_preprocessing(self):
        reader = AmountOcr([], threshold=96)
        model = Mock()
        model.atomic_ocr_for_single_lines.return_value = ['12', '12']
        with patch.object(Ocr, 'cnocr', new_callable=PropertyMock, return_value=model), \
                patch.object(reader, 'pre_process', wraps=reader.pre_process) as prepare:
            self.assertEqual(reader.ocr_batch_with_validation([self.image] * 2, batch_first=True), [12, 12])
        self.assertEqual(prepare.call_count, 2)


class TestCurrencyReadReuse(unittest.TestCase):
    def setUp(self):
        self.handler = OSStatus.__new__(OSStatus)
        self.handler.config = SimpleNamespace(task=SimpleNamespace(command='OpsiHazard1Leveling'),
                                               config_name='isolated')
        self.handler.device = SimpleNamespace(image=np.zeros((720, 1280, 3), np.uint8))
        self.handler.appear = Mock(return_value=False)
        self.handler.appear_then_click = Mock(return_value=False)
        self.handler.loop = lambda **kwargs: iter(range(2))
        self.log_patch = patch('module.os_handler.os_status.LogRes')
        self.recorder = self.log_patch.start()
        self.addCleanup(self.log_patch.stop)
        self.yellow_patch = patch('module.os_handler.os_status.OCR_SHOP_YELLOW_COINS')
        self.yellow = self.yellow_patch.start()
        self.addCleanup(self.yellow_patch.stop)
        self.yellow.buttons = [(0, 0, 20, 20)]
        self.yellow.last_valid = True
        self.yellow.ocr.return_value = 75696
        self.purple_patch = patch('module.os_handler.os_status.OCR_SHOP_PURPLE_COINS')
        self.purple = self.purple_patch.start()
        self.addCleanup(self.purple_patch.stop)
        self.purple.buttons = [(20, 0, 40, 20)]
        self.purple.last_valid = True

    def test_currency_pair_uses_one_batch_and_second_yellow_frame(self):
        with patch('module.os_handler.os_status.ocr_batch', return_value=[75696, 10311]) as batch:
            self.assertEqual(self.handler.get_yellow_coins(with_purple=True), 75696)
        batch.assert_called_once()
        self.yellow.ocr.assert_called_once()
        self.assertEqual(self.handler._paired_purple_read, (10311, True))

    def test_same_verified_region_reuses_read_but_changed_transaction_and_page_do_not(self):
        self.assertEqual(self.handler.get_yellow_coins(), 75696)
        before = self.yellow.ocr.call_count
        self.assertEqual(self.handler.get_yellow_coins(reuse=True), 75696)
        self.assertEqual(self.yellow.ocr.call_count, before)
        self.handler.config._opsi_coin_revision = 1
        self.handler.get_yellow_coins(reuse=True)
        self.assertEqual(self.yellow.ocr.call_count, before + 2)
        self.handler.appear.side_effect = lambda button: button is PORT_SUPPLY_CHECK
        self.handler.get_yellow_coins(reuse=True)
        self.assertEqual(self.yellow.ocr.call_count, before + 4)

    def test_changed_digit_region_requires_new_stable_frames(self):
        self.handler.get_yellow_coins()
        self.handler.device.image[:20, :20] = 255
        self.handler.get_yellow_coins(reuse=True)
        self.assertEqual(self.yellow.ocr.call_count, 4)

    def test_invalid_frame_breaks_consecutive_confirmation(self):
        self.handler.loop = lambda **kwargs: iter(range(4))
        self.yellow.ocr.side_effect = [75696, 0, 75696, 75696]
        self.assertEqual(self.handler.get_yellow_coins(), 75696)
        self.assertEqual(self.yellow.ocr.call_count, 4)

    def test_unread_pair_does_not_reuse_previous_shop_purple(self):
        self.handler._paired_purple_read = (123, True)
        self.handler.loop = lambda **kwargs: iter([])
        self.handler.get_yellow_coins(with_purple=True)
        self.assertEqual(self.handler._paired_purple_read, (0, False))

    def test_smart_meow_shop_captures_actual_source(self):
        self.handler.config.task.command = 'OpsiScheduling'
        self.handler.config._bind_task_override = 'OpsiMeowfficerFarming'
        self.handler.config.save = Mock()
        with patch('module.os_handler.os_status.ocr_batch', return_value=[75696, 10311]), \
                patch('module.statistics.cl1_database.db.async_add_coins_snapshot') as queued:
            self.handler.os_shop_get_coins()
        self.assertEqual(queued.call_args.kwargs['source'], 'meow')


class TestResourceCacheInvalidation(unittest.TestCase):
    def test_in_place_fog_restart_clears_pre_restart_resource_state(self):
        handler = OSMap.__new__(OSMap)
        handler.config = SimpleNamespace(task=SimpleNamespace(command='OpsiHazard1Leveling'))
        handler.device = Mock()
        handler.appear = Mock(return_value=True)
        handler.ui_ensure = Mock()
        names = ('_ap_snapshot', '_ap_purchase_count', '_yellow_coin_read', '_paired_purple_read')
        for name in names:
            setattr(handler, name, object())
        handler.device.app_stop.side_effect = lambda: self.assertTrue(
            all(name not in handler.__dict__ for name in names)
        )
        with patch('module.os.map.LoginHandler'):
            self.assertTrue(handler.handle_fog_block(repair=False))
        handler.device.app_start.assert_called_once()


class TestDeferredResourceHistory(unittest.TestCase):
    def test_business_observation_is_immediate_and_snapshot_is_captured(self):
        config = SimpleNamespace(config_name='isolated', modified={}, data={
            'Dashboard': {'Oil': {'Value': 100, 'Record': None}, 'Coin': {'Value': 200, 'Record': None}},
        })
        recorder = LogRes(config)
        recorder.__dict__['groups'] = {'Oil': {}, 'Coin': {}}
        with patch('module.statistics.resource_stats.async_record_resource_snapshot') as queued, \
                patch('module.statistics.resource_stats.record_resource_snapshot') as synchronous:
            recorder.record('Oil', 150, defer_history=True)
        synchronous.assert_not_called()
        self.assertEqual(queued.call_args.args, ('isolated', {'Oil': 150, 'Coin': 200}))
        self.assertIsInstance(queued.call_args.kwargs['observed_at'], datetime)
        config.modified['Dashboard.Oil.Value'] = 999
        self.assertEqual(queued.call_args.args[1]['Oil'], 150)

    def test_legacy_history_remains_synchronous(self):
        config = SimpleNamespace(config_name='isolated', modified={}, data={
            'Dashboard': {'Oil': {'Value': 100, 'Record': None}},
        })
        recorder = LogRes(config)
        recorder.__dict__['groups'] = {'Oil': {}}
        with patch('module.statistics.resource_stats.record_resource_snapshot') as synchronous, \
                patch('module.statistics.resource_stats.async_record_resource_snapshot') as queued:
            recorder.record('Oil', 150)
        synchronous.assert_called_once()
        queued.assert_not_called()

    def test_sample_month_and_source_are_fixed_before_queue_processing(self):
        db = Cl1Database.__new__(Cl1Database)
        db._stats_transaction = lambda: nullcontext(object())
        db.ensure_coins_history = Mock()
        db._get_stats_in_connection = Mock(return_value={})
        db._get_previous_coins_snapshot = Mock(return_value=None)
        db._save_stats_in_connection = Mock()
        stamp = datetime(2026, 10, 31, 23, 59, 59)
        db.add_coins_snapshot('isolated', 500, 20, source='meow', observed_at=stamp)
        args = db._save_stats_in_connection.call_args.args
        self.assertEqual(args[1:3], ('isolated', '2026-10'))
        self.assertEqual(args[3]['coins_snapshots'][0], {
            'ts': stamp.isoformat(), 'yellow_coins': 500, 'purple_coins': 20, 'source': 'meow',
        })

    def test_purple_history_carry_happens_in_background_writer(self):
        db = Cl1Database.__new__(Cl1Database)
        db._stats_transaction = lambda: nullcontext(object())
        db.ensure_coins_history = Mock()
        db._get_stats_in_connection = Mock(return_value={'coins_snapshots': [
            {'yellow_coins': 10, 'purple_coins': 0},
        ]})
        db._save_stats_in_connection = Mock()
        db.add_coins_snapshot('isolated', 20, carry_purple=True)
        data = db._save_stats_in_connection.call_args.args[3]
        self.assertEqual(data['coins_snapshots'][-1]['purple_coins'], 0)

    def test_async_submission_captures_month_before_delayed_execution(self):
        db = Cl1Database.__new__(Cl1Database)
        db._stats_transaction = lambda: nullcontext(object())
        db.ensure_coins_history = Mock()
        db._get_stats_in_connection = Mock(return_value={})
        db._get_previous_coins_snapshot = Mock(return_value=None)
        db._save_stats_in_connection = Mock()
        stamp = datetime(2026, 10, 31, 23, 59, 59)
        with patch('module.statistics.cl1_database.datetime', wraps=datetime) as clock, \
                patch('module.base.async_executor.async_executor') as queue:
            clock.now.return_value = stamp
            db.async_add_coins_snapshot('isolated', 500, 20, source='meow')
            clock.now.return_value = stamp + timedelta(seconds=2)
            call = queue.submit.call_args
            call.args[0](*call.args[1:], **call.kwargs)
        self.assertEqual(db._save_stats_in_connection.call_args.args[2], '2026-10')

    def test_resource_snapshots_keep_submission_order_and_copied_values(self):
        from threading import Event
        from module.base.async_executor import async_executor
        from module.statistics.resource_stats import async_record_resource_snapshot
        gate = Event()
        observed = []
        async_executor.submit(gate.wait, 5)
        first = {'Oil': 100}
        stamp = datetime(2026, 10, 31, 23, 59, 59)
        try:
            with patch('module.statistics.resource_stats.record_resource_snapshot',
                       side_effect=lambda instance, values, **kwargs: observed.append((values, kwargs['observed_at']))):
                a = async_record_resource_snapshot('isolated', first, observed_at=stamp)
                first['Oil'] = 999
                b = async_record_resource_snapshot('isolated', {'Oil': 200}, observed_at=stamp + timedelta(seconds=2))
                gate.set()
                a.result(timeout=5)
                b.result(timeout=5)
        finally:
            gate.set()
        self.assertEqual(observed, [({'Oil': 100}, stamp), ({'Oil': 200}, stamp + timedelta(seconds=2))])


if __name__ == '__main__':
    unittest.main()
