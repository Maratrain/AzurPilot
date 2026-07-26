"""手动战斗模式管理模块。

管理手动战斗中的舰队移动和操作策略。

手动战斗模式下，需要通过摇杆控制舰队移动。
支持的操作模式：
- stand_still_in_the_middle: 舰队停留在画面中央（适合防空关卡）
- 其他自定义移动模式

手动模式通常用于：
- 需要精确控制舰队位置的关卡
- 自动模式无法通过的高难度关卡
- 特殊战术需求（如潜艇战）

继承自 ModuleBase，被 Combat 组合使用。
"""

from module.base.base import ModuleBase
from module.combat.assets import *


class CombatManual(ModuleBase):
    """手动战斗模式管理器。

    管理手动战斗中的舰队移动和操作。

    Attributes:
        auto_mode_checked (bool): 自动模式是否已检查。
        auto_mode_switched (bool): 是否刚从自动模式切换过来。
        manual_executed (bool): 是否已执行手动操作。
    """
    auto_mode_checked = False
    auto_mode_switched = False
    manual_executed = False

    def combat_manual_reset(self):
        self.manual_executed = False

    def handle_combat_stand_still_in_the_middle(self, auto):
        """处理战斗中停留在画面中央的模式。

        Args:
            auto (str): 战斗自动模式。

        Returns:
            bool: 是否执行了操作。
        """
        if auto != 'stand_still_in_the_middle':
            return False
        # 从自动切换到手动时，舰队通常在中央，无需下移
        # 否则舰队会被移动到底部
        if self.auto_mode_switched:
            return False

        self.device.long_click(MOVE_DOWN, duration=0.8)
        return True

    def handle_combat_stand_still_bottom_left(self, auto):
        """处理战斗中隐藏到左下角的模式。

        Args:
            auto (str): 战斗自动模式。

        Returns:
            bool: 是否执行了操作。
        """
        if auto != 'hide_in_bottom_left':
            return False

        self.device.long_click(MOVE_LEFT_DOWN, duration=(3.5, 5.5))
        return True

    def handle_combat_stand_still_upper_left(self, auto):
        """处理战斗中隐藏到左上角的模式。

        Args:
            auto (str): 战斗自动模式。

        Returns:
            bool: 是否执行了操作。
        """
        if auto != 'hide_in_upper_left':
            return False

        self.device.long_click(MOVE_LEFT_UP, duration=(1.5, 3.5))
        return True

    def handle_combat_weapon_release(self):
        if self.appear_then_click(READY_AIR_RAID, interval=10):
            return True
        if self.appear_then_click(READY_TORPEDO, interval=10):
            return True

        return False

    def handle_combat_manual(self, auto):
        """处理手动战斗模式。

        Args:
            auto (str): 战斗自动模式。

        Returns:
            bool: 是否执行了操作。
        """
        if self.manual_executed or not self.auto_mode_checked:
            return False

        if self.handle_combat_stand_still_in_the_middle(auto):
            self.manual_executed = True
            return True
        if self.handle_combat_stand_still_bottom_left(auto):
            self.manual_executed = True
            return True
        if self.handle_combat_stand_still_upper_left(auto):
            self.manual_executed = True
            return True

        return False
