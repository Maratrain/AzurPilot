"""战斗血量平衡管理器。

监控战斗中各舰船的 HP 状态，提供血量检测、撤退判断和血量平衡功能。

血量检测通过 HP 条的颜色分析实现：
- 绿色 HP 条：血量充足
- 红色 HP 条：血量较低
- 通过颜色占比计算当前血量百分比

功能：
- hp_get(): 从截图中读取所有舰船的 HP 百分比
- hp_retreat_triggered(): 判断是否需要撤退（任一舰船血量过低）
- hp_reset(): 进入地图时重置 HP 数据

每个位置（先锋 3 个 + 主力 3 个 = 6 个）独立追踪。
"""

from module.base.base import ModuleBase
from module.base.button import *
from module.base.decorator import Config
from module.config.utils import to_list
from module.logger import logger

# 前排侦察位置坐标（用于特定场景的 HP 检测）
SCOUT_POSITION = [
    (403, 421),
    (625, 369),
    (821, 326)
]


class HPBalancer(ModuleBase):
    """战斗血量平衡器。

    追踪舰队中每个位置的 HP 值，提供血量检测和撤退判断。
    支持按舰队索引（1 或 2）分别管理。

    Attributes:
        fleet_current_index (int): 当前操作的舰队索引。
        fleet_show_index (int): 当前显示的舰队索引。
        _hp (dict[int, list[float]]): 各舰队的 HP 值缓存。
        _hp_has_ship (dict[int, list[bool]]): 各位置是否有舰船。
        COLOR_HP_GREEN (tuple): HP 条绿色部分的参考颜色。
        COLOR_HP_RED (tuple): HP 条红色部分的参考颜色。
    """
    fleet_current_index = 1
    fleet_show_index = 1
    _hp = {}
    _hp_has_ship = {}
    # HP 条上显示的颜色。
    COLOR_HP_GREEN = (156, 235, 57)
    COLOR_HP_RED = (99, 44, 24)

    @property
    def hp(self):
        """
        Returns:
            list[float]: 各舰船的 HP 值列表。
        """
        return self._hp[self.fleet_current_index]

    @hp.setter
    def hp(self, value):
        """
        Args:
            value (list[float]): 各舰船的 HP 值列表。
        """
        self._hp[self.fleet_current_index] = value

    @property
    def hp_has_ship(self):
        """
        Returns:
            list[bool]: 各位置是否有舰船。
        """
        return self._hp_has_ship[self.fleet_current_index]

    @hp_has_ship.setter
    def hp_has_ship(self, value):
        """
        Args:
            value (list[float]): 各位置是否有舰船。
        """
        self._hp_has_ship[self.fleet_current_index] = value

    def _calculate_hp(self, area):
        """根据颜色计算 HP。

        Args:
            area (tuple): HP 条的区域坐标。

        Returns:
            float: HP 百分比。
        """
        data = max(
            color_bar_percentage(self.device.image, area=area, prev_color=self.COLOR_HP_RED),
            color_bar_percentage(self.device.image, area=area, prev_color=self.COLOR_HP_GREEN)
        )
        return data

    def _hp_grid(self):
        # 六个 HP 条的位置，根据不同服务器的战役界面调整
        if self.config.SERVER == 'en':
            return ButtonGrid(origin=(35, 190), delta=(0, 100), button_shape=(66, 4), grid_shape=(1, 6))
        elif self.config.SERVER == 'jp':
            return ButtonGrid(origin=(35, 205), delta=(0, 100), button_shape=(66, 4), grid_shape=(1, 6))
        else:
            return ButtonGrid(origin=(35, 206), delta=(0, 100), button_shape=(66, 4), grid_shape=(1, 6))

    def hp_get(self):
        """从截图获取当前 HP。

        Returns:
            list: 6 艘舰船的 HP（float）。

        Logs:
            [HP]  98% ____ ____  98%  98%  98%
        """
        # 中文逗号修正
        weight = self.config.HpControl_HpBalanceWeight
        if '，' in self.config.HpControl_HpBalanceWeight:
            weight = self.config.HpControl_HpBalanceWeight.replace('，', ',')
            logger.info(f'[血量-平衡] 血量平衡权重 {self.config.HpControl_HpBalanceWeight} 修正为 {weight}')
            self.config.HpControl_HpBalanceWeight = weight

        hp = [self._calculate_hp(button.area) for button in self._hp_grid().buttons]
        weight = to_list(weight)
        scout = np.array(hp[3:]) * np.array(weight) / np.max(weight)

        self.hp = hp[:3] + scout.tolist()
        if self.fleet_current_index not in self._hp_has_ship:
            self.hp_has_ship = [bool(hp > 0.3) for hp in self.hp]

        logger.attr('血量', ' '.join(
            [str(int(data * 100)).rjust(3) + '%' if use else '____' for data, use in zip(hp, self.hp_has_ship)]))
        if np.sum(np.abs(np.diff(weight))) > 0:
            logger.attr('血量权重', ' '.join([str(int(data * 100)).rjust(3) + '%' for data in self.hp]))

        return self.hp

    def hp_reset(self):
        """进入地图后调用此方法重置 HP 数据。"""
        self._hp = {}
        self._hp_has_ship = {}

    def _scout_position_change(self, p1, p2):
        """交换舰船位置。即使移动到正确位置，也需要稍微上下移动。

        Args:
            p1 (int): 原始位置 [0, 2]。
            p2 (int): 目标位置 [0, 2]。
        """
        logger.info('[血量-平衡] 侦察位置交换 (%s, %s)' % (p1, p2))
        self.device.drag(p1=SCOUT_POSITION[p1], p2=SCOUT_POSITION[p2], segments=3)

    def _expected_scout_order(self, hp):
        count = np.count_nonzero(hp)
        threshold = self.config.HpControl_HpBalanceThreshold

        if count == 3:
            descending = np.sort(hp)[::-1]
            sort = np.argsort(hp)[::-1]
            if descending[0] - descending[2] <= threshold:
                # 90% 80% 70%
                order = [0, 1, 2]
            elif descending[1] - descending[2] <= threshold / 2:
                # 95% 80% 70%
                order = [sort[0], 1, 2]
                order[sort[0]] = 0
            elif descending[0] - descending[1] <= threshold / 2:
                # 90% 80% 65%
                order = [0, sort[2], 2]
                order[sort[2]] = 1
            else:
                # 95% 80% 65%
                order = [sort[0], sort[2], sort[1]]
        elif count == 2:
            if hp[1] - hp[0] > threshold:
                # 70% 100% 0%
                order = [1, 0, 2]
            else:
                # 100% 70% 0%
                order = [0, 1, 2]
        elif count == 1:
            # 80% 0% 0%
            order = [0, 1, 2]
        else:
            logger.warning(f'[血量-平衡] 血量无效: {hp}')
            order = [0, 1, 2]

        return order

    @Config.when(DEVICE_CONTROL_METHOD='minitouch')
    def _gen_exchange_step(self, target):
        """minitouch 拖拽更接近人类操作。当把第一个舰船拖到第三个位置时，
        [0, 1, 2] 变为 [1, 2, 0]，而 adb/uiautomator2 下变为 [2, 1, 0]。

        Args:
            target (list[int]): 目标排列，如 [2, 0, 1]。
        """
        diff = np.array(target) - np.array((0, 1, 2))
        count = np.count_nonzero(diff)
        if count == 3:
            if np.argsort(target)[0] == 1:
                # [0, 1, 2] -> [2, 0, 1]
                yield (2, 0)
            else:
                # [0, 1, 2] -> [1, 2, 0]
                yield (0, 2)
        elif count == 2:
            if np.argsort(target)[0] == 2:
                # [0, 1, 2] -> [1, 2, 0] -> [2, 1, 0]
                yield (0, 2)
                yield (1, 0)
            else:
                # [0, 2, 1]
                # [1, 0, 2]
                yield tuple(np.nonzero(diff)[0])
        elif count == 0:
            # [0, 1, 2]
            # 目标与原始排列相同，无需操作
            pass

    @Config.when(DEVICE_CONTROL_METHOD=None)
    def _gen_exchange_step(self, target):
        """
        Args:
            target (list[int]): 目标排列，如 [2, 0, 1]。
        """
        diff = np.array(target) - np.array((0, 1, 2))
        count = np.count_nonzero(diff)
        if count == 3:
            yield (2, 0)
            if np.argsort(target)[0] == 1:
                # [0, 1, 2] -> [2, 1, 0] -> [2, 0, 1]
                yield (2, 1)
            else:
                # [0, 1, 2] -> [2, 1, 0] -> [1, 2, 0]
                yield (1, 0)
        elif count == 2:
            # [0, 2, 1]
            # [1, 0, 2]
            # [2, 1, 0]
            yield tuple(np.nonzero(diff)[0])
        elif count == 0:
            # [0, 1, 2]
            # 目标与原始排列相同，无需操作
            pass

    def hp_balance(self):
        if self.config.Campaign_UseFleetLock:
            return False

        target = self._expected_scout_order(self.hp[3:])
        for step in self._gen_exchange_step(target):
            self._scout_position_change(*step)
            self.device.sleep(0.5)

        return True

    def hp_retreat_triggered(self):
        if self.config.HpControl_UseLowHpRetreat:
            hp = np.array(self.hp)[self.hp_has_ship]
            if np.any(hp < self.config.HpControl_LowHpRetreatThreshold):
                logger.info('[血量-撤退] 低血量撤退触发')
                return True

        return False
