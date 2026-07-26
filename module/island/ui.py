"""
岛屿（Island）UI 导航模块。

提供岛屿系统中各子页面的导航和检测功能，包括：
- 岛屿管理页面的进入与检测
- 岛屿运输页面的进入与检测
- 季节活动底部导航栏的切换
- 从岛屿子页面返回手机页面
- 确保当前在指定页面的导航逻辑
- 处理岛屿相关的弹窗（维护公告、信息弹窗等）
"""
from module.base.timer import Timer
from module.handler.assets import MAINTENANCE_ANNOUNCE, USE_DATA_KEY_NOTIFIED
from module.island.assets import *
from module.logger import logger
from module.ui.assets import SHOP_BACK_ARROW
from module.ui.page import page_island_phone
from module.ui.ui import UI


class IslandUI(UI):
    """
    岛屿 UI 导航处理器。

    继承 UI 基类，提供岛屿系统特有的页面导航和弹窗处理功能。
    作为岛屿各功能模块（农场、牧场、渔场等）的 UI 基础设施层。

    主要功能：
    - 页面检测：island_in_management()、island_in_transport()
    - 页面导航：island_management_enter()、island_transport_enter()、island_ui_back()
    - 页面确保：ui_ensure_management_page()（带自动返回逻辑）
    - 弹窗处理：handle_get_items()、ui_additional()（维护公告、信息弹窗）

    Attributes:
        继承自 UI 的所有属性（config、device、image 等）。
    """
    def ui_additional(self, get_ship=True):
        """
        处理岛屿页面的额外弹窗，覆盖父类方法禁用舰船获取处理。

        Args:
            get_ship (bool): 是否处理舰船获取弹窗（在岛屿中固定为 False）。
        """
        return super().ui_additional(get_ship=False)

    def island_in_management(self, interval=0):
        """
        检测是否在岛屿管理页面。

        Args:
            interval (int): 点击间隔

        Returns:
            bool: 是否在 ISLAND_MANAGEMENT_CHECK 页面
        """
        return self.appear(ISLAND_MANAGEMENT_CHECK, offset=(20, 20), interval=interval)

    #@cached_property
    def _island_season_bottom_navbar(self):
        """
        创建季节活动底部导航栏实例。

        导航栏包含 6 个选项卡：主页、PT奖励、赛季任务、赛季商店、赛季排名、赛季历史。
        通过活跃颜色 (237, 237, 237) 和非活跃颜色 (65, 78, 96) 区分选中状态。

        Returns:
            Navbar: 季节活动底部导航栏实例。
        """
        island_season_bottom_navbar = ButtonGrid(
            origin=(14, 677), delta=(213, 0),
            button_shape=(186, 33), grid_shape=(6, 1),
            name='ISLAND_SEASON_BOTTOM_NAVBAR'
        )
        return Navbar(grids=island_season_bottom_navbar,
                      active_color=(237, 237, 237),
                      inactive_color=(65, 78, 96),
                      active_count=500,
                      inactive_count=500)

    def island_season_bottom_navbar_ensure(self, left=None, right=None):
        """
        确保切换到季节活动底部导航栏的指定标签页。

        Args:
            left (int): 从左数的标签页位置
                1=主页, 2=PT奖励, 3=赛季任务, 4=赛季商店, 5=赛季排名, 6=赛季历史
            right (int): 从右数的标签页位置
                1=赛季历史, 2=赛季排名, 3=赛季商店, 4=赛季任务, 5=PT奖励, 6=主页
        """
        return self.appear(ISLAND_MANAGEMENT_CHECK, offset=(20, 20), interval=interval)

    def island_in_transport(self, interval=0):
        """
        检测是否在岛屿运输页面。

        Args:
            interval (int): 点击间隔

        Returns:
            bool: 是否在 ISLAND_TRANSPORT_CHECK 页面
        """
        return self.match_template_color(ISLAND_TRANSPORT_CHECK, offset=(20, 20), interval=interval)

    def island_management_enter(self):
        """
        进入岛屿管理页面。

        Returns:
            bool: 是否成功进入

        Pages:
            in: page_island_phone
            out: ISLAND_MANAGEMENT_CHECK
        """
        logger.info('进入岛屿管理')
        self.interval_clear(ISLAND_MANAGEMENT_CHECK)
        if self.appear(ISLAND_MANAGEMENT_LOCKED, offset=(20, 20)):
            return False
        self.ui_click(
            click_button=ISLAND_MANAGEMENT,
            check_button=self.island_in_management,
            offset=(20, 20),
            retry_wait=2,
            skip_first_screenshot=True
        )
        return True

    def island_transport_enter(self):
        """
        进入岛屿运输页面。

        Returns:
            bool: 是否成功进入

        Pages:
            in: page_island_phone
            out: ISLAND_TRANSPORT_CHECK
        """
        logger.info('进入岛屿运输')
        self.ui_click(
            click_button=ISLAND_TRANSPORT,
            check_button=self.island_in_transport,
            offset=(20, 20),
            retry_wait=2,
            skip_first_screenshot=True
        )
        return True

    def island_ui_back(self):
        """
        从岛屿子页面返回到岛屿手机页面。

        Pages:
            in: 任意带有 SHOP_BACK_ARROW 的页面
            out: page_island_phone
        """
        logger.info('岛屿UI返回')
        self.ui_click(
            click_button=SHOP_BACK_ARROW,
            check_button=page_island_phone.check_button,
            offset=(20, 20),
            retry_wait=2,
            skip_first_screenshot=True
        )

    def ui_ensure_management_page(self):
        """
        确保当前在岛屿管理页面，如果不在则导航过去。

        Pages:
            in: page_island_phone 或产品页面
            out: ISLAND_MANAGEMENT_CHECK
        """
        logger.info('UI确保管理页面')
        self.interval_clear(ISLAND_MANAGEMENT_CHECK)
        confirm_timer = Timer(1, count=2).start()
        for _ in self.loop():
            if self.island_in_management():
                if confirm_timer.reached():
                    break
                continue
            else:
                confirm_timer.reset()

            if self.appear_then_click(SHOP_BACK_ARROW, offset=(20, 20), interval=2):
                continue

            if self.appear_then_click(ISLAND_MANAGEMENT, offset=(20, 20), interval=2):
                continue

    def handle_get_items(self):
        """
        处理岛屿中的物品获取弹窗。

        检测 GET_ITEMS_ISLAND 弹窗并点击关闭。

        Returns:
            bool: 是否检测到并处理了物品获取弹窗。
        """
        if self.appear_then_click(GET_ITEMS_ISLAND, offset=(20, 20), interval=2):
            return True
        return False

    def ui_additional(self, get_ship=True):
        # 处理宿舍菜单页面的通知弹窗
        if self.appear(MAINTENANCE_ANNOUNCE, offset=(100, 50)):
            for _ in self.loop():
                enabled = self.image_color_count(
                    USE_DATA_KEY_NOTIFIED, color=(140, 207, 66), threshold=180, count=10)
                if enabled:
                    break

                if self.appear(MAINTENANCE_ANNOUNCE, offset=(100, 50), interval=5):
                    self.device.click(USE_DATA_KEY_NOTIFIED)
                    continue

            self.interval_clear(MAINTENANCE_ANNOUNCE)
            self.appear_then_click(MAINTENANCE_ANNOUNCE, offset=(100, 50), interval=2)
            return True
        
        # 处理岛屿页面的信息弹窗
        if self.appear_then_click(ISLAND_INFO_EXIT, offset=(30, 30), interval=3):
            return True

        return super().ui_additional(get_ship=False)
