"""大世界状态追踪模块。

管理大世界（Operation Siren）模式的状态信息，包括海域代币
（黄币/紫币）的 OCR 数值追踪、任务类型识别、子任务冷却（CD）
状态的实时计算，以及相关日志资源的记录。
"""
# 此文件用于管理大世界（Operation Siren）模式下的状态信息。
# 负责海域代币（黄币/紫币）的数值追踪、任务类型识别以及子任务冷却（CD）状态的实时计算。
import threading
import typing as t
from datetime import timedelta

from module.base.decorator import cached_property
import module.config.server as server
from module.base.timer import Timer
from module.config.config import Function
from module.config.time_source import now as current_time
from module.config.utils import get_server_next_update
from module.logger import logger
from module.map.map_grids import SelectedGrids
from module.ocr.ocr import Digit
from module.combat.assets import GET_ITEMS_1, GET_ITEMS_2, GET_SHIP
from module.os_shop.assets import OS_SHOP_CHECK, OS_SHOP_PURPLE_COINS, SHOP_PURPLE_COINS, SHOP_YELLOW_COINS
from module.ui.ui import UI
from module.log_res.log_res import LogRes


if server.server != 'jp':
    OCR_SHOP_YELLOW_COINS = Digit(SHOP_YELLOW_COINS, letter=(239, 239, 239), threshold=160, name='OCR_SHOP_YELLOW_COINS')
else:
    OCR_SHOP_YELLOW_COINS = Digit(SHOP_YELLOW_COINS, letter=(201, 201, 201), threshold=200, name='OCR_SHOP_YELLOW_COINS')
OCR_SHOP_PURPLE_COINS = Digit(SHOP_PURPLE_COINS, letter=(255, 255, 255), name='OCR_SHOP_PURPLE_COINS')
OCR_OS_SHOP_PURPLE_COINS = Digit(OS_SHOP_PURPLE_COINS, letter=(255, 255, 255), name='OCR_OS_SHOP_PURPLE_COINS')


class OSStatus(UI):
    _shop_yellow_coins = 0
    _shop_purple_coins = 0
    _cache_lock = threading.Lock()
    _last_yellow_coins = 0

    @property
    def is_in_task_explore(self) -> bool:
        return self.config.task.command == 'OpsiExplore'

    @property
    def is_in_task_cl1_leveling(self) -> bool:
        return self.config.task.command == 'OpsiHazard1Leveling'

    @property
    def is_running_cl1_leveling(self) -> bool:
        """判断当前执行上下文是否是侵蚀1练级。"""
        return (
            self.is_in_task_cl1_leveling
            or getattr(self.config, '_bind_task_override', None) == 'OpsiHazard1Leveling'
        )

    @property
    def is_in_task_meow(self) -> bool:
        """判断当前任务是否是耄耋相接任务"""
        return self.config.task.command == 'OpsiMeowfficerFarming'

    @property
    def is_cl1_enabled(self) -> bool:
        return self.config.is_task_enabled('OpsiHazard1Leveling')

    @property
    def is_cl1_mode_enabled(self) -> bool:
        """判断侵蚀1相关策略是否启用，包括智能调度+代理模式。"""
        is_smart_scheduling_enabled = getattr(self, 'is_smart_scheduling_enabled', None)
        return self.is_cl1_enabled or (
            is_smart_scheduling_enabled is not None
            and is_smart_scheduling_enabled()
        )

    @property
    def is_meow_enabled(self) -> bool:
        """判断耄耋相接任务是否启用"""
        return self.config.is_task_enabled('OpsiMeowfficerFarming')

    @property
    def cl1_enough_yellow_coins(self) -> bool:
        return self.get_yellow_coins() >= self.config.cross_get(
            keys='OpsiHazard1Leveling.OpsiHazard1Leveling.OperationCoinsPreserve')

    @property
    def nearest_task_cooling_down(self) -> t.Optional[Function]:
        """
        If having any tasks cooling down,
        such as recon scan cooldown and submarine call cooldown.
        """
        now = current_time()
        update = get_server_next_update('00:00')
        cd_tasks = [
            'OpsiObscure',
            'OpsiAbyssal',
            'OpsiStronghold',
            'OpsiDaily',
        ]

        def func(task: Function):
            if task.command in cd_tasks and task.enable:
                if task.next_run != update and task.next_run - now <= timedelta(minutes=60):
                    return True

            return False

        tasks = SelectedGrids(self.config.pending_task + self.config.waiting_task).filter(func).sort('next_run')
        return tasks.first_or_none()

    @property
    def bought_all_yellow_coin_items_in_port_shop(self) -> bool:
        return self.config.cross_get("OpsiShop.Storage.Storage.BoughtAllYellowCoinItems", False)

    @cached_property
    def yellow_coins_preserve(self):
        if self.is_cl1_enabled and not self.bought_all_yellow_coin_items_in_port_shop:
            return 100000
        else:
            return 35000

    def get_yellow_coins(self) -> int:
        yellow_coins = 0
        timeout = Timer(5, count=10).start()  # 增加超时时间和重试次数
        last_valid_value = None
        
        for _ in self.loop():
            # End
            if self.appear_then_click(GET_ITEMS_1, offset=True, interval=1):
                timeout.reset()
                continue
            if self.appear_then_click(GET_ITEMS_2, offset=True, interval=1):
                timeout.reset()
                continue
            if self.appear_then_click(GET_SHIP, interval=1):
                timeout.reset()
                continue

            current_value = OCR_SHOP_YELLOW_COINS.ocr(self.device.image)
            if timeout.reached():
                logger.warning('[大世界处理-状态] 获取黄币超时')
                break

            if current_value == 0:
                # OCR may get 0 when amount is not immediately loaded
                # Or when popups are obscuring the top bar
                logger.info('[大世界处理-状态] 黄币为 0，可能是 OCR 错误或界面未加载')
                continue
            else:
                # 验证识别稳定性：连续两次识别相同才确认
                if last_valid_value is None:
                    last_valid_value = current_value
                    self.device.sleep(0.2)  # 短暂等待后再次验证
                elif last_valid_value == current_value:
                    yellow_coins = current_value
                    break
                else:
                    last_valid_value = current_value
                    self.device.sleep(0.2)
        
        # 如果最终仍未获取到有效数值，使用上次缓存的值（线程安全）
        with self._cache_lock:
            if yellow_coins == 0:
                logger.info(f'[大世界处理-状态] 使用缓存的黄币值: {self._last_yellow_coins}')
                yellow_coins = self._last_yellow_coins
            
            # 缓存当前值用于降级
            self._last_yellow_coins = yellow_coins
        
        LogRes(self.config).YellowCoin = yellow_coins
        logger.info(f'[大世界处理-状态] 黄币: {yellow_coins}')

        return yellow_coins

    def get_purple_coins(self) -> int:
        if self.appear(OS_SHOP_CHECK):
            purple_coins = OCR_OS_SHOP_PURPLE_COINS.ocr(self.device.image)
        else:
            purple_coins = OCR_SHOP_PURPLE_COINS.ocr(self.device.image)
        LogRes(self.config).PurpleCoin = purple_coins
        return purple_coins

    def os_shop_get_coins(self):
        self._shop_yellow_coins = self.get_yellow_coins()
        self._shop_purple_coins = self.get_purple_coins()
        logger.info(f'[大世界处理-状态] 黄币: {self._shop_yellow_coins}, 紫币: {self._shop_purple_coins}')

        # 记录凭证快照到数据库（用于 WebUI 凭证变化曲线图）
        try:
            instance_name = getattr(self.config, 'config_name', 'default')
            source = 'cl1' if self.is_running_cl1_leveling else ('meow' if self.is_in_task_meow else 'other')
            from module.statistics.cl1_database import db as cl1_db
            cl1_db.add_coins_snapshot(
                instance_name,
                self._shop_yellow_coins,
                self._shop_purple_coins,
                source=source
            )
            # LogRes 已将值写入 config.modified，在此持久化
            self.config.save()
        except Exception:
            logger.exception('[大世界处理-状态] 记录凭证快照失败')
