"""自动战斗模式管理模块。

管理战斗中的自动/手动模式切换。

碧蓝航线的战斗支持两种模式：
- 自动模式（Auto）：舰船自动移动和攻击，玩家无需操作
- 手动模式（Manual）：玩家控制舰船移动和攻击时机

自动模式通过战斗画面中的 Auto 按钮切换。
不同情绪值下 Auto 按钮的位置可能不同（133/150 偏移量）。

继承自 ModuleBase，被 Combat 组合使用。
"""

from module.base.base import ModuleBase
from module.base.timer import Timer
from module.combat.assets import COMBAT_AUTO, COMBAT_AUTO_133, COMBAT_AUTO_150, COMBAT_AUTO_SWITCH
from module.logger import logger


class CombatAuto(ModuleBase):
    """自动战斗模式管理器。

    检测和切换战斗中的自动/手动模式。

    Attributes:
        auto_skip_timer (Timer): 自动跳过检测计时器。
        auto_click_interval_timer (Timer): 自动点击间隔计时器。
        auto_mode_checked (bool): 自动模式是否已检查。
        auto_mode_switched (bool): 自动模式是否已切换。
        auto_mode_click_timer (Timer): 自动模式点击计时器。
    """
    auto_skip_timer = Timer(1)
    auto_click_interval_timer = Timer(1)
    auto_mode_checked = False
    auto_mode_switched = False
    auto_mode_click_timer = Timer(5)

    def combat_joystick_appear(self) -> bool:
        """检测摇杆是否出现，若出现则表示战斗处于手动模式。"""
        if self.appear(COMBAT_AUTO, offset=(20, 20)):
            return True
        if self.appear(COMBAT_AUTO_133, offset=(20, 20)):
            return True
        if self.appear(COMBAT_AUTO_150, offset=(20, 20)):
            return True
        return False

    def combat_auto_reset(self):
        self.auto_mode_click_timer.reset()
        self.auto_skip_timer.reset()
        self.auto_mode_checked = False
        self.auto_mode_switched = False

    def handle_combat_auto(self, auto):
        """处理战斗自动模式切换。

        Args:
            auto (str): 战斗自动模式。

        Returns:
            bool: 是否执行了操作。
        """
        if self.auto_mode_checked:
            return False
        if self.auto_mode_click_timer.reached():
            logger.info('[战斗-自动] 自动模式检查计时器到达')
            self.auto_mode_checked = True
            return False
        if not self.auto_skip_timer.reached():
            return False
        if not self.auto_click_interval_timer.reached():
            return False

        auto = auto == 'combat_auto'
        if self.combat_joystick_appear():
            if auto:
                self.device.click(COMBAT_AUTO_SWITCH)
                self.auto_click_interval_timer.reset()
                self.auto_mode_switched = True
                return True
        else:
            if not auto:
                self.device.click(COMBAT_AUTO_SWITCH)
                self.auto_click_interval_timer.reset()
                self.auto_mode_switched = True
                return True

        return False
