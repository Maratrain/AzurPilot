"""大世界战役运行模块。

管理大世界（Operation Siren）任务的执行流程。

大世界是一个独立于主线战役的开放世界系统，有自己的：
- 行动力（Action Point）机制
- 适应性（Adaptability）系统
- 净化装置（Purification Device）
- 港口商店和任务系统

此模块负责：
- 加载大世界配置和地图操作实例
- 处理行动力溢出保护
- 大世界任务的延迟和调度
- 行动力不足时的任务延迟

继承自 OSMapOperation，提供大世界地图操作能力。
"""

from module.config.utils import get_os_reset_remain
from module.logger import logger
from module.os.config import OSConfig
from module.os.map_operation import OSMapOperation
from module.os.operation_siren import OperationSiren
from module.os_handler.action_point import ActionPointLimit


class OSCampaignRun(OSMapOperation):
    """大世界战役运行器。

    管理大世界任务的执行，包括行动力保护和任务调度。

    Attributes:
        PREVENT_AP_OVERFLOW_TASK (str): 防止行动力溢出的任务名称。
    """
    PREVENT_AP_OVERFLOW_TASK = 'OpsiPreventActionPointOverflow'

    def load_campaign(self, cls=OperationSiren):
        config = self.config.merge(OSConfig())
        campaign = cls(config=config, device=self.device)
        campaign.os_init()
        return campaign

    def delay_opsi_tasks_after_ap_limit(self, error):
        delay_minutes = getattr(error, 'delay_minutes', None)
        if delay_minutes is not None:
            logger.info(f'[大世界-运行] 延迟大世界行动力任务 {delay_minutes} 分钟直到行动力恢复')
        self.config.opsi_task_delay(ap_limit=True, ap_limit_minutes=delay_minutes)

    def _run_opsi_task_with_ap_overflow_guard(self, runner):
        """运行普通大世界任务时临时关闭防溢出任务，并在结束时恢复调度。"""
        campaign = None
        prevent_enabled = self.config.is_task_enabled(self.PREVENT_AP_OVERFLOW_TASK)
        if prevent_enabled:
            logger.info('[战役] 临时关闭防止行动力溢出任务')
            self.config.cross_set(keys=f'{self.PREVENT_AP_OVERFLOW_TASK}.Scheduler.Enable', value=False)

        try:
            campaign = self.load_campaign()
            return runner(campaign)
        finally:
            if prevent_enabled:
                if campaign is not None:
                    try:
                        campaign.update_prevent_action_point_overflow_schedule(enable=True)
                    except Exception:
                        logger.debug('恢复防止行动力溢出任务调度失败，直接重新启用任务', exc_info=True)
                        self.config.cross_set(keys=f'{self.PREVENT_AP_OVERFLOW_TASK}.Scheduler.Enable', value=True)
                else:
                    self.config.cross_set(keys=f'{self.PREVENT_AP_OVERFLOW_TASK}.Scheduler.Enable', value=True)

    def opsi_explore(self):
        try:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.os_explore())
        except ActionPointLimit as e:
            self.delay_opsi_tasks_after_ap_limit(e)

    def opsi_shop(self):
        try:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.os_shop())
        except ActionPointLimit as e:
            self.delay_opsi_tasks_after_ap_limit(e)

    def opsi_voucher(self):
        try:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.os_voucher())
        except ActionPointLimit as e:
            self.delay_opsi_tasks_after_ap_limit(e)

    def opsi_daily(self):
        try:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.os_daily())
        except ActionPointLimit as e:
            self.delay_opsi_tasks_after_ap_limit(e)

    def opsi_meowfficer_farming(self):
        try:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.os_meowfficer_farming())
        except ActionPointLimit:
            if get_os_reset_remain() > 0:
                self.config.task_delay(server_update=True)
                self.config.task_call('Reward', force_call=False)
            else:
                logger.info('[大世界-运行] 距离大世界重置不足1天，延迟2.5小时')
                self.config.task_delay(minute=150, server_update=True)

    def opsi_hazard1_leveling(self):
        try:
            self._run_opsi_task_with_ap_overflow_guard(
                lambda campaign: (campaign.os_check_leveling(), campaign.os_hazard1_leveling())
            )
        except ActionPointLimit:
            self.config.task_delay(server_update=True)

    def opsi_obscure(self):
        try:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.os_obscure())
        except ActionPointLimit as e:
            self.delay_opsi_tasks_after_ap_limit(e)

    def opsi_month_boss(self):
        if self.config.SERVER in ['tw']:
            logger.info(f'[大世界-运行] OpsiMonthBoss不支持服务器 {self.config.SERVER}，'
                        ' please contact server maintainers')
            self.config.task_delay(server_update=True)
            self.config.task_stop()
            return
        try:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.clear_month_boss())
        except ActionPointLimit as e:
            self.delay_opsi_tasks_after_ap_limit(e)

    def opsi_abyssal(self):
        try:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.os_abyssal())
        except ActionPointLimit as e:
            self.delay_opsi_tasks_after_ap_limit(e)

    def opsi_archive(self):
        try:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.os_archive())
        except ActionPointLimit as e:
            self.delay_opsi_tasks_after_ap_limit(e)

    def opsi_stronghold(self):
        try:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.os_stronghold())
        except ActionPointLimit as e:
            self.delay_opsi_tasks_after_ap_limit(e)

    def opsi_scheduling(self):
        # 必须在 load_campaign() 前拦截，否则 os_init() 会执行首次自律寻敌。
        if self.is_in_opsi_explore():
            logger.info('[大世界-智能调度+] 每月开荒+正在运行，初始化前延期智能调度+')
            self.config.task_delay(
                server_update=self.config.cross_get(
                    keys='OpsiScheduling.Scheduler.ServerUpdate',
                    default='00:00',
                ),
                task='OpsiScheduling',
            )
            self.config.task_stop()
            return

        self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.run_smart_scheduling())

    def opsi_prevent_action_point_overflow(self):
        campaign = self.load_campaign()
        campaign.os_prevent_action_point_overflow()

    def opsi_cross_month(self):
        try:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.os_cross_month())
        except ActionPointLimit:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.os_cross_month_end())
