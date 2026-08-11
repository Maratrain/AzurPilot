"""WebUI 轻量依赖层：仅标准库类型、协议、常量、轻量工具。

不导入任何重依赖（pywebio、requests、config、ocr 等）。
供类型注解、基类定义、常量访问使用。

运行时对象（State、task_handler、updater、Frame 真实类、RichLog 真实类等）
由 heavy 层提供，通过兼容层 __getattr__ 懒加载。
"""

from __future__ import annotations

import os
import re
import argparse
import json
import queue
import secrets
import string
import threading
import time
import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Protocol, cast, runtime_checkable


# ====== 类型别名 & 协议（仅供类型检查，运行时从 heavy 获取真实实现） ======
class Frame(Protocol):
    """页面框架协议。"""

    ALAS_MENU: Dict[str, Dict[str, List[str]]]
    ALAS_ARGS: Dict[str, Dict[str, Dict[str, Dict[str, str]]]]
    theme: str

    def run(self, initial_page: str = "home", localstorage: Optional[Dict] = None) -> None: ...
    def set_theme(self, theme: str) -> None: ...


class RichLog(Protocol):
    """富日志协议。"""

    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


# ====== 常量（有默认值，重依赖层加载后会被真实值覆盖） ======
IS_ON_PHONE_CLOUD: bool = False
RESTRICTED_DEVICE_IDS: set[str] = {"1", "2"}
RESTRICTED_DEVICE_MESSAGE: str = ""
PUBLIC_WEBUI_PASSWORD_GENERATE_FAILED_MESSAGE: str = ""

# ====== 轻量工具函数 ======
def readable_time(seconds: float) -> str:
    """可读时间格式化。"""
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{seconds/60:.1f}m"
    return f"{seconds/3600:.1f}h"


def time_delta(seconds: float) -> timedelta:
    return timedelta(seconds=seconds)


# ====== 供兼容层显式导出的公共符号 ======
__all__ = [
    # 协议
    "Frame",
    "RichLog",
    # 常量
    "IS_ON_PHONE_CLOUD",
    "RESTRICTED_DEVICE_IDS",
    "RESTRICTED_DEVICE_MESSAGE",
    "PUBLIC_WEBUI_PASSWORD_GENERATE_FAILED_MESSAGE",
    # 工具函数
    "readable_time",
    "time_delta",
    # 标准库类型别名（重导出）
    "Dict",
    "List",
    "Optional",
    "Callable",
    "Any",
    "Protocol",
    "cast",
    "runtime_checkable",
    # 标准库模块（重导出常用）
    "os",
    "re",
    "argparse",
    "json",
    "queue",
    "secrets",
    "string",
    "threading",
    "time",
    "base64",
    "datetime",
    "timedelta",
    "timezone",
    "Path",
    "partial",
]