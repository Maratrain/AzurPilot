"""限界挑战：按难度顺序每月各出击一次，战败后等待下月。"""

import module.config.server as server
from module.combat.assets import OPTS_INFO_D
from module.combat.combat import Combat
from module.config.time_source import now as current_time
from module.config.utils import get_os_next_reset_after, server_time_offset
from module.exception import GameStuckError, RequestHumanTakeover
from module.limit_challenge.assets import (
    CHALLENGE_PREPARATION, CHALLENGE_START, LEVEL_1, LEVEL_2, LEVEL_3,
    SURFACE_FLEET, TEMPLATE_LEVEL_LOCKED, TEMPLATE_LEVEL_SELECTED,
)
from module.logger import logger
from module.ui.assets import LIMIT_CHALLENGE_CHECK
from module.ui.page import page_limit_challenge


LEVELS = (LEVEL_1, LEVEL_2, LEVEL_3)
LEVEL_NAMES = ('窥探', '审视', '洞察')
TARGET_LEVELS = {'easy': 1, 'normal': 2, 'hard': 3}


def challenge_month():
    """本地时间转换成服务器月份，避免在跨月时沿用旧进度。"""
    now = current_time() - server_time_offset()
    return now.year * 100 + now.month


class LimitChallenge(Combat):
    """复用普通战斗执行与结算，使用游戏内预先配置的限界挑战舰队。

    本地 Combat 无 combat_result 战果对象，胜败改由 `_combat_rank`（公共
    结算记录的评价）与 `_challenge_defeated`（沉船弹窗确认）组合判定。
    """

    def is_in_challenge(self):
        """同时核对标题的图像与颜色，领奖遮罩不算已回到挑战页。"""
        return self.match_template_color(LIMIT_CHALLENGE_CHECK, offset=(3, 3))

    def is_in_challenge_preparation(self):
        """编队名可由玩家修改，只用出击按钮与水面舰队页签确认。"""
        return (self.match_template_color(CHALLENGE_PREPARATION, offset=(3, 3))
                and self.appear(SURFACE_FLEET, offset=(3, 3)))

    def selected_level(self):
        """共用选中菱形定位难度；未知或同时命中多行时拒绝猜测。"""
        selected = [index for index, button in enumerate(LEVELS, 1)
                    if TEMPLATE_LEVEL_SELECTED.match(self.image_crop(button.area), similarity=0.9)]
        return selected[0] if len(selected) == 1 else None

    def handle_challenge_rewards(self):
        """领奖会解锁下一难度，操作后必须继续截图确认新状态。"""
        return (self.handle_get_items() or self.handle_get_ship()
                or self.handle_get_items_ship() or self.handle_popup_confirm('LIMIT_CHALLENGE')
                or self.handle_urgent_commission())

    def handle_exp_info(self):
        """战败沉船确认弹窗由本任务处理并记录，普通结算交给公共流程。"""
        if self.is_combat_executing():
            return False
        if self.appear_then_click(OPTS_INFO_D, offset=True, similarity=0.9, interval=self.exp_info_click_interval):
            self._challenge_defeated = True
            self.device.sleep((0.25, 0.5))
            return True
        return super().handle_exp_info()

    def enter_challenge_preparation(self, level):
        """进入专用编队页；左侧未识别时沿用游戏自动选择的下一关。

        Pages:
            in: page_limit_challenge
            out: page_limit_challenge_preparation
        """
        row = LEVELS[level - 1]
        for _ in self.loop(timeout=30):
            if self.is_in_challenge_preparation():
                return
            if self.handle_challenge_rewards():
                continue
            if self.is_in_challenge():
                selected = self.selected_level()
                if selected is None and TEMPLATE_LEVEL_LOCKED.match(self.image_crop(row.area)):
                    continue
                if selected in (None, level):
                    if self.appear_then_click(CHALLENGE_START, offset=(3, 3), interval=2):
                        if selected is None:
                            logger.info('[限界挑战] 左侧难度未识别，直接开始游戏当前关卡')
                        continue
                elif not TEMPLATE_LEVEL_LOCKED.match(self.image_crop(row.area)):
                    if self.get_interval_timer(row, interval=2).reached():
                        self.device.click(row)
                        self.interval_reset(row)
                        continue
        raise GameStuckError(f'限界挑战未确认{LEVEL_NAMES[level - 1]}已解锁并进入编队页')

    def handle_battle_preparation(self):
        """在真正点击出击前保存尝试记录，异常恢复也不会重复挑战。

        Pages:
            in: page_limit_challenge_preparation
            out: 战斗加载或战斗中
        """
        if not self.is_in_challenge_preparation():
            return False
        if not self.get_interval_timer(CHALLENGE_PREPARATION, interval=2).reached():
            return False
        if challenge_month() != self.config.LimitChallenge_Month:
            logger.info('[限界挑战] 出击前已跨月，重新安排任务')
            self.config.task_delay(minute=0)
            self.config.task_stop()
        if not self._attempt_recorded:
            with self.config.multi_set():
                self.config.LimitChallenge_AttemptedLevel = self._challenge_level
                self.delay_next_month()
            self._attempt_recorded = True
        self.device.click(CHALLENGE_PREPARATION)
        self.interval_reset(CHALLENGE_PREPARATION)
        return True

    def delay_next_month(self):
        """与 Lua 的 GetNextMonthTS 一致，按服务器下月 1 日零点调度。"""
        self.config.task_delay(target=get_os_next_reset_after(current_time()))

    def run(self):
        """依次打到目标等级，S/A/B 继续，C/D 或结果未确认则本月停止。

        Pages:
            in: 任意可导航页面
            out: page_limit_challenge（实际战斗后）或原页面（本月无需出击）
        """
        if server.server != 'cn':
            raise RequestHumanTakeover('限界挑战目前仅有国服识别资源，请补充对应服务器截图')
        target = TARGET_LEVELS.get(self.config.LimitChallenge_TargetLevel)
        if target is None:
            raise RequestHumanTakeover('限界挑战目标等级无效，请重新选择窥探、审视或洞察')
        month = challenge_month()
        if self.config.LimitChallenge_Month != month:
            with self.config.multi_set():
                self.config.LimitChallenge_Month = month
                self.config.LimitChallenge_CompletedLevel = 0
                self.config.LimitChallenge_AttemptedLevel = 0
                self.config.LimitChallenge_Failed = False
        if self.config.LimitChallenge_Failed or self.config.LimitChallenge_CompletedLevel >= target:
            self.delay_next_month()
            return

        # 不绑定普通关卡，不读取石油阈值，也不初始化或写入心情账本。
        self.config.override(Campaign_Name='limit_challenge', Campaign_UseFleetLock=False)
        self.ui_ensure(page_limit_challenge)
        for _ in self.loop(timeout=30):
            if self.handle_challenge_rewards():
                continue
            if self.is_in_challenge():
                selected = self.selected_level()
                if selected is not None:
                    # Lua 进入时自动选择最高已解锁等级；解锁本身证明前级已领奖。
                    completed = max(self.config.LimitChallenge_CompletedLevel, selected - 1)
                    self.config.LimitChallenge_CompletedLevel = completed
                else:
                    logger.info('[限界挑战] 左侧难度未识别，按本月已通关数继续')
                break
        else:
            raise GameStuckError('限界挑战页面未确认')

        if self.config.LimitChallenge_AttemptedLevel > self.config.LimitChallenge_CompletedLevel:
            logger.warning('[限界挑战] 上次出击未确认通关，本月不重复尝试，等待下月')
            self.delay_next_month()
            return

        for level in range(self.config.LimitChallenge_CompletedLevel + 1, target + 1):
            if challenge_month() != month:
                self.config.task_delay(minute=0)
                return
            logger.hr(f'限界挑战：{LEVEL_NAMES[level - 1]}')
            self.enter_challenge_preparation(level)
            self._challenge_level = level
            self._attempt_recorded = False
            self._challenge_defeated = False
            self.combat(balance_hp=False, emotion_reduce=False, auto_mode='combat_auto',
                        submarine_mode='every_combat', save_get_items=False,
                        expected_end=self.is_in_challenge)
            if challenge_month() != month:
                logger.info('[限界挑战] 战斗期间跨月，不把旧月份结果记入新月份')
                self.config.task_delay(minute=0)
                return
            if self._challenge_defeated or self._combat_rank in ('C', 'D'):
                self.config.LimitChallenge_Failed = True
                logger.warning('[限界挑战] 挑战失败，等待下月')
                break
            if self._combat_rank not in ('S', 'A', 'B'):
                logger.warning('[限界挑战] 未确认胜利评价，本月不重复尝试')
                break
            self.config.LimitChallenge_CompletedLevel = level
            logger.info(f'[限界挑战] 已通关{LEVEL_NAMES[level - 1]}')
        self.delay_next_month()
