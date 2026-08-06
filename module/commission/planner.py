"""委托动态规划调度器。

根据委托价值层级、执行时长、可启动截止时间、当前运行槽位和服务器刷新时间，
计算当前可见委托的全局最优启动计划。价值使用层级计数向量表示，并按字典序比较，
因此任意一个高层级委托都优先于任意数量的低层级委托；价值向量相同时，
再按层级依次比较候选编号和，优先选择首个不同层级中编号和更小的策略。
例如 ``(T1=4, T2=9)`` 劣于 ``(T1=3, T2=13)``，不会用 T2 的优势抵消 T1。
对同一委托集合，先按原过滤器顺序选出字典序最小的可行排列，再比较最晚结束时间。

求解分为两个严格等价阶段：第一阶段用完成截止时间排序定理把排列搜索降为
槽位分配搜索，并结合状态支配和容量上界求价值目标；第二阶段只在价值目标
最优切面上枚举精确集合，用可行性判定器直接恢复各集合的字典序最小计划，
最后才比较这些规范计划的最晚结束时间。
所有界均为乐观界，只会排除已被数学证明不可能更优的状态。

正确性依据：``start < limit`` 等价于 ``finish < limit + duration``，固定
槽位分配后可用 EDD 交换论证规范化顺序；容量上界允许任务可分割并任选
最短耗时，只会高估可选数量；槽位支配只删除逐项更晚的同价值状态。第二
阶段完整枚举主目标精确相等的集合，固定集合可行性使用 EDD 顺序穷举所有
互不支配的槽位分配，再逐位选择仍有可行后缀的最小过滤器编号。因此每个
集合恢复的是过滤器字典序最小计划，集合间剩余目标也在完整候选上比较。
"""

from dataclasses import dataclass
from functools import lru_cache
from itertools import accumulate, product

from module.commission.planner_utils import (
    cardinality_profile,
    cardinality_upper,
    makespan_lower_bound,
    nondominated_slot_updates,
)


@dataclass(frozen=True)
class CommissionPlanJob:
    """动态规划使用的不可变委托信息。"""

    source_index: int
    tier: int
    duration: int
    deadline: int | None
    commission: object


@dataclass(frozen=True)
class CommissionPlanAction:
    """一条计划启动记录，时间均为相对规划时刻的秒数。"""

    job_index: int
    start: int
    finish: int


@dataclass(frozen=True)
class CommissionPlan:
    """动态规划结果。

    ``priority_sums`` 是各价值层级的候选编号和；``slot_fill_limits``
    按当前空闲槽位列出传统委托可占用的最长秒数，``None`` 表示该槽位
    在规划边界内未被动态规划占用。
    """

    score: tuple[int, ...]
    actions: tuple[CommissionPlanAction, ...]
    makespan: int
    completion_sum: int
    priority_sums: tuple[int, ...] = ()
    state_count: int = 0
    slot_fill_limits: tuple[int | None, ...] = ()


def _get_slot_fill_limits(actions, slot_available):
    """还原当前空闲槽位在首个动态规划动作前可使用的时间窗口。"""
    initial = tuple(max(int(value), 0) for value in slot_available)
    slots = sorted((available, index) for index, available in enumerate(initial))
    first_starts = {}

    for action in actions:
        available, slot_index = slots.pop(0)
        if available != action.start:
            raise RuntimeError('委托规划动作与槽位时间线不一致')
        if initial[slot_index] == 0 and slot_index not in first_starts:
            first_starts[slot_index] = action.start
        slots.append((action.finish, slot_index))
        slots.sort()

    return tuple(
        first_starts.get(index)
        for index, available in enumerate(initial)
        if available == 0
    )


def optimize_commission_plan(jobs, slot_available, horizon):
    """计算最大价值的并行委托启动计划。

    所有待选委托在规划时刻已经可用。求主目标时按完成截止时间规范化每个
    槽位内的顺序；恢复动作时通过精确后缀可行性判定，把下一个委托放入最早
    空闲槽位。完全相同的槽位排序后记忆化，以消除槽位编号造成的重复状态。
    由于任务没有未来释放时间且约束均为启动时间上界，主动闲置槽位不可能
    改善可行性或任何后续目标，因此只需考虑不空转计划。

    Args:
        jobs (list[CommissionPlanJob]): 待选委托。
        slot_available (list[int]): 各槽位距离空闲的秒数，空闲槽位为 0。
        horizon (int): 最晚允许启动新委托的相对秒数。

    Returns:
        tuple[CommissionPlan, list[CommissionPlanJob]]: 全局最优计划和规划器内部的稳定委托顺序。
    """
    if not jobs or not slot_available or horizon <= 0:
        tier_count = max((job.tier for job in jobs), default=-1) + 1
        return CommissionPlan(
            score=(0,) * tier_count,
            actions=(),
            makespan=0,
            completion_sum=0,
            priority_sums=(0,) * tier_count,
            slot_fill_limits=tuple(
                None for available in slot_available if max(int(available), 0) == 0
            ),
        ), list(jobs)

    if any(job.duration <= 0 for job in jobs):
        raise ValueError('委托规划要求所有委托耗时为正数')
    if any(job.tier < 0 for job in jobs):
        raise ValueError('委托规划要求价值层级为非负整数')

    # 同层级先按调度约束排序；约束相同时按候选编号排序，以保留编号价值。
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
    slot_available = tuple(sorted(max(int(value), 0) for value in slot_available))
    horizon = max(int(horizon), 0)

    state_count = 0
    limits = tuple(
        min(job.deadline if job.deadline is not None else horizon, horizon)
        for job in jobs
    )

    # 第一阶段只求价值与编号和。把启动截止约束改写为完成
    # 截止约束 ``finish < limit + duration`` 后，Jackson 交换论证保证：固定
    # 到每个槽位的委托集合均存在按完成截止时间非递减排列的最优日程。
    # 因而这里只需枚举“跳过或分配到某个槽位”，不再枚举同槽位内的排列。
    primary_jobs = sorted(
        (
            (job_index, job, limits[job_index])
            for job_index, job in enumerate(jobs)
        ),
        key=lambda value: (
            value[2] + value[1].duration,
            value[1].tier,
            value[1].source_index,
        ),
    )
    suffix_tier_counts = [None] * (len(primary_jobs) + 1)
    suffix_source_prefixes = [None] * (len(primary_jobs) + 1)
    suffix_cardinality_profiles = [None] * (len(primary_jobs) + 1)
    candidates_by_tier = [() for _ in range(tier_count)]
    suffix_tier_counts[-1] = (0,) * tier_count
    suffix_source_prefixes[-1] = tuple((0,) for _ in range(tier_count))
    suffix_cardinality_profiles[-1] = ((), tuple(() for _ in range(tier_count)))
    for position in range(len(primary_jobs) - 1, -1, -1):
        candidate = primary_jobs[position]
        tier = candidate[1].tier
        candidates_by_tier = list(candidates_by_tier)
        candidates_by_tier[tier] = (candidate, *candidates_by_tier[tier])
        suffix_tier_counts[position] = tuple(
            len(candidates) for candidates in candidates_by_tier
        )
        suffix_source_prefixes[position] = tuple(
            (0, *accumulate(sources))
            for sources in (
                sorted(value[1].source_index for value in candidates)
                for candidates in candidates_by_tier
            )
        )
        suffix_cardinality_profiles[position] = (
            cardinality_profile(primary_jobs[position:]),
            tuple(cardinality_profile(candidates) for candidates in candidates_by_tier),
        )

    @lru_cache(maxsize=None)
    def selection_gain_upper(position, slots):
        """返回给定后缀和槽位状态下主目标的乐观增量上界。"""
        tier_counts = suffix_tier_counts[position]
        all_profile, tier_profiles = suffix_cardinality_profiles[position]
        remaining_count_upper = cardinality_upper(
            len(primary_jobs) - position,
            all_profile,
            slots,
        )
        score_gain = []
        minimum_source_sums = []
        for tier, candidate_count in enumerate(tier_counts):
            tier_upper = cardinality_upper(
                candidate_count,
                tier_profiles[tier],
                slots,
            )
            count_upper = min(tier_upper, remaining_count_upper)
            remaining_count_upper -= count_upper
            score_gain.append(count_upper)
            minimum_source_sums.append(
                suffix_source_prefixes[position][tier][count_upper]
            )
        return tuple(score_gain), tuple(minimum_source_sums)

    primary_seen = set()
    best_selection_rank = ((0,) * tier_count, (0,) * tier_count)

    def search_primary(position, slots, score, priority_sums):
        """求解价值向量与各层候选编号和的全局最优值。"""
        nonlocal state_count, best_selection_rank

        selection_rank = (
            score,
            tuple(-value for value in priority_sums),
        )
        exact_state = (position, slots, score, priority_sums)
        if exact_state in primary_seen:
            return
        primary_seen.add(exact_state)
        state_count += 1

        if selection_rank > best_selection_rank:
            best_selection_rank = selection_rank

        if position >= len(primary_jobs):
            return
        score_gain, minimum_source_sums = selection_gain_upper(position, slots)
        upper_selection_rank = (
            tuple(value + gain for value, gain in zip(score, score_gain)),
            tuple(
                -(value + gain)
                for value, gain in zip(priority_sums, minimum_source_sums)
            ),
        )
        # 第一阶段只需要主目标的数值；上界即使只能追平，也不必保留路径供
        # 第二阶段恢复，因此等号同样可以无损剪枝。
        if upper_selection_rank <= best_selection_rank:
            return

        _, job, limit = primary_jobs[position]
        # 当前委托相同，较早槽位向量严格支配较晚向量，只展开 Pareto 前沿。
        for next_slots in nondominated_slot_updates(slots, job.duration, limit):
            next_score = list(score)
            next_score[job.tier] += 1
            next_priority_sums = list(priority_sums)
            next_priority_sums[job.tier] += job.source_index
            search_primary(
                position + 1,
                next_slots,
                tuple(next_score),
                tuple(next_priority_sums),
            )
        search_primary(position + 1, slots, score, priority_sums)

    search_primary(
        position=0,
        slots=slot_available,
        score=(0,) * tier_count,
        priority_sums=(0,) * tier_count,
    )

    target_score = best_selection_rank[0]
    target_priority_sums = tuple(-value for value in best_selection_rank[1])

    # 第二阶段先按精确数量与编号和生成价值目标允许的选择集合，再用同一
    # EDD 可行性定理作为后缀判定器，逐位选择过滤器编号最小的可行动作。
    # 每个集合只构造其规范排列，无需枚举其余排列；规范化后才比较工期。
    tier_options = []
    for tier in range(tier_count):
        indices = tuple(sorted(
            (index for index, job in enumerate(jobs) if job.tier == tier),
            key=lambda index: jobs[index].source_index,
        ))
        source_values = tuple(jobs[index].source_index for index in indices)
        source_prefix = [0]
        for value in source_values:
            source_prefix.append(source_prefix[-1] + value)

        @lru_cache(maxsize=None)
        def exact_mask_reachable(position, count, source_sum):
            """判断后缀能否组成精确数量与编号和。"""
            if not count:
                return not source_sum
            if len(indices) - position < count:
                return False
            minimum_sum = source_prefix[position + count] - source_prefix[position]
            maximum_sum = source_prefix[-1] - source_prefix[-count - 1]
            if (
                source_sum < minimum_sum
                or source_sum > maximum_sum
            ):
                return False

            source_index = source_values[position]
            return (
                exact_mask_reachable(
                    position + 1,
                    count - 1,
                    source_sum - source_index,
                )
                or exact_mask_reachable(position + 1, count, source_sum)
            )

        def iter_exact_masks(position, count, source_sum, selected_mask=0):
            """只沿可达状态惰性生成精确掩码，避免缓存重复的掩码元组。"""
            if not count:
                if not source_sum:
                    yield selected_mask
                return

            index = indices[position]
            source_index = source_values[position]
            if exact_mask_reachable(
                position + 1,
                count - 1,
                source_sum - source_index,
            ):
                yield from iter_exact_masks(
                    position + 1,
                    count - 1,
                    source_sum - source_index,
                    selected_mask | (1 << index),
                )
            if exact_mask_reachable(position + 1, count, source_sum):
                yield from iter_exact_masks(
                    position + 1,
                    count,
                    source_sum,
                    selected_mask,
                )

        tier_options.append(tuple(iter_exact_masks(
            position=0,
            count=target_score[tier],
            source_sum=target_priority_sums[tier],
        )))

    feasibility_order = tuple(sorted(
        range(len(jobs)),
        key=lambda index: (
            limits[index] + jobs[index].duration,
            jobs[index].tier,
            jobs[index].source_index,
        ),
    ))
    duration_order = tuple(sorted(
        range(len(jobs)),
        key=lambda index: (jobs[index].duration, index),
    ))
    deadline_masks = tuple(
        (
            limit,
            sum(
                1 << index
                for index, job_limit in enumerate(limits)
                if job_limit <= limit
            ),
            tuple(
                index for index in duration_order
                if limits[index] <= limit
            ),
        )
        for limit in sorted(set(limits))
    )

    def deadline_capacity_allows(selected_mask, slots):
        """检查所有启动截止截面的必要容量条件。"""
        for limit, deadline_mask, early_duration_order in deadline_masks:
            early_mask = selected_mask & deadline_mask
            early_count = early_mask.bit_count()
            if not early_count:
                continue

            active_upper = 0
            capacity = 0
            for available in slots:
                if available < limit:
                    active_upper += 1
                    capacity += limit - available
            completed_required = early_count - active_upper
            if completed_required <= 0:
                continue
            minimum_workload = 0
            completed = 0
            for job_index in early_duration_order:
                if not early_mask & (1 << job_index):
                    continue
                minimum_workload += jobs[job_index].duration
                completed += 1
                if completed >= completed_required:
                    break
            if minimum_workload > capacity:
                return False
        return True

    @lru_cache(maxsize=None)
    def can_finish(selected_mask, slots):
        """判断固定集合能否从当前槽位状态满足全部启动截止约束。"""
        nonlocal state_count
        state_count += 1
        if not selected_mask:
            return True

        if not deadline_capacity_allows(selected_mask, slots):
            return False

        job_index = next(
            index for index in feasibility_order
            if selected_mask & (1 << index)
        )
        job = jobs[job_index]
        remaining_mask = selected_mask ^ (1 << job_index)
        for next_slots in nondominated_slot_updates(
            slots,
            job.duration,
            limits[job_index],
        ):
            if can_finish(
                remaining_mask,
                next_slots,
            ):
                return True
        return False

    source_order = sorted(range(len(jobs)), key=lambda index: jobs[index].source_index)
    best_actions = ()
    best_makespan = 0
    best_completion_sum = 0
    best_rank = None
    for tier_masks in product(*tier_options):
        selected_mask = 0
        for tier_mask in tier_masks:
            selected_mask |= tier_mask
        if not can_finish(selected_mask, slot_available):
            continue
        if best_rank is not None:
            selected_durations = [
                jobs[job_index].duration
                for job_index in source_order
                if selected_mask & (1 << job_index)
            ]
            makespan_lower = makespan_lower_bound(
                slot_available,
                selected_durations,
            )
            if makespan_lower > best_makespan:
                continue
            completion_lower = sum(
                slot_available[0] + duration
                for duration in selected_durations
            )
            if (
                makespan_lower == best_makespan
                and completion_lower > best_completion_sum
            ):
                continue

        remaining_mask = selected_mask
        slots = slot_available
        actions = []
        job_order = []
        makespan = 0
        completion_sum = 0
        while remaining_mask:
            start = slots[0]
            for job_index in source_order:
                bit = 1 << job_index
                if not remaining_mask & bit:
                    continue
                job = jobs[job_index]
                finish = start + job.duration
                if start >= limits[job_index]:
                    continue
                next_slots = tuple(sorted((finish, *slots[1:])))
                if not can_finish(remaining_mask ^ bit, next_slots):
                    continue
                actions.append(CommissionPlanAction(
                    job_index=job_index,
                    start=start,
                    finish=finish,
                ))
                job_order.append(job_index)
                makespan = max(makespan, finish)
                completion_sum += finish
                remaining_mask ^= bit
                slots = next_slots
                break
            else:
                raise RuntimeError('委托规划器无法恢复已证明可行的最优计划')
        rank = (
            # 过滤器顺序已在集合内部完成规范化，后续目标只比较规范计划。
            -makespan,
            -completion_sum,
            tuple(-job_index for job_index in job_order),
        )
        if best_rank is None or rank > best_rank:
            best_rank = rank
            best_actions = tuple(actions)
            best_makespan = makespan
            best_completion_sum = completion_sum

    return CommissionPlan(
        score=target_score,
        actions=best_actions,
        makespan=best_makespan,
        completion_sum=best_completion_sum,
        priority_sums=target_priority_sums,
        state_count=state_count,
        slot_fill_limits=_get_slot_fill_limits(best_actions, slot_available),
    ), jobs
