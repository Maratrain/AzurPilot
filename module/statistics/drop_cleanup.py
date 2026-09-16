"""掉落记录截图的保留天数清理（Drop Record Retention）。

掉落记录模块会写入两类截图，默认都永久保留：

    - ``DropRecord_SaveFolder``（默认 ``./screenshots``）下按 genre 分子目录
      保存的掉落截图，文件名是 13 位毫秒时间戳（见 ``AzurStats.commit()``）；
    - ``log/commission_rewards/<实例>/<YYYY-MM>/`` 下供统计页「查看截图」
      使用的委托收益截图。

用户在「掉落记录」设置里填了保留天数后，超过天数的截图会被删除；
填 0 表示不按天数清理。删除是不可逆操作，所以只删文件名符合本模块
命名规则的图片，``screenshots/item_templates`` 里用户自己命名的模板
文件不会被误删。
"""

import os
import re
import time

from module.logger import logger

# 两次实际清理之间的最小间隔（秒）。掉落记录提交非常频繁（每场战斗一次），
# 扫目录的成本没必要每次都付。
CLEANUP_INTERVAL = 3600

# 委托收益截图目录，与 Commission._save_commission_reward_screenshots 保持一致
COMMISSION_REWARD_FOLDER = os.path.join('.', 'log', 'commission_rewards')

# 掉落截图文件名：13 位毫秒时间戳，可带 info 后缀
# （``AzurStats.commit()`` 生成的 ``{now}.png`` / ``{now}_{info}.png``）
DROP_IMAGE_PATTERN = re.compile(r'^\d{13}(_.+)?\.png$')

_LAST_CLEANUP = 0.0


def drop_screenshot_retention_days(config):
    """读取掉落截图保留天数。

    配置值不可解析时按 0（不清理）处理：删除是不可逆操作，
    配置异常时宁可什么都不删。

    Args:
        config: 当前运行实例的 AzurLaneConfig。

    Returns:
        int: 保留天数，0 表示不按天数清理。
    """
    value = getattr(config, 'DropRecord_RetentionDays', 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning(f'[掉落记录] 截图保留天数配置无效: {value!r}，本次跳过清理')
        return 0


def _remove_expired(folder, deadline, now, pattern=None):
    """删除文件夹下超过保留时长的截图，并移除清空后的空目录。

    Args:
        folder (str): 待清理目录，不存在时直接返回。
        deadline (float): 保留时长（秒），文件年龄超过它即删除。
        now (float): 当前时间戳。
        pattern (re.Pattern): 文件名过滤器，None 表示所有 .png 都算。

    Returns:
        int: 删除的文件数。
    """
    if not os.path.isdir(folder):
        return 0

    removed = 0
    for path, _, names in os.walk(folder, topdown=False):
        for name in names:
            if not name.endswith('.png'):
                continue
            if pattern is not None and not pattern.match(name):
                continue
            file = os.path.join(path, name)
            try:
                if now - os.path.getmtime(file) < deadline:
                    continue
                os.remove(file)
                removed += 1
            except OSError:
                # 文件被占用/权限不足时跳过，下次清理再试
                continue
        if path != folder:
            try:
                os.rmdir(path)
            except OSError:
                pass
    return removed


def cleanup_drop_screenshots(config, retention_days):
    """按保留天数清理掉落截图与委托收益截图。

    只清理当前实例自己的委托收益截图目录，其它实例由各自的
    配置实例负责（各实例的保留天数可能不同）。

    Args:
        config: 当前运行实例的 AzurLaneConfig。
        retention_days (int): 保留天数，小于等于 0 表示不清理。

    Returns:
        int: 删除的文件数。
    """
    if retention_days <= 0:
        return 0

    now = time.time()
    deadline = retention_days * 86400
    removed = _remove_expired(
        str(config.DropRecord_SaveFolder), deadline, now, DROP_IMAGE_PATTERN)

    instance = getattr(config, 'config_name', None)
    if instance:
        removed += _remove_expired(
            os.path.join(COMMISSION_REWARD_FOLDER, str(instance)), deadline, now)

    if removed:
        logger.info(f'[掉落记录] 已清理 {removed} 张超过 {retention_days} 天的截图')
    return removed


def cleanup_drop_screenshots_if_due(config):
    """按配置清理过期掉落截图，带节流（每小时最多真正清理一次）。

    由 ``AzurStats.new()`` 调用：掉落记录提交在战斗中很频繁，
    但清理没必要跟着那么勤。

    Args:
        config: 当前运行实例的 AzurLaneConfig。

    Returns:
        int: 本次实际删除的文件数；未到清理时间或保留天数为 0 时返回 0。
    """
    global _LAST_CLEANUP

    days = drop_screenshot_retention_days(config)
    if days <= 0:
        return 0

    now = time.time()
    if now - _LAST_CLEANUP < CLEANUP_INTERVAL:
        return 0
    _LAST_CLEANUP = now

    return cleanup_drop_screenshots(config, days)
