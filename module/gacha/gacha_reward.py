"""建造（Gacha）系统模块，处理舰船建造的完整流程。
包括建造页面导航、资源消耗预计算、订单提交、
自动收菜（获取建造结果）以及建造队列的清理管理。"""

# 此文件处理建造（Gacha/Build）相关的操作。
# 包括多级建造页面的导航、资源消耗预计算、提交建造订单以及自动化收菜和队列清理逻辑。

import re
from datetime import date, timezone

import module.config.server as server
from module.base.timer import Timer
from module.campaign.campaign_status import CampaignStatus
from module.combat.assets import GET_SHIP
from module.config.time_source import now as current_time
from module.config.utils import server_timezone
from module.exception import GameStuckError, ScriptError
from module.gacha.assets import *
from module.gacha.ui import GachaUI
from module.handler.assets import POPUP_CONFIRM, STORY_SKIP
from module.logger import logger
from module.ocr.ocr import Digit, Ocr
from module.retire.retirement import Retirement
from module.log_res import LogRes

RECORD_GACHA_OPTION = ('RewardRecord', 'gacha')
RECORD_GACHA_SINCE = (0,)
OCR_BUILD_CUBE_COUNT = Digit(BUILD_CUBE_COUNT, letter=(255, 247, 247), threshold=64)
OCR_BUILD_TICKET_COUNT = Digit(BUILD_TICKET_COUNT, letter=(255, 247, 247), threshold=64)
OCR_BUILD_SUBMIT_COUNT = Digit(BUILD_SUBMIT_COUNT, letter=(255, 247, 247), threshold=64)
OCR_BUILD_SUBMIT_WW_COUNT = Digit(BUILD_SUBMIT_WW_COUNT, letter=(255, 247, 247), threshold=64)
OCR_BUILD_EVENT_TIME = Ocr(BUILD_EVENT_TIME, lang='cnocr')


class RewardGacha(GachaUI, Retirement, CampaignStatus):
    build_coin_count = 0
    build_cube_count = 0
    build_ticket_count = 0

    def gacha_prep(self, target, skip_first_screenshot=True):
        """
        准备提交建造订单。

        Args:
            target (int): 要提交的建造订单数量。
            skip_first_screenshot (bool): 是否跳过首次截图。

        Returns:
            bool: 准备完成返回 True，否则返回 False。

        Pages:
            in: page_build（任意子页面）
            out: 提交确认弹窗

        Raises:
            ScriptError: 无法识别 OCR 资源时抛出。
        """
        # target 为 0 时无需准备
        if not target:
            return False

        # 确保在正确的页面上才能进行准备
        if not self.appear(BUILD_SUBMIT_ORDERS) \
                and not self.appear(BUILD_SUBMIT_WW_ORDERS):
            return False

        # 使用 'appear' 更新资源的实际位置，供 ui_ensure_index 使用
        confirm_timer = Timer(1, count=2).start()
        ocr_submit = None
        index_offset = (60, 20)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear_then_click(BUILD_SUBMIT_ORDERS, interval=3):
                ocr_submit = OCR_BUILD_SUBMIT_COUNT
                confirm_timer.reset()
                continue

            if self.appear_then_click(BUILD_SUBMIT_WW_ORDERS, interval=3):
                ocr_submit = OCR_BUILD_SUBMIT_WW_COUNT
                confirm_timer.reset()
                continue
            # 即使 UR 兑换点已满也继续建造
            if self.handle_popup_confirm('GACHA_PREP'):
                confirm_timer.reset()
                continue

            # 结束
            if self.appear(BUILD_PLUS, offset=index_offset) \
                    and self.appear(BUILD_MINUS, offset=index_offset):
                if confirm_timer.reached():
                    break

        # 检查是否异常提前退出，并设置正确的提交数量
        if ocr_submit is None:
            raise ScriptError('[建造-准备] 无法识别OCR资产，'
                              '无法继续准备工作')
        area = ocr_submit.buttons[0]
        ocr_submit.buttons = [(BUILD_MINUS.button[2] + 3, area[1], BUILD_PLUS.button[0] - 3, area[3])]
        self.ui_ensure_index(target, letter=ocr_submit, prev_button=BUILD_MINUS,
                             next_button=BUILD_PLUS, skip_first_screenshot=True)

        return True

    def gacha_calculate(self, target_count, gold_cost, cube_cost):
        """
        根据当前资源计算实际可提交的建造数量。

        Args:
            target_count (int): 期望提交的建造订单数量。
            gold_cost (int): 金币消耗。
            cube_cost (int): 魔方消耗。

        Returns:
            int: 根据当前资源可实际提交的数量。
        """
        while 1:
            # 根据 target_count 计算资源消耗
            gold_total = gold_cost * target_count
            cube_total = cube_cost * target_count

            # 数量为 0，无法执行建造
            if not target_count:
                logger.warning('物资和/或心智魔方不足，无法建造')
                break

            # 资源不足，减少 1 并重新计算
            if gold_total > self.build_coin_count or cube_total > self.build_cube_count:
                target_count -= 1
                continue

            break

        # 扣除资源，返回当前 target_count
        logger.info(f'最多可提交 {target_count} 个建造订单')
        self.build_coin_count -= gold_total
        self.build_cube_count -= cube_total
        LogRes(self.config).Cube = self.build_cube_count
        self.config.update()
        return target_count

    def gacha_goto_pool(self, target_pool):
        """
        导航到指定的建造池页面。

        Args:
            target_pool (str): 建造池名称，超出范围时默认使用 'light' 池。

        Returns:
            str: 当前可用的建造池名称。

        Pages:
            in: page_build（建造池选择）
            out: page_build（建造池操作页面）

        Raises:
            ScriptError: 选择 'wishing_well' 但未完成配置时抛出。
        """
        # 切换到 'light' 池视图
        self.gacha_bottom_navbar_ensure(right=3, is_build=True)

        # 根据需要导航到 target_pool，并更新 target_pool 的实际值
        if target_pool == 'wishing_well':
            if self._gacha_side_navbar.get_total(main=self) != 5:
                logger.warning('\'wishing_well\' is not available, '
                               'default to \'light\' pool')
                target_pool = 'light'
            else:
                self.gacha_side_navbar_ensure(upper=2)
                if self.appear(BUILD_WW_CHECK):
                    raise ScriptError('\'wishing_well\' must be configured '
                                      'manually by user, cannot continue '
                                      'gacha_goto_pool')
        elif target_pool == 'event':
            gacha_bottom_navbar = self._gacha_bottom_navbar(is_build=True)
            total = gacha_bottom_navbar.get_total(main=self)
            if total == 3:
                logger.warning('\'event\' is not available, default '
                               'to \'light\' pool')
                target_pool = 'light'
            else:
                # 活动池可用时位于最左侧标签。
                # Navbar 的 left 索引从 1 开始；get_info() 返回从 0 开始的绝对索引，
                # 因此不要将 get_info() 的值作为 `left` 传入。
                self.gacha_bottom_navbar_ensure(left=1, is_build=True)
        elif target_pool in ['heavy', 'special']:
            if target_pool == 'heavy':
                self.gacha_bottom_navbar_ensure(right=2, is_build=True)
            else:
                self.gacha_bottom_navbar_ensure(right=1, is_build=True)

        return target_pool

    def gacha_flush_queue(self, skip_first_screenshot=True):
        """
        清空建造订单队列，确保提交前队列为空。

        Args:
            skip_first_screenshot (bool): 是否跳过首次截图。

        Pages:
            in: page_build（任意子页面）
            out: page_build（建造池选择）

        Raises:
            ScriptError: 无法完全清空队列时退出（船坞可能已满）。
        """
        # 进入建造/订单页面
        self.gacha_side_navbar_ensure(bottom=3)

        # 切换到对应界面，最终回到建造/建造页面
        confirm_timer = Timer(1, count=2).start()
        confirm_mode = True  # 演习，锁定舰船
        # 清除按钮偏移，否则会点击到钻石的 PLUS 按钮或 HOME 按钮
        STORY_SKIP.clear_offset()
        queue_clean = True
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(BUILD_QUEUE_EMPTY, offset=(20, 20)) and queue_clean:
                self.gacha_side_navbar_ensure(upper=1)
                break
            else:
                queue_clean = False

            if self.appear_then_click(BUILD_FINISH_ORDERS, interval=3):
                confirm_timer.reset()
                continue

            if self.handle_retirement():
                confirm_timer.reset()
                continue

            if self.handle_popup_confirm('FINISH_ORDERS'):
                if confirm_mode:
                    self.device.sleep((0.5, 0.8))
                    self.device.click(BUILD_FINISH_ORDERS)  # 跳过动画，安全区域
                    confirm_mode = False
                confirm_timer.reset()
                continue

            if self.appear(GET_SHIP, interval=1):
                self.device.click(STORY_SKIP)  # 多个订单时快进
                confirm_timer.reset()
                continue
            if self.handle_get_items_ship():
                continue

            if self.appear(BUILD_FINISH_RESULTS, offset=(20, 150), interval=3):
                self.device.click(BUILD_FINISH_ORDERS)  # 安全区域
                confirm_timer.reset()
                continue

            # 结束，队列为空时点击后会返回建造池页面
            if self.appear(BUILD_SUBMIT_ORDERS) or self.appear(BUILD_SUBMIT_WW_ORDERS):
                if confirm_timer.reached():
                    break

        # 许愿池不再显示金币，返回普通建造池
        if self.appear(BUILD_SUBMIT_WW_ORDERS):
            logger.info('在许愿池中，返回普通池')
            self.gacha_side_navbar_ensure(upper=1)

    def gacha_submit(self, skip_first_screenshot=True):
        """
        提交建造订单。

        Args:
            skip_first_screenshot (bool): 是否跳过首次截图。

        Pages:
            in: POPUP_CONFIRM
            out: BUILD_FINISH_ORDERS
        """
        logger.info('提交建造')
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(POPUP_CONFIRM, offset=(20, 80), interval=3):
                # 修改资源名称用于点击
                POPUP_CONFIRM.name = POPUP_CONFIRM.name + '_' + 'GACHA_ORDER'
                self.device.click(POPUP_CONFIRM)
                POPUP_CONFIRM.name = POPUP_CONFIRM.name[:-len('GACHA_ORDER') - 1]
                continue

            # 结束
            if self.appear(BUILD_FINISH_ORDERS):
                break

    @staticmethod
    def _process_ocr_digit(raw: str):
        """修正活动日期中常见的数字误识别。"""
        replacements = str.maketrans({
            'D': '0', 'I': '1', '己': '2', '已': '2', '乙': '2', 'Z': '2', 'z': '2',
            'S': '5', 'B': '8', '日': '8', '曰': '8',
        })
        return int(raw.strip().translate(replacements))

    def _get_event_deadline(self, today=None):
        """读取当前活动的结束日期，年份按服务器日期和活动起止月份推断。

        Pages:
            in: page_build（活动卡池）
        """
        # 目前只有国服日期区域和中文格式得到核实，不使用其他服务器的回退资源清券。
        if server.server != 'cn':
            return None
        if today is None:
            today = current_time(timezone(server_timezone())).date()
        period = OCR_BUILD_EVENT_TIME.ocr(self.device.image)
        digits = r'[\dDIZz己已乙SB日曰]'
        matches = re.findall(fr'(?<!{digits})({digits}{{1,2}})\s*月\s*({digits}{{1,2}})\s*日', period)
        if len(matches) != 2:
            return None
        start_month, start_day = map(self._process_ocr_digit, matches[0])
        end_month, end_day = map(self._process_ocr_digit, matches[1])
        cross_year = end_month < start_month
        for start_year in (today.year - 1, today.year):
            try:
                start = date(start_year, start_month, start_day)
                end = date(start_year + cross_year, end_month, end_day)
            except ValueError:
                continue
            if start <= today <= end:
                return end
        return None

    def _handle_clear_ticket(self):
        """在已确认的活动末日分批清券，准备失败时交给上层恢复。

        Returns:
            bool: 已完成清券返回 True，未满足清券条件返回 False。

        Pages:
            in: page_build（活动卡池）
            out: page_build（活动卡池）
        """
        if not self.config.Gacha_UseTicket or not self.config.Gacha_ClearTicket or self.build_ticket_count <= 0:
            return False
        today = current_time(timezone(server_timezone())).date()
        result = self._get_event_deadline(today=today)
        if result is None:
            logger.warning('[建造-卡池] 无法获得当前卡池结束时间')
            return False
        logger.attr('活动结束日期', result)
        if result != today:
            return False

        logger.hr('清理活动建造券')
        while self.build_ticket_count > 0:
            consumption = min(10, self.build_ticket_count)
            forced_use_drill = self.build_ticket_count > 10
            if not self.gacha_run_once('event', consumption, True, forced_use_drill):
                raise GameStuckError('[建造-清券] 无法准备建造订单')

        return True

    def gacha_run_once(self, pool, count, use_ticket, forced_use_drill=False):
        """提交一批订单，成功后更新资源记录并返回建造池页面。

        Returns:
            bool: 已提交订单返回 True，数量为零或准备失败返回 False。

        Pages:
            in: page_build（建造池选择）
            out: page_build（建造池选择；准备失败时保持当前页面）
        """
        if count <= 0:
            return False
        # 不能使用 handle_popup_confirm，因为该窗口没有 POPUP_CANCEL
        actual_pool = self.gacha_goto_pool(pool)
        if use_ticket and (actual_pool != 'event' or not self.appear(BUILD_TICKET_CHECK, offset=(30, 30))):
            raise GameStuckError('[建造-卡池] 活动池或建造券不可用，停止提交用券订单')
        gold_cost = 600
        cube_cost = 1
        if actual_pool in ['heavy', 'special', 'event', 'wishing_well']:
            gold_cost = 1500
            cube_cost = 2
        buy_count = count if use_ticket else self.gacha_calculate(count, gold_cost, cube_cost)

        if not self.gacha_prep(buy_count):
            return False

        self.gacha_submit()
        if use_ticket:
            self.build_ticket_count -= buy_count

        # 按配置使用快速完成工具收取建造结果。
        if forced_use_drill or self.config.Gacha_UseDrill:
            self.gacha_flush_queue()
        # 提交会进入队列页；未使用快速完成时先返回建造页再切换卡池。
        elif not self.gacha_side_navbar_ensure(upper=1):
            raise GameStuckError('[建造-卡池] 无法从队列返回建造页面')
        return True

    def gacha_run(self):
        """
        执行建造操作，提交建造订单。

        Returns:
            bool: 至少一批订单提交成功时返回 True，否则返回 False。

        Pages:
            in: 任意页面
            out: page_build
        """
        # 进入建造页面
        self.ui_goto_gacha()

        # 清空已有的建造队列，确保从头开始
        # 退出后预计在主建造页面
        self.gacha_flush_queue()

        # OCR 识别金币和魔方数量
        self.build_coin_count = self.get_coin()
        self.build_cube_count = OCR_BUILD_CUBE_COUNT.ocr(self.device.image)
        LogRes(self.config).record('Cube', self.build_cube_count,
                                   observed=bool(getattr(OCR_BUILD_CUBE_COUNT, 'last_valid', False)))

        # 开启用券时先检查活动池，不受用户设定的卡池类型限制。
        self.build_ticket_count = 0
        if self.config.Gacha_UseTicket:
            actual_pool = self.gacha_goto_pool('event')
            if actual_pool == 'event' and self.appear(BUILD_TICKET_CHECK, offset=(30, 30)):
                self.build_ticket_count = OCR_BUILD_TICKET_COUNT.ocr(self.device.image)
            logger.attr('单次建造券', self.build_ticket_count)
            if not self.build_ticket_count:
                logger.info('活动池无可用单次建造券，按设定卡池建造')

        # 建造券只支付活动池订单；无券或券不足时，剩余次数交给设定卡池。
        ticket_count = self.build_ticket_count
        if not self._handle_clear_ticket():
            ticket_target = self.config.Gacha_TicketAmount
            # 活动池会自动使用余券；补足最低次数时也必须按券记账。
            if self.config.Gacha_Pool == 'event':
                ticket_target = max(ticket_target, self.config.Gacha_Amount)
            ticket_count = min(ticket_target, self.build_ticket_count)
            if ticket_count and not self.gacha_run_once('event', ticket_count, True):
                return False
        remain_count = max(0, self.config.Gacha_Amount - ticket_count)
        if not remain_count:
            LogRes(self.config).record('Cube', self.build_cube_count, observed=False)
            self.config.update()
            return ticket_count > 0
        result = self.gacha_run_once(self.config.Gacha_Pool, remain_count, False)
        return result or ticket_count > 0

    def run(self):
        """
        根据配置执行建造操作。

        Pages:
            in: 任意页面
            out: page_build
        """
        self.gacha_run()
        self.config.task_delay(server_update=True)
