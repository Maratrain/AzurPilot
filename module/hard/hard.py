"""困难模式战役执行模块。

自动执行碧蓝航线的困难模式关卡。困难模式与普通模式共享同一张地图，
但使用独立的战役入口和额外的限制条件（如每日出击次数、舰队锁定等）。
通过 OCR 识别剩余出击次数，循环执行直到用尽。

配置路径: Hard.HardStage (关卡选择), Hard.HardFleet (舰队选择), Hard.UseRecommendFleet (自动配队)
"""

import importlib

from campaign.campaign_hard.campaign_hard import Campaign
from module.campaign.run import CampaignRun
from module.handler.fast_forward import to_map_file_name
from module.hard.assets import *
from module.logger import logger
from module.ocr.ocr import Digit

OCR_HARD_REMAIN = Digit(OCR_HARD_REMAIN, letter=(123, 227, 66), threshold=128, alphabet='0123')


class CampaignHard(CampaignRun):
    """困难模式战役执行器。

    继承自 CampaignRun，负责执行困难模式关卡。从普通模式战役加载地图数据，
    强制启用舰队锁定和自动搜索，通过 OCR 识别每日剩余出击次数并循环执行。

    Attributes:
        equipment_has_take_on: 装备是否已穿戴（当前未使用）。
        campaign: 战役执行实例，由 CampaignRun 提供。
    """

    equipment_has_take_on = False
    campaign: Campaign

    def run(self):
        logger.hr('困难战役', level=1)
        name = to_map_file_name(self.config.Hard_HardStage)
        self.config.override(
            Campaign_Mode='hard',
            Campaign_UseFleetLock=True,
            Campaign_UseAutoSearch=True,
            Fleet_FleetOrder='fleet1_all_fleet2_standby' if self.config.Hard_HardFleet == 1 else 'fleet1_standby_fleet2_all',
            Emotion_Mode='nothing',  # 不计算也不忽略
        )
        # 装备穿戴
        # campaign/campaign_hard/campaign_hard.py Campaign.fleet_preparation()

        # 初始化
        self.load_campaign(name='campaign_hard', folder='campaign_hard')  # 加载战役文件
        module = importlib.import_module('.' + name, 'campaign.campaign_main')  # 从普通模式加载地图
        self.campaign.MAP = module.MAP

        # UI 确认
        self.device.screenshot()
        self.campaign.device.image = self.device.image
        self.campaign.ensure_campaign_ui(
            name=self.config.Hard_HardStage,
            mode='hard'
        )

        # 执行
        remain = OCR_HARD_REMAIN.ocr(self.device.image)
        logger.attr('剩余次数', remain)
        for n in range(remain):
            self.campaign.run()

        self.campaign.ensure_auto_search_exit()
        # self.campaign.equipment_take_off_when_finished()

        # 调度器
        self.config.task_delay(server_update=True)
        self.config.task_call('Reward', force_call=False)
