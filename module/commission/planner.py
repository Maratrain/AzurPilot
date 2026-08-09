"""基于启动时间折现价值、带最优性证书的委托多项式规划器。

候选委托在规划时刻均已可用；调用层把无游戏 deadline 的委托截止时间设为
服务器刷新时间。求解器展开非空转列表调度，因为任务没有释放时间且价值随
启动时间单调递减，任意最优日程都能左移成这种形式。

一般问题包含 PARTITION，除非 P=NP，不存在任意输入精确的多项式算法。本实现
每层只保留 O(n) 个严格乐观上界最大的状态，并记录所有被裁剪状态的上界。
最终值达到这些上界时即可证明全局最优，否则给出最大可能差距。折现因子先按
明确规则舍入为整数，此后目标比较全部使用整数。
"""

from dataclasses import dataclass
from functools import lru_cache
from heapq import nlargest
from math import ceil, exp2, isfinite, log, log1p


VALUE_SCALE = 1_000_000_000
LN_2 = log(2)
MIN_BEAM_WIDTH = 128
BEAM_WIDTH_FACTOR = 16


@dataclass(frozen=True)
class CommissionValueModel:
    """委托价值模型参数。

    ``tier_ratio ** (max_tier - t) * filter_factor(r) * delay_factor(s, d)``
    ``f(s, d) = (1 - s / d) ** ((T / d) ** 2) * 2 ** (-s / H)``

    ``d`` 始终是规划时刻到最晚启动时刻的总秒数，不会改成 ``d-s``；
    ``s >= d`` 时不可启动。默认值与 GUI 的 ``Commission`` 配置组一致。

    Args:
        tier_value_ratio: 相邻 tier 的基础价值倍率。
        delay_half_life: 启动等待价值减半所需秒数，支持小数。
        deadline_future_horizon: deadline 相对窗口折现的基准时间 ``T``，单位为秒。
        filter_value_floor: 层内过滤器价值下限，单位为万分比。
        filter_value_half_life: 层内编号修正衰减一半所需的规则数，支持小数。
    """

    tier_value_ratio: float = 2.0  # GUI: Commission_TierValueRatio
    delay_half_life: float = 100 * 60 * 60  # GUI 小时数转换为秒
    filter_value_floor: int = 6_000  # GUI 0~1 比例转换为万分比
    filter_value_half_life: float = 4.0  # GUI: Commission_FilterValueHalfLife
    deadline_future_horizon: float = 0.5 * 60 * 60  # GUI 小时数转换为秒

    def __post_init__(self):
        if not isfinite(self.tier_value_ratio) or self.tier_value_ratio <= 1:
            raise ValueError('委托 tier 价值倍率必须大于 1')
        if not isfinite(self.delay_half_life) or self.delay_half_life <= 0:
            raise ValueError('委托等待半衰期必须为正数')
        if not isfinite(self.deadline_future_horizon) or self.deadline_future_horizon <= 0:
            raise ValueError('委托未来机会窗口必须为正数')
        if not 0 < self.filter_value_floor <= 10_000:
            raise ValueError('委托层内价值下限必须在 1 到 10000 之间')
        if not isfinite(self.filter_value_half_life) or self.filter_value_half_life <= 0:
            raise ValueError('委托层内编号半衰期必须为正数')

    def filter_factor(self, filter_index):
        """返回层内过滤器编号的定点价值修正。"""
        if filter_index < 0:
            raise ValueError('委托过滤器编号必须为非负整数')
        floor = self.filter_value_floor / 10_000
        factor = floor + (1 - floor) * exp2(
            -filter_index / self.filter_value_half_life
        )
        return round(VALUE_SCALE * factor)

    @classmethod
    def from_config(cls, config):
        """从委托 UI 配置构造与开发工具一致的价值模型。"""
        defaults = cls()
        return cls(
            tier_value_ratio=round(float(getattr(
                config,
                'Commission_TierValueRatio',
                defaults.tier_value_ratio,
            )), 2),
            # UI 只保留一位小数；换算成整数秒后，搜索热路径无需处理小数时间。
            delay_half_life=round(round(float(getattr(
                config,
                'Commission_DelayHalfLife',
                defaults.delay_half_life / 60 / 60,
            )), 1) * 60 * 60),
            deadline_future_horizon=round(round(float(getattr(
                config,
                'Commission_DeadlineFutureHorizon',
                defaults.deadline_future_horizon / 60 / 60,
            )), 1) * 60 * 60),
            filter_value_floor=round(float(getattr(
                config,
                'Commission_FilterValueFloor',
                defaults.filter_value_floor / 10_000,
            )) * 10_000),
            # 层内修正只在构造每个候选的缓存价值时计算一次，使用小数没有可感知开销。
            filter_value_half_life=round(float(getattr(
                config,
                'Commission_FilterValueHalfLife',
                defaults.filter_value_half_life,
            )), 1),
        )

    def delay_factor(self, seconds, deadline):
        """返回等待指定秒数后的定点价值修正。

        Args:
            seconds: 从规划时刻到预计启动时刻的等待秒数。
            deadline: 从规划时刻到最晚启动时刻的总秒数。
        """
        seconds = max(int(seconds), 0)
        deadline = int(deadline)
        if deadline <= 0:
            raise ValueError('委托 deadline 必须为正数')
        if seconds >= deadline:
            return 0
        if not seconds:
            return VALUE_SCALE

        # log1p 在 s/d 很小时避免 ``1 - s/d`` 丢失有效位；先处理 s=0，
        # 还可避免极大 T 使指数溢出时出现 ``inf * 0``。乘法溢出为 inf
        # 时 log2_factor 自然为 -inf，exp2 正确下溢到 0。
        relative_delay = seconds / deadline
        horizon_ratio = self.deadline_future_horizon / deadline
        deadline_exponent = horizon_ratio * horizon_ratio
        log2_factor = (
            deadline_exponent * log1p(-relative_delay) / LN_2
            - seconds / self.delay_half_life
        )
        return round(VALUE_SCALE * exp2(log2_factor))


DEFAULT_VALUE_MODEL = CommissionValueModel()


@dataclass(frozen=True)
class CommissionPlanJob:
    """动态规划使用的不可变委托信息。"""

    source_index: int
    tier: int
    duration: int
    deadline: int
    commission: object
    filter_index: int = 0


@dataclass(frozen=True)
class CommissionPlanAction:
    """一条计划启动记录，时间均为相对规划时刻的秒数。"""

    job_index: int
    start: int
    finish: int


@dataclass(frozen=True)
class CommissionPlan:
    """委托规划结果。"""

    score: tuple[int, ...]
    actions: tuple[CommissionPlanAction, ...]
    makespan: int
    completion_sum: int
    utility: int = 0
    full_value: int = 0
    value_scale: int = VALUE_SCALE * VALUE_SCALE
    state_count: int = 0
    pruned_state_count: int = 0
    beam_width: int = 0
    utility_upper_bound: int = 0
    full_value_upper_bound: int = 0
    optimality_proven: bool = True

    @property
    def delay_loss(self):
        """返回所选委托因等待损失的定点价值。"""
        return self.full_value - self.utility

    @property
    def utility_gap(self):
        """返回相对全局最优值严格上界的最大可能差距。"""
        return max(self.utility_upper_bound - self.utility, 0)


@dataclass(frozen=True)
class _StateResult:
    """搜索过程中一个可行部分计划的累计目标。"""

    utility: int = 0
    full_value: int = 0
    makespan: int = 0
    completion_sum: int = 0
    order_key: tuple[int, ...] = ()

    @property
    def rank(self):
        """返回完整且稳定的目标比较键。"""
        return (
            self.utility,
            self.full_value,
            -self.makespan,
            -self.completion_sum,
            self.order_key,
        )


@dataclass(frozen=True, slots=True)
class _BeamState:
    """束搜索中的一个部分计划，父指针用于低成本还原动作序列。"""

    mask: int
    slots: tuple[int, ...]
    result: _StateResult
    parent: object = None
    action: CommissionPlanAction | None = None


def _job_base_values(jobs, model):
    """构造各委托的未折现定点价值。"""
    maximum_tier = max((job.tier for job in jobs), default=0)
    return tuple(
        round(
            (model.tier_value_ratio ** (maximum_tier - job.tier))
            * model.filter_factor(job.filter_index)
        )
        for job in jobs
    )


def optimize_commission_plan(
    jobs,
    slot_available,
    horizon,
    model=DEFAULT_VALUE_MODEL,
    beam_width=None,
):
    """返回多项式束搜索的最佳计划、最优性证书与稳定委托列表。"""
    jobs = list(jobs)
    job_count = len(jobs)
    tier_count = max((job.tier for job in jobs), default=-1) + 1
    empty_score = (0,) * tier_count
    slots = tuple(sorted(max(int(value), 0) for value in slot_available))
    horizon = max(int(horizon), 0)
    if beam_width is None:
        # 小规模实例优先保留 n² 个状态；n >= 16 后严格按 16n 线性增长。
        beam_width = max(MIN_BEAM_WIDTH, min(job_count * job_count, BEAM_WIDTH_FACTOR * job_count))
    else:
        beam_width = int(beam_width)
        if beam_width <= 0:
            raise ValueError('委托规划束宽必须为正整数')

    if any(job.duration <= 0 for job in jobs):
        raise ValueError('委托规划要求所有委托耗时为正数')
    if any(job.tier < 0 for job in jobs):
        raise ValueError('委托规划要求价值层级为非负整数')
    if any(job.filter_index < 0 for job in jobs):
        raise ValueError('委托规划要求过滤器编号为非负整数')
    if any(job.deadline < 0 for job in jobs):
        raise ValueError('委托规划要求 deadline 为非负整数')
    if not jobs or not slots or horizon <= 0:
        return CommissionPlan(
            score=empty_score,
            actions=(),
            makespan=0,
            completion_sum=0,
            beam_width=beam_width,
        ), jobs

    limits = tuple(min(job.deadline, horizon) for job in jobs)
    base_values = _job_base_values(jobs, model)
    full_values = tuple(value * VALUE_SCALE for value in base_values)
    equivalence_keys = tuple(
        (
            job.tier,
            job.duration,
            job.deadline,
            base_values[index],
        )
        for index, job in enumerate(jobs)
    )
    branch_order = tuple(sorted(
        range(len(jobs)),
        key=lambda index: (
            -base_values[index],
            jobs[index].duration,
            jobs[index].source_index,
        ),
    ))
    initial_mask = (1 << len(jobs)) - 1
    state_count = 0
    pruned_state_count = 0
    best = _StateResult()
    root = _BeamState(initial_mask, slots, best)
    best_state = root
    discarded_upper_rank = None
    order_sentinel = max((-job.source_index for job in jobs), default=0) + 1

    def remaining_indices(mask):
        """按价值顺序返回掩码中的委托编号。"""
        return tuple(index for index in branch_order if mask & (1 << index))

    @lru_cache(maxsize=None)
    def delay_factor(start, deadline):
        """在单次规划内缓存定点折现，避免跨规划持有模型实例。"""
        return model.delay_factor(start, deadline)

    def optimistic_rank(state):
        """返回任意后续计划完整比较键的逐项严格乐观上界。"""
        current = state.result
        start = state.slots[0]
        # 所有真实后续启动都不会早于当前最早槽位。假设每个可行委托都能同时
        # 在该时刻启动，会高估折现价值和可选未折现价值，因此是严格安全上界。
        future_utility = 0
        future_full_value = 0
        if start < horizon:
            for index in branch_order:
                if not state.mask & (1 << index) or start >= limits[index]:
                    continue
                future_utility += (
                    base_values[index] * delay_factor(start, jobs[index].deadline)
                )
                future_full_value += full_values[index]
        return (
            current.utility + future_utility,
            current.full_value + future_full_value,
            -current.makespan,
            -current.completion_sum,
            (*current.order_key, order_sentinel),
        )

    def beam_rank(state, upper_rank):
        """优先保留潜在上限高、当前结果好且槽位更早的状态。"""
        return (
            upper_rank,
            state.result.rank,
            tuple(-value for value in state.slots),
            state.mask,
        )

    def update_best(state):
        """用一个可随时终止的部分计划更新全局最优解。"""
        nonlocal best, best_state
        if state.result.rank > best.rank:
            best = state.result
            best_state = state

    frontier = ((root, optimistic_rank(root)),)
    while frontier:
        candidates = []
        for state, state_upper_rank in frontier:
            update_best(state)
            start = state.slots[0]
            if not state.mask or start >= horizon or state_upper_rank <= best.rank:
                continue
            state_count += 1

            # 完全等价的委托在任意位置互换都不改变价值、可行性或完成时间；
            # 稳定决胜必然选择 source_index 最小者先出现，因此每类只展开首个代表。
            equivalent_seen = set()
            for job_index in remaining_indices(state.mask):
                equivalence_key = equivalence_keys[job_index]
                if equivalence_key in equivalent_seen:
                    continue
                equivalent_seen.add(equivalence_key)
                if start >= limits[job_index]:
                    continue
                job = jobs[job_index]
                # 必须使用委托自己的原始 deadline。limits 还包含服务器刷新边界，
                # 后者只限制搜索范围，不应成为紧急委托的价值惩罚来源。
                factor = delay_factor(start, job.deadline)
                finish = start + job.duration
                next_slots = tuple(sorted((*state.slots[1:], finish)))
                action = CommissionPlanAction(job_index, start, finish)
                next_result = _StateResult(
                    utility=state.result.utility + base_values[job_index] * factor,
                    full_value=state.result.full_value + full_values[job_index],
                    makespan=max(state.result.makespan, finish),
                    completion_sum=state.result.completion_sum + finish,
                    order_key=(*state.result.order_key, -job.source_index),
                )
                child = _BeamState(
                    state.mask ^ (1 << job_index),
                    next_slots,
                    next_result,
                    state,
                    action,
                )
                update_best(child)
                child_upper_rank = optimistic_rank(child)
                if child_upper_rank > best.rank:
                    candidates.append((child, child_upper_rank))

        if len(candidates) <= beam_width:
            frontier = tuple(candidates)
            continue

        kept = nlargest(
            beam_width,
            candidates,
            key=lambda item: beam_rank(*item),
        )
        kept_ids = {id(state) for state, _ in kept}
        for state, upper_rank in candidates:
            if id(state) in kept_ids:
                continue
            pruned_state_count += 1
            if discarded_upper_rank is None or upper_rank > discarded_upper_rank:
                discarded_upper_rank = upper_rank
        frontier = tuple(kept)

    actions = []
    state = best_state
    while state.action is not None:
        actions.append(state.action)
        state = state.parent
    best_actions = tuple(reversed(actions))
    global_upper_rank = best.rank
    if discarded_upper_rank is not None and discarded_upper_rank > global_upper_rank:
        global_upper_rank = discarded_upper_rank

    score = [0] * tier_count
    for action in best_actions:
        score[jobs[action.job_index].tier] += 1
    top_value_scale = round(
        (model.tier_value_ratio ** max(tier_count - 1, 0))
        * VALUE_SCALE
        * VALUE_SCALE
    )
    return CommissionPlan(
        score=tuple(score),
        actions=best_actions,
        makespan=best.makespan,
        completion_sum=best.completion_sum,
        utility=best.utility,
        full_value=best.full_value,
        value_scale=top_value_scale,
        state_count=state_count,
        pruned_state_count=pruned_state_count,
        beam_width=beam_width,
        utility_upper_bound=global_upper_rank[0],
        full_value_upper_bound=global_upper_rank[1],
        optimality_proven=(discarded_upper_rank is None or discarded_upper_rank <= best.rank),
    ), jobs


def delay_threshold_seconds(
    tier_gap,
    delayed_count,
    delayed_deadline,
    model=DEFAULT_VALUE_MODEL,
    delaying_filter_index=0,
    delayed_filter_index=0,
):
    """返回低 tier 委托允许推迟高 tier 委托的最大整数秒数。

    临界条件与规划器完全一致：一个低 ``tier_gap`` 层的委托立即启动，
    与放弃它并让 ``delayed_count`` 个高层委托立即启动进行比较。
    ``delayed_deadline`` 是被推迟委托距离最晚启动时刻的总秒数；到达 deadline
    即视为不可行。返回 ``None`` 表示即使放弃被推迟委托仍值得启动低层委托。
    """
    if tier_gap < 0:
        raise ValueError('tier 间隔必须为非负整数')
    if delayed_count <= 0:
        raise ValueError('被延迟委托数必须为正整数')
    delayed_deadline = int(delayed_deadline)
    if delayed_deadline <= 0:
        raise ValueError('被延迟委托的 deadline 必须为正数')

    high = (
        model.tier_value_ratio ** tier_gap
        * model.filter_factor(delayed_filter_index)
    )
    low = model.filter_factor(delaying_filter_index)
    immediate_high = round(delayed_count * high * VALUE_SCALE)
    immediate_low = round(low * VALUE_SCALE)
    if immediate_low > immediate_high:
        return None

    def is_allowed(seconds):
        if seconds >= delayed_deadline:
            return False
        delayed_high = round(
            delayed_count * high * model.delay_factor(seconds, delayed_deadline)
        )
        return immediate_low + delayed_high > immediate_high

    lower = 0
    upper = min(max(ceil(model.delay_half_life), 1), delayed_deadline)
    while is_allowed(upper):
        lower = upper
        upper = min(upper * 2, delayed_deadline)
    while lower + 1 < upper:
        middle = (lower + upper) // 2
        if is_allowed(middle):
            lower = middle
        else:
            upper = middle
    return lower
