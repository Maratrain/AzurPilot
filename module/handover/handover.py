"""作战委托模块。

在主线关卡界面启动「作战委托」：消耗石油与作战全权委托书，让舰队离线自动
执行指定主线关卡若干次，委托结束后再领取掉落。委托进行期间游戏会阻止正常
出击，因此本任务默认排在调度优先级的末尾。

流程：
1. 打开主线关卡界面，按 Fleet 组准备编队
2. 检测该关卡是否支持作战委托（HANDOVER_TAB / HANDOVER_TAB_UNSUPPORTED）
3. 打开作战委托面板，把委托次数设置到指定值
4. 比较「需要时间」与「剩余可用时间」，判断剩余时间能否完成委托
5. 时间不足且启用了自动补充时，用作战全权委托书兑换可用时间（1 本 = 1 小时）
6. 启用了使用委托书时，把委托书投入量拉到最大
7. 点击「开始」

配置路径: Campaign.Name, OperationHandover.Count,
         OperationHandover.AutoSupplementTime, OperationHandover.UseHandoverBook
"""

import math

from module.campaign.run import CampaignRun
from module.handler.assets import POPUP_CONFIRM
from module.handler.fast_forward import to_map_file_name
from module.logger import logger
from module.map.assets import (HANDOVER_BOOK_AMOUNT_OCR, HANDOVER_BOOK_COUNT_OCR,
                               HANDOVER_BOOK_ITEM, HANDOVER_BOOK_MAX, HANDOVER_COUNT_MAX,
                               HANDOVER_COUNT_MINUS, HANDOVER_COUNT_OCR, HANDOVER_COUNT_PLUS,
                               HANDOVER_EXCHANGE_TIME, HANDOVER_START_CLICK, HANDOVER_TAB,
                               HANDOVER_TAB_UNSUPPORTED, HANDOVER_TIME_NEEDED_OCR,
                               HANDOVER_TIME_REMAINING_OCR)
from module.ocr.ocr import Digit, DigitCounter, Duration

OCR_HANDOVER_COUNT = Digit(HANDOVER_COUNT_OCR, letter=(255, 255, 255), threshold=128, alphabet='0123456789')
OCR_HANDOVER_BOOK_COUNT = Digit(HANDOVER_BOOK_COUNT_OCR, letter=(255, 255, 255), threshold=128,
                                alphabet='0123456789')
OCR_HANDOVER_BOOK_AMOUNT = DigitCounter(HANDOVER_BOOK_AMOUNT_OCR, letter=(255, 255, 255), threshold=128)
OCR_HANDOVER_TIME_NEEDED = Duration(HANDOVER_TIME_NEEDED_OCR, letter=(255, 255, 255), threshold=128)
OCR_HANDOVER_TIME_REMAINING = Duration(HANDOVER_TIME_REMAINING_OCR, letter=(255, 255, 255), threshold=128)

# 一本作战全权委托书可兑换 1 小时可用时间
HANDOVER_BOOK_HOURS = 1
HANDOVER_BOOK_SECONDS = HANDOVER_BOOK_HOURS * 3600


class OperationHandover(CampaignRun):
    """作战委托执行器。

    继承 CampaignRun 以复用 load_campaign()，拿到 self.campaign（config 已
    deepcopy 合并过的 CampaignBase 实例），借它的 ensure_campaign_ui() 与
    fleet_preparation() 复用战役 UI 能力。全程不调用 self.campaign.run()，
    因此不会进入地图战斗。

    Attributes:
        campaign: 战役执行实例，由 CampaignRun.load_campaign() 提供。
    """

    def run(self):
        """执行一次作战委托。

        Pages: in: any, out: 主线关卡界面
        """
        logger.hr('作战委托', level=1)
        count = self.config.OperationHandover_Count
        auto_supplement = self.config.OperationHandover_AutoSupplementTime
        use_book = self.config.OperationHandover_UseHandoverBook
        logger.attr('委托关卡', self.config.Campaign_Name)
        logger.attr('委托次数', count)
        logger.attr('自动补充时间', auto_supplement)
        logger.attr('使用作战全权委托书', use_book)

        # 打开主线关卡界面，并按 Fleet 组准备编队
        self.handover_enter()

        # 检测该关卡是否支持作战委托
        if not self.handover_check_support():
            self.handover_delay()
            return

        # 打开作战委托面板并设置委托次数
        self.handover_panel_enter()
        self.handover_set_count(count)

        # 判断剩余可用时间能否完成委托
        self.device.screenshot()
        needed, remaining = self.handover_get_time()
        if needed > remaining:
            if not auto_supplement:
                logger.warning('[作战委托] 剩余可用时间不足以完成委托，且未启用自动补充时间')
                self.handover_delay()
                return
            if not self.handover_exchange_time(needed - remaining):
                self.handover_delay()
                return
            # 兑换弹窗关闭后画面已更新，重新截图供后续使用
            self.device.screenshot()

        # 点击确定前按需把委托书投入量拉满
        if use_book:
            self.handover_book_max()

        self.handover_start()
        self.config.task_delay(server_update=True)

    def handover_enter(self):
        """打开主线关卡界面，并按 Fleet 组准备编队。

        Pages: in: any, out: 主线关卡界面
        """
        name = to_map_file_name(self.config.Campaign_Name)
        self.load_campaign(name, folder='campaign_main')

        self.device.screenshot()
        self.campaign.ensure_campaign_ui(name=self.stage, mode='normal', skip_first_screenshot=True)

        # 编队栏就在关卡界面上，直接复用地图的编队准备逻辑应用 Fleet 组。
        # fleet_preparation() 靠 map_fleet_checked 做幂等保护，先置 False 保证本次一定会应用。
        self.campaign.map_fleet_checked = False
        self.campaign.fleet_preparation()

    def handover_check_support(self):
        """检测当前关卡是否支持作战委托。

        HANDOVER_TAB 与 HANDOVER_TAB_UNSUPPORTED 的区域完全相同，只有填充色不同，
        因此用颜色判定区分。

        Returns:
            bool: 该关卡支持作战委托返回 True。
        """
        self.device.screenshot()
        if self.appear(HANDOVER_TAB):
            logger.info('[作战委托] 该关卡支持作战委托')
            return True
        if self.appear(HANDOVER_TAB_UNSUPPORTED):
            logger.warning('[作战委托] 该关卡不支持作战委托')
            return False
        logger.warning('[作战委托] 未找到作战委托入口')
        return False

    def handover_panel_appear(self):
        """作战委托面板是否已打开。

        「最大」按钮只在面板内出现，用它作为面板标志物。该按钮与次数加号等
        按钮颜色相同，因此必须用模板匹配而不是颜色判定。

        Returns:
            bool: 面板已打开返回 True。
        """
        return self.appear(HANDOVER_COUNT_MAX, offset=(20, 20))

    def handover_panel_enter(self, skip_first_screenshot=True):
        """点击作战委托入口，打开作战委托面板。

        Pages: in: 主线关卡界面, out: 作战委托面板
        """
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.handover_panel_appear():
                break

            if self.appear_then_click(HANDOVER_TAB, interval=2):
                continue

        logger.info('[作战委托] 已打开作战委托面板')

    def handover_set_count(self, count):
        """把委托次数设置到指定值。

        ui_ensure_index() 以游戏显示的次数为唯一依据点击加减号，点击丢失时能自行补齐。

        Args:
            count (int): 目标委托次数。
        """
        self.ui_ensure_index(
            count,
            letter=OCR_HANDOVER_COUNT,
            next_button=HANDOVER_COUNT_PLUS,
            prev_button=HANDOVER_COUNT_MINUS,
            skip_first_screenshot=True,
        )

    def handover_get_time(self):
        """识别委托需要的时间和当天剩余可用时间。

        Returns:
            tuple[timedelta, timedelta]: (需要时间, 剩余可用时间)。
        """
        needed = OCR_HANDOVER_TIME_NEEDED.ocr(self.device.image)
        remaining = OCR_HANDOVER_TIME_REMAINING.ocr(self.device.image)
        logger.attr('需要时间', needed)
        logger.attr('剩余可用时间', remaining)
        return needed, remaining

    def handover_exchange_time(self, deficit):
        """用作战全权委托书兑换可用时间。

        Pages: in: 作战委托面板, out: 作战委托面板

        Args:
            deficit (timedelta): 缺少的时间。

        Returns:
            bool: 兑换成功返回 True。
        """
        need = math.ceil(deficit.total_seconds() / HANDOVER_BOOK_SECONDS)
        logger.info(f'[作战委托] 剩余时间不足，需补充 {need} 小时，即 {need} 本作战全权委托书')

        # 点击「兑换可用时间」打开兑换弹窗
        skip_first_screenshot = True
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(POPUP_CONFIRM, offset=self._popup_offset):
                break

            if self.appear_then_click(HANDOVER_EXCHANGE_TIME, interval=2):
                continue

        # 识别持有的委托书数量，判断是否足够
        current, _, total = OCR_HANDOVER_BOOK_AMOUNT.ocr(self.device.image)
        logger.attr('作战全权委托书', f'{current}/{total}')
        if current < need:
            logger.critical(f'[作战委托] 作战全权委托书不足，需要 {need} 本，持有 {current} 本')
            self.handle_popup_cancel('HANDOVER')
            return False

        # 点击委托书道具，累计到需要的本数
        clicked = 0
        while clicked < need:
            self.device.screenshot()
            if self.appear_then_click(HANDOVER_BOOK_ITEM, offset=(20, 20), interval=1):
                clicked += 1

        # 通用的确认按钮
        while 1:
            self.device.screenshot()
            if not self.handle_popup_confirm('HANDOVER'):
                break

        logger.info(f'[作战委托] 已补充 {need} 小时可用时间')
        return True

    def handover_book_max(self):
        """把作战全权委托书的投入量拉到最大。

        Pages: in: 作战委托面板

        Returns:
            bool: 找到并使用「最大」按钮返回 True。
        """
        if not self.appear(HANDOVER_BOOK_MAX, offset=(20, 20)):
            logger.warning('[作战委托] 未找到委托书最大按钮，跳过使用委托书')
            return False

        # 点击最大按钮，直到投入量不再变化
        book = -1
        while 1:
            self.device.screenshot()
            current = OCR_HANDOVER_BOOK_COUNT.ocr(self.device.image)
            if current == book:
                break
            book = current

            if self.appear_then_click(HANDOVER_BOOK_MAX, offset=(20, 20), interval=2):
                continue

        logger.attr('投入作战全权委托书', book)
        return True

    def handover_start(self, skip_first_screenshot=True):
        """点击「开始」，处理二次确认弹窗。

        Pages: in: 作战委托面板, out: 主线关卡界面
        """
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.handle_popup_confirm('HANDOVER_START'):
                continue

            # 面板关闭，说明委托已经开始
            if not self.handover_panel_appear():
                break

            if self.appear_then_click(HANDOVER_START_CLICK, offset=(20, 20), interval=3):
                continue

        logger.info('[作战委托] 委托已开始，委托期间游戏会阻止正常出击')

    def handover_delay(self):
        """本次无法开始委托，推迟到下一个可用时间额度刷新。

        作战委托的可用时间额度每天 0 点重置，因此失败路径统一延迟到次日。
        """
        logger.warning('[作战委托] 本次未开始委托，推迟到下一个可用时间额度刷新')
        self.config.task_delay(server_update=True)
