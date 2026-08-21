# -*- coding: utf-8 -*-
"""
委托收益聚合统计模块。

从 Cl1Database 读取原始委托收益条目，
按日/周/月维度聚合，供统计页面渲染使用。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple

from module.logger import logger
from module.statistics.cl1_database import db as cl1_db

COMMISSION_TRACKED_ITEMS = ['Gem', 'Cube', 'Chip', 'Oil', 'Coin']

COMMISSION_ITEM_META = {
    'Gem':  {'color': '#ff4757', 'order': 0},
    'Cube': {'color': '#3742fa', 'order': 1},
    'Chip': {'color': '#8854d0', 'order': 2},
    'Oil':  {'color': '#2d3436', 'order': 3},
    'Coin': {'color': '#ffa502', 'order': 4},
}

COMMISSION_ITEM_NAME_MAP = {
    'Gems': 'Gem',
    'Cubes': 'Cube',
    'CognitiveChips': 'Chip',
    'Coins': 'Coin',
}


def _parse_ts(ts_str: str) -> Optional[datetime]:
    try:
        timestamp = datetime.fromisoformat(ts_str)
    except (TypeError, ValueError):
        return None
    return timestamp if timestamp.tzinfo is None else None


def _validate_interval(start: datetime, end: datetime) -> None:
    """校验日报区间使用的本地 naive datetime 参数。"""
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        raise TypeError('start 和 end 必须是 datetime')
    if start.tzinfo is not None or end.tzinfo is not None:
        raise ValueError('start 和 end 必须是不带时区的本地时间')
    if start > end:
        raise ValueError('start 不能晚于 end')


def _iter_months(start: datetime, end: datetime):
    """按时间顺序枚举闭区间覆盖的月份，避免日报跨月漏算。"""
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1


def _build_income_summary(entries: List[Dict[str, Any]], period: str) -> Dict[str, Any]:
    """将已过滤的委托条目聚合为与既有统计页兼容的摘要。"""
    totals: Dict[str, int] = {}
    counts: Dict[str, int] = {}
    total_commissions = 0

    for entry in entries:
        try:
            commission_count = int(entry.get('commission_count', 1))
        except (TypeError, ValueError):
            commission_count = 0
        total_commissions += commission_count

        items = entry.get('items', {})
        if not isinstance(items, dict):
            continue
        for item_name, amount in items.items():
            mapped_name = COMMISSION_ITEM_NAME_MAP.get(item_name, item_name)
            if mapped_name not in COMMISSION_TRACKED_ITEMS:
                continue
            try:
                amount = int(amount)
            except (TypeError, ValueError):
                continue
            totals[mapped_name] = totals.get(mapped_name, 0) + amount
            counts[mapped_name] = counts.get(mapped_name, 0) + 1

    items_summary = {}
    detail_rows = []
    for item_name in COMMISSION_TRACKED_ITEMS:
        total = totals.get(item_name, 0)
        count = counts.get(item_name, 0)
        avg = round(total / count, 1) if count > 0 else 0
        meta = COMMISSION_ITEM_META.get(item_name, {'color': '#888', 'order': 99})

        items_summary[item_name] = {
            'total': total,
            'count': count,
            'avg': avg,
        }
        detail_rows.append({
            'name': item_name,
            'color': meta['color'],
            'total': total,
            'count': count,
            'avg': avg,
        })

    return {
        'period': period,
        'total_commissions': total_commissions,
        'items': items_summary,
        'detail_rows': detail_rows,
    }


def _filter_entries_by_period(
    entries: List[Dict[str, Any]],
    period: str,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """按时间维度过滤条目。

    Args:
        entries: 原始条目列表
        period: 'day' | 'week' | 'month'
        now: 参考时间，默认当前时间

    Returns:
        过滤后的条目列表
    """
    if now is None:
        now = datetime.now()

    if period == 'month':
        return entries

    filtered = []
    for entry in entries:
        ts = _parse_ts(entry.get('ts', ''))
        if ts is None:
            continue
        if period == 'day':
            if ts.date() == now.date():
                filtered.append(entry)
        elif period == 'week':
            week_start = now - timedelta(days=now.weekday())
            week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
            if ts >= week_start:
                filtered.append(entry)

    return filtered


def get_commission_income_summary(
    instance: str,
    period: str = 'month',
    year: int = None,
    month: int = None,
) -> Dict[str, Any]:
    """获取委托收益聚合摘要。

    Args:
        instance: 实例名称
        period: 'day' | 'week' | 'month'
        year: 年份
        month: 月份

    Returns:
        {
            'period': str,
            'total_commissions': int,
            'items': {
                'Gem': {'total': int, 'count': int, 'avg': float},
                ...
            },
            'detail_rows': [
                {'name': str, 'color': str, 'total': int, 'count': int, 'avg': float},
                ...
            ],
        }
    """
    now = datetime.now()
    if year is None:
        year = now.year
    if month is None:
        month = now.month

    entries = cl1_db.get_commission_income(instance, year, month)
    filtered = _filter_entries_by_period(entries, period, now)

    return _build_income_summary(filtered, period)


def get_commission_income_interval_summary(
    instance: str,
    start: datetime,
    end: datetime,
) -> Dict[str, Any]:
    """获取指定日报区间内的委托收益，支持跨月聚合。

    原始委托条目按月份分库保存，因此会枚举 ``[start, end)`` 涵盖的
    每个月并按时间戳二次过滤。返回结构沿用
    :func:`get_commission_income_summary`，额外带上区间和原始结算条目数。
    """
    _validate_interval(start, end)
    entries: List[Dict[str, Any]] = []

    for year, month in _iter_months(start, end):
        for entry in cl1_db.get_commission_income(instance, year, month):
            timestamp = _parse_ts(entry.get('ts', ''))
            if timestamp is not None and start <= timestamp < end:
                entries.append(entry)

    entries.sort(key=lambda entry: entry.get('ts', ''))
    summary = _build_income_summary(entries, 'interval')
    available = bool(entries)
    if not available:
        # 旧委托统计没有“本周期已检查但无结算”的心跳记录，空列表不能等同于零收益。
        summary['total_commissions'] = None
        for item in summary['items'].values():
            item.update({'total': None, 'count': None, 'avg': None})
        for item in summary['detail_rows']:
            item.update({'total': None, 'count': None, 'avg': None})
    summary.update({
        'start': start.isoformat(),
        'end': end.isoformat(),
        'available': available,
        'entry_count': len(entries) if available else None,
        # 委托原始记录的 commission_count 即本次结算的委托数量。
        # 保留既有 total_commissions，同时提供日报语义更直观的别名。
        'settled_count': summary['total_commissions'] if available else None,
    })
    return summary


def get_recent_commission_entries(
    instance: str,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """获取最近 N 条委托收益记录（按时间降序）。

    Args:
        instance: 实例名称
        limit: 返回条数上限，默认 10

    Returns:
        最近 N 条委托记录，每条包含 ts, items, commission_count
    """
    now = datetime.now()
    all_entries = []
    for offset in range(3):
        dt = now - timedelta(days=offset * 32)
        entries = cl1_db.get_commission_income(instance, dt.year, dt.month)
        for entry in entries:
            ts = _parse_ts(entry.get('ts', ''))
            if ts is not None:
                all_entries.append(entry)
        if len(all_entries) >= limit:
            break

    all_entries.sort(key=lambda e: e.get('ts', ''), reverse=True)
    return all_entries[:limit]
