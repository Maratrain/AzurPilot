"""补给包处理器，自动购买每周和每日补给包。
根据星期和服务器时间判断可购买的补给包类型。
"""

from calendar import day_name

from module.base.timer import Timer
from module.campaign.campaign_status import CampaignStatus
from module.combat.assets import GET_ITEMS_1, GET_ITEMS_2
from module.config.utils import get_server_weekday
from module.freebies.assets import *
from module.logger import logger
from module.ocr.ocr import Digit
from module.shop.assets import SHOP_OCR_OIL, SHOP_OCR_OIL_CHECK
from module.ui.page import page_shop, page_supply_pack


class SupplyPack(CampaignStatus):
    def supply_pack_buy(self, supply_pack, skip_first_screenshot=True):
        """
        Args:
            supply_pack (Button): Button of supply pack, click to buy.
            skip_first_screenshot (bool):

        Returns:
            bool: If bought.
        """
        logger.hr('购买补给包')
        [self.interval_clear(asset) for asset in [GET_ITEMS_1, GET_ITEMS_2, supply_pack, BUY_CONFIRM]]

        logger.info(f'[免费福利-补给] 购买 {supply_pack}')
        executed = False
        click_count = 0
        confirm_timer = Timer(1, count=3).start()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(supply_pack, offset=(200, 20), interval=3):
                if click_count >= 3:
                    logger.warning(f'[免费福利-补给] 购买 {supply_pack} 尝试3次后失败，可能达到资源限制，跳过')
                    break
                self.device.click(supply_pack)
                click_count += 1
                confirm_timer.reset()
                continue
            if self.appear_then_click(BUY_CONFIRM, offset=(20, 20), interval=3):
                confirm_timer.reset()
                continue
            if self.handle_popup_confirm('BUY_SUPPLY_PACK'):
                self.interval_reset(supply_pack)
                self.interval_reset(BUY_CONFIRM)
                executed = True
                continue
            for button in [GET_ITEMS_1, GET_ITEMS_2]:
                if self.appear_then_click(button, offset=(30, 30), interval=3):
                    confirm_timer.reset()
                    continue

            # End
            if self.appear(page_supply_pack.check_button, offset=(20, 20)) \
                    and not self.appear(supply_pack, offset=(20, 20)):
                if confirm_timer.reached():
                    break
            else:
                confirm_timer.reset()

        logger.info(f'购买补给包 finished, executed={executed}')
        return executed

    def goto_supply_pack(self, skip_first_screenshot=True):
        """
        Pages:
            in: page_shop
            out: page_supply_pack, supply pack tab
        """
        self.ui_goto(page_supply_pack, skip_first_screenshot=skip_first_screenshot)

    def run(self):
        """
        Pages:
            in: Any page
            out: page_supply_pack, supply pack tab
        """
        self.ui_ensure(page_shop)
        self.goto_supply_pack()
        if self.get_oil() < 21000:
            server_today = get_server_weekday()
            target = self.config.SupplyPack_DayOfWeek
            target_name = day_name[target]
            if server_today >= target:
                self.supply_pack_buy(FREE_SUPPLY_PACK)
            else:
                logger.info(f'[免费福利-补给] 将免费周补给包延迟到 {target_name}')
        else:
            logger.info('石油超限，无法购买免费周补给包')


class SupplyPack_250814(SupplyPack):
    def get_oil(self, skip_first_screenshot=True):
        """
        Returns:
            int: Oil amount
        """
        amount = 0
        timeout = Timer(1, count=2).start()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning('获取石油超时')
                break

            if not self.appear(SHOP_OCR_OIL_CHECK, offset=(10, 2)):
                logger.info('无石油图标')
                continue
            ocr = Digit(SHOP_OCR_OIL, name='OCR_OIL', letter=(247, 247, 247), threshold=128)
            amount = ocr.ocr(self.device.image)
            if amount >= 100:
                break

        return amount

    def goto_supply_pack(self, skip_first_screenshot=True):
        """
        Pages:
            in: page_shop
            out: page_supply_pack, supply pack tab
        """
        logger.info('前往补给包')
        for _ in self.loop():

            if self.match_template_color(page_supply_pack.check_button, offset=(20, 20)):
                logger.info('在补给包')
                break

            elif self.appear_then_click(page_supply_pack.check_button, offset=(20, 20), interval=3):
                continue
