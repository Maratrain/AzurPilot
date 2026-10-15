"""大世界资源读取的运行期状态；不写入用户配置或跨进程复用。"""

from dataclasses import dataclass


FARMING_TASKS = {'OpsiHazard1Leveling', 'OpsiMeowfficerFarming'}


def effective_task(config):
    """取得代理绑定后的真实任务，避免把智能调度子任务误认成父任务。"""
    return getattr(config, '_bind_task_override', None) or getattr(
        getattr(config, 'task', None), 'command', None
    )


def resource_fast_path(config):
    """只有侵蚀一、短猫和智能调度决策启用资源提速。"""
    return effective_task(config) in FARMING_TASKS | {'OpsiScheduling'}


def invalidate_action_point_inventory(config):
    """可能改变箱子库存时使完整面板缓存失效。"""
    config._opsi_ap_inventory_revision = getattr(config, '_opsi_ap_inventory_revision', 0) + 1


def invalidate_coin_cache(config):
    """交易及奖励后必须重新确认货币，不能复用尚未更新的计数动画。"""
    config._opsi_coin_revision = getattr(config, '_opsi_coin_revision', 0) + 1


def reset_resource_caches(handler):
    """初始化或应用内恢复后，旧面板、购买次数与货币画面均须重新确认。"""
    for name in ('_ap_snapshot', '_ap_purchase_count', '_yellow_coin_read', '_paired_purple_read'):
        handler.__dict__.pop(name, None)


@dataclass(frozen=True)
class ActionPointSnapshot:
    """仅完整面板成功读取时更新的箱子库存与单调计时。"""

    identity: tuple
    checked_at: float
    inventory_revision: int
    boxes: tuple
