"""Simple test script for notification helpers.

Usage:
    python tools/test_notify.py

This script tests `crash_notify` cooldown logic and `send_crash_messages` integration
with a local `notify_push` function. It avoids calling external OnePush providers.
"""
from datetime import datetime, timedelta
import time
import os
import sys

# Ensure repository root is on sys.path so `module` package can be imported when
# running this script (sys.path[0] is the script directory by default).
script_dir = os.path.dirname(os.path.abspath(__file__))
repo_root = os.path.abspath(os.path.join(script_dir, '..'))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from module.notify.notify import crash_notify, send_crash_messages, notify_webui


class DummyConfig:
    """Minimal config-like object used by crash_notify.

    Attributes:
        OpsiGeneral_LastCrashNotifyTime: datetime | None
    """

    def __init__(self):
        self.OpsiGeneral_LastCrashNotifyTime = None

    def save(self):
        # emulate a save method; in real config this persists to disk
        print("[DummyConfig] save() called")


def dummy_send_func(*args, **kwargs):
    print("[dummy_send_func] called with args:", args, "kwargs:", kwargs)
    return True


def main():
    cfg = DummyConfig()

    # 模拟：桌面已经出现过一个通知，配置中记录了上次通知时间 -> 新通知应被冷却期拦截
    cfg.OpsiGeneral_LastCrashNotifyTime = datetime.now()
    print("模拟：配置中记录的上次坠机通知时间已设置为现在 -> 新通知应被冷却期拦截")

    print("\n== 使用 send_crash_messages (由于冷却，应被拦截，不会调用 dummy_send_func) ==")
    send_crash_messages(
        cfg,
        config_name="TestInstance",
        total_ap=123,
        onepush_config=None,
        notify_push_func=dummy_send_func,
        webui_instance="TestInstance",
    )

    # 清除冷却以验证实际发送
    cfg.OpsiGeneral_LastCrashNotifyTime = None
    print("\n已清除冷却时间，下面将实际发送两条通知（会调用 dummy_send_func）:")
    send_crash_messages(
        cfg,
        config_name="TestInstance",
        total_ap=456,
        onepush_config=None,
        notify_push_func=dummy_send_func,
        webui_instance="TestInstance",
    )

    print("\n== 直接调用 notify_webui（如本机有 WebUI 会收到） ==")
    ok3 = notify_webui(
        "TestInstance",
        title=f"AzurPilot <{'TestInstance'}> 新消息♥♥♥",
        content=(
            f'当前总行动力：{789}\n'
            f'雪风大人提醒您，71即将坠机，请及时加仓'
        ),
    )
    print("notify_webui result:", ok3)


if __name__ == '__main__':
    main()
