"""守护模式基类。

继承 ModuleBase 并禁用卡死检测，为所有守护任务提供基础功能。
守护模式用于后台持续运行，不会因超时自动停止。
"""

from module.base.base import ModuleBase


class DaemonBase(ModuleBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.device.disable_stuck_detection()
