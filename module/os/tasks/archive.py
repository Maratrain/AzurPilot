"""
档案任务模块。

执行大世界档案坐标任务：先完成已持有的档案坐标，再通过白票商店
购买新的档案坐标，循环执行直到资源耗尽。建议每周运行一次，
开发团队通常在维护后更新新档案数据。

Classes:
    OpsiArchive: 档案任务处理器，继承 OSMap。
"""

from module.config.utils import get_nearest_weekday_date
from module.logger import logger
from module.os.map import OSMap
from module.shop.shop_voucher import VoucherShop


class OpsiArchive(OSMap):
    def os_archive(self):
        """
        执行大世界档案坐标任务。

        完成每日任务中的活跃档案坐标，购买下一个可用的档案坐标，
        循环执行直到耗尽。建议每周运行一次，开发团队会在维护后添加新档案。
        """
        if self.is_in_opsi_explore():
            logger.info('每月开荒+正在运行，停止档案坐标')
            self.config.task_delay(server_update=True)
            self.config.task_stop()

        shop = VoucherShop(self.config, self.device)
        while True:
            # 防止日志仪被手动购买，先完成已存在的档案坐标
            self.os_finish_daily_mission(
                skip_siren_mission=self.config.cross_get('OpsiDaily.OpsiDaily.SkipSirenResearchMission'),
                question=False, rescan=False)

            logger.hr('大世界-白票商店', level=1)
            self._os_voucher_enter()
            bought = shop.run_once()
            self._os_voucher_exit()
            if not bought:
                break

        # 延迟到最近的周三重置
        next_reset = get_nearest_weekday_date(target=2)
        logger.info('档案坐标已全部完成，延迟到下次重置')
        logger.attr('大世界下次重置', next_reset)
        self.config.task_delay(target=next_reset)
