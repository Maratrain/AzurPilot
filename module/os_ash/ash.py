"""大世界余烬信标模块。

提供碧蓝航线大世界（Operation Siren）中余烬（Ash/Ember）信标系统的自动化功能，包括：
- 信标数据收集进度的 OCR 读取与状态判断
- 每日信标收集上限的检测
- 信标战斗的特殊处理（战斗状态、战斗准备、经验信息跳过）
- 信标战斗完成的异常信号处理（AshBeaconFinished）
- 信标攻击任务的自动调度（收集进度 >= 100 时触发 OpsiAshBeacon 任务）

余烬信标系统允许玩家收集信标数据来挑战 META 级别的敌人，
战斗结束后可获得 META 角色碎片等奖励。
"""
from datetime import timedelta

from module.base.utils import image_left_strip
from module.combat.combat import BATTLE_PREPARATION, Combat
from module.config.time_source import now as current_time
from module.config.utils import DEFAULT_TIME
from module.logger import logger
from module.ocr.ocr import DigitCounter
from module.os_ash.assets import *
from module.os_handler.map_event import MapEventHandler
from module.ui.assets import BACK_ARROW
from module.ui.ui import UI


class DailyDigitCounter(DigitCounter):
    """每日计数器，对图像左侧进行裁剪以去除干扰区域。"""

    def pre_process(self, image):
        image = super().pre_process(image)
        image = image_left_strip(image, threshold=120, length=35)
        return image


class AshBeaconFinished(Exception):
    """信标战斗已完成的异常信号。"""
    pass


# 持久化"信标任务已用某持有量读数确认过无可做内容"的存储路径，
# 防止持有量 OCR 误读（如 70 被读成 170）反复触发信标任务调用
CONFIG_PATH_ASH_NOTHING_TO_DO = 'OpsiAshBeacon.Storage.NothingToDo'


class AshCombat(Combat):
    """余烬信标战斗处理器。

    继承 Combat，覆盖部分战斗处理逻辑以适配 META 战斗的特殊行为：
    - 战斗状态页面的自定义点击处理（含掉落图像处理）
    - 经验信息的跳过（META 战斗不掉落经验）
    - 战斗准备页面的信标状态检测（完成 / 空信标 / 已在对决页面）
    - 战斗执行中捕获 AshBeaconFinished 异常以正常退出

    当信标已完成或为空时，抛出 AshBeaconFinished 异常通知上层停止战斗循环。
    """

    def handle_battle_status(self, drop=None):
        """
        处理战斗结束状态，点击结算画面。

        Args:
            drop (DropImage): 掉落图像处理器。

        Returns:
            bool: 是否采取了行动。
        """
        if self.is_combat_executing():
            return False
        if self.appear(BATTLE_STATUS, offset=(120, 20), interval=self.battle_status_click_interval):
            if drop:
                drop.handle_add(self)
            else:
                self.device.sleep((0.25, 0.5))
            self.device.click(BATTLE_STATUS)
            return True
        if self.appear(BATTLE_PREPARATION, offset=(30, 30), interval=2):
            self.device.click(BACK_ARROW)
            return True
        if super().handle_battle_status(drop=drop):
            return True

        return False

    def handle_exp_info(self):
        """
        META 战斗不掉落经验，无需处理经验信息。

        BATTLE_STATUS 的随机背景可能误触发 EXP_INFO_B，直接忽略。
        """
        return False

    def handle_battle_preparation(self):
        """
        处理战斗准备页面，点击开始战斗按钮。

        如果信标已完成或为空，则抛出 AshBeaconFinished。

        Returns:
            bool: 是否采取了行动。
        """
        if super().handle_battle_preparation():
            return True

        if self.appear_then_click(ASH_START, offset=(30, 30), interval=2):
            return True
        if self.handle_get_items():
            return True
        if self.appear(BEACON_REWARD):
            logger.info("[META作战] 信标已完成")
            raise AshBeaconFinished
        if self.appear(BEACON_EMPTY, offset=(20, 20)):
            logger.info("[META作战] 信标为空")
            raise AshBeaconFinished
        if self.appear(ASH_SHOWDOWN, offset=(20, 20)):
            logger.info("[META作战] 已在 META 对决页面")
            raise AshBeaconFinished

        return False

    def combat(self, *args, expected_end=None, **kwargs):
        """
        执行战斗，捕获信标完成异常以正常退出。

        Args:
            expected_end: 战斗结束判断函数。
        """
        try:
            super().combat(*args, expected_end=expected_end, **kwargs)
        except AshBeaconFinished:
            pass


class OSAsh(UI, MapEventHandler):
    """大世界余烬信标模块。

    负责信标收集状态检测和信标攻击任务的自动调度。

    工作流程：
    1. 通过 OCR 读取信标收集进度（DigitCounter）
    2. 判断收集状态：可收集 / 未收集满 / 已达上限 / 被遮挡
    3. 当收集进度 >= 100 且信标任务可调度时，触发 OpsiAshBeacon 任务
    4. 检查信标任务的下次执行时间，距今超过 30 分钟才允许调用

    Attributes:
        _ash_fully_collected (bool): 信标数据是否已收集满（达到每日上限或持有上限）。
    """
    _ash_fully_collected = False

    def ash_collect_status(self):
        """
        通过 OCR 读取余烬信标的收集进度。

        Returns:
            int: 收集进度值，0 到 100。
        """
        if self._ash_fully_collected:
            return 0
        if self.image_color_count(ASH_COLLECT_STATUS, color=(235, 235, 235), threshold=221, count=20):
            logger.info('[META作战] 信标状态：可收集')
            ocr_collect = DigitCounter(
                ASH_COLLECT_STATUS, letter=(235, 235, 235), threshold=160, name='OCR_ASH_COLLECT_STATUS')
            ocr_daily = DailyDigitCounter(
                ASH_DAILY_STATUS, letter=(235, 235, 235), threshold=160, name='OCR_ASH_DAILY_STATUS')
        elif self.image_color_count(ASH_COLLECT_STATUS, color=(140, 142, 140), threshold=221, count=20):
            logger.info('[META作战] 信标状态：未收集满')
            ocr_collect = DigitCounter(
                ASH_COLLECT_STATUS, letter=(140, 142, 140), threshold=160, name='OCR_ASH_COLLECT_STATUS')
            ocr_daily = DailyDigitCounter(
                ASH_DAILY_STATUS, letter=(140, 142, 140), threshold=160, name='OCR_ASH_DAILY_STATUS')
        else:
            # 大世界每日+任务领取或完成时，弹窗会遮挡信标状态
            logger.info('[META作战] 信标状态被遮挡，下次再检查')
            return 0

        status, _, _ = ocr_collect.ocr(self.device.image)
        daily, _, _ = ocr_daily.ocr(self.device.image)

        if daily >= 200:
            logger.info('[META作战] 今日信标数据已收集满')
            self._ash_fully_collected = True
        elif status >= 200:
            logger.info('[META作战] 信标数据达到持有上限')
            self._ash_fully_collected = True

        if status < 0:
            status = 0
        return status

    def _support_call_ash_beacon_task(self):
        """
        检查是否可以调用信标任务。

        当信标任务的下次执行时间距今超过 30 分钟时，允许调用。

        Returns:
            bool: 是否支持调用信标任务。
        """
        # 信标任务的下次运行时间
        next_run = self.config.cross_get(keys="OpsiAshBeacon.Scheduler.NextRun", default=DEFAULT_TIME)
        # 距下次执行时间超过 30 分钟
        return next_run - current_time() > timedelta(minutes=30)

    def _ash_beacon_confirmed_nothing_to_do(self, status):
        """
        检查信标任务今天是否已用相同持有量读数确认过无可攻击内容。

        持有量 OCR 区域覆盖半透明面板，边框线与背景对比度变化会造成
        稳定的误读（如 70/200 被读成 170/200），误读值跨过 100 调用阈值后
        若无此检查，其他任务每轮都会重复调用信标任务并被打断，形成死循环。
        状态由 OpsiAshBeacon 空转结束时写入并持久化，日期或读数变化后自动失效。

        Args:
            status (int): 当前读取的持有量。

        Returns:
            bool: True 表示今天已用相同读数确认过无可做内容，应跳过调用。
        """
        state = self.config.cross_get(keys=CONFIG_PATH_ASH_NOTHING_TO_DO, default={})
        if not isinstance(state, dict):
            return False
        today = current_time().strftime('%Y-%m-%d')
        return state.get('NothingToDoDate') == today and state.get('NothingToDoValue') == status

    def handle_ash_beacon_attack(self):
        """
        检查余烬信标收集状态，满足条件时调用信标攻击任务。

        当收集进度 >= 100 且信标任务可调度时，触发 OpsiAshBeacon 任务。

        Returns:
            bool: 是否触发了信标攻击。

        Pages:
            in: is_in_map
            out: is_in_map
        """
        if not self.config.is_task_enabled('OpsiAshBeacon'):
            return False
        status = self.ash_collect_status()
        if status < 100:
            return False
        if not self._support_call_ash_beacon_task():
            return False
        if self._ash_beacon_confirmed_nothing_to_do(status):
            return False
        # 记录本次调用时的读数，供信标任务空转结束时写入确认状态
        self.config._ash_beacon_call_value = status
        self.config.task_call(task='OpsiAshBeacon')
        return True
