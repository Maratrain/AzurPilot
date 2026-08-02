"""困难模式装备管理模块。

处理困难关卡中舰队装备的自动装卸。根据配置的舰队编号
选择对应的装备入口，继承通用装备管理逻辑，实现在进入
困难关卡前自动换装、通关后还原装备的流程。
"""

from module.equipment.equipment import Equipment
from module.hard.assets import *
from module.map.assets import *


class HardEquipment(Equipment):
    def equipment_take_on(self):
        if self.config.FLEET_HARD_EQUIPMENT is None:
            return False
        if self.equipment_has_take_on:
            return False

        enter = EQUIP_ENTER_1 if self.config.FLEET_HARD == 1 else EQUIP_ENTER_2
        super().equipment_take_on(enter=enter, out=FLEET_PREPARATION, fleet=self.config.FLEET_HARD_EQUIPMENT)
        return True

    def equipment_take_off(self):
        if self.config.FLEET_HARD_EQUIPMENT is None:
            return False
        if not self.equipment_has_take_on:
            return False

        enter = EQUIP_ENTER_1 if self.config.FLEET_HARD == 1 else EQUIP_ENTER_2
        super().equipment_take_off(enter=enter, out=FLEET_PREPARATION, fleet=self.config.FLEET_HARD_EQUIPMENT)
        return True
