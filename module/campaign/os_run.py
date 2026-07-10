from module.config.utils import get_os_reset_remain
from module.logger import logger
from module.os.config import OSConfig
from module.os.map_operation import OSMapOperation
from module.os.operation_siren import OperationSiren
from module.os_handler.action_point import ActionPointLimit


class OSCampaignRun(OSMapOperation):
    PREVENT_AP_OVERFLOW_TASK = 'OpsiPreventActionPointOverflow'

    def load_campaign(self, cls=OperationSiren):
        config = self.config.merge(OSConfig())
        campaign = cls(config=config, device=self.device)
        campaign.os_init()
        return campaign

    def delay_opsi_tasks_after_ap_limit(self, error):
        delay_minutes = getattr(error, 'delay_minutes', None)
        if delay_minutes is not None:
            logger.info(f'Delay OpSi AP tasks for {delay_minutes} minutes until action points recover')
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
                logger.info('Just less than 1 day to OpSi reset, delay 2.5 hours')
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
            logger.info(f'OpsiMonthBoss is not supported in {self.config.SERVER},'
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
        self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.run_smart_scheduling())

    def opsi_prevent_action_point_overflow(self):
        campaign = self.load_campaign()
        campaign.os_prevent_action_point_overflow()

    def opsi_cross_month(self):
        try:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.os_cross_month())
        except ActionPointLimit:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.os_cross_month_end())
