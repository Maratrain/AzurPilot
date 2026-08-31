from module.ui.ui import UI
from module.logger import logger
from dataclasses import dataclass
from module.base.filter import Filter
from module.ui.page import page_profile, page_main
from module.secretary.scanner import SecretaryScanner
from module.secretary.dock import SecretaryDockMixin
from module.secretary.ship_scanner import ShipScanner
from module.notify.notify import handle_notify, notify_webui
from datetime import timedelta
from threading import Thread
import yaml
from module.base.timer import Timer, current_time
from module.secretary.slot import SECRETARY_SLOT
from module.secretary.group_scanner import SecretaryGroupScanner
from module.secretary.assets import (
    SECRETARY_BUTTON,
    SECRETARY_GROUP_CHECK,
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

# 秘书舰放置可获取的好感度上限，达到后执行更换
FAVORABILITY_LIMIT = 90

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
        """
        秘书舰任务入口。

        好感度未满时按剩余好感安排下次检查；
        已满时执行更换，更换失败按失败间隔重试，
        避免好感度 >=90 时 NextRun=now 造成的热循环。
        """
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

            # 当前秘书舰未满好感，等待好感度自然增长
            if old_ship.favorability < FAVORABILITY_LIMIT:
                self.schedule_next_run(old_ship.favorability)
                return

            # 已满好感，开始更换流程
            group_ships = self.scan_secretary_group()
            self.notify_before_replace(old_ship, group_ships)

            if self.is_group_all_full(group_ships):
                logger.info(
                    "Secretary group all favorability >=90, "
                    "refresh all secretary slots"
                )
                replace_ok = self.replace_all_secretary()
            elif self.config.Secretary_BackupEnable:
                replace_ok = self.handle_backup_secretary()
            else:
                replace_ok = self.replace_secretary()

            if not replace_ok:
                logger.warning("Secretary replace failed, retry later")
                self.config.task_delay(success=False)
                return

            # OCR 新秘书舰
            new_ship = self.scan_current_secretary()
            if new_ship is None:
                logger.warning("Secretary OCR failed after replace")
                self.config.task_delay(success=False)
                return

            # 新秘书舰仍满好感说明更换未生效（OCR 误判或拖拽失败），
            # 按失败处理，避免 schedule_next_run 计算出 0 小时形成热循环
            if new_ship.favorability >= FAVORABILITY_LIMIT:
                logger.warning(
                    f"New secretary favorability={new_ship.favorability} "
                    f"still >= 90, treat as failure"
                )
                self.config.task_delay(success=False)
                return

            # 自定义检测时间优先
            if self.config.Secretary_CheckInterval > 0:
                next_run = current_time() + timedelta(
                    hours=self.config.Secretary_CheckInterval
                )
                self.config.task_delay(target=next_run)
            else:
                next_run = self.schedule_next_run(new_ship.favorability)

            self.notify_after_replace(new_ship, next_run, old_ship)

        finally:
            # 无论成功失败，先退出可能残留的船坞界面再恢复随机秘书组
            try:
                self.exit_dock()
                if restore_random:
                    self.handle_random_group(True)
            except Exception as e:
                logger.warning(f"Failed to restore secretary state: {e}")

        self.ui_goto(page_main)

    def exit_dock(self):
        """退出船坞选人界面，返回秘书组页面。"""
        if self.appear(SECRETARY_DOCK_CHECK):
            self.ui_back(SECRETARY_GROUP_CHECK)

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
        """点击秘书舰槽位进入船坞选人界面。

        状态循环：点击后等待 SECRETARY_DOCK_CHECK 出现，
        间隔防连击，不使用 sleep 等待。
        """
        logger.hr("Enter Secretary select")

        click_timer = Timer(3)
        while True:
            self.device.screenshot()

            if self.appear(SECRETARY_DOCK_CHECK):
                logger.info("Enter secretary dock")
                return

            if click_timer.reached():
                click_timer.reset()
                self.device.click(button)
                logger.info(f"Click secretary slot: {button}")

    def choose_secretary(self):
        """
        在船坞中搜索并选中候选秘书舰。

        初始化收藏过滤与稀有度筛选，搜索结束后恢复船坞状态。

        Returns:
            bool: 是否成功选中秘书舰（尚未确认）。
        """
        logger.hr("Choose Secretary")

        self.dock_favourite_set(True)
        ship = self.search_ship()

        if ship is None:
            logger.warning("未找到可用的舰船")
            self.restore_dock_state()
            return False

        self.select_ship(ship)
        self.restore_dock_state()
        logger.info("已成功选择秘书舰")
        return True

    def search_ship(self):
        """
        按稀有度优先级搜索候选秘书舰。

        船坞按好感度排序扫描：低好感度优先时使用升序，
        最低好感度候选必然位于第一页首位，单页扫描即可覆盖；
        高好感度优先时使用降序。

        Returns:
            SecretaryShip: 可用候选，没有则返回 None。
        """
        self.RARITY_FILTER.load(self.config.Secretary_CustomFilter)
        self.search_priority = self.RARITY_FILTER.apply(RARITIES)
        self.search_priority_index = 0

        if not self.search_priority:
            logger.warning("No secretary rarity configured")
            return None

        while self.search_priority_index < len(self.search_priority):
            rarity = self.search_priority[self.search_priority_index]
            logger.info(f"Searching secretary: {rarity.rarity}")

            self.secretary_filter_set(
                sort="intimacy",
                rarity=rarity.rarity,
                wait_loading=True,
            )
            self.set_search_sort()

            ship = self.scan_ship()

            if ship is not None:
                logger.info(f"Found ship: Lv{ship.level} FAVORABILITY={ship.favorability}")
                return ship

            # 当前稀有度已经没有可选舰船，尝试下一稀有度
            self.search_priority_index += 1

        logger.warning("No secretary candidate found")
        return None

    def scan_ship(self):
        """
        扫描船坞第一页并返回一个可用候选，没有则返回 None。

        高好感度优先时，若第一页全部满好感（大量满好感舰船的船坞中常见），
        回退为好感度升序重扫一次，取最低好感度候选，
        避免漏掉降序第一页之外的舰船。
        """
        descending = not self.config.Secretary_LowFavorabilityPriority

        ships = self._scan_dock_page(descending=descending)

        if not ships and descending:
            logger.info("No candidate in descending order, retry ascending")
            self.dock_sort_method_dsc_set(False)
            ships = self._scan_dock_page(descending=False)

        if not ships:
            return None

        # 列表顺序与船坞排序方向一致，首位即优先级最高的候选
        return ships[0]

    def _scan_dock_page(self, descending):
        """
        扫描船坞第一页并过滤出可用候选。

        Args:
            descending: True 按好感度降序扫描，False 升序。

        Returns:
            list[SecretaryShip]: 满足条件的候选列表。
        """
        scanner = ShipScanner(
            favorability=(0, 200),
            rarity=False,
            descending=descending,
        )
        self.device.screenshot()
        ships = scanner.scan(self.device.image)

        # 过滤：
        # - 低等级 + 0好感度 的舰船不作为秘书舰
        # - 已满可获取好感度的舰船不再需要放置
        # - 已被选中的舰船不可重复选择
        return [
            ship for ship in ships
            if not (ship.level < 20 and ship.favorability == 0)
            if ship.favorability < FAVORABILITY_LIMIT
            if not ship.selected
        ]
    def select_ship(self, ship):
        logger.info(f"Select secretary: Lv{ship.level} FAVORABILITY={ship.favorability}")
        self.device.click(ship.button)

    def confirm(self):
        while True:
            self.device.screenshot()

            if self.appear(SECRETARY_GROUP_CHECK):
                logger.info("已成功更换秘书舰")
                return

            if self.appear_then_click(
                SECRETARY_CONFIRM,
                interval=3
            ):
                continue

    def schedule_next_run(self, favorability):
        """
        根据秘书舰好感计算下一次运行时间。
        好感每 6 小时增加 1 点，达到可获取上限时执行更换。
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
        hours = max(
            0,
            FAVORABILITY_LIMIT - min(favorability, FAVORABILITY_LIMIT),
        ) * 6

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
        """通过船坞筛选直接更换主秘书舰。

        Returns:
            bool: 是否成功更换。
        """
        self.replace_type = "船坞筛选"

        self.open_ship_select(SECRETARY_SLOT[0])

        if not self.choose_secretary():
            logger.warning("未找到可更换秘书舰")
            return False

        self.confirm()

        return True

    @staticmethod
    def _onepush_configured(config: str) -> bool:
        """判断 OnePush 配置是否填写了推送渠道。

        留空或仅保留默认的 provider: null 均视为未配置。
        """
        if not config or not config.strip():
            return False
        try:
            parsed = yaml.safe_load(config)
        except Exception:
            return False
        if not isinstance(parsed, dict):
            return False
        return bool(parsed.get("provider"))

    def _notify_worker(self, title, content):
        instance = self.config.config_name

        # 秘书舰专用 OnePush 配置，留空时回退到全局错误推送配置
        if self.config.Secretary_Notify:
            push_config = self.config.Secretary_OnePushConfig
            if not self._onepush_configured(push_config):
                push_config = self.config.Error_OnePushConfig
            handle_notify(
                push_config,
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
                s.favorability >= FAVORABILITY_LIMIT
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
                s.favorability >= FAVORABILITY_LIMIT
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
                "秘书组仍全部满好感，请检查船坞中是否有可用候选舰船"
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
        """切换随机秘书组开关到目标状态。

        Args:
            enable: True 开启，False 关闭。
        """
        logger.hr(f"Random secretary group {'ON' if enable else 'OFF'}")

        target = (
            SECRETARY_RANDOM_ON
            if enable
            else SECRETARY_RANDOM_OFF
        )

        click_timer = Timer(3)
        while True:
            self.device.screenshot()

            if self.appear(target):
                return

            if click_timer.reached():
                click_timer.reset()
                self.appear_then_click(SECRETARY_RANDOM_SWITCH)

    def random_group_enabled(self):
        """
        Returns:
            bool: 随机秘书组是否开启。
        """
        self.device.screenshot()
        return self.appear(SECRETARY_RANDOM_ON)

    def set_search_sort(self):
        """按配置设置船坞好感度排序方向。"""
        if self.config.Secretary_LowFavorabilityPriority:
            logger.info("Sort by low favorability")
            self.dock_sort_method_dsc_set(False)
        else:
            logger.info("Sort by high favorability")
            self.dock_sort_method_dsc_set(True)

    def restore_dock_state(self):
        """恢复任务修改过的船坞状态（关闭收藏过滤、恢复降序、重置筛选）。

        需在船坞界面调用，避免任务的筛选残留影响用户手动操作船坞。
        """
        if not self.appear(SECRETARY_DOCK_CHECK):
            return
        self.dock_favourite_set(False, wait_loading=False)
        self.dock_sort_method_dsc_set(True)
        self.secretary_filter_set()

    def handle_backup_secretary(self):
        """优先提升候补秘书舰，无候补时更换第五槽位。

        Returns:
            bool: 主秘书舰是否被成功更换。
        """
        ships = self.scan_secretary_group()

        candidate = self.search_backup_secretary(ships)

        # 有候补
        if candidate:
            self.replace_type = "秘书组候补"
            self.promote_backup(candidate)
            return True

        logger.info("No backup secretary, replace slot5")

        # 只更换第五位
        self.open_ship_select(SECRETARY_SLOT[4])

        if not self.choose_secretary():
            logger.warning("No secretary candidate")
            return False

        self.confirm()

        # 再扫描一次
        ships = self.scan_secretary_group()

        candidate = self.search_backup_secretary(ships)

        if candidate:
            self.promote_backup(candidate)

        return True

    def replace_all_secretary(self):
        """
        当秘书组全部满90时，
        从主秘书舰开始依次替换。

        Returns:
            bool: 是否至少成功更换一艘。
        """
        ships = self.scan_secretary_group()
        if not ships:
            return False

        self.replace_type = "秘书组全满刷新"

        # 找所有需要替换的位置
        targets = [
            ship
            for ship in ships
            if ship.favorability >= FAVORABILITY_LIMIT
        ]
        if not targets:
            return False
        logger.info(f"Secretary all full, replace {len(targets)} ships")

        count = 0

        for ship in targets:
            slot = SECRETARY_SLOT[ship.index]
            logger.info(f"Replace secretary slot {ship.index}: {ship.name} {ship.favorability}")
            self.open_ship_select(slot)
            if not self.choose_secretary():
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
            and ship.favorability < FAVORABILITY_LIMIT
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

    def is_group_all_full(self, ships):
        if not ships:
            return False

        return all(
            ship.favorability >= FAVORABILITY_LIMIT
            for ship in ships
        )