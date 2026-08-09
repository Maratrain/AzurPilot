"""生成委托动态规划价值模型的完整评估报告。

报告直接调用运行时 ``CommissionValueModel`` 和临界值函数，因此展示的是规划器
实际使用的定点模型，而不是另一套近似计算。默认输出 Markdown 到终端，也可以
通过 ``--output`` 写入文件。

常用示例：
``uv run python dev_tools/commission_value_table.py``
``uv run python dev_tools/commission_value_table.py --tier-ratio 4``
``uv run python dev_tools/commission_value_table.py --deadline-future-horizon-hours 2``
``uv run python dev_tools/commission_value_table.py --deadline-hours 0.5,1,2,6``
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from module.commission.planner import (
    VALUE_SCALE,
    CommissionValueModel,
    delay_threshold_seconds,
)

DEFAULT_DEADLINE_HOURS = (0.5, 1, 2, 6, 12, 24)
DEFAULT_DELAY_MINUTES = (5, 15, 30, 60, 120, 240)
DEFAULT_DEADLINE_HORIZON_RATIOS = (0.5, 1, 2, 3, 6)
DEFAULT_THRESHOLD_DEADLINE_HOURS = (1, 2, 6)
DEFAULT_SERVER_REFRESH_HORIZON = 12 * 60 * 60


def format_duration(seconds):
    """把秒数格式化为紧凑且适合表格阅读的时长。"""
    if seconds is None:
        return '不限'
    seconds = max(round(seconds), 0)
    days, seconds = divmod(seconds, 24 * 60 * 60)
    hours, seconds = divmod(seconds, 60 * 60)
    minutes, seconds = divmod(seconds, 60)
    prefix = f'{days}天 ' if days else ''
    return f'{prefix}{hours:02d}:{minutes:02d}:{seconds:02d}'
def format_number(value):
    """避免在参数和标题中输出无意义的小数尾零。"""
    return f'{value:g}'

def parse_number_list(value, name, *, maximum=None):
    """解析逗号分隔的浮点数列表，并保持用户给定顺序。"""
    try:
        values = tuple(float(item.strip()) for item in value.split(',') if item.strip())
    except ValueError as error:
        raise argparse.ArgumentTypeError(f'{name} 必须是逗号分隔的数字') from error
    if not values:
        raise argparse.ArgumentTypeError(f'{name} 不能为空')
    if any(item <= 0 for item in values):
        raise argparse.ArgumentTypeError(f'{name} 中的数字必须大于 0')
    if maximum is not None and any(item >= maximum for item in values):
        raise argparse.ArgumentTypeError(f'{name} 中的数字必须小于 {maximum}')
    return values

def factor_percent(model, seconds, deadline):
    """返回指定等待场景的运行时价值百分比。"""
    return model.delay_factor(seconds, deadline) / VALUE_SCALE * 100


def build_parameter_section(model, server_refresh_horizon):
    """解释五个模型参数及当前值的直接含义。"""
    adjacent = 100 / model.tier_value_ratio
    return [
        '## 一眼看懂当前参数',
        '',
        '| 参数 | 当前值 | 通俗解释 |',
        '| --- | ---: | --- |',
        f'| 相邻 tier 价值倍率 | {model.tier_value_ratio:g} | '
        f'每降低一个 tier，基础价值降到上一层的 {adjacent:.2f}% |',
        f'| 基础等待半衰期 | {format_duration(model.delay_half_life)} | '
        '公式中 `2^(-s/H)` 的 H |',
        f'| Deadline 折现基准时间 | {format_duration(model.deadline_future_horizon)} | '
        '公式中相对窗口指数 `(T/d)^2` 的 T |',
        f'| 报告使用的刷新剩余时间 | {format_duration(server_refresh_horizon)} | '
        '没有显式 deadline 的委托在本报告中统一使用的 d |',
        f'| 层内价值下限 | {model.filter_value_floor / 100:.2f}% | '
        '同一 tier 中再靠后的规则也不会低于这个比例 |',
        f'| 层内编号半衰期 | {model.filter_value_half_life:g} | '
        '超过价值下限的那部分，每后移这么多条规则减半 |',
        '',
        '规划器把三件事相乘：委托所在 tier 的基础价值、同 tier 内的位置修正、'
        '预计启动时间的折现。运行时没有显式 deadline 的委托使用实际服务器刷新剩余时间。',
        '',
        '对本次规划时刻到最晚启动时刻的总秒数 `d`，完整折现公式为：',
        '',
        '`f(s,d) = (1-s/d)^((T/d)^2) * 2^(-s/H)`，其中 `0 <= s < d`。',
        '',
        '当 `s >= d` 时委托不可启动，折现定义为 0；`d` 在一次规划中固定，递归时不会改成 `d-s`。',
        '',
        '因为完整公式同时包含基础半衰期，临界等待占比没有参考脚本所写的简单幂函数闭式；'
        '本报告的抢槽临界值直接对运行时定点函数做整数秒二分。',
        '',
    ]

def build_tier_section(model, max_tier_gap):
    """展示跨 tier 的基础价值比例。"""
    lines = [
        '## Tier 基础价值',
        '',
        '以下把 T1 记为 100。它只展示过滤规则的层级差异，尚未计算等待。',
        '',
        '| Tier | 相对 T1 价值 |',
        '| ---: | ---: |',
    ]
    for gap in range(max_tier_gap + 1):
        lines.append(f'| T{gap + 1} | {100 / model.tier_value_ratio ** gap:.4f}% |')
    lines.append('')
    return lines

def build_filter_section(model, max_filter_index):
    """展示同一 tier 内过滤器编号造成的价值修正。"""
    lines = [
        '## 层内价值衰减表（同 Tier 内的过滤器顺序）',
        '',
        '过滤器编号从 0 开始。越靠前价值越高，但后排规则不会跌破配置下限。',
        '',
        '| 层内位置 | 过滤器编号 | 相对价值比例 |',
        '| ---: | ---: | ---: |',
    ]
    for index in range(max_filter_index + 1):
        ratio = model.filter_factor(index) / VALUE_SCALE * 100
        lines.append(f'| 第 {index + 1} 个元素 | {index} | {ratio:.2f}% |')
    lines.append('')
    return lines

def build_baseline_delay_section(model, delay_minutes, server_refresh_horizon):
    """展示普通委托使用服务器刷新时间时的完整折现。"""
    refresh_text = format_duration(server_refresh_horizon)
    lines = [
        f'## 普通委托的等待损失（d = 刷新剩余时间 {refresh_text}）',
        '',
        '普通委托和限时委托调用同一个折现函数，不存在无 deadline 特例。',
        '',
        '| 等待时间 | 剩余价值 | 损失 |',
        '| ---: | ---: | ---: |',
    ]
    for minutes in delay_minutes:
        remain = factor_percent(model, round(minutes * 60), server_refresh_horizon)
        lines.append(
            f'| {format_duration(minutes * 60)} | {remain:.2f}% | {100 - remain:.2f}% |'
        )
    lines.append('')
    return lines

def build_deadline_fraction_section(model, deadline_horizon_ratios):
    """展示不同 d/T 下相对窗口指数和完整折现。"""
    lines = [
        '## Deadline 相对窗口强度',
        '',
        '下表固定等待 `s=d/10`。它展示的是包含 `2^(-s/H)` 的完整总折现，'
        '不把只适用于忽略基础半衰期的临界占比公式当作总模型。',
        '',
        '| d/T | 剩余 deadline d | 指数 (T/d)^2 | 等待 d/10 后剩余价值 |',
        '| ---: | ---: | ---: | ---: |',
    ]
    for ratio in deadline_horizon_ratios:
        deadline = round(ratio * model.deadline_future_horizon)
        delay = max(round(deadline / 10), 1)
        exponent = (model.deadline_future_horizon / deadline) ** 2
        remain = factor_percent(model, delay, deadline)
        lines.append(
            f'| {ratio:g} | {format_duration(deadline)} | {exponent:.4f} | {remain:.2f}% |'
        )
    lines.append('')
    return lines


def build_deadline_matrix(model, deadline_hours, delay_minutes, server_refresh_horizon):
    """展示绝对等待时间与剩余 deadline 共同作用后的总损失。"""
    lines = [
        '## 不同 Deadline 下的总等待损失',
        '',
        '`已过期` 表示预计启动时间已经达到最晚启动时间，规划器不会选择该计划。',
        '',
        f'| 等待时间 | 刷新 d={format_duration(server_refresh_horizon)} | '
        + ' | '.join(f'D={format_number(hours)}小时' for hours in deadline_hours)
        + ' |',
        '| ---: | ---: | ' + ' | '.join('---:' for _ in deadline_hours) + ' |',
    ]
    for minutes in delay_minutes:
        seconds = round(minutes * 60)
        values = [
            '已过期'
            if seconds >= server_refresh_horizon
            else f'{100 - factor_percent(model, seconds, server_refresh_horizon):.2f}%'
        ]
        for hours in deadline_hours:
            deadline = round(hours * 60 * 60)
            if seconds >= deadline:
                values.append('已过期')
            else:
                values.append(f'{100 - factor_percent(model, seconds, deadline):.2f}%')
        lines.append(f'| {format_duration(seconds)} | ' + ' | '.join(values) + ' |')
    lines.append('')
    return lines


def build_threshold_table(
    model,
    max_tier_gap,
    max_delayed_count,
    delaying_filter_index,
    delayed_filter_index,
    deadline,
):
    """生成低 tier 委托抢槽的延迟临界值矩阵。"""
    title = f'd={format_duration(deadline)}'
    lines = [
        f'### {title}',
        '',
        '| 低价值委托落后层数 | '
        + ' | '.join(f'延迟 {count} 个高价值委托' for count in range(1, max_delayed_count + 1))
        + ' |',
        '| ---: | ' + ' | '.join('---:' for _ in range(max_delayed_count)) + ' |',
    ]
    for tier_gap in range(1, max_tier_gap + 1):
        values = [
            format_duration(delay_threshold_seconds(
                tier_gap=tier_gap,
                delayed_count=count,
                model=model,
                delaying_filter_index=delaying_filter_index,
                delayed_filter_index=delayed_filter_index,
                delayed_deadline=deadline,
            ))
            for count in range(1, max_delayed_count + 1)
        ]
        lines.append(f'| {tier_gap} | ' + ' | '.join(values) + ' |')
    lines.append('')
    return lines


def build_threshold_section(
    model,
    max_tier_gap,
    max_delayed_count,
    delaying_filter_index,
    delayed_filter_index,
    threshold_deadline_hours,
    server_refresh_horizon,
):
    """解释并展示不同 deadline 下的抢槽盈亏边界。"""
    delaying_ratio = model.filter_factor(delaying_filter_index) / VALUE_SCALE * 100
    delayed_ratio = model.filter_factor(delayed_filter_index) / VALUE_SCALE * 100
    lines = [
        '## 低价值委托抢槽的临界值',
        '',
        '场景：一个较低 tier 的委托现在启动，并让若干较高 tier 委托一起等待。'
        '表格给出这次抢槽仍然值得的最大等待时间；再多等一秒，规划器就会放弃'
        '低价值委托。`不限` 表示低价值委托本身已经足以覆盖被延迟委托的总价值。',
        '',
        '| 临界场景参数 | 值 |',
        '| --- | ---: |',
        f'| 低价值委托层内编号 | {delaying_filter_index} ({delaying_ratio:.2f}%) |',
        f'| 被延迟委托层内编号 | {delayed_filter_index} ({delayed_ratio:.2f}%) |',
        '',
    ]
    lines.extend(build_threshold_table(
        model,
        max_tier_gap,
        max_delayed_count,
        delaying_filter_index,
        delayed_filter_index,
        deadline=server_refresh_horizon,
    ))
    for hours in threshold_deadline_hours:
        lines.extend(build_threshold_table(
            model,
            max_tier_gap,
            max_delayed_count,
            delaying_filter_index,
            delayed_filter_index,
            deadline=round(hours * 60 * 60),
        ))
    return lines


def build_example_section(
    model,
    low_value_ratio,
    example_delay_minutes,
    server_refresh_horizon,
):
    """生成一个不依赖 tier 配置的直观抢槽例子。"""
    delay = round(example_delay_minutes * 60)
    low_value = low_value_ratio * 100
    deadlines = (server_refresh_horizon, 6 * 3600, 2 * 3600, 1 * 3600)
    lines = [
        '## 一个直观决策例子',
        '',
        f'假设一个价值 {low_value:g}、只能现在启动的短委托，会让价值 100 的委托'
        f'等待 {format_duration(delay)}。低价值委托的收益必须大于高价值委托的等待损失。',
        '',
        '| 高价值委托的剩余窗口 | 等待损失 | 净收益 | 规划器判断 |',
        '| ---: | ---: | ---: | --- |',
    ]
    for deadline in deadlines:
        if delay >= deadline:
            loss = 100.0
            label = format_duration(deadline)
        else:
            loss = 100 - factor_percent(model, delay, deadline)
            label = format_duration(deadline)
        net = low_value - loss
        decision = '值得抢槽' if net > 0 else '不应抢槽'
        lines.append(f'| {label} | {loss:.2f} | {net:+.2f} | {decision} |')
    lines.extend([
        '',
        '这个例子只帮助理解力度；真实运行会同时枚举全部槽位、委托顺序、时长和'
        '各自 deadline，因此不是逐对贪心判断。',
        '',
    ])
    return lines


def build_report(
    model,
    max_tier_gap,
    max_delayed_count,
    delaying_filter_index,
    delayed_filter_index,
    max_filter_index=16,
    deadline_hours=DEFAULT_DEADLINE_HOURS,
    delay_minutes=DEFAULT_DELAY_MINUTES,
    deadline_horizon_ratios=DEFAULT_DEADLINE_HORIZON_RATIOS,
    threshold_deadline_hours=DEFAULT_THRESHOLD_DEADLINE_HOURS,
    example_low_value_ratio=0.2,
    example_delay_minutes=30,
    server_refresh_horizon=DEFAULT_SERVER_REFRESH_HORIZON,
):
    """构造完整 Markdown 模型评估报告。"""
    if max_tier_gap <= 0:
        raise ValueError('最大 tier 间隔必须为正整数')
    if max_delayed_count <= 0:
        raise ValueError('最大被延迟委托数必须为正整数')
    if max_filter_index < 0:
        raise ValueError('最大过滤器编号必须为非负整数')
    if delaying_filter_index < 0 or delayed_filter_index < 0:
        raise ValueError('过滤器编号必须为非负整数')
    if not 0 < example_low_value_ratio < 1:
        raise ValueError('示例低价值比例必须在 0 到 1 之间')
    if server_refresh_horizon <= 0:
        raise ValueError('服务器刷新剩余时间必须为正数')

    lines = [
        '# 委托动态规划统一折现模型评估报告',
        '',
        '本报告使用与游戏运行时完全相同的定点折现函数。百分比用于阅读，临界值按'
        '整数秒精确搜索。修改命令行参数后重新生成，即可比较不同模型的整体行为。',
        '',
    ]
    lines.extend(build_parameter_section(model, server_refresh_horizon))
    lines.extend(build_tier_section(model, max_tier_gap))
    lines.extend(build_filter_section(model, max_filter_index))
    lines.extend(build_baseline_delay_section(model, delay_minutes, server_refresh_horizon))
    lines.extend(build_deadline_fraction_section(model, deadline_horizon_ratios))
    lines.extend(build_deadline_matrix(
        model,
        deadline_hours,
        delay_minutes,
        server_refresh_horizon,
    ))
    lines.extend(build_threshold_section(
        model,
        max_tier_gap,
        max_delayed_count,
        delaying_filter_index,
        delayed_filter_index,
        threshold_deadline_hours,
        server_refresh_horizon,
    ))
    lines.extend(build_example_section(
        model,
        example_low_value_ratio,
        example_delay_minutes,
        server_refresh_horizon,
    ))
    return '\n'.join(lines).rstrip() + '\n'


def build_table(
    model,
    max_tier_gap,
    max_delayed_count,
    delaying_filter_index,
    delayed_filter_index,
    max_filter_index=16,
):
    """兼容旧调用名称，返回新的完整评估报告。"""
    return build_report(
        model=model,
        max_tier_gap=max_tier_gap,
        max_delayed_count=max_delayed_count,
        delaying_filter_index=delaying_filter_index,
        delayed_filter_index=delayed_filter_index,
        max_filter_index=max_filter_index,
    )


def parse_args():
    """解析模型参数、报告场景范围和输出位置。"""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--tier-ratio', type=float, default=2.0, help='相邻 tier 价值倍率，必须大于 1')
    parser.add_argument(
        '--delay-half-life-hours',
        type=float,
        default=100,
        help='公式中基础等待价值半衰期 H，单位小时',
    )
    parser.add_argument(
        '--deadline-future-horizon-hours',
        type=float,
        default=0.5,
        help='公式中 Deadline 折现基准时间 T，单位小时且必须为正数',
    )
    parser.add_argument(
        '--server-refresh-hours',
        type=float,
        default=DEFAULT_SERVER_REFRESH_HORIZON / 3600,
        help='报告中普通委托使用的服务器刷新剩余小时数',
    )
    parser.add_argument('--filter-value-floor', type=float, default=0.6, help='层内价值下限，范围 (0, 1]')
    parser.add_argument('--filter-value-half-life', type=float, default=4, help='层内编号修正半衰期')
    parser.add_argument('--delaying-filter-index', type=int, default=0, help='抢槽委托的层内编号')
    parser.add_argument('--delayed-filter-index', type=int, default=0, help='被延迟委托的层内编号')
    parser.add_argument('--max-tier-gap', type=int, default=8, help='报告展示的最大 tier 间隔')
    parser.add_argument('--max-delayed-count', type=int, default=4, help='最多同时被延迟的高价值委托数')
    parser.add_argument('--max-filter-index', type=int, default=16, help='层内价值表的最大编号')
    parser.add_argument(
        '--deadline-hours',
        default=','.join(map(format_number, DEFAULT_DEADLINE_HOURS)),
        help='总损失矩阵使用的剩余 deadline 小时列表',
    )
    parser.add_argument(
        '--delay-minutes',
        default=','.join(map(format_number, DEFAULT_DELAY_MINUTES)),
        help='折现表使用的等待分钟列表',
    )
    parser.add_argument(
        '--deadline-horizon-ratios',
        default=','.join(map(format_number, DEFAULT_DEADLINE_HORIZON_RATIOS)),
        help='未来机会表使用的 deadline/机会窗口比例列表',
    )
    parser.add_argument(
        '--threshold-deadline-hours',
        default=','.join(map(format_number, DEFAULT_THRESHOLD_DEADLINE_HOURS)),
        help='抢槽临界值表使用的 deadline 小时列表',
    )
    parser.add_argument('--example-low-value-ratio', type=float, default=0.2, help='直观例子的低价值/高价值比例')
    parser.add_argument('--example-delay-minutes', type=float, default=30, help='直观例子的抢槽等待分钟数')
    parser.add_argument('--output', type=Path, help='可选的 Markdown 输出文件')
    return parser.parse_args()


def main():
    """使用指定参数生成报告。"""
    args = parse_args()
    model = CommissionValueModel(
        tier_value_ratio=args.tier_ratio,
        delay_half_life=round(round(args.delay_half_life_hours, 1) * 60 * 60),
        deadline_future_horizon=round(round(
            args.deadline_future_horizon_hours, 1
        ) * 60 * 60),
        filter_value_floor=round(args.filter_value_floor * 10_000),
        filter_value_half_life=round(args.filter_value_half_life, 1),
    )
    report = build_report(
        model=model,
        max_tier_gap=args.max_tier_gap,
        max_delayed_count=args.max_delayed_count,
        delaying_filter_index=args.delaying_filter_index,
        delayed_filter_index=args.delayed_filter_index,
        max_filter_index=args.max_filter_index,
        deadline_hours=parse_number_list(args.deadline_hours, 'deadline 小时'),
        delay_minutes=parse_number_list(args.delay_minutes, '等待分钟'),
        deadline_horizon_ratios=parse_number_list(
            args.deadline_horizon_ratios,
            'deadline/机会窗口比例',
        ),
        threshold_deadline_hours=parse_number_list(
            args.threshold_deadline_hours,
            '临界值 deadline 小时',
        ),
        example_low_value_ratio=args.example_low_value_ratio,
        example_delay_minutes=args.example_delay_minutes,
        server_refresh_horizon=round(args.server_refresh_hours * 60 * 60),
    )
    if args.output:
        args.output.write_text(report, encoding='utf-8')
        print(f'已生成: {args.output.resolve()}')
    else:
        print(report, end='')


if __name__ == '__main__':
    main()
