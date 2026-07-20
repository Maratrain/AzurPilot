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
from threading import Thread
from module.base.timer import current_time
from module.secretary.slot import SECRETARY_SLOT
from module.secretary.group_scanner import SecretaryGroupScanner
from module.secretary.assets import (
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
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.replace_type = None
        self.secretary_scanner = SecretaryScanner()
        self.group_scanner = SecretaryGroupScanner()
        self.search_priority = None
        self.search_priority_index = 0

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
            old_ship = self.scan_current_secretary()

            if old_ship is None:
                logger.warning("Secretary OCR failed")
                self.config.task_delay(success=False)
                return

            ship = old_ship

            if ship is None:
                logger.warning("Secretary OCR failed")
                self.config.task_delay(success=False)
                return

            # 判断是否需要更换
            # 当前秘书舰未满好感
            if ship.favorability < 90:
                self.schedule_next_run(ship.favorability)
                return

            # 已满好感，开始更换流程
            group_ships = self.scan_secretary_group()
            self.notify_before_replace(ship, group_ships)

            if self.is_group_all_full(group_ships):
                logger.info(
                    "Secretary group all favorability >=90, "
                    "refresh all secretary slots"
                )
                self.replace_all_secretary()

            elif self.config.Secretary_BackupEnable:
                self.handle_backup_secretary()

            else:
                self.replace_secretary()

            # OCR 新秘书舰
            new_ship = self.scan_current_secretary()
            if new_ship:
                ship = new_ship

            # 自定义检测时间优先
            if self.config.Secretary_CheckInterval > 0:
                next_run = current_time() + timedelta(
                    hours=self.config.Secretary_CheckInterval
                )
                self.config.task_delay(target=next_run)
            else:
                next_run = self.schedule_next_run(ship.favorability)

            self.notify_after_replace(ship, next_run, old_ship)

        finally:
            # 恢复随机秘书组
            if restore_random:
                self.handle_random_group(True)
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

    def open_ship_select(self, button):
        logger.hr("Enter Secretary select")

        while True:
            self.device.screenshot()
            if self.appear(SECRETARY_DOCK_CHECK):
                logger.info("Already in secretary dock")
                return

            self.device.click(button)

            logger.info(f"Click secretary slot: {button}")

            # 等页面切换
            self.device.sleep(1)

            self.device.screenshot()

            if self.appear(SECRETARY_DOCK_CHECK):
                logger.info("Enter secretary dock")
                return

    def choose_secretary(self, initialize=True):

        logger.hr("Choose Secretary")

        if initialize:
            self.dock_favourite_set(True)

        ship = self.search_ship(initialize=initialize)

        if ship is None:
            logger.warning("未找到可用的舰船")
            return False

        self.select_ship(ship)
        self.restore_sort()
        logger.info("已成功更换秘书舰")
        return True

    def search_ship(self, initialize=True):
        """
        Search secretary.

        Args:
            initialize:
                True：初始化筛选，从第一种稀有度开始。
                False：继续当前搜索状态，不重新开始。
        """
        if initialize:
            self.RARITY_FILTER.load(self.config.Secretary_CustomFilter)
            self.search_priority = self.RARITY_FILTER.apply(RARITIES)
            self.search_priority_index = 0

            if not self.search_priority:
                logger.warning("No secretary rarity configured")
                return None

            rarity = self.search_priority[0]
            logger.info(f"Searching secretary: {rarity.rarity}")

            self.secretary_filter_set(
                sort="intimacy",
                rarity=rarity.rarity,
                wait_loading=True,
            )
            self.set_low_favorability_priority()

        while self.search_priority_index < len(self.search_priority):

            ship = self.scan_ship()

            if ship:
                logger.info(f"Found ship: Lv{ship.level} FAVORABILITY={ship.favorability}")
                return ship

            # 当前稀有度已经没有可选舰船
            self.search_priority_index += 1

            if self.search_priority_index >= len(self.search_priority):
                break

            rarity = self.search_priority[self.search_priority_index]
            logger.info(f"Searching secretary: {rarity.rarity}")
            self.secretary_filter_set(
                sort="intimacy",
                rarity=rarity.rarity,
                wait_loading=True,
            )
            self.set_low_favorability_priority()

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
            if not ship.selected
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
        interval = self.config.Secretary_CheckInterval
        # 用户指定检测时间
        if interval > 0:
            next_run = current_time() + timedelta(hours=interval)

            logger.info(
                f"Secretary custom interval={interval}h, "
                f"next run: {next_run:%Y-%m-%d %H:%M:%S}"
            )
            self.config.task_delay(target=next_run)
            return next_run

        # 自动计算
        hours = max(0, 90 - min(favorability, 90)) * 6

        next_run = current_time() + timedelta(hours=hours)

        logger.info(
            f"Secretary favorability={favorability}, "
            f"next run: {next_run:%Y-%m-%d %H:%M:%S}"
        )

        self.config.task_delay(target=next_run)
        return next_run
            
    def scan_current_secretary(self):
        """
        OCR 当前秘书舰信息。

        Returns:
            SecretaryInfo
        """

        self.device.screenshot()

        scanner = SecretaryScanner()

        secretary = self.secretary_scanner.scan(self.device.image)

        if secretary is None:
            logger.warning("Secretary scan failed")
            return None

        logger.info(
            f"Secretary: {secretary.name} "
            f"Lv{secretary.level} "
            f"FAVORABILITY={secretary.favorability}"
        )

        return secretary

    def scan_secretary_group(self):
        """
        OCR 五个秘书舰。
        """
        self.device.screenshot()

        scanner = SecretaryGroupScanner()

        ships = self.group_scanner.scan(self.device.image)

        logger.hr("Secretary Group")

        for ship in ships:
            logger.info(
                f"[{ship.index}] "
                f"Secretary:{ship.name} "
                f"Lv{ship.level} "
                f"Favorability={ship.favorability}"
            )

        return ships

    def replace_secretary(self):

        self.replace_type = "船坞筛选"

        self.open_ship_select(SECRETARY_SLOT[0])

        if not self.choose_secretary(initialize=True):
            logger.warning("未找到可更换秘书舰")
            return False

        return True

    def search_group_candidate(self, ships):

        ships = ships[1:]          # 跳过主秘书舰

        ships = [
            ship
            for ship in ships
            if (
                ship.level > 1
                and ship.favorability < 90
            )
        ]

        if not ships:
            return None

        reverse = not self.config.Secretary_LowFavorabilityPriority

        ships.sort(
            key=lambda x: x.favorability,
            reverse=reverse,
        )

        return ships[0]

    def swap_group_secretary(self, ship):

        src = SECRETARY_SLOT[ship.index]
        dst = SECRETARY_SLOT[0]

        logger.info(
            f"Swap secretary {ship.index} -> 0"
        )

        self.device.drag(
            src,
            dst,
            duration=0.4,
        )

        self.device.sleep(1)

    def _notify_worker(self, title, content):
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

    def notify(self, title, content):
        Thread(
            target=self._notify_worker,
            args=(title, content),
            daemon=True,
        ).start()

    def notify_before_replace(self, ship, group_ships=None):

        group_full = False

        if group_ships:
            group_full = all(
                s.favorability >= 90
                for s in group_ships
            )

        content = (
            f"好感度已达到可获取上限。\n\n"
            f"当前秘书舰：{ship.name}\n"
            f"等级：Lv{ship.level}\n"
            f"好感度：{ship.favorability}\n"
        )

        if group_full:
            content += (
                "\n秘书组状态：\n"
                "所有候补秘书舰好感度已达到90"
            )

        content += (
            "\n\n开始执行秘书舰更换。"
        )

        self.notify(
            title=f"AzurPilot <{self.config.config_name}> 秘书舰提醒",
            content=content,
        )

    def notify_after_replace(self, ship, next_run, old_ship=None):

        # 更换后重新扫描秘书组
        group_full = False

        ships = self.scan_secretary_group()

        if ships:
            group_full = all(
                s.favorability >= 90
                for s in ships
            )

        content = (
            f"秘书舰更换成功！\n\n"

            f"原秘书舰："
            f"{old_ship.name if old_ship else '船坞筛选'}\n"

            f"新秘书舰：{ship.name}\n"
            f"等级：Lv{ship.level}\n"
            f"好感度：{ship.favorability}\n\n"

            f"更换方式：{self.replace_type or '船坞筛选'}\n"
        )

        if group_full:
            content += (
                "\n秘书组状态：\n"
                "所有候补秘书舰已成功更换"
                "\n"
            )

        content += (
            f"\n下次检查时间："
            f"{next_run:%Y-%m-%d %H:%M:%S}"
        )

        self.notify(
            title=f"AzurPilot <{self.config.config_name}> 秘书舰更换完成",
            content=content,
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

    def handle_backup_secretary(self):
        ships = self.scan_secretary_group()

        candidate = self.search_backup_secretary(ships)

        # 有候补
        if candidate:
            self.replace_type = "秘书组候补"
            self.promote_backup(candidate)
            return

        logger.info("No backup secretary, replace slot5")

        # 只更换第五位
        self.open_ship_select(SECRETARY_SLOT[4])

        if not self.choose_secretary(initialize=True):
            logger.warning("No secretary candidate")
            return

        self.confirm()

        # 再扫描一次
        ships = self.scan_secretary_group()

        candidate = self.search_backup_secretary(ships)

        if candidate:
            self.promote_backup(candidate)

    def replace_all_secretary(self):
        """
        当秘书组全部满90时，
        从主秘书舰开始依次替换。
        """
        ships = self.scan_secretary_group()
        if not ships:
            return False

        # 找所有需要替换的位置
        targets = [
            ship
            for ship in ships
            if ship.favorability >= 90
        ]
        if not targets:
            return False
        logger.info(f"Secretary all full, replace {len(targets)} ships")
        first = True
        count = 0

        for ship in targets:
            slot = SECRETARY_SLOT[ship.index]
            logger.info(f"Replace secretary slot {ship.index}: {ship.name} {ship.favorability}")
            self.open_ship_select(slot)
            initialize = first
            first = False
            if not self.choose_secretary(initialize=initialize):
                logger.warning(f"No replacement for slot {ship.index}")
                continue

            self.confirm()
            count += 1

        logger.info(f"Secretary replace finished {count}/{len(targets)}")
        return count > 0

    def search_backup_secretary(self, ships):

        backups = [
            ship
            for ship in ships
            if not ship.is_main
        ]

        backups = [
            ship
            for ship in backups
            if ship.level >= 1
            and ship.favorability < 90
        ]

        if not backups:

            return None

        backups.sort(
            key=lambda s: s.favorability,
            reverse=not self.config.Secretary_LowFavorabilityPriority,
        )

        return backups[0]

    @staticmethod
    def button_center(button):
        x1, y1, x2, y2 = button.button
        return (
            (x1 + x2) // 2,
            (y1 + y2) // 2,
        )

    def promote_backup(self, ship):

        logger.info(f"Promote backup secretary: {ship.name}")
        logger.info(
            f"Drag secretary: {ship.name} "
            f"{ship.button.button} -> {SECRETARY_SLOT[0].button}"
        )
        self.device.drag(
            self.button_center(ship.button),
            self.button_center(SECRETARY_SLOT[0]),
        )

        self.device.sleep(1)

    def refresh_secretary_group(self):

        logger.hr("Refresh secretary group")

        count = 0

        for slot in range(1,5):
            if self.replace_group_slot(slot):
                count += 1

        logger.info(
            f"Refresh secretary group success {count}/4"
        )

    def replace_group_slot(self, slot):

        self.open_ship_select(SECRETARY_SLOT[slot])

        if not self.choose_secretary():
            logger.warning(f"Slot {slot} no candidate.")
            return False

        self.confirm()
        return True

    def is_group_all_full(self, ships):
        if not ships:
            return False

        return all(
            ship.favorability >= 90
            for ship in ships
        )