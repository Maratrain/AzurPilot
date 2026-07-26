"""自动搜索处理器。

管理游戏的自动搜索（Auto Search）功能，包括：
- 舰队准备界面的侧边栏切换（编队/指挥喵/自动搜索设置）
- 自动搜索设置选项的切换（如舰队1打道中/舰队2打Boss等）
- 地图中自动搜索开关的检测和控制
- 自动搜索菜单的继续/退出操作

自动搜索是碧蓝航线的核心功能之一，允许玩家在通关模式下
自动进行地图探索，无需手动操作。

继承自 EnemySearchingHandler，在 FastForwardHandler 中被进一步扩展。
"""

import numpy as np

from module.base.button import ButtonGrid
from module.base.decorator import Config
from module.base.timer import Timer
from module.handler.assets import *
from module.handler.enemy_searching import EnemySearchingHandler
from module.logger import logger
from module.map.assets import FLEET_PREPARATION_CHECK

# 自动搜索设置按钮列表，对应游戏界面中的 6 个选项
AUTO_SEARCH_SETTINGS = [
    AUTO_SEARCH_SET_MOB,       # 舰队1打道中，舰队2打Boss
    AUTO_SEARCH_SET_BOSS,      # 舰队1打Boss，舰队2打道中
    AUTO_SEARCH_SET_ALL,       # 舰队1全出击，舰队2待命
    AUTO_SEARCH_SET_STANDBY,   # 舰队1待命，舰队2全出击
    AUTO_SEARCH_SET_SUB_AUTO,  # 潜艇自动呼叫
    AUTO_SEARCH_SET_SUB_STANDBY  # 潜艇待命
]
# 设置名称到按钮索引的映射
dic_setting_name_to_index = {
    'fleet1_mob_fleet2_boss': 0,
    'fleet1_boss_fleet2_mob': 1,
    'fleet1_all_fleet2_standby': 2,
    'fleet1_standby_fleet2_all': 3,
    'sub_auto_call': 4,
    'sub_standby': 5,
}
# 按钮索引到设置名称的反向映射
dic_setting_index_to_name = {v: k for k, v in dic_setting_name_to_index.items()}


class AutoSearchHandler(EnemySearchingHandler):
    """自动搜索功能处理器。

    管理舰队准备界面和地图中的自动搜索相关操作。
    不同服务器的 UI 布局略有差异（侧边栏按钮位置和大小），
    通过 @Config.when 装饰器实现服务器特定的适配。

    Attributes:
        _auto_search_offset (tuple): 自动搜索选项的匹配偏移量。
        _auto_search_menu_offset (tuple): 自动搜索菜单的匹配偏移量，
            当 MULTIPLE_SORTIE 出现时向左偏移 213px。
    """
    @Config.when(SERVER='en')
    def _fleet_sidebar(self):
        if FLEET_PREPARATION_CHECK.match(self.device.image, offset=(20, 80)):
            offset = np.subtract(FLEET_PREPARATION_CHECK.button, FLEET_PREPARATION_CHECK._button)[1]
        else:
            offset = 0
        logger.attr('_fleet_sidebar_offset', offset)
        return ButtonGrid(
            origin=(1178, 171 + offset), delta=(0, 53),
            button_shape=(98, 42), grid_shape=(1, 3), name='FLEET_SIDEBAR')

    @Config.when(SERVER=None)
    def _fleet_sidebar(self):
        if FLEET_PREPARATION_CHECK.match(self.device.image, offset=(20, 80)):
            offset = np.subtract(FLEET_PREPARATION_CHECK.button, FLEET_PREPARATION_CHECK._button)[1]
        else:
            offset = 0
        logger.attr('_fleet_sidebar_offset', offset)
        return ButtonGrid(
            origin=(1185, 155 + offset), delta=(0, 111),
            button_shape=(53, 104), grid_shape=(1, 3), name='FLEET_SIDEBAR')

    def _fleet_preparation_get(self):
        """
        获取舰队准备界面当前选中的侧边栏索引。

        Returns:
            int:
                1 表示编队
                2 表示指挥喵
                3 表示自动搜索设置
        """
        current = 0
        total = 0
        sidebar = self._fleet_sidebar()

        for idx, button in enumerate(sidebar.buttons):
            if self.image_color_count(button, color=(99, 235, 255), threshold=221, count=50):
                current = idx + 1
                total = idx + 1
                continue
            if self.image_color_count(button, color=(255, 255, 255), threshold=221, count=100):
                total = idx + 1
            else:
                break

        if not current:
            logger.warning('[处理器-自动搜索] 没有活跃的舰队侧边栏')
        logger.attr('舰队侧边栏', f'{current}/{total}')
        return current

    def fleet_preparation_sidebar_ensure(self, index):
        """
        确保舰队准备界面切换到指定的侧边栏标签。

        Args:
            index (int):
                1 表示编队
                2 表示指挥喵
                3 表示自动搜索设置

        Returns:
            bool: 是否成功切换到目标侧边栏，最多尝试 3 次，
                  超过则返回 False，成功则返回 True。
        """
        if index <= 0 or index > 5:
            logger.warning(f'[处理器-自动搜索] 无法确保侧边栏索引，{index}，限制为1到5')
            return False

        interval = Timer(1, count=2)
        sidebar = self._fleet_sidebar()
        for _ in self.loop(timeout=3):
            current = self._fleet_preparation_get()
            if current == index:
                return True
            if interval.reached():
                self.device.click(sidebar[0, index - 1])
                interval.reset()
                continue
        else:
            logger.warning('[处理器-自动搜索] 无法确保侧边栏切换')
            return False

    def _auto_search_set_click(self, setting):
        """
        点击自动搜索设置选项。

        Args:
            setting (str): 目标设置名称。

        Returns:
            bool: 是否已选中正确的选项。
        """
        active = []

        for index, button in enumerate(AUTO_SEARCH_SETTINGS):
            if self.image_color_count(button.button, color=(156, 255, 82), threshold=221, count=20):
                active.append(index)

        if not active:
            logger.warning('[处理器-自动搜索] 未找到活跃的自动搜索设置')
            return False

        logger.attr('自动搜索设置', ', '.join([dic_setting_index_to_name[index] for index in active]))

        if setting not in dic_setting_name_to_index:
            logger.warning(f'[处理器-自动搜索] 未知的自动搜索设置: {setting}')
        target_index = dic_setting_name_to_index[setting]

        if target_index in active:
            logger.info('[处理器-自动搜索] 已选择正确的自动搜索设置')
            return True
        else:
            self.device.click(AUTO_SEARCH_SETTINGS[target_index])
            return False

    def auto_search_setting_ensure(self, setting, skip_first_screenshot=True):
        """
        确保自动搜索设置切换到指定选项。

        Args:
            setting (str):
                fleet1_mob_fleet2_boss, fleet1_boss_fleet2_mob, fleet1_all_fleet2_standby,
                fleet1_standby_fleet2_all, sub_auto_call, sub_standby
            skip_first_screenshot (bool): 是否跳过首次截图。

        Returns:
            bool: 是否成功切换到目标设置，最多尝试 5 次，
                  超过则返回 False，成功则返回 True。
        """
        counter = 0
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()
            if self._auto_search_set_click(setting):
                return True
            else:
                if counter >= 5:
                    logger.warning('[处理器-自动搜索] 无法确保自动搜索设置切换')
                    return False
                counter += 1
                self.device.sleep((0.3, 0.5))
                continue

    _auto_search_offset = (5, 5)
    # 当 MULTIPLE_SORTIE 出现时向左偏移 213px
    _auto_search_menu_offset = (250, 30)

    def is_auto_search_running(self):
        """
        判断自动搜索是否正在运行。

        Returns:
            bool: 自动搜索是否已开启。
        """
        return self.appear(AUTO_SEARCH_MAP_OPTION_ON, offset=self._auto_search_offset) \
               and self.appear(AUTO_SEARCH_MAP_OPTION_ON)

    def handle_auto_search_map_option(self):
        """
        确保地图中的自动搜索选项已开启。

        Returns:
            bool: 是否进行了点击操作。
        """
        if self.appear(AUTO_SEARCH_MAP_OPTION_OFF, offset=self._auto_search_offset) \
                and self.appear_then_click(AUTO_SEARCH_MAP_OPTION_OFF, interval=2):
            return True

        return False

    def is_in_auto_search_menu(self):
        """
        判断是否处于自动搜索菜单界面。

        Returns:
            bool: 是否在自动搜索菜单中。
        """
        return AUTO_SEARCH_MENU_CONTINUE.match_luma(self.device.image, offset=self._auto_search_menu_offset)

    def handle_auto_search_continue(self):
        return self.appear_then_click(AUTO_SEARCH_MENU_CONTINUE, offset=self._auto_search_menu_offset, interval=2)

    def handle_auto_search_exit(self, drop=None):
        """
        处理自动搜索菜单的退出操作。

        Args:
            drop (DropImage): 掉落记录对象。

        Returns:
            bool: 是否执行了退出操作。
        """
        if self.appear(AUTO_SEARCH_MENU_EXIT, offset=self._auto_search_menu_offset, interval=2):
            # 此处实现较粗糙
            if drop:
                drop.handle_add(main=self, before=4)
            self.device.click(AUTO_SEARCH_MENU_EXIT)
            self.interval_reset(AUTO_SEARCH_MENU_EXIT)
            return True
        else:
            return False

    def ensure_auto_search_exit(self, skip_first_screenshot=True):
        """
        Pages:
            in: is_in_auto_search_menu
            out: page_campaign 或 page_event 或 page_sp
        """
        if not self.is_in_auto_search_menu():
            return False

        with self.stat.new(
                genre=self.config.campaign_name, method=self.config.DropRecord_CombatRecord
        ) as drop:
            while 1:
                if skip_first_screenshot:
                    skip_first_screenshot = False
                else:
                    self.device.screenshot()

                if self.handle_auto_search_exit(drop=drop):
                    continue

                # 结束条件
                if self.is_in_stage():
                    break

        return True