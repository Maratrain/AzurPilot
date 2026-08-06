import random
import unittest
from datetime import datetime, timedelta
from itertools import product
from types import SimpleNamespace
from unittest.mock import patch

from module.commission.commission import RewardCommission
from module.commission.planner import (
    CommissionPlan,
    CommissionPlanAction,
    CommissionPlanJob,
    optimize_commission_plan,
)
from module.commission.preset import DICT_FILTER_PRESET
from module.commission.project import COMMISSION_FILTER, Commission
from module.config.config_generated import GeneratedConfig
from module.map.map_grids import SelectedGrids


def commission(name, genre, duration=1):
    category, sub_genre = genre.split('_', 1)
    return SimpleNamespace(
        name=name,
        genre=genre,
        category_str=category,
        genre_str=sub_genre,
        duration=timedelta(hours=duration),
        duration_hm=f'{duration}:00',
        duration_hour=str(duration),
        repeat_count=1,
    )


def selectable_commission(name, genre, duration=1):
    """构造可直接进入委托选择算法的测试对象。"""
    value = object.__new__(Commission)
    value.name = name
    value.genre = genre
    value.category_str, value.genre_str = genre.split('_', 1)
    value.status = 'pending'
    value.valid = True
    value.duration = timedelta(hours=duration)
    value.duration_hm = f'{duration}:00'
    value.duration_hour = str(duration)
    value.suffix_hash = ''
    value.suffix_image = None
    value.available_time = timedelta(0)
    value.deadline_time = None
    value.repeat_count = 1
    return value


def brute_force_plan(jobs, slot_available, horizon):
    """用完整排列穷举生成小规模实例的精确对照结果。"""
    jobs = sorted(
        jobs,
        key=lambda job: (
            job.tier,
            job.deadline if job.deadline is not None else horizon,
            job.duration,
            job.source_index,
        ),
    )
    tier_count = max(job.tier for job in jobs) + 1
    slots = tuple(sorted(slot_available))
    best_by_selection = {}

    def search(selected_mask, current_slots, actions, source_order, makespan, completion_sum):
        key = (source_order, makespan, completion_sum, tuple(action[0] for action in actions))
        current = best_by_selection.get(selected_mask)
        if current is None or key < current[0]:
            best_by_selection[selected_mask] = (
                key,
                actions,
                makespan,
                completion_sum,
            )

        start = current_slots[0]
        if start >= horizon:
            return
        for job_index, job in enumerate(jobs):
            bit = 1 << job_index
            if selected_mask & bit or job.deadline is not None and start >= job.deadline:
                continue
            finish = start + job.duration
            search(
                selected_mask | bit,
                tuple(sorted((finish, *current_slots[1:]))),
                (*actions, (job_index, start, finish)),
                (*source_order, job.source_index),
                max(makespan, finish),
                completion_sum + finish,
            )

    search(0, slots, (), (), 0, 0)
    best = None
    for selected_mask, (_, actions, makespan, completion_sum) in best_by_selection.items():
        score = [0] * tier_count
        priority_sums = [0] * tier_count
        for job_index, job in enumerate(jobs):
            if selected_mask & (1 << job_index):
                score[job.tier] += 1
                priority_sums[job.tier] += job.source_index
        rank = (
            tuple(score),
            tuple(-value for value in priority_sums),
            -makespan,
            -completion_sum,
            tuple(-action[0] for action in actions),
        )
        if best is None or rank > best[0]:
            best = (rank, actions, tuple(score), tuple(priority_sums), makespan, completion_sum)
    return best[1:], jobs


class TestCommissionAlgorithmSwitch(unittest.TestCase):
    def test_dynamic_programming_is_disabled_by_default(self):
        self.assertIs(GeneratedConfig.Commission_DynamicProgramming, False)

    def test_dispatches_to_legacy_algorithm_when_disabled(self):
        worker = object.__new__(RewardCommission)
        worker.config = SimpleNamespace(Commission_DynamicProgramming=False)

        with (
            patch.object(worker, '_commission_choose_legacy', return_value='legacy') as legacy,
            patch.object(worker, '_commission_choose_dynamic', return_value='dynamic') as dynamic,
        ):
            result = worker._commission_choose('daily', 'urgent')

        self.assertEqual(result, 'legacy')
        legacy.assert_called_once_with('daily', 'urgent')
        dynamic.assert_not_called()

    def test_dispatches_to_dynamic_algorithm_only_when_enabled(self):
        worker = object.__new__(RewardCommission)
        worker.config = SimpleNamespace(Commission_DynamicProgramming=True)

        with (
            patch.object(worker, '_commission_choose_legacy', return_value='legacy') as legacy,
            patch.object(worker, '_commission_choose_dynamic', return_value='dynamic') as dynamic,
        ):
            result = worker._commission_choose('daily', 'urgent')

        self.assertEqual(result, 'dynamic')
        dynamic.assert_called_once_with('daily', 'urgent')
        legacy.assert_not_called()

    def test_legacy_algorithm_ignores_dynamic_control_tokens(self):
        worker = object.__new__(RewardCommission)
        worker.config = SimpleNamespace(
            Commission_PresetFilter='custom',
            Commission_CustomFilter='DailyResource > tier > ignore > shortest',
        )
        commissions = []
        for index in range(4):
            value = object.__new__(Commission)
            value.name = f'委托{index}'
            value.genre = 'daily_resource'
            value.category_str = 'daily'
            value.genre_str = 'resource'
            value.status = 'pending'
            value.valid = True
            value.duration = timedelta(hours=1)
            value.suffix_hash = ''
            value.suffix_image = None
            value.available_time = timedelta(0)
            value.deadline_time = None
            value.repeat_count = 1
            commissions.append(value)
        daily = SelectedGrids(commissions)

        with patch.object(
            COMMISSION_FILTER,
            'apply',
            return_value=[*commissions, 'tier', 'ignore', 'shortest'],
        ):
            daily_choose, urgent_choose = worker._commission_choose_legacy(
                daily,
                SelectedGrids([]),
            )

        self.assertEqual(daily_choose.grids, commissions)
        self.assertEqual(urgent_choose.grids, [])
        self.assertEqual(worker.comm_choose.grids, commissions)


class TestCommissionTierFilter(unittest.TestCase):
    def test_every_builtin_filter_defines_value_tiers(self):
        for name, value in DICT_FILTER_PRESET.items():
            with self.subTest(name=name):
                COMMISSION_FILTER.load(value)
                tiers = COMMISSION_FILTER.apply_tiers([])
                self.assertIsInstance(tiers, list)

    def test_tier_separator_groups_equal_value_commissions(self):
        urgent = commission('紧急魔方', 'urgent_cube')
        gem = commission('钻石', 'urgent_gem')
        daily = commission('每日资源', 'daily_resource')
        fallback = commission('兜底', 'extra_oil', duration=0.5)

        COMMISSION_FILTER.load('UrgentCube > Gem > tier > DailyResource > shortest')
        tiers = COMMISSION_FILTER.apply_tiers([urgent, gem, daily, fallback])

        self.assertEqual(tiers, [[urgent, gem], [daily, fallback]])

    def test_legacy_filter_keeps_each_rule_as_independent_tier(self):
        urgent = commission('紧急魔方', 'urgent_cube')
        gem = commission('钻石', 'urgent_gem')

        COMMISSION_FILTER.load('UrgentCube > Gem')
        tiers = COMMISSION_FILTER.apply_tiers([urgent, gem])

        self.assertEqual(tiers, [[urgent], [gem]])

    def test_ignore_splits_dynamic_tiers_and_legacy_filter(self):
        urgent = commission('紧急魔方', 'urgent_cube')
        gem = commission('钻石', 'urgent_gem')
        daily = commission('每日资源', 'daily_resource')

        COMMISSION_FILTER.load(
            'UrgentCube > tier > ignore > Gem > DailyResource > shortest'
        )
        tiers = COMMISSION_FILTER.apply_tiers([urgent, gem, daily])
        legacy = COMMISSION_FILTER.apply_after_ignore(
            [urgent, gem, daily],
            excluded=[comm for tier in tiers for comm in tier],
        )

        self.assertEqual(tiers, [[urgent]])
        self.assertEqual(legacy, [gem, daily, 'shortest'])

    def test_filter_without_ignore_has_no_legacy_part(self):
        urgent = commission('紧急魔方', 'urgent_cube')

        COMMISSION_FILTER.load('UrgentCube')

        self.assertEqual(COMMISSION_FILTER.apply_tiers([urgent]), [[urgent]])
        self.assertEqual(COMMISSION_FILTER.apply_after_ignore([urgent]), [])

    def test_tier_after_ignore_does_not_change_dynamic_grouping(self):
        urgent = commission('紧急魔方', 'urgent_cube')
        gem = commission('钻石', 'urgent_gem')

        COMMISSION_FILTER.load('UrgentCube > Gem > ignore > tier > shortest')

        self.assertEqual(COMMISSION_FILTER.apply_tiers([urgent, gem]), [[urgent], [gem]])

    def test_running_commission_no_longer_has_start_deadline(self):
        value = object.__new__(Commission)
        value.valid = True
        value.status = 'pending'
        value.available_time = timedelta(hours=1)
        value.deadline_time = datetime(2026, 8, 5, 12, 0, 0)

        with patch('module.commission.project.current_time', return_value=datetime(2026, 8, 5, 11, 0, 0)):
            value.convert_to_running()

        self.assertEqual(value.status, 'running')
        self.assertEqual(value.available_time, timedelta(0))
        self.assertIsNone(value.deadline_time)


class TestCommissionDynamicPlanner(unittest.TestCase):
    def test_legacy_jobs_use_tightest_compatible_slot_first(self):
        one_hour = selectable_commission('一小时委托', 'urgent_gem', duration=1)
        two_hours = selectable_commission('两小时委托', 'daily_resource', duration=2)

        selected = RewardCommission._commission_fill_legacy_slots(
            SelectedGrids([one_hour, two_hours]),
            slot_fill_limits=[3 * 60 * 60, 60 * 60],
        )

        self.assertEqual(selected.grids, [one_hour, two_hours])

    def test_ignore_tail_respects_per_slot_fill_limits_from_planner(self):
        dynamic = [
            selectable_commission(f'动态委托{index}', 'urgent_cube', duration=index + 1)
            for index in range(4)
        ]
        legacy_long = selectable_commission('传统长委托', 'urgent_gem', duration=2)
        legacy_short = selectable_commission('传统短委托', 'daily_resource', duration=0.5)
        worker = object.__new__(RewardCommission)
        worker.config = SimpleNamespace(
            Commission_PresetFilter='custom',
            Commission_CustomFilter='UrgentCube > ignore > Gem > DailyResource',
            Commission_DoMajorCommission=True,
            Scheduler_ServerUpdate='00:00',
        )
        daily = SelectedGrids([legacy_short])
        urgent = SelectedGrids([*dynamic, legacy_long])
        now = datetime(2026, 8, 6, 10, 0, 0)

        def plan_with_one_hour_reservation(jobs, slot_available, horizon):
            actions = tuple(
                CommissionPlanAction(
                    job_index=index,
                    start=0 if index < 3 else 3600,
                    finish=(0 if index < 3 else 3600) + job.duration,
                )
                for index, job in enumerate(jobs)
            )
            return CommissionPlan(
                score=(4,),
                actions=actions,
                makespan=max(action.finish for action in actions),
                completion_sum=sum(action.finish for action in actions),
                priority_sums=(sum(job.source_index for job in jobs),),
                slot_fill_limits=(0, 0, 0, 3600),
            ), jobs

        with (
            patch('module.commission.commission.current_time', return_value=now),
            patch(
                'module.commission.commission.get_server_next_update',
                return_value=now + timedelta(days=1),
            ),
            patch(
                'module.commission.commission.optimize_commission_plan',
                side_effect=plan_with_one_hour_reservation,
            ),
        ):
            daily_choose, urgent_choose = worker._commission_choose_dynamic(daily, urgent)

        self.assertEqual(daily_choose.grids, [legacy_short])
        self.assertNotIn(legacy_long, urgent_choose.grids)
        self.assertEqual(len(urgent_choose.grids), 3)

    def test_ignore_at_start_matches_full_legacy_filter_with_shortest(self):
        gem = selectable_commission('钻石委托', 'urgent_gem', duration=8)
        long = selectable_commission('长委托', 'daily_resource', duration=3)
        short = selectable_commission('短委托', 'extra_oil', duration=1)
        medium = selectable_commission('中委托', 'extra_drill', duration=2)
        config = SimpleNamespace(
            Commission_PresetFilter='custom',
            Commission_CustomFilter='ignore > Gem > shortest',
            Commission_DoMajorCommission=True,
            Scheduler_ServerUpdate='00:00',
        )
        daily = SelectedGrids([long, short, medium])
        urgent = SelectedGrids([gem])
        dynamic_worker = object.__new__(RewardCommission)
        dynamic_worker.config = config
        legacy_worker = object.__new__(RewardCommission)
        legacy_worker.config = config

        dynamic_choose = dynamic_worker._commission_choose_dynamic(daily, urgent)
        legacy_choose = legacy_worker._commission_choose_legacy(daily, urgent)

        self.assertEqual(dynamic_choose[0].grids, legacy_choose[0].grids)
        self.assertEqual(dynamic_choose[1].grids, legacy_choose[1].grids)
        self.assertEqual(dynamic_worker.comm_choose.grids, legacy_worker.comm_choose.grids)

    def test_ignore_tail_uses_legacy_order_without_entering_planner(self):
        dynamic = selectable_commission('动态规划委托', 'urgent_cube', duration=2)
        legacy_first = selectable_commission('传统委托一', 'urgent_gem')
        legacy_second = selectable_commission('传统委托二', 'daily_resource')
        worker = object.__new__(RewardCommission)
        worker.config = SimpleNamespace(
            Commission_PresetFilter='custom',
            Commission_CustomFilter='UrgentCube > ignore > Gem > DailyResource',
            Commission_DoMajorCommission=True,
            Scheduler_ServerUpdate='00:00',
        )
        daily = SelectedGrids([legacy_second])
        urgent = SelectedGrids([dynamic, legacy_first])
        now = datetime(2026, 8, 6, 10, 0, 0)

        with (
            patch('module.commission.commission.current_time', return_value=now),
            patch(
                'module.commission.commission.get_server_next_update',
                return_value=now + timedelta(days=1),
            ),
            patch(
                'module.commission.commission.optimize_commission_plan',
                wraps=optimize_commission_plan,
            ) as optimizer,
        ):
            daily_choose, urgent_choose = worker._commission_choose_dynamic(daily, urgent)

        planned_jobs = optimizer.call_args.args[0]
        self.assertEqual([job.commission for job in planned_jobs], [dynamic])
        self.assertEqual(urgent_choose.grids, [dynamic, legacy_first])
        self.assertEqual(daily_choose.grids, [legacy_second])
        self.assertEqual(worker.comm_choose.grids, [dynamic, legacy_first, legacy_second])

    def test_matches_complete_enumeration_on_random_extreme_cases(self):
        rng = random.Random(20260806)
        for case_index in range(1000):
            job_count = rng.randint(1, 7)
            horizon = rng.randint(1, 8)
            source_indices = rng.sample(range(job_count * 3 + 5), job_count)
            jobs = [
                CommissionPlanJob(
                    source_index=source_indices[index],
                    tier=rng.choice([0, 0, 1, 2, 4]),
                    duration=rng.randint(1, 6),
                    deadline=rng.choice([None, 0, *range(1, horizon + 3)]),
                    commission=index,
                )
                for index in range(job_count)
            ]
            slots = [rng.randint(0, horizon + 2) for _ in range(rng.randint(1, 4))]

            expected, expected_jobs = brute_force_plan(jobs, slots, horizon)
            plan, planned_jobs = optimize_commission_plan(jobs, slots, horizon)
            actual_actions = tuple(
                (action.job_index, action.start, action.finish)
                for action in plan.actions
            )

            with self.subTest(case=case_index, jobs=jobs, slots=slots, horizon=horizon):
                self.assertEqual(plan.score, expected[1])
                self.assertEqual(plan.priority_sums, expected[2])
                self.assertEqual(plan.makespan, expected[3])
                self.assertEqual(plan.completion_sum, expected[4])
                self.assertEqual(actual_actions, expected[0])
                self.assertEqual(
                    [job.source_index for job in planned_jobs],
                    [job.source_index for job in expected_jobs],
                )

    def test_matches_complete_enumeration_on_systematic_boundaries(self):
        deadline_cases = (None, 0, 1, 3)
        slot_cases = ((0,), (0, 0), (0, 2))
        for durations in product(range(1, 4), repeat=3):
            for deadlines in product(deadline_cases, repeat=3):
                jobs = [
                    CommissionPlanJob(
                        source_index=(0, 2, 5)[index],
                        tier=(0, 1, 1)[index],
                        duration=durations[index],
                        deadline=deadlines[index],
                        commission=index,
                    )
                    for index in range(3)
                ]
                for slots in slot_cases:
                    expected, _ = brute_force_plan(jobs, slots, horizon=3)
                    plan, _ = optimize_commission_plan(jobs, slots, horizon=3)
                    actual = (
                        tuple(
                            (action.job_index, action.start, action.finish)
                            for action in plan.actions
                        ),
                        plan.score,
                        plan.priority_sums,
                        plan.makespan,
                        plan.completion_sum,
                    )
                    self.assertEqual(actual, expected)

    def test_regular_twenty_job_case_keeps_state_space_small(self):
        jobs = [
            CommissionPlanJob(
                source_index=index,
                tier=index // 4,
                duration=index % 7 + 1,
                deadline=None,
                commission=index,
            )
            for index in range(20)
        ]

        plan, _ = optimize_commission_plan(jobs, slot_available=[0, 0, 0, 0], horizon=10)

        self.assertEqual(plan.score, (4, 4, 4, 4, 1))
        self.assertLess(plan.state_count, 10000)

    def test_planner_returns_fill_limit_for_each_currently_free_slot(self):
        jobs = [
            CommissionPlanJob(0, tier=0, duration=2, deadline=None, commission=object()),
        ]

        plan, _ = optimize_commission_plan(jobs, slot_available=[0, 0], horizon=10)

        self.assertEqual(plan.slot_fill_limits, (0, None))

    def test_short_job_can_run_first_without_losing_higher_tier_job(self):
        high = object()
        low_first = object()
        low_second = object()
        jobs = [
            CommissionPlanJob(0, tier=0, duration=4, deadline=2, commission=high),
            CommissionPlanJob(1, tier=1, duration=1, deadline=1, commission=low_first),
            CommissionPlanJob(2, tier=1, duration=1, deadline=None, commission=low_second),
        ]

        plan, planned_jobs = optimize_commission_plan(jobs, slot_available=[0], horizon=10)
        actions = [(planned_jobs[action.job_index].commission, action.start) for action in plan.actions]

        self.assertEqual(plan.score, (1, 2))
        self.assertIn(actions[0][0], [low_first, low_second])
        self.assertEqual(actions[0][1], 0)
        self.assertEqual(actions[1], (high, 1))

    def test_running_slots_and_deadline_can_make_job_infeasible(self):
        high = object()
        low = object()
        jobs = [
            CommissionPlanJob(0, tier=0, duration=2, deadline=2, commission=high),
            CommissionPlanJob(1, tier=1, duration=1, deadline=None, commission=low),
        ]

        plan, planned_jobs = optimize_commission_plan(jobs, slot_available=[2], horizon=10)
        selected = [planned_jobs[action.job_index].commission for action in plan.actions]

        self.assertEqual(plan.score, (0, 1))
        self.assertEqual(selected, [low])

    def test_one_high_tier_job_outweighs_many_lower_tier_jobs(self):
        high = object()
        jobs = [CommissionPlanJob(0, 0, 8, 1, high)]
        jobs.extend(
            CommissionPlanJob(index, 1, 1, None, object())
            for index in range(1, 7)
        )

        plan, _ = optimize_commission_plan(jobs, slot_available=[0], horizon=6)

        self.assertEqual(plan.score[0], 1)
        self.assertLess(plan.score[1], 6)

    def test_equal_score_prefers_smaller_source_index_sum(self):
        preferred = object()
        faster = object()
        jobs = [
            CommissionPlanJob(0, tier=0, duration=10, deadline=None, commission=preferred),
            CommissionPlanJob(1, tier=0, duration=2, deadline=None, commission=faster),
        ]

        plan, planned_jobs = optimize_commission_plan(jobs, slot_available=[0], horizon=1)
        selected = [planned_jobs[action.job_index].commission for action in plan.actions]

        self.assertEqual(plan.score, (1,))
        self.assertEqual(plan.priority_sums, (0,))
        self.assertEqual(selected, [preferred])

    def test_same_selection_uses_filter_order_before_makespan(self):
        first_in_filter = object()
        second_in_filter = object()
        last_in_filter = object()
        jobs = [
            CommissionPlanJob(0, tier=0, duration=1, deadline=None, commission=first_in_filter),
            CommissionPlanJob(1, tier=0, duration=1, deadline=None, commission=second_in_filter),
            CommissionPlanJob(2, tier=0, duration=10, deadline=None, commission=last_in_filter),
        ]

        plan, planned_jobs = optimize_commission_plan(jobs, slot_available=[0, 0], horizon=20)
        selected = [planned_jobs[action.job_index].commission for action in plan.actions]

        # 长委托先启动可在 T+10 完成；过滤器顺序优先后，规范计划在 T+11 完成。
        self.assertEqual(plan.score, (3,))
        self.assertEqual(plan.makespan, 11)
        self.assertEqual(plan.completion_sum, 13)
        self.assertEqual(selected, [first_in_filter, second_in_filter, last_in_filter])

    def test_priority_sums_are_compared_by_tier(self):
        durations = [3, 4, 3, 4, 4, 3, 1, 5]
        deadlines = [5, 5, None, 5, None, 3, None, 1]
        jobs = [
            CommissionPlanJob(
                source_index=index,
                tier=0 if index < 4 else 1,
                duration=durations[index],
                deadline=deadlines[index],
                commission=index,
            )
            for index in range(8)
        ]

        plan, planned_jobs = optimize_commission_plan(jobs, slot_available=[0], horizon=7)
        selected = sorted(planned_jobs[action.job_index].commission for action in plan.actions)

        # (0, 1, 6) 的总编号和 7 大于 (0, 2, 4) 的 6，但其 T1 编号和更小。
        self.assertEqual(plan.score, (2, 1))
        self.assertEqual(plan.priority_sums, (1, 6))
        self.assertEqual(selected, [0, 1, 6])

    def test_empty_plan_keeps_tier_shaped_priority_sums(self):
        jobs = [
            CommissionPlanJob(0, tier=0, duration=1, deadline=None, commission=object()),
            CommissionPlanJob(1, tier=1, duration=1, deadline=None, commission=object()),
        ]

        plan, _ = optimize_commission_plan(jobs, slot_available=[], horizon=10)

        self.assertEqual(plan.score, (0, 0))
        self.assertEqual(plan.priority_sums, (0, 0))

    def test_rejects_invalid_job_domain_instead_of_pruning_unsafely(self):
        invalid_cases = [
            CommissionPlanJob(0, tier=0, duration=0, deadline=None, commission=object()),
            CommissionPlanJob(0, tier=-1, duration=1, deadline=None, commission=object()),
        ]

        for job in invalid_cases:
            with self.subTest(job=job), self.assertRaises(ValueError):
                optimize_commission_plan([job], slot_available=[0], horizon=10)

    def test_log_contains_current_and_future_timeline_nodes(self):
        now = datetime(2026, 8, 5, 10, 0, 0)
        jobs = [
            CommissionPlanJob(0, 0, 60, None, SimpleNamespace(name='当前委托')),
            CommissionPlanJob(1, 1, 60, None, SimpleNamespace(name='后续委托')),
        ]
        plan = CommissionPlan(
            score=(1, 1),
            actions=(
                CommissionPlanAction(0, 0, 60),
                CommissionPlanAction(1, 60, 120),
            ),
            makespan=120,
            completion_sum=180,
            priority_sums=(0, 1),
            state_count=3,
        )

        with patch('module.commission.commission.logger.info') as log:
            RewardCommission._commission_plan_log(
                plan=plan,
                jobs=jobs,
                running=[],
                plan_time=now,
                horizon_time=now + timedelta(hours=1),
            )

        output = '\n'.join(str(call.args[0]) for call in log.call_args_list)
        self.assertIn('同集合按过滤器顺序去重', output)
        self.assertIn('| T+0:00:00] 启动 T1 委托: 当前委托', output)
        self.assertIn('| T+0:01:00] 预计委托完成: 当前委托；预计启动 T2 委托: 后续委托', output)


if __name__ == '__main__':
    unittest.main()
