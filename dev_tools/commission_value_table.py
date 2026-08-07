"""生成委托折现价值模型的延迟临界值表。

用途
----
本工具回答一个具体问题：一个较低 tier 的限时委托必须立即启动，并因此让
若干较高 tier 委托等待时，最多允许延迟多久；超过表中临界值后，规划器会
放弃低价值委托。计算直接调用 ``module.commission.planner`` 的定点模型，
所以表格结果与游戏运行时完全一致，不是独立的近似公式。

模型位置
--------
委托 ``i`` 的目标值由三部分相乘：

``tier_ratio ** (max_tier - tier_i)``
    tier 基础价值。``tier_ratio`` 越大，跨 tier 取舍越保守。

``floor + (1 - floor) * 2 ** (-filter_index / filter_half_life)``
    同 tier 内过滤器编号修正。编号越靠前价值越高，但最低不会小于 ``floor``。

``2 ** (-start_seconds / delay_half_life)``
    启动等待折现。等待一个 ``delay_half_life`` 后价值正好减半。

参数对应关系
------------
``--tier-ratio`` 对应 UI ``Commission.TierValueRatio``；
``--delay-half-life-hours`` 对应 ``Commission.DelayHalfLife``；
``--filter-value-floor`` 对应 ``Commission.FilterValueFloor``；
``--filter-value-half-life`` 对应 ``Commission.FilterValueHalfLife``。
两个半衰期均保留一位小数，与 UI 和运行时的归一化精度一致。
后四个表格范围/场景参数不改变运行模型，只决定比较哪些 tier、多少个委托，
以及双方在各自 tier 内采用哪个过滤器编号。

使用示例
--------
直接在终端查看默认参数的 8×8 表格：

``uv run python dev_tools/commission_value_table.py``

模拟较激进的 tier 倍率和较短等待半衰期，并写入 Markdown：

``uv run python dev_tools/commission_value_table.py --tier-ratio 4 --delay-half-life-hours 3 --filter-value-floor 0.7 --filter-value-half-life 2 --output commission_thresholds.md``

比较层内编号 12 的低价值委托对编号 0 的高价值委托造成的影响：

``uv run python dev_tools/commission_value_table.py --delaying-filter-index 12 --delayed-filter-index 0``
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


def format_duration(seconds):
    """把整数秒格式化为紧凑的临界时长。"""
    if seconds is None:
        return '不限'
    days, seconds = divmod(seconds, 24 * 60 * 60)
    hours, seconds = divmod(seconds, 60 * 60)
    minutes, seconds = divmod(seconds, 60)
    prefix = f'{days}天 ' if days else ''
    return f'{prefix}{hours:02d}:{minutes:02d}:{seconds:02d}'


def build_table(
    model,
    max_tier_gap,
    max_delayed_count,
    delaying_filter_index,
    delayed_filter_index,
    max_filter_index=16,
):
    """构造 Markdown 参数说明、层内价值衰减表与临界值表。

    行表示低价值委托比被延迟委托落后多少个 tier，列表示同时被推迟的
    高价值委托数量。单元格是低价值委托仍会被选择的最大延迟整数秒。
    """
    if max_tier_gap <= 0:
        raise ValueError('最大 tier 间隔必须为正整数')
    if max_delayed_count <= 0:
        raise ValueError('最大被延迟委托数必须为正整数')
    if max_filter_index < 0:
        raise ValueError('最大过滤器编号必须为非负整数')

    delaying_ratio = model.filter_factor(delaying_filter_index) / VALUE_SCALE * 100
    delayed_ratio = model.filter_factor(delayed_filter_index) / VALUE_SCALE * 100
    lines = [
        '# 委托延迟临界值表',
        '',
        '低价值委托立即启动的收益，必须严格大于它对高价值委托造成的等待损失。',
        '表中时长是仍会选择低价值委托的最大整数秒；再延迟一秒就会放弃它。',
        '',
        '## 模型参数',
        '',
        '| 参数 | 值 |',
        '| --- | ---: |',
        f'| 相邻 tier 价值倍率 | {model.tier_value_ratio} |',
        f'| 启动等待半衰期 | {format_duration(model.delay_half_life)} |',
        f'| 层内价值下限 | {model.filter_value_floor / 100:.2f}% |',
        f'| 层内编号半衰期 | {model.filter_value_half_life:g} |',
        f'| 低价值委托层内编号 | {delaying_filter_index} ({delaying_ratio:.2f}%) |',
        f'| 被延迟委托层内编号 | {delayed_filter_index} ({delayed_ratio:.2f}%) |',
        '',
        '## 层内价值衰减表',
        '',
        '| 层内位置 | 过滤器编号 | 相对价值比例 |',
        '| ---: | ---: | ---: |',
    ]
    for idx in range(max_filter_index + 1):
        ratio = model.filter_factor(idx) / VALUE_SCALE * 100
        lines.append(f'| 第 {idx + 1} 个元素 | {idx} | {ratio:.2f}% |')

    lines.extend([
        '',
        '## 临界值',
        '',
        '| 低价值委托落后层数 | '
        + ' | '.join(f'延迟 {count} 个高价值委托' for count in range(1, max_delayed_count + 1))
        + ' |',
        '| ---: | ' + ' | '.join('---:' for _ in range(max_delayed_count)) + ' |',
    ])
    for tier_gap in range(1, max_tier_gap + 1):
        values = [
            format_duration(delay_threshold_seconds(
                tier_gap=tier_gap,
                delayed_count=count,
                model=model,
                delaying_filter_index=delaying_filter_index,
                delayed_filter_index=delayed_filter_index,
            ))
            for count in range(1, max_delayed_count + 1)
        ]
        lines.append(f'| {tier_gap} | ' + ' | '.join(values) + ' |')
    return '\n'.join(lines) + '\n'


def parse_args():
    """解析全部价值模型参数和表格范围。"""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--tier-ratio',
        type=float,
        default=2.0,
        help='相邻 tier 的基础价值倍率，对应 UI TierValueRatio，必须大于 1',
    )
    parser.add_argument(
        '--delay-half-life-hours',
        type=float,
        default=100,
        help='启动等待价值减半所需小时数，对应 UI DelayHalfLife，必须大于 0，保留一位小数',
    )
    parser.add_argument(
        '--filter-value-floor',
        type=float,
        default=0.6,
        help='层内编号价值下限，对应 UI FilterValueFloor，范围为 (0, 1]',
    )
    parser.add_argument(
        '--filter-value-half-life',
        type=float,
        default=4,
        help='层内编号修正衰减一半所需规则数，对应 UI FilterValueHalfLife，保留一位小数',
    )
    parser.add_argument(
        '--delaying-filter-index',
        type=int,
        default=0,
        help='立即启动的低价值委托在其 tier 内的过滤器编号',
    )
    parser.add_argument(
        '--delayed-filter-index',
        type=int,
        default=0,
        help='被推迟的高价值委托在其 tier 内的过滤器编号',
    )
    parser.add_argument('--max-tier-gap', type=int, default=8, help='表格最大 tier 间隔')
    parser.add_argument(
        '--max-delayed-count',
        type=int,
        default=4,
        help='表格最大同时被延迟委托数',
    )
    parser.add_argument(
        '--max-filter-index',
        type=int,
        default=16,
        help='层内价值衰减表展示的最大过滤器编号',
    )
    parser.add_argument('--output', type=Path, help='可选的 Markdown 输出文件')
    return parser.parse_args()


def main():
    """使用指定参数生成并输出临界值表。"""
    args = parse_args()
    model = CommissionValueModel(
        tier_value_ratio=args.tier_ratio,
        delay_half_life=round(round(args.delay_half_life_hours, 1) * 60 * 60),
        filter_value_floor=round(args.filter_value_floor * 10_000),
        filter_value_half_life=round(args.filter_value_half_life, 1),
    )
    table = build_table(
        model=model,
        max_tier_gap=args.max_tier_gap,
        max_delayed_count=args.max_delayed_count,
        delaying_filter_index=args.delaying_filter_index,
        delayed_filter_index=args.delayed_filter_index,
        max_filter_index=args.max_filter_index,
    )
    if args.output:
        args.output.write_text(table, encoding='utf-8')
        print(f'已生成: {args.output.resolve()}')
    else:
        print(table, end='')


if __name__ == '__main__':
    main()
