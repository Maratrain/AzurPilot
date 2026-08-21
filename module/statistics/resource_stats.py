"""资源快照统计模块，实现游戏资源的本地记录与历史查询。
通过 SQLite 数据库存储资源变动快照，
支持按实例和时间范围查询，用于绘制资源趋势图。"""

# 此文件实现了通用资源快照的记录与查询功能。
# 当各项资源数值（如石油、物资、钻石等）发生变化时，记录快照以便后续绘制历史趋势图。
import sqlite3
import threading
import os
from datetime import datetime
from typing import Any, Dict, List

from module.logger import logger


_local_lock = threading.Lock()
_LOCAL_DB = './config/azurstats_local.db'
_table_ensured = False


class _ClosingConnection(sqlite3.Connection):
    """事务结束后立即释放连接，避免资源快照库在 Windows 上残留文件锁。"""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(_LOCAL_DB, factory=_ClosingConnection)


# Dashboard 使用的资源名称与数据库列名保持在同一处，供区间聚合复用。
RESOURCE_COLUMNS = {
    'Oil': 'oil',
    'Coin': 'coin',
    'Gem': 'gem',
    'Pt': 'pt',
    'Cube': 'cube',
    'Core': 'core',
    'Medal': 'medal',
    'Merit': 'merit',
    'GuildCoin': 'guild_coin',
    'ActionPoint': 'action_point',
    'YellowCoin': 'yellow_coin',
    'PurpleCoin': 'purple_coin',
}


def _ensure_table():
    """确保 resource_snapshots 表存在（仅首次调用时执行）"""
    global _table_ensured
    if _table_ensured:
        return
    os.makedirs(os.path.dirname(_LOCAL_DB), exist_ok=True)
    with _connect() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS resource_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance TEXT NOT NULL,
                ts TEXT NOT NULL,
                oil INTEGER,
                coin INTEGER,
                gem INTEGER,
                pt INTEGER,
                cube INTEGER,
                core INTEGER,
                medal INTEGER,
                merit INTEGER,
                guild_coin INTEGER,
                action_point INTEGER,
                yellow_coin INTEGER,
                purple_coin INTEGER
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_res_snap_instance ON resource_snapshots(instance)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_res_snap_ts ON resource_snapshots(instance, ts)')
        conn.commit()
    _table_ensured = True


def record_resource_snapshot(instance: str, resources: Dict[str, Any]) -> bool:
    """记录一次资源快照。

    当游戏内任何资源数值发生变化时调用，记录所有资源的当前值。

    Args:
        instance: 实例名称
        resources: 资源字典，包含所有 Dashboard 资源的当前值
            key 为资源名（如 Oil, Coin, Gem, Pt, Cube 等），
            value 为资源数值（int）

    Returns:
        bool: 是否成功记录
    """
    try:
        _ensure_table()
        now = datetime.now().isoformat()

        row = {
            'instance': instance,
            'ts': now,
            'oil': resources.get('Oil'),
            'coin': resources.get('Coin'),
            'gem': resources.get('Gem'),
            'pt': resources.get('Pt'),
            'cube': resources.get('Cube'),
            'core': resources.get('Core'),
            'medal': resources.get('Medal'),
            'merit': resources.get('Merit'),
            'guild_coin': resources.get('GuildCoin'),
            'action_point': resources.get('ActionPoint'),
            'yellow_coin': resources.get('YellowCoin'),
            'purple_coin': resources.get('PurpleCoin'),
        }

        with _local_lock:
            with _connect() as conn:
                conn.execute('''
                    INSERT INTO resource_snapshots (
                        instance, ts,
                        oil, coin, gem, pt, cube,
                        core, medal, merit, guild_coin,
                        action_point, yellow_coin, purple_coin
                    ) VALUES (
                        :instance, :ts,
                        :oil, :coin, :gem, :pt, :cube,
                        :core, :medal, :merit, :guild_coin,
                        :action_point, :yellow_coin, :purple_coin
                    )
                ''', row)
                conn.commit()
        return True
    except Exception as e:
        logger.warning(f'[统计-资源] 记录资源快照失败: {e}')
        return False


def get_resource_timeline(
    instance: str = 'default',
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """获取资源快照时间序列数据，用于绘制资源变化曲线。

    Args:
        instance: 实例名称
        limit: 最大返回条数

    Returns:
        list[dict]: 按时间排序的快照列表，每个包含:
            - ts: ISO 格式时间戳
            - oil, coin, gem, pt, cube, core, medal, merit, guild_coin,
              action_point, yellow_coin, purple_coin: 资源数值（可能为 None）
    """
    try:
        _ensure_table()
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                '''
                SELECT * FROM resource_snapshots
                WHERE instance = ?
                ORDER BY id DESC
                LIMIT ?
                ''',
                (instance, limit),
            ).fetchall()
            result = [dict(row) for row in rows]
            result.reverse()
            return result
    except Exception as e:
        logger.warning(f'[统计-资源] 获取资源时间线失败: {e}')
        return []


def _validate_interval(start: datetime, end: datetime) -> None:
    """校验日报区间使用的本地 naive datetime 参数。"""
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        raise TypeError('start 和 end 必须是 datetime')
    if start.tzinfo is not None or end.tzinfo is not None:
        raise ValueError('start 和 end 必须是不带时区的本地时间')
    if start > end:
        raise ValueError('start 不能晚于 end')


def _parse_snapshot_timestamp(value: Any) -> datetime | None:
    """解析历史快照时间，遇到旧数据或损坏数据时跳过。"""
    if not isinstance(value, str):
        return None
    try:
        timestamp = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if timestamp.tzinfo is not None:
        return None
    return timestamp


def get_resource_interval_summary(
    instance: str,
    start: datetime,
    end: datetime,
) -> Dict[str, Any]:
    """获取指定日报区间内 Dashboard 资源的首末值与变化。

    起始值取 ``start`` 时刻及之前最后一次有效快照，终止值取半开区间
    ``[start, end)`` 内最后一次有效快照。这样相邻日报不会重复使用
    恰好发生在结束边界的快照，也不会将窗口中首次
    采集到的值错误地当作窗口开始基线；任一端缺失时 ``delta`` 为
    ``None``，并通过对应的 ``*_known`` 字段明确标识。

    Args:
        instance: 实例名称。
        start: 本地 naive datetime 的统计起点（包含）。
        end: 本地 naive datetime 的统计终点（不包含）。

    Returns:
        可直接 JSON 序列化的字典，``resources`` 按 Dashboard 资源名
        返回 ``start``、``end``、``delta``、观测时间和可用状态。
    """
    _validate_interval(start, end)

    # 每种资源独立寻找有效快照，避免同一行中某个资源缺失影响其他资源。
    summary = {
        resource_name: {
            'start': None,
            'end': None,
            'delta': None,
            'baseline_known': False,
            'end_known': False,
            'start_observed_at': None,
            'end_observed_at': None,
        }
        for resource_name in RESOURCE_COLUMNS
    }

    try:
        _ensure_table()
        with _local_lock:
            with _connect() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    '''
                    SELECT * FROM resource_snapshots
                    WHERE instance = ?
                    ORDER BY id ASC
                    ''',
                    (instance,),
                ).fetchall()

        for row in rows:
            row_data = dict(row)
            timestamp = _parse_snapshot_timestamp(row_data.get('ts'))
            if timestamp is None or timestamp >= end:
                continue

            for resource_name, column_name in RESOURCE_COLUMNS.items():
                value = row_data.get(column_name)
                if value is None:
                    continue
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    continue

                item = summary[resource_name]
                if timestamp <= start:
                    previous_timestamp = _parse_snapshot_timestamp(
                        item['start_observed_at']
                    )
                    if previous_timestamp is None or timestamp >= previous_timestamp:
                        item['start'] = value
                        item['baseline_known'] = True
                        item['start_observed_at'] = timestamp.isoformat()
                if start <= timestamp < end:
                    previous_timestamp = _parse_snapshot_timestamp(
                        item['end_observed_at']
                    )
                    if previous_timestamp is None or timestamp >= previous_timestamp:
                        item['end'] = value
                        item['end_known'] = True
                        item['end_observed_at'] = timestamp.isoformat()

        for item in summary.values():
            if item['baseline_known'] and item['end_known']:
                item['delta'] = item['end'] - item['start']

        return {
            'instance': instance,
            'start': start.isoformat(),
            'end': end.isoformat(),
            'resources': summary,
        }
    except Exception as e:
        logger.warning(f'[统计-资源] 获取资源区间摘要失败: {e}')
        return {
            'instance': instance,
            'start': start.isoformat(),
            'end': end.isoformat(),
            'resources': summary,
        }


__all__ = [
    'RESOURCE_COLUMNS',
    'record_resource_snapshot',
    'get_resource_timeline',
    'get_resource_interval_summary',
]
