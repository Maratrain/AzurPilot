from module.logger import logger
from module.os.map import OSMap
from module.os.tasks.scheduling import CoinTaskMixin


class OpsiObscure(CoinTaskMixin, OSMap):
    
    def clear_obscure(self):
        """
        清理一个隐秘海域。

        从仓库取出隐秘海域坐标，前往目标区域执行自动搜索。
        如果没有可执行内容，会在代理模式下标记本轮无内容。

        Raises:
            ActionPointLimit: 行动力不足。

        Pages:
            in: page_os, 大世界地图
            out: page_os, 大世界地图
        """
        logger.hr('OS clear obscure', level=1)
        self.cl1_ap_preserve()
        if self.config.OpsiObscure_ForceRun:
            logger.info('OS obscure finish is under force run')

        result = self.storage_get_next_item('OBSCURE', use_logger=self.config.OpsiGeneral_UseLogger,
                                            skip_obscure_hazard_2=self.config.OpsiObscure_SkipHazard2Obscure)
        if not result:
            if self._handle_coin_task_no_content('隐秘海域', '隐秘海域没有可执行内容'):
                return

        self.config.override(
            OpsiGeneral_DoRandomMapEvent=False,
            HOMO_EDGE_DETECT=False,
            STORY_OPTION=0,
        )
        self.zone_init()
        self.fleet_set(self.config.OpsiFleet_Fleet)
        with self.config.temporary(_disable_task_switch=True):
            self.os_order_execute(
                recon_scan=True,
                submarine_call=self.config.OpsiFleet_Submarine)
            self.run_auto_search(rescan='current')

            self.map_exit()
            self.handle_after_auto_search()

    def os_obscure(self):
        while True:
            self.clear_obscure()

            # 非强制模式每次只清一个隐秘海域，保留 os_order_execute 写入的侦查/潜艇冷却。
            if not self.config.OpsiObscure_ForceRun:
                break
            
            self.config.check_task_switch()
            continue
