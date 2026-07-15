from module.ui.ui import UI
from module.logger import logger
from dataclasses import dataclass
from module.base.filter import Filter
from module.ui.page import page_profile, page_main
from module.secretary.scanner import SecretaryScanner
from module.secretary.dock import SecretaryDockMixin,DOCK_SORTING
from module.secretary.ship_scanner import ShipScanner
from module.notify.notify import handle_notify, notify_webui
from datetime import timedelta
from module.base.timer import current_time
from module.ui_white.assets import (
    PROFILE_CHECK,
    SECRETARY_BUTTON,
    SECRETARY_GROUP_CHECK,
    SECRETARY_FIRST_SHIP_SLOT,
    SECRETARY_CONFIRM,
    SECRETARY_RANDOM_SWITCH,
    SECRETARY_RANDOM_ON,
    SECRETARY_RANDOM_OFF,
    SECRETARY_DOCK_CHECK,
)

@dataclass(frozen=True)
class SecretaryRarity:
    rarity: str

RARITIES = [
    SecretaryRarity("ultra"),
    SecretaryRarity("super_rare"),
    SecretaryRarity("elite"),
    SecretaryRarity("rare"),
    SecretaryRarity("common"),
]

class Secretary(SecretaryDockMixin,UI):

    RARITY_FILTER = Filter(
        regex=r'^(common|rare|elite|super_rare|ultra)$',
        attr=('rarity',),
    )   

    def run(self):
        self.device.screenshot()

        if not self.appear(SECRETARY_GROUP_CHECK):
            self.ui_goto(page_main)
            self.ui_ensure(page_profile)
            self.enter_secretary_group()
        else:
            logger.info("Already in secretary group")

        # 先判断随机秘书组
        restore_random = self.random_group_enabled()

        try:
            if restore_random:
                self.handle_random_group(False)

            # OCR 当前秘书舰
            ship = self.scan_current_secretary()

            if ship is None:
                logger.warning("Secretary OCR failed")
                self.config.task_delay(success=False)
                return

            # 判断是否需要更换
            if ship.favorability >= 90:
                self.notify_before_replace(ship)

                self.open_ship_select()
                if self.choose_secretary():
                    new_ship = self.scan_current_secretary()
                    if new_ship:
                        ship = new_ship
                        self.notify_after_replace(ship)
                    else:
                        logger.warning(
                            "New secretary OCR failed, use old secretary data"
                        )
        finally:
            # 恢复随机秘书组
            if restore_random:
                self.handle_random_group(True)
        # 计算下次运行时间
        self.schedule_next_run(ship.favorability)
        self.ui_goto(page_main)

    def enter_secretary_group(self):
        logger.hr("Secretary Group")
        while True:
            self.device.screenshot()
            if self.appear(SECRETARY_GROUP_CHECK):
                logger.info("已进入秘书组页面")
                return

            if self.appear_then_click(
                SECRETARY_BUTTON,
                interval=3
            ):
                continue

    def open_ship_select(self):
        logger.hr("Enter Secretary select")

        while True:
            self.device.screenshot()
            if self.appear(SECRETARY_DOCK_CHECK):
                logger.info("Already in secretary dock")
                return

            self.device.click(SECRETARY_FIRST_SHIP_SLOT)

            logger.info("Clicked first secretary slot")

            # 等页面切换
            self.device.sleep(1)

            self.device.screenshot()

            if self.appear(SECRETARY_DOCK_CHECK):
                logger.info("Enter secretary dock")
                return

    def choose_secretary(self):
        logger.hr("Choose Secretary")
        # 常用
        self.dock_favourite_set(True)
        ship = None

        # 从高到低尝试
        ship = self.search_ship()

        if ship is None:
            logger.warning("未找到可用的舰船")
            return False

        self.select_ship(ship)
        # 这里还在选择页面
        self.restore_sort()
        self.confirm()

        logger.info("已成功更换秘书舰")
        return True

    def search_ship(self):
        self.RARITY_FILTER.load(self.config.Secretary_CustomFilter)
        priority = self.RARITY_FILTER.apply(RARITIES)

        for rarity in priority:
            logger.info(f"Searching secretary: {rarity}")
            self.secretary_filter_set(
                sort="intimacy",
                rarity=rarity.rarity,
                wait_loading=True,
            )
            # ★★★★★ 筛选后重新设置排序 ★★★★★
            self.set_low_favorability_priority()

            ship = self.scan_ship()
            if ship is not None:
                logger.info(
                    f"Found ship: Lv{ship.level} FAVORABILITY={ship.favorability}"
                )
                return ship

        logger.warning("No secretary candidate found")
        return None

    def scan_ship(self):
        scanner = ShipScanner(
            favorability=(0,200),
            rarity=False,
            descending=not self.config.Secretary_LowFavorabilityPriority,
        )
        self.device.screenshot()
        ships = scanner.scan(self.device.image)

        if not ships:
            return None
        
        # 过滤：
        # 低等级 + 0好感度 的舰船不作为秘书舰
        ships = [
            ship for ship in ships
            if not (ship.level < 20 and ship.favorability == 0)
            if ship.favorability < 90
        ]

        if not ships:
            return None

        return ships[0]
    def select_ship(self, ship):
        logger.info(f"Select secretary: Lv{ship.level} FAVORABILITY={ship.favorability}")
        self.device.click(ship.button)

    def confirm(self):
        while True:
            self.device.screenshot()

            if self.appear(SECRETARY_GROUP_CHECK):
                return

            if self.appear_then_click(
                SECRETARY_CONFIRM,
                interval=3
            ):
                continue

    def schedule_next_run(self, favorability):
        """
        根据秘书舰好感计算下一次运行时间。
        好感每 6 小时增加 1 点，90 时执行更换。
        """
        hours = max(0, 90 - min(favorability, 90)) * 6

        next_run = current_time() + timedelta(hours=hours)

        logger.info(
            f"Secretary favorability={favorability}, "
            f"next run: {next_run:%Y-%m-%d %H:%M:%S}"
        )

        self.config.task_delay(target=next_run)

    def scan_current_secretary(self):
        """
        OCR 当前秘书舰信息。

        Returns:
            SecretaryInfo
        """

        self.device.screenshot()

        scanner = SecretaryScanner()

        secretary = scanner.scan(self.device.image)

        if secretary is None:
            logger.warning("Secretary scan failed")
            return None

        logger.info(
            f"Secretary: {secretary.name} "
            f"Lv{secretary.level} "
            f"FAVORABILITY={secretary.favorability}"
        )

        return secretary

    def notify(self, title, content):
        instance = self.config.config_name

        handle_notify(
            self.config.Error_OnePushConfig,
            title=title,
            content=content,
        )

        notify_webui(
            instance,
            title=title,
            content=content,
        )

    def notify_before_replace(self, ship):
        self.notify(
            title=f"AzurPilot <{self.config.config_name}> 秘书舰更换",
            content=(
                f"当前秘书舰好感度已达到 {ship.favorability}。\n"
                f"准备更换秘书舰。"
            ),
        )

    def notify_after_replace(self, ship):
        hours = max(0, 90 - ship.favorability) * 6

        self.notify(
            title=f"AzurPilot <{self.config.config_name}> 秘书舰更换完成",
            content=(
                f"秘书舰更换成功！\n"
                f"当前好感度：{ship.favorability}\n"
                f"预计 {hours} 小时后再次检查。"
            ),
        )   

    def handle_random_group(self, enable):
        logger.hr(f"Random secretary group {'ON' if enable else 'OFF'}")

        target = (
            SECRETARY_RANDOM_ON
            if enable
            else SECRETARY_RANDOM_OFF
        )

        while True:
            self.device.screenshot()

            if self.appear(target):
                self.device.sleep(0.5)
                self.device.screenshot()
                return

            self.appear_then_click(
                SECRETARY_RANDOM_SWITCH,
                interval=3,
            )

    def random_group_enabled(self):
        """
        Returns:
            bool: 随机秘书组是否开启。
        """
        self.device.screenshot()
        return self.appear(SECRETARY_RANDOM_ON)

    def set_low_favorability_priority(self):
        if self.config.Secretary_LowFavorabilityPriority:
            logger.info("Sort by low favorability")
            self.dock_sort_method_dsc_set(False)

    def restore_sort(self):
        if self.config.Secretary_LowFavorabilityPriority:
            logger.info("Restore default sort")
            self.dock_sort_method_dsc_set(True)

    def dock_sort_method_dsc_set(self, enable=True, wait_loading=True):
        """
        Args:
            enable: True to set descending sorting
            wait_loading: Default to True, use False on continuous operation
        """
        if DOCK_SORTING.set('Descending' if enable else 'Ascending', main=self):
            if wait_loading:
                self.handle_dock_cards_loading()