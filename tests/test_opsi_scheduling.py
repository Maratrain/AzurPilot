import unittest
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.campaign.os_run import OSCampaignRun
from module.config.config import Function, TaskEnd
from module.os.operation_siren import OperationSiren
from module.os.tasks.prevent_action_point_overflow import OpsiPreventActionPointOverflow
from module.os.tasks.scheduling import OpsiScheduling
from module.os_handler.action_point import ActionPointLimit
from module.os_handler.os_status import OSStatus


class TestOpsiTaskCooldown(unittest.TestCase):
    """到期任务不能被当成冷却任务，防止代理任务反复写回过去的运行时间。"""

    def setUp(self):
        self.now = datetime(2026, 9, 8, 7, 18, 17)
        self.update = datetime(2026, 9, 9)
        self.status = OSStatus.__new__(OSStatus)
        self.status.config = SimpleNamespace(pending_task=[], waiting_task=[])
        for name, value in (
            ('current_time', self.now),
            ('get_server_next_update', self.update),
        ):
            patcher = patch(f'module.os_handler.os_status.{name}', return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def make_task(next_run, command='OpsiDaily', enabled=True):
        return Function({'Scheduler': {
            'Command': command,
            'Enable': enabled,
            'NextRun': next_run,
        }})

    def test_expired_or_due_tasks_are_not_cooling_down(self):
        for next_run in (datetime(2026, 9, 7), self.now - timedelta(seconds=1), self.now):
            # 队列是较早生成的快照，等待队列里的任务也可能已经到期。
            for queue in ('pending_task', 'waiting_task'):
                with self.subTest(next_run=next_run, queue=queue):
                    self.status.config.pending_task = []
                    self.status.config.waiting_task = []
                    setattr(self.status.config, queue, [self.make_task(next_run)])
                    self.assertIsNone(self.status.nearest_task_cooling_down)

    def test_future_cooldown_keeps_the_sixty_minute_boundary(self):
        for seconds, expected in ((1, True), (3600, True), (3601, False)):
            with self.subTest(seconds=seconds):
                task = self.make_task(self.now + timedelta(seconds=seconds))
                self.status.config.waiting_task = [task]
                result = self.status.nearest_task_cooling_down
                self.assertIs(result, task if expected else None)

    def test_selects_nearest_enabled_cooldown_and_excludes_server_reset(self):
        # 将日更设在一小时内，确认它仍不会被误认为短期冷却。
        update = self.now + timedelta(minutes=10)
        nearest = self.make_task(self.now + timedelta(minutes=20), 'OpsiObscure')
        self.status.config.pending_task = [self.make_task(datetime(2026, 9, 7))]
        self.status.config.waiting_task = [
            self.make_task(self.now + timedelta(minutes=50), 'OpsiAbyssal'),
            self.make_task(update),
            self.make_task(self.now + timedelta(minutes=1), enabled=False),
            self.make_task(self.now + timedelta(minutes=2), 'Research'),
            nearest,
        ]
        with patch('module.os_handler.os_status.get_server_next_update', return_value=update):
            self.assertIs(self.status.nearest_task_cooling_down, nearest)

    def test_prevent_overflow_runs_meow_instead_of_requeueing_in_the_past(self):
        runner = OperationSiren.__new__(OperationSiren)
        owner = self.make_task(datetime(2026, 9, 7), 'OpsiPreventActionPointOverflow')
        runner.config = SimpleNamespace(
            task=owner,
            data={},
            pending_task=[owner, self.make_task(datetime(2026, 9, 7))],
            waiting_task=[],
            OpsiMeowfficerFarming_HazardLevel=5,
            OpsiMeowfficerFarming_TargetZone=0,
            OpsiMeowfficerFarming_StayInZone=False,
            OpsiTarget_TargetFarming=False,
            is_task_enabled=Mock(return_value=True),
            override=Mock(),
            bind=Mock(),
            temporary=lambda **kwargs: nullcontext(),
            task_delay=Mock(),
            task_stop=Mock(side_effect=TaskEnd),
        )
        with (
            patch.object(runner, '_get_prevent_action_point_overflow_thresholds', return_value=(200, 30)),
            patch.object(runner, '_get_prevent_action_point_overflow_task', return_value='OpsiMeowfficerFarming'),
            patch.object(runner, '_get_current_action_point_for_overflow', side_effect=[300, 20]),
            patch.object(runner, 'update_prevent_action_point_overflow_schedule') as reschedule,
            patch.object(runner, 'is_in_opsi_explore', return_value=False),
            patch.object(runner, '_meow_ap_check', return_value=True),
            patch.object(runner, '_meow_handle_normal_search') as search,
            patch('module.os.tasks.meowfficer_farming.get_os_reset_remain', return_value=22),
            patch('module.base.debug_clip.cleanup_clips_if_due'),
        ):
            with self.assertRaises(TaskEnd):
                runner.run_prevent_action_point_overflow()

        # 保留真实的代理上下文和短猫准备逻辑，仅替换设备交互。
        search.assert_called_once_with()
        runner.config.task_delay.assert_not_called()
        reschedule.assert_called_once_with(current_ap=20, enable=True)
        self.assertIs(runner.config.task, owner)
        self.assertFalse(runner.is_running_prevent_action_point_overflow_task())
        self.assertFalse(runner.is_running_smart_scheduling_task())
        self.assertFalse(hasattr(runner, runner.RUNTIME_ATTR_PREVENT_OVERFLOW_DELAY))


class SmartSchedulingConfig:
    """仅提供智能调度与防溢出测试所需的配置接口。"""

    def __init__(self, task_command='OpsiScheduling'):
        self.task = SimpleNamespace(command=task_command)
        self.task_delay_calls = []

    def cross_get(self, keys, default=None):
        if keys == 'OpsiScheduling.Scheduler.ServerUpdate':
            return '00:00'
        return default

    def task_delay(self, *args, **kwargs):
        self.task_delay_calls.append((args, kwargs))

    @staticmethod
    def temporary(**kwargs):
        return nullcontext()

    @staticmethod
    def task_stop():
        raise TaskEnd


class MeowPreserveConfig:
    """提供智能调度代跑短猫时的共享行动力保留状态。"""

    def __init__(self):
        self.OS_ACTION_POINT_PRESERVE = 180

    @contextmanager
    def temporary(self, **kwargs):
        backup = {key: getattr(self, key) for key in kwargs}
        for key, value in kwargs.items():
            setattr(self, key, value)
        try:
            yield
        finally:
            for key, value in backup.items():
                setattr(self, key, value)

    @staticmethod
    def task_stop():
        raise AssertionError('达到短猫保留值不应停止智能调度')


class SchedulingMeowHarness:
    """复现短猫达到自身阈值后异常冒泡的最小调度环境。"""

    TASK_NAME_MEOWFFICER_FARMING = OpsiScheduling.TASK_NAME_MEOWFFICER_FARMING

    def __init__(self):
        self.config = MeowPreserveConfig()
        self.executed_task_name = None

    def run_meowfficer_farming_once(self, ap_preserve):
        self.config.OS_ACTION_POINT_PRESERVE = ap_preserve
        raise ActionPointLimit(total=5985, preserve=ap_preserve)

    def _run_with_opsi_task_context(self, task_name, func, **kwargs):
        self.executed_task_name = task_name
        return func(**kwargs)

    def run_scheduled_meowfficer_farming(self, ap_preserve):
        return OpsiScheduling._run_scheduled_meowfficer_farming(self, ap_preserve)


class SchedulingMeowCostLimitHarness(SchedulingMeowHarness):
    def run_meowfficer_farming_once(self, ap_preserve):
        self.config.OS_ACTION_POINT_PRESERVE = ap_preserve
        raise ActionPointLimit(current=15, total=15, cost=120)


class TestSmartSchedulingMeowPreserve(unittest.TestCase):
    def test_returns_to_scheduling_and_restores_global_preserve_at_meow_limit(self):
        scheduling = SchedulingMeowHarness()

        scheduling.run_scheduled_meowfficer_farming(ap_preserve=6000)

        self.assertEqual(
            scheduling.executed_task_name,
            OpsiScheduling.TASK_NAME_MEOWFFICER_FARMING,
        )
        self.assertEqual(scheduling.config.OS_ACTION_POINT_PRESERVE, 180)

    def test_propagates_real_ap_shortage_and_still_restores_global_preserve(self):
        scheduling = SchedulingMeowCostLimitHarness()

        with self.assertRaises(ActionPointLimit):
            scheduling.run_scheduled_meowfficer_farming(ap_preserve=6000)

        self.assertEqual(scheduling.config.OS_ACTION_POINT_PRESERVE, 180)


class TestSmartSchedulingExploreDelay(unittest.TestCase):
    def test_skips_campaign_initialization_when_opsi_explore_is_in_progress(self):
        runner = OSCampaignRun.__new__(OSCampaignRun)
        runner.config = SmartSchedulingConfig()

        with (
            patch.object(runner, 'is_in_opsi_explore', return_value=True),
            patch.object(runner, '_run_opsi_task_with_ap_overflow_guard') as run_task,
        ):
            with self.assertRaises(TaskEnd):
                runner.opsi_scheduling()

        self.assertEqual(
            runner.config.task_delay_calls,
            [
                (
                    (),
                    {
                        'server_update': '00:00',
                        'task': 'OpsiScheduling',
                    },
                )
            ],
        )
        run_task.assert_not_called()

    def test_initializes_campaign_when_opsi_explore_is_complete(self):
        runner = OSCampaignRun.__new__(OSCampaignRun)
        runner.config = SmartSchedulingConfig()

        with (
            patch.object(runner, 'is_in_opsi_explore', return_value=False),
            patch.object(runner, '_run_opsi_task_with_ap_overflow_guard') as run_task,
        ):
            runner.opsi_scheduling()

        self.assertEqual(runner.config.task_delay_calls, [])
        run_task.assert_called_once()

    def test_delays_scheduling_when_opsi_explore_is_in_progress(self):
        scheduling = OpsiScheduling.__new__(OpsiScheduling)
        scheduling.config = SmartSchedulingConfig()

        with (
            patch.object(scheduling, 'is_in_opsi_explore', return_value=True),
            patch.object(scheduling, 'is_smart_scheduling_enabled') as enabled,
        ):
            with self.assertRaises(TaskEnd):
                scheduling.run_smart_scheduling()

        self.assertEqual(
            scheduling.config.task_delay_calls,
            [
                (
                    (),
                    {
                        'server_update': '00:00',
                        'task': 'OpsiScheduling',
                    },
                )
            ],
        )
        enabled.assert_not_called()

    def test_does_not_delay_when_smart_scheduling_is_normally_disabled(self):
        scheduling = OpsiScheduling.__new__(OpsiScheduling)
        scheduling.config = SmartSchedulingConfig()

        with (
            patch.object(scheduling, 'is_in_opsi_explore', return_value=False),
            patch.object(scheduling, 'is_smart_scheduling_enabled', return_value=False),
        ):
            scheduling.run_smart_scheduling()

        self.assertEqual(scheduling.config.task_delay_calls, [])

    def test_prevent_overflow_delays_itself_during_opsi_explore(self):
        prevent = OpsiPreventActionPointOverflow.__new__(OpsiPreventActionPointOverflow)
        prevent.config = SmartSchedulingConfig(
            task_command='OpsiPreventActionPointOverflow'
        )

        with (
            patch.object(
                prevent,
                '_get_prevent_action_point_overflow_thresholds',
                return_value=(200, 0),
            ),
            patch.object(
                prevent,
                '_get_prevent_action_point_overflow_task',
                return_value='OpsiScheduling',
            ),
            patch.object(
                prevent,
                '_get_current_action_point_for_overflow',
                return_value=200,
            ),
            patch.object(prevent, 'is_in_opsi_explore', return_value=True),
            patch.object(
                prevent,
                '_run_with_opsi_task_context',
                side_effect=lambda task, func, *args, **kwargs: func(*args, **kwargs),
            ),
            patch.object(
                prevent,
                'get_yellow_coins',
                side_effect=AssertionError('开荒期间不应进入智能调度决策'),
            ),
        ):
            with self.assertRaises(TaskEnd):
                prevent.run_prevent_action_point_overflow()

        self.assertEqual(
            prevent.config.task_delay_calls,
            [
                (
                    (),
                    {
                        'server_update': True,
                        'task': 'OpsiPreventActionPointOverflow',
                    },
                )
            ],
        )
