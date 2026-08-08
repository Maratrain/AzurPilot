import random
import unittest
from datetime import datetime, timedelta
from itertools import product
from types import SimpleNamespace
from unittest.mock import patch

from dev_tools.commission_value_table import build_table
from module.commission.commission import RewardCommission
from module.commission.planner import (
    DEFAULT_VALUE_MODEL,
    VALUE_SCALE,
    CommissionPlan,
    CommissionPlanAction,
    CommissionPlanJob,
    CommissionValueModel,
    delay_threshold_seconds,
    optimize_commission_plan,
)
from module.commission.preset import DICT_FILTER_PRESET
from module.commission.project import COMMISSION_FILTER, Commission
from module.config.config_generated import GeneratedConfig
from module.map.map_grids import SelectedGrids


def commission(name, genre, duration=1):
    """构造过滤器测试使用的简化委托。"""
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


def brute_force_plan(jobs, slot_available, horizon, model=DEFAULT_VALUE_MODEL):
    """完整枚举小规模实例，返回与规划器相同的目标和动作。"""
    maximum_tier = max(job.tier for job in jobs)
    base_values = [
        round(
            (model.tier_value_ratio ** (maximum_tier - job.tier))
            * model.filter_factor(job.filter_index)
        )
        for job in jobs
    ]
    full_values = [value * VALUE_SCALE for value in base_values]
    limits = [
        min(job.deadline if job.deadline is not None else horizon, horizon)
        for job in jobs
    ]
    best = None

    def search(selected_mask, slots, actions, utility, full_value, makespan, completion_sum, order_key):
        nonlocal best
        rank = (utility, full_value, -makespan, -completion_sum, order_key)
        if best is None or rank > best[0]:
            best = (rank, tuple(actions))

        start = slots[0]
        if start >= horizon:
            return
        for job_index, job in enumerate(jobs):
            bit = 1 << job_index
            if selected_mask & bit or start >= limits[job_index]:
                continue
            finish = start + job.duration
            search(
                selected_mask | bit,
                tuple(sorted((*slots[1:], finish))),
                (*actions, (job_index, start, finish)),
                utility + base_values[job_index] * model.delay_factor(start),
                full_value + full_values[job_index],
                max(makespan, finish),
                completion_sum + finish,
                (*order_key, -job.source_index),
            )

    search(0, tuple(sorted(slot_available)), (), 0, 0, 0, 0, ())
    return best


class TestCommissionTierFilter(unittest.TestCase):
    def test_builtin_filters_no_longer_contain_ignore(self):
        for name, value in DICT_FILTER_PRESET.items():
            with self.subTest(name=name):
                self.assertNotIn('ignore', value.lower().split())
                COMMISSION_FILTER.load(value)
                self.assertIsInstance(COMMISSION_FILTER.apply_tiers([]), list)

    def test_tier_separator_groups_equal_value_commissions(self):
        urgent = commission('紧急魔方', 'urgent_cube')
        gem = commission('钻石', 'urgent_gem')
        daily = commission('每日资源', 'daily_resource')
        fallback = commission('兜底', 'extra_oil', duration=0.5)

        COMMISSION_FILTER.load('UrgentCube > Gem > tier > DailyResource > shortest')
        tiers = COMMISSION_FILTER.apply_tiers([urgent, gem, daily, fallback])

        self.assertEqual(
            tiers,
            [[(0, urgent), (1, gem)], [(0, daily), (1, fallback)]],
        )

    def test_filter_without_tier_keeps_each_rule_as_independent_tier(self):
        urgent = commission('紧急魔方', 'urgent_cube')
        gem = commission('钻石', 'urgent_gem')

        COMMISSION_FILTER.load('UrgentCube > Gem')

        self.assertEqual(
            COMMISSION_FILTER.apply_tiers([urgent, gem]),
            [[(0, urgent)], [(0, gem)]],
        )

    def test_unmatched_rules_keep_stable_tier_distance(self):
        daily = commission('每日资源', 'daily_resource')

        COMMISSION_FILTER.load('UrgentCube > Gem > DailyResource')

        self.assertEqual(
            COMMISSION_FILTER.apply_tiers([daily]),
            [[], [], [(0, daily)]],
        )

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


class TestCommissionAlgorithmSwitch(unittest.TestCase):
    def test_dynamic_programming_is_disabled_by_default(self):
        self.assertIs(GeneratedConfig.Commission_DynamicProgramming, False)
        self.assertIsInstance(GeneratedConfig.Commission_DelayHalfLife, float)
        self.assertIsInstance(GeneratedConfig.Commission_FilterValueHalfLife, float)

    def test_dispatches_to_legacy_algorithm_by_default(self):
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

    def test_dispatches_to_experimental_planner_when_enabled(self):
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

    def test_legacy_strategy_keeps_filter_order(self):
        first = selectable_commission('优先委托', 'urgent_cube')
        second = selectable_commission('次级委托', 'daily_resource')
        worker = object.__new__(RewardCommission)
        worker.config = SimpleNamespace(
            Commission_PresetFilter='custom',
            Commission_CustomFilter='UrgentCube > tier > DailyResource > shortest',
            Commission_DoMajorCommission=True,
        )

        daily_choose, urgent_choose = worker._commission_choose_legacy(
            SelectedGrids([second]),
            SelectedGrids([first]),
        )

        self.assertEqual(urgent_choose.grids, [first])
        self.assertEqual(daily_choose.grids, [second])


class TestCommissionValueModel(unittest.TestCase):
    def test_default_adjacent_tier_threshold_is_finite(self):
        threshold = delay_threshold_seconds(tier_gap=1, delayed_count=1)

        self.assertIsInstance(threshold, int)
        self.assertGreaterEqual(threshold, 0)
        self.assertLess(threshold, DEFAULT_VALUE_MODEL.delay_half_life)

    def test_delaying_more_high_value_jobs_reduces_threshold(self):
        one = delay_threshold_seconds(tier_gap=1, delayed_count=1)
        three = delay_threshold_seconds(tier_gap=1, delayed_count=3)

        self.assertLess(three, one)

    def test_earlier_delayed_filter_has_larger_delay_penalty(self):
        early = delay_threshold_seconds(
            tier_gap=1,
            delayed_count=1,
            delayed_filter_index=0,
        )
        late = delay_threshold_seconds(
            tier_gap=1,
            delayed_count=1,
            delayed_filter_index=20,
        )

        self.assertLess(early, late)

    def test_all_model_parameters_are_reflected_in_table(self):
        model = CommissionValueModel(
            tier_value_ratio=4,
            delay_half_life=3 * 60 * 60,
            filter_value_floor=7_500,
            filter_value_half_life=2,
        )

        table = build_table(model, 2, 2, delaying_filter_index=3, delayed_filter_index=1)

        self.assertIn('| 相邻 tier 价值倍率 | 4 |', table)
        self.assertIn('| 启动等待半衰期 | 03:00:00 |', table)
        self.assertIn('| 层内价值下限 | 75.00% |', table)
        self.assertIn('| 层内编号半衰期 | 2 |', table)
        self.assertIn('| 低价值委托层内编号 | 3 (83.84%) |', table)
        self.assertIn('| 被延迟委托层内编号 | 1 (92.68%) |', table)
        self.assertIn('## 层内价值衰减表', table)
        self.assertIn('| 第 1 个元素 | 0 | 100.00% |', table)
        self.assertIn('| 第 2 个元素 | 1 | 92.68% |', table)
        self.assertIn('| 第 4 个元素 | 3 | 83.84% |', table)
        self.assertIn('| 2 |', table)

    def test_runtime_model_reads_all_ui_parameters(self):
        model = CommissionValueModel.from_config(SimpleNamespace(
            Commission_TierValueRatio=5,
            Commission_DelayHalfLife=2.54,
            Commission_FilterValueFloor=0.7,
            Commission_FilterValueHalfLife=3.46,
        ))

        self.assertEqual(model.tier_value_ratio, 5)
        self.assertEqual(model.delay_half_life, 2.5 * 60 * 60)
        self.assertEqual(model.filter_value_floor, 7_000)
        self.assertEqual(model.filter_value_half_life, 3.5)

    def test_decimal_half_lives_are_used_by_value_factors(self):
        model = CommissionValueModel(
            delay_half_life=2.5,
            filter_value_half_life=1.5,
        )

        self.assertEqual(model.delay_factor(5), round(VALUE_SCALE / 4))
        self.assertGreater(model.filter_factor(1), model.filter_factor(2))

    def test_threshold_is_the_last_strictly_profitable_second(self):
        model = CommissionValueModel(
            tier_value_ratio=5,
            delay_half_life=3 * 60 * 60,
            filter_value_floor=6_000,
            filter_value_half_life=3,
        )
        threshold = delay_threshold_seconds(
            tier_gap=2,
            delayed_count=3,
            model=model,
            delaying_filter_index=4,
            delayed_filter_index=1,
        )
        high = 5 ** 2 * model.filter_factor(1)
        low = model.filter_factor(4)
        immediate = 3 * high * VALUE_SCALE

        self.assertGreater(low * VALUE_SCALE + 3 * high * model.delay_factor(threshold), immediate)
        self.assertLessEqual(
            low * VALUE_SCALE + 3 * high * model.delay_factor(threshold + 1),
            immediate,
        )


class TestCommissionPlanner(unittest.TestCase):
    @staticmethod
    def conservative_model():
        """返回用于验证高低 tier 取舍边界的固定基准模型。"""
        return CommissionValueModel(
            tier_value_ratio=8,
            delay_half_life=6 * 60 * 60,
            filter_value_floor=5_000,
            filter_value_half_life=4,
        )

    def test_short_adjacent_tier_job_may_delay_higher_tier(self):
        high = object()
        low = object()
        jobs = [
            CommissionPlanJob(0, 0, 4 * 3600, None, high),
            CommissionPlanJob(1, 1, 1 * 3600, 1, low),
        ]

        plan, planned_jobs = optimize_commission_plan(
            jobs, [0], 12 * 3600, self.conservative_model()
        )
        selected = [planned_jobs[action.job_index].commission for action in plan.actions]

        self.assertEqual(selected, [low, high])

    def test_long_adjacent_tier_job_is_dropped_instead_of_delaying_high_value(self):
        high = object()
        low = object()
        jobs = [
            CommissionPlanJob(0, 0, 4 * 3600, None, high),
            CommissionPlanJob(1, 1, 2 * 3600, 1, low),
        ]

        plan, planned_jobs = optimize_commission_plan(
            jobs, [0], 12 * 3600, self.conservative_model()
        )
        selected = [planned_jobs[action.job_index].commission for action in plan.actions]

        self.assertEqual(selected, [high])

    def test_extremely_low_tier_job_does_not_delay_high_value_job(self):
        high = object()
        low = object()
        jobs = [
            CommissionPlanJob(0, 0, 4 * 3600, None, high),
            CommissionPlanJob(1, 3, 20 * 60, 1, low),
        ]

        plan, planned_jobs = optimize_commission_plan(
            jobs, [0], 12 * 3600, self.conservative_model()
        )
        selected = [planned_jobs[action.job_index].commission for action in plan.actions]

        self.assertEqual(selected, [high])

    def test_matches_complete_enumeration_on_random_cases(self):
        rng = random.Random(20260807)
        for case_index in range(1000):
            job_count = rng.randint(1, 7)
            horizon = rng.randint(1, 8)
            model = CommissionValueModel(
                tier_value_ratio=rng.randint(2, 10),
                delay_half_life=rng.randint(2, 20) / 2,
                filter_value_floor=rng.randint(1, 10_000),
                filter_value_half_life=rng.randint(2, 16) / 2,
            )
            jobs = [
                CommissionPlanJob(
                    source_index=index,
                    tier=rng.choice([0, 0, 1, 2, 4]),
                    duration=rng.randint(1, 6),
                    deadline=rng.choice([None, 0, *range(1, horizon + 3)]),
                    commission=index,
                    filter_index=rng.randint(0, 6),
                )
                for index in range(job_count)
            ]
            slots = [rng.randint(0, horizon + 2) for _ in range(rng.randint(1, 4))]

            expected_rank, expected_actions = brute_force_plan(jobs, slots, horizon, model)
            plan, _ = optimize_commission_plan(jobs, slots, horizon, model)
            actual_rank = (
                plan.utility,
                plan.full_value,
                -plan.makespan,
                -plan.completion_sum,
                tuple(-jobs[action.job_index].source_index for action in plan.actions),
            )
            actual_actions = tuple(
                (action.job_index, action.start, action.finish)
                for action in plan.actions
            )

            with self.subTest(case=case_index, jobs=jobs, slots=slots, model=model):
                self.assertEqual(actual_rank, expected_rank)
                self.assertEqual(actual_actions, expected_actions)

    def test_matches_complete_enumeration_on_systematic_boundaries(self):
        for durations in product(range(1, 4), repeat=3):
            for deadlines in product((None, 0, 1, 3), repeat=3):
                jobs = [
                    CommissionPlanJob(
                        source_index=index,
                        tier=(0, 1, 1)[index],
                        duration=durations[index],
                        deadline=deadlines[index],
                        commission=index,
                        filter_index=index,
                    )
                    for index in range(3)
                ]
                for slots in ((0,), (0, 0), (0, 2)):
                    expected_rank, expected_actions = brute_force_plan(jobs, slots, 3)
                    plan, _ = optimize_commission_plan(jobs, slots, 3)
                    actual_rank = (
                        plan.utility,
                        plan.full_value,
                        -plan.makespan,
                        -plan.completion_sum,
                        tuple(-jobs[action.job_index].source_index for action in plan.actions),
                    )
                    actual_actions = tuple(
                        (action.job_index, action.start, action.finish)
                        for action in plan.actions
                    )
                    self.assertEqual((actual_rank, actual_actions), (expected_rank, expected_actions))

    def test_regular_twenty_job_case_keeps_state_space_small(self):
        model = self.conservative_model()
        jobs = [
            CommissionPlanJob(
                source_index=index,
                tier=index // 4,
                duration=(index % 7 + 1) * 3600,
                deadline=None,
                commission=index,
                filter_index=index % 4,
            )
            for index in range(20)
        ]

        plan, _ = optimize_commission_plan(jobs, [0, 0, 0, 0], 10 * 3600, model)

        self.assertLess(plan.state_count, 5000)

    def test_empty_plan_keeps_tier_shaped_score(self):
        jobs = [
            CommissionPlanJob(0, 0, 1, None, object()),
            CommissionPlanJob(1, 1, 1, None, object()),
        ]

        plan, _ = optimize_commission_plan(jobs, [], 10)

        self.assertEqual(plan.score, (0, 0))

    def test_rejects_invalid_model_and_job_domains(self):
        with self.assertRaises(ValueError):
            CommissionValueModel(tier_value_ratio=1)
        invalid_jobs = [
            CommissionPlanJob(0, 0, 0, None, object()),
            CommissionPlanJob(0, -1, 1, None, object()),
            CommissionPlanJob(0, 0, 1, None, object(), filter_index=-1),
        ]
        for job in invalid_jobs:
            with self.subTest(job=job), self.assertRaises(ValueError):
                optimize_commission_plan([job], [0], 10)

    def test_float_tier_value_ratio(self):
        model = CommissionValueModel(tier_value_ratio=1.5)
        self.assertEqual(model.tier_value_ratio, 1.5)

        jobs = [
            CommissionPlanJob(0, 0, 10, None, object()),
            CommissionPlanJob(1, 1, 10, None, object()),
        ]
        plan, _ = optimize_commission_plan(jobs, [0], 30, model=model)
        expected_rank, expected_actions = brute_force_plan(jobs, [0], 30, model=model)
        actual_rank = (
            plan.utility,
            plan.full_value,
            -plan.makespan,
            -plan.completion_sum,
            tuple(-action.job_index for action in plan.actions),
        )
        self.assertEqual(actual_rank, expected_rank)

        config = SimpleNamespace(Commission_TierValueRatio=2.5)
        config_model = CommissionValueModel.from_config(config)
        self.assertEqual(config_model.tier_value_ratio, 2.5)


class TestCommissionIntegration(unittest.TestCase):
    def test_all_filtered_candidates_enter_single_planner(self):
        urgent = selectable_commission('紧急魔方', 'urgent_cube')
        daily = selectable_commission('每日资源', 'daily_resource')
        fallback = selectable_commission('兜底委托', 'extra_oil')
        worker = object.__new__(RewardCommission)
        worker.config = SimpleNamespace(
            Commission_DynamicProgramming=True,
            Commission_PresetFilter='custom',
            Commission_CustomFilter='UrgentCube > tier > DailyResource > shortest',
            Commission_DoMajorCommission=True,
            Scheduler_ServerUpdate='00:00',
        )
        now = datetime(2026, 8, 7, 10, 0, 0)

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
            daily_choose, urgent_choose = worker._commission_choose(
                SelectedGrids([daily, fallback]),
                SelectedGrids([urgent]),
            )

        planned = optimizer.call_args.args[0]
        self.assertEqual({job.commission for job in planned}, {urgent, daily, fallback})
        self.assertEqual(daily_choose.count, 2)
        self.assertEqual(urgent_choose.count, 1)

    def test_log_contains_value_and_timeline(self):
        now = datetime(2026, 8, 7, 10, 0, 0)
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
            utility=9 * VALUE_SCALE * VALUE_SCALE,
            full_value=10 * VALUE_SCALE * VALUE_SCALE,
            value_scale=8 * VALUE_SCALE * VALUE_SCALE,
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
        self.assertIn('折现价值', output)
        self.assertIn('等待损失', output)
        self.assertIn('启动 T1 委托: 当前委托', output)


if __name__ == '__main__':
    unittest.main()
