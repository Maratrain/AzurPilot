"""委托规划器的数学下界与状态支配工具。"""

from bisect import bisect_right
from itertools import accumulate


def slots_no_later(left, right):
    """判断排序后的槽位向量是否逐项不晚于另一个向量。"""
    for left_value, right_value in zip(left, right):
        if left_value > right_value:
            return False
    return True


def nondominated_slot_updates(slots, duration, limit):
    """枚举安排同一委托后互不支配的槽位向量。

    若一个结果的全部槽位都不晚于另一个结果，则它对所有后续启动截止约束
    至少同样有利；两者当前价值完全相同，因此较晚结果不可能导向更优解。
    """
    candidates = set()
    for slot_index, start in enumerate(slots):
        if start >= limit:
            continue
        next_slots = list(slots)
        next_slots[slot_index] = start + duration
        candidates.add(tuple(sorted(next_slots)))

    return tuple(sorted(
        candidate
        for candidate in candidates
        if not any(
            other != candidate and slots_no_later(other, candidate)
            for other in candidates
        )
    ))


def cardinality_profile(candidates):
    """预计算候选集合在各启动截止截面的有序耗时。"""
    return tuple(
        (
            limit,
            (0, *accumulate(sorted(
                job.duration
                for _, job, job_limit in candidates
                if job_limit <= limit
            ))),
        )
        for limit in sorted({limit for _, _, limit in candidates})
    )


def cardinality_upper(candidate_count, profile, slots):
    """返回候选集合在忽略不可分割性后的数量上界。

    对任意截止时刻 ``D``，所有截止不晚于 ``D`` 的已选委托都必须已经
    启动；其中至多有在 ``D`` 前可用的槽位数条仍可跨越 ``D`` 运行，其余
    委托的总耗时必须放进 ``D`` 前的槽位容量。允许任选最短耗时并忽略
    不可分割性得到的只是必要条件，因此以它剪枝保持数学无损。
    """
    if not candidate_count:
        return 0

    upper = candidate_count
    for limit, duration_prefix in profile:
        capacity = 0
        active_upper = 0
        for available in slots:
            if available < limit:
                capacity += limit - available
                active_upper += 1
        completed = bisect_right(duration_prefix, capacity) - 1
        early_count = len(duration_prefix) - 1
        early_upper = min(early_count, active_upper + completed)
        upper = min(
            upper,
            candidate_count - early_count + early_upper,
        )
    return upper


def makespan_lower_bound(slots, durations):
    """返回完成固定委托集合所需工期的可分割负载下界。"""
    if not durations:
        return 0

    earliest = slots[0]
    lower = max(earliest + duration for duration in durations)
    workload = sum(durations)
    low = earliest
    high = earliest + workload
    while low < high:
        middle = (low + high) // 2
        capacity = sum(max(middle - available, 0) for available in slots)
        if capacity >= workload:
            high = middle
        else:
            low = middle + 1
    return max(lower, low)
