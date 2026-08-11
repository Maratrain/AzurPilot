import gc
import random
import unittest
import weakref
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
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
    limits = [min(job.deadline, horizon) for job in jobs]
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
                utility
                + base_values[job_index] * model.delay_factor(start, job.deadline),
                full_value + full_values[job_index],
                max(makespan, finish),
                completion_sum + finish,
                (*order_key, -job.source_index),
            )

    search(0, tuple(sorted(slot_available)), (), 0, 0, 0, 0, ())
    return best


def decimal_delay_factor(model, seconds, deadline):
    """用高精度十进制独立计算折现定点值。"""
    seconds = max(int(seconds), 0)
    deadline = int(deadline)
    if deadline <= 0:
        raise ValueError('委托 deadline 必须为正数')
    if seconds >= deadline:
        return 0
    if not seconds:
        return VALUE_SCALE

    with localcontext() as context:
        context.prec = 100
        value_s = Decimal(seconds)
        value_d = Decimal(deadline)
        value_t = Decimal(str(model.deadline_future_horizon))
        value_h = Decimal(str(model.delay_half_life))
        logarithm = (
            (value_t / value_d) ** 2
            * (Decimal(1) - value_s / value_d).ln()
            - Decimal(2).ln() * value_s / value_h
        )
        return int(
            (Decimal(VALUE_SCALE) * logarithm.exp()).to_integral_value(
                rounding=ROUND_HALF_EVEN
            )
        )


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

    def test_first_filters_ignore_control_tokens_and_deduplicate(self):
        urgent = commission('紧急魔方', 'urgent_cube')
        daily = commission('每日资源', 'daily_resource')
        extra = commission('额外石油', 'extra_oil')

        COMMISSION_FILTER.load(
            'Urgent > UrgentCube > tier > DailyResource > shortest > ExtraOil'
        )

        self.assertEqual(
            COMMISSION_FILTER.apply_first([urgent, daily, extra], count=2),
            [urgent],
        )
        self.assertEqual(
            COMMISSION_FILTER.apply_first([urgent, daily, extra], count=3),
            [urgent, daily],
        )

    def test_first_filters_apply_availability_check(self):
        pending = selectable_commission('待执行', 'urgent_cube')
        running = selectable_commission('运行中', 'urgent_cube')
        running.status = 'running'

        COMMISSION_FILTER.load('UrgentCube')

        self.assertEqual(
            COMMISSION_FILTER.apply_first(
                [pending, running],
                count=1,
                func=lambda value: value.status == 'pending',
            ),
            [pending],
        )

    def test_default_high_value_filter_count_stops_at_rule_18(self):
        rule_18 = commission('第十八条规则', 'extra_cube', duration=5)
        rule_19 = commission('第十九条规则', 'urgent_box', duration=1)

        COMMISSION_FILTER.load(DICT_FILTER_PRESET['cube_24h'])

        self.assertEqual(
            COMMISSION_FILTER.apply_first([rule_18, rule_19], count=18),
            [rule_18],
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
        self.assertIsNone(GeneratedConfig.Commission_Blacklist)
        self.assertIsInstance(GeneratedConfig.Commission_DelayHalfLife, float)
        self.assertIsInstance(GeneratedConfig.Commission_DeadlineFutureHorizon, float)
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

    def test_blacklist_uses_comma_separated_filter_rules(self):
        worker = object.__new__(RewardCommission)
        worker.config = SimpleNamespace(
            Commission_Blacklist=' ExtraBook, UrgentOil-8, Major, ',
            Commission_DoMajorCommission=True,
        )
        COMMISSION_FILTER.load('UrgentCube > DailyResource')

        self.assertFalse(worker._commission_check(
            selectable_commission('书委托', 'extra_book')
        ))
        self.assertFalse(worker._commission_check(
            selectable_commission('八小时油委托', 'urgent_oil', duration=8)
        ))
        self.assertFalse(worker._commission_check(
            selectable_commission('主要委托', 'major_comm')
        ))
        self.assertTrue(worker._commission_check(
            selectable_commission('四小时油委托', 'urgent_oil', duration=4)
        ))
        self.assertEqual(COMMISSION_FILTER.filter_raw, ['UrgentCube', 'DailyResource'])

    def test_blacklist_applies_to_both_selection_algorithms(self):
        blocked = selectable_commission('黑名单委托', 'extra_oil', duration=0.5)
        allowed = selectable_commission('允许委托', 'urgent_cube')
        daily = SelectedGrids([blocked])
        urgent = SelectedGrids([allowed])

        for dynamic in (False, True):
            with self.subTest(dynamic=dynamic):
                worker = object.__new__(RewardCommission)
                worker.config = SimpleNamespace(
                    Commission_PresetFilter='custom',
                    Commission_CustomFilter='UrgentCube > shortest',
                    Commission_Blacklist='ExtraOil',
                    Commission_DoMajorCommission=True,
                    Commission_DynamicProgramming=dynamic,
                )

                daily_choose, urgent_choose = worker._commission_choose(daily, urgent)

                self.assertEqual(daily_choose.grids, [])
                self.assertEqual(urgent_choose.grids, [allowed])

    def test_high_value_count_uses_first_rules_and_pending_status(self):
        urgent = selectable_commission('高价值紧急委托', 'urgent_cube')
        daily = selectable_commission('高价值每日委托', 'daily_resource')
        extra = selectable_commission('低价值额外委托', 'extra_oil')
        running = selectable_commission('运行中的高价值委托', 'urgent_cube')
        running.status = 'running'

        worker = object.__new__(RewardCommission)
        worker.config = SimpleNamespace(
            Commission_PresetFilter='custom',
            Commission_CustomFilter=(
                'UrgentCube > tier > DailyResource > shortest > ExtraOil'
            ),
            Commission_Blacklist='',
            Commission_DoMajorCommission=True,
        )
        worker.daily = SelectedGrids([daily, extra])
        worker.urgent = SelectedGrids([urgent, running])

        self.assertEqual(worker._commission_high_value_count(2), 2)
        self.assertEqual(worker._commission_high_value_count(1), 1)


class TestCommissionValueModel(unittest.TestCase):
    def test_default_adjacent_tier_threshold_is_finite(self):
        deadline = 12 * 60 * 60
        threshold = delay_threshold_seconds(
            tier_gap=1,
            delayed_count=1,
            delayed_deadline=deadline,
        )

        self.assertIsInstance(threshold, int)
        self.assertGreaterEqual(threshold, 0)
        self.assertLess(threshold, deadline)

    def test_delaying_more_high_value_jobs_reduces_threshold(self):
        one = delay_threshold_seconds(1, 1, 12 * 60 * 60)
        three = delay_threshold_seconds(1, 3, 12 * 60 * 60)

        self.assertLess(three, one)

    def test_earlier_delayed_filter_has_larger_delay_penalty(self):
        early = delay_threshold_seconds(
            tier_gap=1,
            delayed_count=1,
            delayed_deadline=60 * 60,
            delayed_filter_index=0,
        )
        late = delay_threshold_seconds(
            tier_gap=1,
            delayed_count=1,
            delayed_deadline=60 * 60,
            delayed_filter_index=20,
        )

        self.assertLess(early, late)

    def test_all_model_parameters_are_reflected_in_table(self):
        model = CommissionValueModel(
            tier_value_ratio=4,
            delay_half_life=3 * 60 * 60,
            filter_value_floor=7_500,
            filter_value_half_life=2,
            deadline_future_horizon=4 * 60 * 60,
        )

        table = build_table(model, 2, 2, delaying_filter_index=3, delayed_filter_index=1)

        self.assertIn('| 相邻 tier 价值倍率 | 4 |', table)
        self.assertIn('| 基础等待半衰期 | 03:00:00 |', table)
        self.assertIn('| Deadline 折现基准时间 | 04:00:00 |', table)
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
            Commission_DeadlineFutureHorizon=2.56,
            Commission_FilterValueFloor=0.7,
            Commission_FilterValueHalfLife=3.46,
        ))

        self.assertEqual(model.tier_value_ratio, 5)
        self.assertEqual(model.delay_half_life, 2.5 * 60 * 60)
        self.assertEqual(model.deadline_future_horizon, 2.6 * 60 * 60)
        self.assertEqual(model.filter_value_floor, 7_000)
        self.assertEqual(model.filter_value_half_life, 3.5)

    def test_decimal_half_lives_are_used_by_value_factors(self):
        model = CommissionValueModel(
            delay_half_life=2.5,
            filter_value_half_life=1.5,
        )

        self.assertEqual(
            model.delay_factor(5, 100),
            decimal_delay_factor(model, 5, 100),
        )
        self.assertGreater(model.filter_factor(1), model.filter_factor(2))

    def test_delay_factor_matches_independent_decimal_formula(self):
        model = CommissionValueModel(
            delay_half_life=3.5 * 3600,
            deadline_future_horizon=2.5 * 3600,
        )
        cases = ((0, 1), (1, 3803), (2468, 4937), (3599, 3600), (3600, 3600))

        for seconds, deadline in cases:
            with self.subTest(seconds=seconds, deadline=deadline):
                self.assertEqual(
                    model.delay_factor(seconds, deadline),
                    decimal_delay_factor(model, seconds, deadline),
                )

    def test_delay_factor_matches_decimal_on_random_game_range(self):
        rng = random.Random(20260809)
        for case_index in range(200):
            deadline = rng.randint(1, 24 * 3600)
            seconds = rng.randint(0, deadline + 1)
            model = CommissionValueModel(
                delay_half_life=rng.randint(1, 200) * 1800,
                deadline_future_horizon=rng.randint(1, 24) * 1800,
            )

            with self.subTest(case=case_index, seconds=seconds, deadline=deadline):
                self.assertEqual(
                    model.delay_factor(seconds, deadline),
                    decimal_delay_factor(model, seconds, deadline),
                )

    def test_delay_factor_boundaries_and_monotonicity(self):
        model = CommissionValueModel(delay_half_life=100 * 3600)
        deadline = 6 * 3600
        values = [model.delay_factor(seconds, deadline) for seconds in range(0, deadline, 60)]

        self.assertEqual(values[0], VALUE_SCALE)
        self.assertTrue(all(left > right for left, right in zip(values, values[1:])))
        self.assertEqual(model.delay_factor(deadline, deadline), 0)
        self.assertEqual(model.delay_factor(deadline + 1, deadline), 0)

    def test_larger_deadline_reduces_penalty_at_same_delay(self):
        model = CommissionValueModel(delay_half_life=100 * 3600)
        delay = 30 * 60

        self.assertLess(
            model.delay_factor(delay, 1 * 3600),
            model.delay_factor(delay, 2 * 3600),
        )
        self.assertLess(
            model.delay_factor(delay, 2 * 3600),
            model.delay_factor(delay, 6 * 3600),
        )

    def test_future_horizon_controls_deadline_penalty_strength(self):
        frequent = CommissionValueModel(deadline_future_horizon=30)
        balanced = CommissionValueModel(deadline_future_horizon=60)
        rare = CommissionValueModel(deadline_future_horizon=120)

        self.assertLess(rare.delay_factor(30, 60), balanced.delay_factor(30, 60))
        self.assertLess(balanced.delay_factor(30, 60), frequent.delay_factor(30, 60))

    def test_extreme_horizon_does_not_overflow(self):
        model = CommissionValueModel(deadline_future_horizon=1e300)

        self.assertEqual(model.delay_factor(0, 1), VALUE_SCALE)
        self.assertEqual(model.delay_factor(1, 2), 0)

    def test_value_model_is_not_retained_by_process_wide_cache(self):
        references = []
        for index in range(100):
            model = CommissionValueModel(deadline_future_horizon=index + 1)
            optimize_commission_plan(
                [CommissionPlanJob(0, 0, 1, 10, object())],
                [0],
                10,
                model,
            )
            references.append(weakref.ref(model))

        del model
        gc.collect()
        self.assertTrue(all(reference() is None for reference in references))

    def test_deadline_threshold_is_before_expiration(self):
        threshold = delay_threshold_seconds(
            tier_gap=0,
            delayed_count=1,
            delayed_deadline=60,
        )

        self.assertGreaterEqual(threshold, 0)
        self.assertLess(threshold, 60)

    def test_more_valuable_delaying_job_has_no_deadline_threshold(self):
        threshold = delay_threshold_seconds(
            tier_gap=0,
            delayed_count=1,
            delaying_filter_index=0,
            delayed_filter_index=20,
            delayed_deadline=60,
        )

        self.assertIsNone(threshold)

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
            delayed_deadline=6 * 3600,
            model=model,
            delaying_filter_index=4,
            delayed_filter_index=1,
        )
        high = 5 ** 2 * model.filter_factor(1)
        low = model.filter_factor(4)
        immediate = 3 * high * VALUE_SCALE

        self.assertGreater(
            low * VALUE_SCALE + 3 * high * model.delay_factor(threshold, 6 * 3600),
            immediate,
        )
        self.assertLessEqual(
            low * VALUE_SCALE
            + 3 * high * model.delay_factor(threshold + 1, 6 * 3600),
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
            CommissionPlanJob(0, 0, 4 * 3600, 12 * 3600, high),
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
            CommissionPlanJob(0, 0, 4 * 3600, 12 * 3600, high),
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
            CommissionPlanJob(0, 0, 4 * 3600, 12 * 3600, high),
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
                deadline_future_horizon=rng.randint(1, 10),
            )
            jobs = [
                CommissionPlanJob(
                    source_index=index,
                    tier=rng.choice([0, 0, 1, 2, 4]),
                    duration=rng.randint(1, 6),
                    deadline=rng.choice([0, *range(1, horizon + 3)]),
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
            for deadlines in product((0, 1, 3, 4), repeat=3):
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
                deadline=10 * 3600,
                commission=index,
                filter_index=index % 4,
            )
            for index in range(20)
        ]

        plan, _ = optimize_commission_plan(jobs, [0, 0, 0, 0], 10 * 3600, model)

        self.assertLess(plan.state_count, 5000)

    def test_beam_search_has_polynomial_state_bound(self):
        jobs = [
            CommissionPlanJob(index, index % 4, index % 5 + 1, 30, index, index % 3)
            for index in range(12)
        ]

        plan, _ = optimize_commission_plan(jobs, [0, 0], 30, beam_width=5)

        # 最多 n 层、每层最多展开 beam_width 个状态。
        self.assertLessEqual(plan.state_count, len(jobs) * plan.beam_width)
        self.assertEqual(plan.beam_width, 5)

    def test_default_beam_width_grows_at_most_linearly(self):
        jobs = [
            CommissionPlanJob(index, 0, 1, 10, index)
            for index in range(100)
        ]

        plan, _ = optimize_commission_plan(jobs, [], 10)

        self.assertLessEqual(plan.beam_width, 128 + 16 * len(jobs))

    def test_pruned_plan_reports_strict_upper_bound(self):
        model = CommissionValueModel(
            tier_value_ratio=1.5,
            delay_half_life=7,
            filter_value_floor=9477,
            filter_value_half_life=7.5,
            deadline_future_horizon=1,
        )
        values = (
            (3, 5, 5, 5),
            (2, 4, 9, 1),
            (0, 2, 7, 5),
            (2, 5, 6, 0),
            (3, 3, 7, 6),
            (1, 1, 7, 1),
            (1, 2, 8, 5),
            (1, 6, 7, 4),
        )
        jobs = [
            CommissionPlanJob(index, tier, duration, deadline, index, filter_index)
            for index, (tier, duration, deadline, filter_index) in enumerate(values)
        ]

        expected_rank, _ = brute_force_plan(jobs, [3, 3, 3, 2], 6, model)
        plan, _ = optimize_commission_plan(
            jobs,
            [3, 3, 3, 2],
            6,
            model,
            beam_width=1,
        )

        self.assertGreater(plan.pruned_state_count, 0)
        self.assertLessEqual(expected_rank[0], plan.utility_upper_bound)
        self.assertGreaterEqual(plan.utility_gap, expected_rank[0] - plan.utility)
        self.assertFalse(plan.optimality_proven)

    def test_unpruned_search_proves_global_optimality(self):
        jobs = [
            CommissionPlanJob(0, 0, 2, 8, 0),
            CommissionPlanJob(1, 1, 1, 5, 1),
            CommissionPlanJob(2, 2, 3, 7, 2),
        ]

        expected_rank, _ = brute_force_plan(jobs, [0], 8)
        plan, _ = optimize_commission_plan(jobs, [0], 8)

        self.assertEqual(plan.pruned_state_count, 0)
        self.assertTrue(plan.optimality_proven)
        self.assertEqual(plan.utility_upper_bound, expected_rank[0])

    def test_equivalent_jobs_use_one_stable_representative_per_state(self):
        jobs = [
            CommissionPlanJob(
                source_index=index,
                tier=0,
                duration=1,
                deadline=1000,
                commission=index,
            )
            for index in range(64)
        ]

        plan, _ = optimize_commission_plan(jobs, [0], 1000)

        self.assertEqual(plan.state_count, len(jobs))
        self.assertEqual(
            [action.job_index for action in plan.actions],
            list(range(len(jobs))),
        )

    def test_nearer_deadline_job_is_started_first_when_other_values_match(self):
        near = object()
        far = object()
        jobs = [
            CommissionPlanJob(0, 0, 3600, 2 * 3600, near),
            CommissionPlanJob(1, 0, 3600, 8 * 3600, far),
        ]

        plan, planned_jobs = optimize_commission_plan(jobs, [0], 12 * 3600)

        self.assertEqual(
            [planned_jobs[action.job_index].commission for action in plan.actions],
            [near, far],
        )

    def test_near_deadline_prevents_two_tier_job_from_taking_slot(self):
        high = object()
        low = object()
        jobs = [
            CommissionPlanJob(0, 0, 4 * 3600, 1 * 3600, high),
            CommissionPlanJob(1, 2, 30 * 60, 1, low),
        ]
        model = CommissionValueModel(
            tier_value_ratio=2,
            delay_half_life=100 * 3600,
            deadline_future_horizon=2 * 3600,
        )

        plan, planned_jobs = optimize_commission_plan(jobs, [0], 12 * 3600, model)

        self.assertEqual(
            [planned_jobs[action.job_index].commission for action in plan.actions],
            [high],
        )

    def test_far_deadline_allows_two_tier_job_to_take_slot(self):
        high = object()
        low = object()
        jobs = [
            CommissionPlanJob(0, 0, 4 * 3600, 6 * 3600, high),
            CommissionPlanJob(1, 2, 30 * 60, 1, low),
        ]
        model = CommissionValueModel(
            tier_value_ratio=2,
            delay_half_life=100 * 3600,
            deadline_future_horizon=2 * 3600,
        )

        plan, planned_jobs = optimize_commission_plan(jobs, [0], 12 * 3600, model)

        self.assertEqual(
            [planned_jobs[action.job_index].commission for action in plan.actions],
            [low, high],
        )

    def test_empty_plan_keeps_tier_shaped_score(self):
        jobs = [
            CommissionPlanJob(0, 0, 1, 10, object()),
            CommissionPlanJob(1, 1, 1, 10, object()),
        ]

        plan, _ = optimize_commission_plan(jobs, [], 10)

        self.assertEqual(plan.score, (0, 0))

    def test_rejects_invalid_model_and_job_domains(self):
        with self.assertRaises(ValueError):
            CommissionValueModel(tier_value_ratio=1)
        with self.assertRaises(ValueError):
            CommissionValueModel(deadline_future_horizon=0)
        invalid_jobs = [
            CommissionPlanJob(0, 0, 0, 10, object()),
            CommissionPlanJob(0, -1, 1, 10, object()),
            CommissionPlanJob(0, 0, 1, 10, object(), filter_index=-1),
            CommissionPlanJob(0, 0, 1, -1, object()),
        ]
        for job in invalid_jobs:
            with self.subTest(job=job), self.assertRaises(ValueError):
                optimize_commission_plan([job], [0], 10)

    def test_float_tier_value_ratio(self):
        model = CommissionValueModel(tier_value_ratio=1.5)
        self.assertEqual(model.tier_value_ratio, 1.5)

        jobs = [
            CommissionPlanJob(0, 0, 10, 30, object()),
            CommissionPlanJob(1, 1, 10, 30, object()),
        ]
        plan, _ = optimize_commission_plan(jobs, [0], 30, model=model)
        expected_rank, expected_actions = brute_force_plan(jobs, [0], 30, model=model)
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
