"""基于启动时间折现价值的委托精确规划器。

所有候选委托在规划时刻都已经可用，约束只有槽位可用时间和最晚启动时间。
委托的基础价值按 tier 以有限倍率递减，并用一个有下限的指数函数区分同层
过滤器编号；实际收益再按预计启动等待时间指数折现。规划目标是最大化全部
已选委托的折现价值之和，而不是按 tier 数量做绝对字典序比较。

求解器只展开非空转列表调度：每一步在最早空闲槽位启动一个尚未选择的
可行委托。因为任务没有释放时间，且更早启动只会提高价值并放宽最晚启动
约束，所以任意最优日程都能左移成这种形式。分支定界完整覆盖所有选择集合
和有效执行顺序，并只使用严格乐观上界与已证明安全的状态支配剪枝。

所有目标比较都使用整数。指数函数仅用于构造模型定义中的定点折现表；求解
过程本身没有浮点比较或近似剪枝，所以返回值是该定点模型的全局最优解。
"""

from dataclasses import dataclass
from functools import lru_cache
from math import ceil, exp2, isfinite


VALUE_SCALE = 1_000_000_000


@dataclass(frozen=True)
class CommissionValueModel:
    """委托价值模型参数。

    对 tier 为 ``t``、层内过滤器编号为 ``r``、预计等待 ``s`` 秒的委托，
    求解器使用以下定点价值：

    ``tier_ratio ** (max_tier - t) * filter_factor(r) * delay_factor(s)``

    四个字段就是模型的全部行为参数。默认值与 GUI 的 ``Commission`` 配置组
    保持一致；开发调参时既可以直接实例化本类，也可以使用 ``from_config()``
    从运行配置构造，临界值工具同样复用本类，避免出现两套参数定义。

    Args:
        tier_value_ratio: 相邻 tier 的基础价值倍率。
        delay_half_life: 启动等待价值减半所需秒数，支持小数。
        filter_value_floor: 层内过滤器价值下限，单位为万分比。
        filter_value_half_life: 层内编号修正衰减一半所需的规则数，支持小数。
    """

    tier_value_ratio: float = 2.0  # GUI: Commission_TierValueRatio
    delay_half_life: float = 100 * 60 * 60  # GUI 小时数转换为秒
    filter_value_floor: int = 6_000  # GUI 0~1 比例转换为万分比
    filter_value_half_life: float = 2.0  # GUI: Commission_FilterValueHalfLife

    def __post_init__(self):
        if self.tier_value_ratio <= 1:
            raise ValueError('委托 tier 价值倍率必须大于 1')
        if not isfinite(self.delay_half_life) or self.delay_half_life <= 0:
            raise ValueError('委托等待半衰期必须为正数')
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

    @lru_cache(maxsize=None)
    def delay_factor(self, seconds):
        """返回等待指定秒数后的定点价值修正。"""
        seconds = max(int(seconds), 0)
        return round(VALUE_SCALE * exp2(-seconds / self.delay_half_life))


DEFAULT_VALUE_MODEL = CommissionValueModel()


@dataclass(frozen=True)
class CommissionPlanJob:
    """动态规划使用的不可变委托信息。"""

    source_index: int
    tier: int
    duration: int
    deadline: int | None
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

    @property
    def delay_loss(self):
        """返回所选委托因等待损失的定点价值。"""
        return self.full_value - self.utility


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
):
    """计算折现总价值最大的并行委托启动计划。

    Args:
        jobs (list[CommissionPlanJob]): 当前全部待选委托。
        slot_available (list[int]): 各槽位距离空闲的秒数。
        horizon (int): 最晚允许启动新委托的相对秒数。
        model (CommissionValueModel): 委托价值模型。

    Returns:
        tuple[CommissionPlan, list[CommissionPlanJob]]: 全局最优计划与稳定委托列表。
    """
    jobs = list(jobs)
    tier_count = max((job.tier for job in jobs), default=-1) + 1
    empty_score = (0,) * tier_count
    slots = tuple(sorted(max(int(value), 0) for value in slot_available))
    horizon = max(int(horizon), 0)

    if any(job.duration <= 0 for job in jobs):
        raise ValueError('委托规划要求所有委托耗时为正数')
    if any(job.tier < 0 for job in jobs):
        raise ValueError('委托规划要求价值层级为非负整数')
    if any(job.filter_index < 0 for job in jobs):
        raise ValueError('委托规划要求过滤器编号为非负整数')
    if not jobs or not slots or horizon <= 0:
        return CommissionPlan(
            score=empty_score,
            actions=(),
            makespan=0,
            completion_sum=0,
        ), jobs

    limits = tuple(
        min(job.deadline if job.deadline is not None else horizon, horizon)
        for job in jobs
    )
    base_values = _job_base_values(jobs, model)
    full_values = tuple(value * VALUE_SCALE for value in base_values)
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
    best = _StateResult()
    best_actions = ()
    frontiers = {}

    @lru_cache(maxsize=None)
    def remaining_indices(mask):
        """按价值顺序返回掩码中的委托编号。"""
        return tuple(index for index in branch_order if mask & (1 << index))

    @lru_cache(maxsize=None)
    def optimistic_future(mask, current_slots):
        """返回允许统一最短耗时且忽略截止时间的折现价值上界。"""
        indices = remaining_indices(mask)
        if not indices:
            return 0

        # 把所有委托耗时替换为剩余集合中的最短耗时，并把最高价值依次放到
        # 最早的虚拟槽位。虚拟启动时刻逐项不晚于任何真实计划，故为严格上界。
        minimum_duration = min(jobs[index].duration for index in indices)
        virtual_slots = list(current_slots)
        utility = 0
        for index in indices:
            start = virtual_slots[0]
            utility += base_values[index] * model.delay_factor(start)
            virtual_slots[0] = start + minimum_duration
            virtual_slots.sort()
        return utility

    def slots_no_later(left, right):
        """判断一个排序槽位向量是否逐项不晚于另一个。"""
        return all(left_value <= right_value for left_value, right_value in zip(left, right))

    def state_dominates(left, right):
        """判断同一剩余集合中的状态是否可安全支配。"""
        left_slots, left_result = left
        right_slots, right_result = right
        if not slots_no_later(left_slots, right_slots):
            return False
        if left_result.utility != right_result.utility:
            return left_result.utility > right_result.utility
        return (
            left_result.makespan <= right_result.makespan
            and left_result.completion_sum <= right_result.completion_sum
            and left_result.order_key >= right_result.order_key
        )

    def update_best(result, actions):
        """用一个可随时终止的部分计划更新全局最优解。"""
        nonlocal best, best_actions
        if result.rank > best.rank:
            best = result
            best_actions = actions

    def search(mask, current_slots, current, actions):
        """使用严格上界和同集合状态支配枚举全部可能的非空转计划。"""
        nonlocal state_count
        update_best(current, actions)
        start = current_slots[0]
        if not mask or start >= horizon:
            return

        upper_rank = (
            current.utility + optimistic_future(mask, current_slots),
            current.full_value + sum(full_values[index] for index in remaining_indices(mask)),
        )
        if upper_rank < best.rank[:2]:
            return

        frontier = frontiers.setdefault(mask, [])
        state = (current_slots, current)
        if any(state_dominates(item, state) for item in frontier):
            return
        frontier[:] = [item for item in frontier if not state_dominates(state, item)]
        frontier.append(state)
        state_count += 1

        factor = model.delay_factor(start)
        for job_index in remaining_indices(mask):
            if start >= limits[job_index]:
                continue
            job = jobs[job_index]
            finish = start + job.duration
            next_slots = tuple(sorted((*current_slots[1:], finish)))
            action = CommissionPlanAction(job_index, start, finish)
            next_result = _StateResult(
                utility=current.utility + base_values[job_index] * factor,
                full_value=current.full_value + full_values[job_index],
                makespan=max(current.makespan, finish),
                completion_sum=current.completion_sum + finish,
                order_key=(*current.order_key, -job.source_index),
            )
            search(
                mask ^ (1 << job_index),
                next_slots,
                next_result,
                (*actions, action),
            )

    search(initial_mask, slots, _StateResult(), ())

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
    ), jobs


def delay_threshold_seconds(
    tier_gap,
    delayed_count,
    model=DEFAULT_VALUE_MODEL,
    delaying_filter_index=0,
    delayed_filter_index=0,
):
    """返回低 tier 委托允许推迟高 tier 委托的最大整数秒数。

    临界条件与规划器完全一致：一个低 ``tier_gap`` 层的委托立即启动，
    与放弃它并让 ``delayed_count`` 个高层委托立即启动进行比较。
    返回 ``None`` 表示无有限临界值。
    """
    if tier_gap < 0:
        raise ValueError('tier 间隔必须为非负整数')
    if delayed_count <= 0:
        raise ValueError('被延迟委托数必须为正整数')

    high = (
        model.tier_value_ratio ** tier_gap
        * model.filter_factor(delayed_filter_index)
    )
    low = model.filter_factor(delaying_filter_index)
    immediate_high = round(delayed_count * high * VALUE_SCALE)
    immediate_low = round(low * VALUE_SCALE)
    if immediate_low >= immediate_high:
        return None

    def is_allowed(seconds):
        delayed_high = round(delayed_count * high * model.delay_factor(seconds))
        return immediate_low + delayed_high > immediate_high

    lower = 0
    upper = max(ceil(model.delay_half_life), 1)
    while is_allowed(upper):
        lower = upper
        upper *= 2
    while lower + 1 < upper:
        middle = (lower + upper) // 2
        if is_allowed(middle):
            lower = middle
        else:
            upper = middle
    return lower
