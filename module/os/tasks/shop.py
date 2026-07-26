"""大世界港口商店模块。

执行大世界港口商店的补给物资购买，包括：
- 遍历所有友方港口购买补给
- 黄币和紫币余额检查
- 月度购买限制日期配置
- 港口间的自动导航和购买执行

继承自 OSMap，提供港口导航和商店购买的完整操作链路，
是大世界代币消耗的重要途径之一。
"""

from datetime import datetime, timedelta

from module.config.time_source import now as current_time
from module.config.utils import get_server_next_update, get_os_reset_remain, get_os_next_reset
from module.logger import logger
from module.os.map import OSMap
from module.os_shop.assets import OS_SHOP_CHECK


class OpsiShop(OSMap):
    def os_shop(self):
        """
        购买所有港口的补给物资。

        如果黄币或紫币不足，跳过下一个港口的补给购买。

        Pages:
            in: page_os, 大世界地图
            out: page_os, 大世界地图
        """
        logger.hr('大世界-大世界商店+', level=1)
        today = current_time().day
        limit = self.config.OpsiShop_DisableBeforeDate
        if today <= limit:
            logger.info(f'大世界商店+延迟运行，今日日期 {today} <= 限制日期 {limit}')
            self.config.task_delay(server_update=True)
            self.config.task_stop()

        not_empty = self.perform_port_shop_purchase()

        next_reset = self._os_shop_delay(not_empty)
        if not_empty:
            logger.info('大世界商店+已完成，延迟到下次重置')
        else:
            logger.warning('[大世界-商店] 港口中没有商店，跳到下个月')
        logger.attr('大世界商店下次重置', next_reset)

        self.config.task_delay(target=next_reset)
        self.config.task_stop()

    def perform_port_shop_purchase(self):
        """
        执行一次港口商店购买流程，不包含任务延迟和停止逻辑。

        供 os_shop 和智能调度+月末清理共用。前往最近友方港口，
        进入商店购买所有补给，购买完成后退出港口。

        Returns:
            bool: True 表示商店非空且已尝试购买，False 表示商店为空。

        Pages:
            in: page_os, 大世界地图
            out: page_os, 大世界地图
        """
        if not self.zone.is_azur_port:
            self.globe_goto(self.zone_nearest_azur_port(self.zone))

        self.port_enter()
        self.port_shop_enter()

        if self.appear(OS_SHOP_CHECK):
            not_empty = self.handle_port_supply_buy()
        else:
            not_empty = False
            logger.warning('[大世界-商店] 港口中没有商店')

        self.port_shop_quit()
        self.port_quit()
        return not_empty

    def _os_shop_delay(self, not_empty) -> datetime:
        """
        计算大世界商店+的延迟时间。

        根据商店是否为空和距月底重置的天数决定下次运行时间。

        Args:
            not_empty (bool): 商店是否非空。

        Returns:
            datetime: 下次商店重置时间。
        """
        next_reset = None

        if not_empty:
            next_reset = get_server_next_update(self.config.Scheduler_ServerUpdate)
        else:
            remain = get_os_reset_remain()
            next_reset = get_os_next_reset()
            if remain == 0:
                next_reset = get_server_next_update(self.config.Scheduler_ServerUpdate)
            elif remain < 7:
                next_reset = next_reset - timedelta(days=1)
            else:
                next_reset = (
                    get_server_next_update(self.config.Scheduler_ServerUpdate) +
                    timedelta(days=6)
                )
        return next_reset
