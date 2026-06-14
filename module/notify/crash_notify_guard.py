from datetime import datetime, timedelta


CRASH_NOTIFY_COOLDOWN = timedelta(minutes=30)


def _get_last_time(config):
    """
    从配置读取上次坠机通知时间
    """
    return getattr(config, "OpsiGeneral_LastCrashNotifyTime", None)


def _set_last_time(config, value: datetime):
    """
    写入上次坠机通知时间（用于持久化防重启重复发送）
    """
    config.OpsiGeneral_LastCrashNotifyTime = value
    try:
        config.save()
    except Exception:
        # 防止某些config没有save方法导致崩溃
        pass

def _parse_time(t):
    if not t:
        return None
    if isinstance(t, str):
        try:
            return datetime.fromisoformat(t)
        except Exception:
            return None
    return t


def can_send_crash_notify(config) -> bool:
    last_time = _parse_time(_get_last_time(config))
    now = datetime.now()

    if last_time is None:
        return True

    return (now - last_time) >= CRASH_NOTIFY_COOLDOWN


def mark_crash_notify_sent(config):
    _set_last_time(config, datetime.now())


def crash_notify(config, sender_fn, *args, **kwargs):
    """
    ⭐ 统一坠机通知入口（推荐你只用这个）

    sender_fn = notify_push / handle_notify
    """
    if not can_send_crash_notify(config):
        from module.logger import logger
        logger.info("[Notify] crash notify skipped (30min cooldown)")
        return False

    result = sender_fn(*args, **kwargs)
    mark_crash_notify_sent(config)
    return result
    # module/notify/crash_notify_guard.py