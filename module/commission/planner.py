"""委托动态规划调度器。

根据委托价值层级、执行时长、可启动截止时间、当前运行槽位和服务器刷新时间，
计算当前可见委托的全局最优启动计划。价值使用层级计数向量表示，并按字典序比较，
因此任意一个高层级委托都优先于任意数量的低层级委托；价值向量相同时，
再按层级依次比较候选编号和，优先选择首个不同层级中编号和更小的策略。
例如 ``(T1=4, T2=9)`` 劣于 ``(T1=3, T2=13)``，不会用 T2 的优势抵消 T1。
最晚结束时间仍相同时，同一委托集合的不同排列按过滤器顺序去重，再比较后续规则。

求解分为两个严格等价阶段：第一阶段用完成截止时间排序定理把排列搜索降为
槽位分配搜索，并结合状态支配、容量上界和可分割负载下界求主目标；第二阶段
只在主目标最优切面上枚举精确集合，用可行性判定器直接恢复字典序最小计划。
所有界均为乐观界，只会排除已被数学证明不可能更优的状态。
"""

from dataclasses import dataclass
from functools import lru_cache
from itertools import product


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


@dataclass(frozen=True)
class _PartialPlan:
    """分支限界搜索中的一条部分计划。"""

    parent: '_PartialPlan | None'
    job_index: int
    start: int
    finish: int
    job_order: tuple[int, ...]
    makespan: int
    completion_sum: int


def _slots_no_later(left, right):
    """判断排序后的槽位向量是否逐项不晚于另一个向量。"""
    return all(left_value <= right_value for left_value, right_value in zip(left, right))


def _mandatory_makespan_lower_bound(slots, durations, current_makespan):
    """计算安排全部给定委托所需最晚结束时间的乐观下界。

    下界允许把委托耗时任意切分到槽位，因此只会低估真实最优工期；同时
    每条委托都不可能早于当前最早槽位完成。它仅用于证明某分支不可能改善
    已知工期，不会排除任何可能更优的整数调度。
    """
    if not durations:
        return current_makespan

    earliest = slots[0]
    lower = max(current_makespan, max(earliest + duration for duration in durations))
    workload = sum(durations)
    low = earliest
    high = max(slots) + workload
    while low < high:
        middle = (low + high) // 2
        capacity = sum(max(middle - available, 0) for available in slots)
        if capacity >= workload:
            high = middle
        else:
            low = middle + 1
    return max(lower, low)


def _tier_cardinality_upper(candidates, slots):
    """返回单个价值层级在忽略其他层级竞争时的数量上界。

    对任意截止时刻 ``D``，所有截止不晚于 ``D`` 的已选委托都必须已经
    启动；其中至多有槽位数条仍可跨越 ``D`` 运行，其余委托的总耗时必须
    放进 ``D`` 之前的槽位容量。允许任选最短耗时并忽略不可分割性得到的
    只是必要条件，因此以它剪枝保持数学无损。
    """
    if not candidates:
        return 0

    machine_count = len(slots)
    upper = len(candidates)
    for limit in sorted({limit for _, _, limit in candidates}):
        early = sorted(
            job.duration
            for _, job, job_limit in candidates
            if job_limit <= limit
        )
        capacity = sum(max(limit - available, 0) for available in slots)
        completed = 0
        used = 0
        for duration in early:
            if used + duration > capacity:
                break
            used += duration
            completed += 1
        early_upper = min(len(early), machine_count + completed)
        upper = min(upper, len(candidates) - len(early) + early_upper)
    return upper


def _restore_actions(partial):
    """沿父指针恢复最终动作序列。"""
    actions = []
    while partial.parent is not None:
        actions.append(CommissionPlanAction(
            job_index=partial.job_index,
            start=partial.start,
            finish=partial.finish,
        ))
        partial = partial.parent
    actions.reverse()
    return tuple(actions)


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

    empty = _PartialPlan(
        parent=None,
        job_index=-1,
        start=0,
        finish=0,
        job_order=(),
        makespan=0,
        completion_sum=0,
    )
    state_count = 0
    limits = tuple(
        min(job.deadline if job.deadline is not None else horizon, horizon)
        for job in jobs
    )

    def selection_upper(score, priority_sums, candidates_by_tier, slots):
        """计算忽略不可分割性和跨层竞争细节后的字典序乐观上界。"""
        upper_score = list(score)
        upper_priority_sums = list(priority_sums)
        all_candidates = [candidate for tier in candidates_by_tier for candidate in tier]
        remaining_count_upper = _tier_cardinality_upper(all_candidates, slots)
        all_candidates_mandatory = remaining_count_upper == len(all_candidates)
        for tier, candidates in enumerate(candidates_by_tier):
            tier_upper = _tier_cardinality_upper(candidates, slots)
            count_upper = min(tier_upper, remaining_count_upper)
            remaining_count_upper -= count_upper
            upper_score[tier] += count_upper
            upper_priority_sums[tier] += sum(sorted(
                job.source_index for _, job, _ in candidates
            )[:count_upper])
            if count_upper != len(candidates):
                all_candidates_mandatory = False
        return (
            (tuple(upper_score), tuple(-value for value in upper_priority_sums)),
            all_candidates_mandatory,
        )

    # 第一阶段只求价值、编号和与最晚结束时间。把启动截止约束改写为完成
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
    primary_frontiers = {}
    best_primary_rank = ((0,) * tier_count, (0,) * tier_count, 0)

    def search_primary(position, slots, makespan, score, priority_sums):
        """求解前三项主目标的全局最优值。"""
        nonlocal state_count, best_primary_rank

        state_key = (position, score, priority_sums)
        frontier = primary_frontiers.setdefault(state_key, [])
        for known_slots, known_makespan in frontier:
            if known_makespan <= makespan and _slots_no_later(known_slots, slots):
                return
        frontier[:] = [
            (known_slots, known_makespan)
            for known_slots, known_makespan in frontier
            if not (
                makespan <= known_makespan
                and _slots_no_later(slots, known_slots)
            )
        ]
        frontier.append((slots, makespan))
        state_count += 1

        primary_rank = (
            score,
            tuple(-value for value in priority_sums),
            -makespan,
        )
        if primary_rank > best_primary_rank:
            best_primary_rank = primary_rank

        if position >= len(primary_jobs):
            return
        remaining = primary_jobs[position:]
        candidates_by_tier = [[] for _ in range(tier_count)]
        for candidate in remaining:
            candidates_by_tier[candidate[1].tier].append(candidate)
        upper_selection_rank, all_candidates_mandatory = selection_upper(
            score,
            priority_sums,
            candidates_by_tier,
            slots,
        )
        best_selection_rank = best_primary_rank[:2]
        if upper_selection_rank < best_selection_rank:
            return
        if upper_selection_rank == best_selection_rank and all_candidates_mandatory:
            lower_bound = _mandatory_makespan_lower_bound(
                slots=slots,
                durations=[job.duration for _, job, _ in remaining],
                current_makespan=makespan,
            )
            if lower_bound > -best_primary_rank[2]:
                return

        _, job, limit = primary_jobs[position]
        # 相同可用时间的槽位完全对称，只展开一次。先尝试分配可尽早形成强下界。
        used_available = set()
        for slot_index, start in enumerate(slots):
            if start in used_available:
                continue
            used_available.add(start)
            if start >= limit:
                continue
            finish = start + job.duration
            next_slots_list = list(slots)
            next_slots_list[slot_index] = finish
            next_slots = tuple(sorted(next_slots_list))
            next_score = list(score)
            next_score[job.tier] += 1
            next_priority_sums = list(priority_sums)
            next_priority_sums[job.tier] += job.source_index
            search_primary(
                position + 1,
                next_slots,
                max(makespan, finish),
                tuple(next_score),
                tuple(next_priority_sums),
            )
        search_primary(position + 1, slots, makespan, score, priority_sums)

    search_primary(
        position=0,
        slots=slot_available,
        makespan=0,
        score=(0,) * tier_count,
        priority_sums=(0,) * tier_count,
    )

    target_score = best_primary_rank[0]
    target_priority_sums = tuple(-value for value in best_primary_rank[1])
    target_makespan = -best_primary_rank[2]

    # 第二阶段先按精确数量与编号和生成主目标允许的选择集合，再用同一 EDD
    # 可行性定理作为后缀判定器，逐位选择过滤器编号最小的可行动作。这样可
    # 直接构造每个集合的字典序最小序列，无需枚举其余排列。
    best_by_selection = {}
    tier_options = []
    for tier in range(tier_count):
        indices = tuple(sorted(
            (index for index, job in enumerate(jobs) if job.tier == tier),
            key=lambda index: jobs[index].source_index,
        ))

        @lru_cache(maxsize=None)
        def exact_masks(position, count, source_sum):
            """生成满足精确数量与编号和的掩码，并用和区间无损剪枝。"""
            if not count:
                return (0,) if not source_sum else ()
            if len(indices) - position < count:
                return ()
            remaining_sources = [
                jobs[index].source_index for index in indices[position:]
            ]
            if (
                source_sum < sum(remaining_sources[:count])
                or source_sum > sum(remaining_sources[-count:])
            ):
                return ()

            index = indices[position]
            source_index = jobs[index].source_index
            selected = tuple(
                mask | (1 << index)
                for mask in exact_masks(position + 1, count - 1, source_sum - source_index)
            )
            skipped = exact_masks(position + 1, count, source_sum)
            return (*selected, *skipped)

        tier_options.append(exact_masks(
            0,
            target_score[tier],
            target_priority_sums[tier],
        ))

    @lru_cache(maxsize=None)
    def can_finish(selected_mask, slots):
        """判断固定集合能否从当前槽位状态在目标工期内完成。"""
        nonlocal state_count
        state_count += 1
        if not selected_mask:
            return True

        job_index = min(
            (index for index in range(len(jobs)) if selected_mask & (1 << index)),
            key=lambda index: (
                limits[index] + jobs[index].duration,
                jobs[index].tier,
                jobs[index].source_index,
            ),
        )
        job = jobs[job_index]
        used_available = set()
        for slot_index, start in enumerate(slots):
            if start in used_available:
                continue
            used_available.add(start)
            finish = start + job.duration
            if start >= limits[job_index] or finish > target_makespan:
                continue
            next_slots = list(slots)
            next_slots[slot_index] = finish
            if can_finish(
                selected_mask ^ (1 << job_index),
                tuple(sorted(next_slots)),
            ):
                return True
        return False

    source_order = sorted(range(len(jobs)), key=lambda index: jobs[index].source_index)
    for tier_masks in product(*tier_options):
        selected_mask = 0
        for tier_mask in tier_masks:
            selected_mask |= tier_mask
        if not can_finish(selected_mask, slot_available):
            continue

        remaining_mask = selected_mask
        slots = slot_available
        partial = empty
        while remaining_mask:
            start = slots[0]
            for job_index in source_order:
                bit = 1 << job_index
                if not remaining_mask & bit:
                    continue
                job = jobs[job_index]
                finish = start + job.duration
                if start >= limits[job_index] or finish > target_makespan:
                    continue
                next_slots = tuple(sorted((finish, *slots[1:])))
                if not can_finish(remaining_mask ^ bit, next_slots):
                    continue
                partial = _PartialPlan(
                    parent=partial,
                    job_index=job_index,
                    start=start,
                    finish=finish,
                    job_order=(*partial.job_order, job_index),
                    makespan=max(partial.makespan, finish),
                    completion_sum=partial.completion_sum + finish,
                )
                remaining_mask ^= bit
                slots = next_slots
                break
            else:
                raise RuntimeError('委托规划器无法恢复已证明可行的最优计划')
        best_by_selection[selected_mask] = partial

    best_plan = empty
    best_rank = None
    for selected_mask, partial in best_by_selection.items():
        rank = (
            -partial.completion_sum,
            tuple(-job_index for job_index in partial.job_order),
        )
        if best_rank is None or rank > best_rank:
            best_rank = rank
            best_plan = partial

    actions = _restore_actions(best_plan)
    return CommissionPlan(
        score=target_score,
        actions=actions,
        makespan=target_makespan,
        completion_sum=best_plan.completion_sum,
        priority_sums=target_priority_sums,
        state_count=state_count,
        slot_fill_limits=_get_slot_fill_limits(actions, slot_available),
    ), jobs
