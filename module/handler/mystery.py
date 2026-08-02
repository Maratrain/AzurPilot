"""神秘格子（Mystery Node）处理器。

处理地图上的神秘格子事件，包括：
- 获取道具（装备箱、材料等）
- 获取弹药补给
- 获取航空支援（航母编队）

继承自 StrategyHandler 和 EnemySearchingHandler，
可在地图操作的状态循环中直接调用。
"""

from module.base.timer import Timer
from module.base.utils import area_cross_area
from module.combat.assets import GET_ITEMS_1
from module.handler.assets import *
from module.handler.enemy_searching import EnemySearchingHandler
from module.handler.strategy import StrategyHandler
from module.logger import logger


class MysteryHandler(StrategyHandler, EnemySearchingHandler):
    """神秘格子事件处理器。

    处理地图中踩到神秘格子（问号格子）后触发的各类事件。
    神秘格子可能给予道具、弹药或航空支援。

    Attributes:
        _get_ammo_log_timer (Timer): 弹药获取日志节流计时器，
            避免频繁记录相同的弹药获取日志。
        carrier_count (int): 本次地图中获取的航空支援次数。
    """
    _get_ammo_log_timer = Timer(3)
    carrier_count = 0

    def handle_mystery(self, button=None):
        """处理神秘格子事件的统一入口。

        依次检测道具获取、弹药获取和航空支援三种事件类型。

        Args:
            button (Button | None): 获取道具时点击的按钮。
                可以是目标格子，使操作更接近人类行为。
                为 None 时使用默认的 MYSTERY_ITEM 按钮。

        Returns:
            str | bool: 事件类型字符串（'get_item'、'get_ammo'、'get_carrier'），
                未检测到任何事件时返回 False。
        """
        with self.stat.new(
                genre=self.config.campaign_name, method=self.config.DropRecord_CombatRecord
        ) as drop:
            if self.handle_mystery_items(button=button, drop=drop):
                return 'get_item'
            if self.handle_mystery_ammo(drop=drop):
                return 'get_ammo'
            if self.handle_mystery_carrier(drop=drop):
                return 'get_carrier'

            return False

    def handle_mystery_items(self, button=None, drop=None):
        """处理神秘格子的道具获取事件。

        检测 "获得道具" 界面，记录掉落并关闭界面。

        Args:
            button (Button | None): 点击按钮。当 `MAP_MYSTERY_MAP_CLICK` 关闭时
                使用默认 MYSTERY_ITEM 按钮。
            drop (DropImage | None): 掉落记录对象。

        Returns:
            bool: 是否处理了道具获取事件。
        """
        if not self.config.MAP_MYSTERY_MAP_CLICK:
            button = MYSTERY_ITEM
        if button is None or area_cross_area(button.button, MYSTERY_ITEM.area, threshold=5):
            button = MYSTERY_ITEM

        if self.appear(GET_ITEMS_1, offset=5):
            logger.attr('神秘格子', '获得道具')
            if drop:
                drop.add(self.device.image)
            self.device.click(button)
            self.device.sleep(0.5)
            self.device.screenshot()
            self.strategy_close()
            return True

        return False

    def handle_mystery_ammo(self, drop=None):
        """处理神秘格子的弹药补给事件。

        检测信息栏中的弹药获取提示，记录掉落。

        Args:
            drop (DropImage | None): 掉落记录对象。

        Returns:
            bool: 是否检测到弹药获取。
        """
        if self.info_bar_count():
            if self._get_ammo_log_timer.reached() and self.appear(GET_AMMO):
                logger.attr('神秘格子', '获得弹药')
                self._get_ammo_log_timer.reset()
                if drop:
                    drop.add(self.device.image)
                return True

        return False

    def handle_mystery_carrier(self, drop=None):
        """处理神秘格子的航空支援（航母编队）事件。

        当配置允许时，检测地图中出现的敌人搜索动画（航母加入），
        等待动画完成并记录掉落。

        Args:
            drop (DropImage | None): 掉落记录对象。

        Returns:
            bool: 是否处理了航空支援事件。
        """
        if self.config.MAP_MYSTERY_HAS_CARRIER:
            if self.is_in_map() and self.enemy_searching_appear():
                logger.attr('神秘格子', '获得航母支援')
                self.carrier_count += 1
                if drop:
                    drop.add(self.device.image)
                self.handle_in_map_with_enemy_searching()
                return True

        return False
