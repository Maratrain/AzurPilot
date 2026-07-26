"""大世界代币商店模块。

管理大世界代币商店（白票商店）的购买流程，包括：
- 从全球地图进入代币兑换界面
- 调用 VoucherShop 执行商品购买
- 退出商店并返回全球地图
- 完成后延迟至下次大世界重置

继承自 OSMap，提供地图导航和代币商店的完整操作链路。
"""

from module.config.utils import get_os_next_reset
from module.logger import logger
from module.os.map import OSMap
from module.os_handler.assets import EXCHANGE_CHECK, EXCHANGE_ENTER
from module.shop.shop_voucher import VoucherShop


class OpsiVoucher(OSMap):
    def _os_voucher_enter(self):
        self.os_map_goto_globe(unpin=False)
        self.ui_click(click_button=EXCHANGE_ENTER, check_button=EXCHANGE_CHECK,
                      offset=(200, 20), retry_wait=3, skip_first_screenshot=True)

    def _os_voucher_exit(self):
        self.ui_back(check_button=EXCHANGE_ENTER, appear_button=EXCHANGE_CHECK,
                     offset=(200, 20), retry_wait=3, skip_first_screenshot=True)
        self.os_globe_goto_map()

    def os_voucher(self):
        logger.hr('大世界-白票商店', level=1)
        self._os_voucher_enter()
        VoucherShop(self.config, self.device).run()
        self._os_voucher_exit()
        self.logger_use()

        next_reset = get_os_next_reset()
        logger.info('白票商店已完成，延迟到下次重置')
        logger.attr('大世界下次重置', next_reset)
        self.config.task_delay(target=next_reset)
