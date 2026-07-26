"""伏击和空袭处理器。处理地图探索中的伏击回避/迎击和空袭等待。"""

from module.base.timer import Timer
from module.base.utils import get_color, red_overlay_transparency
from module.combat.combat import Combat
from module.handler.assets import *
from module.handler.info_handler import info_letter_preprocess
from module.logger import logger
from module.template.assets import *

TEMPLATE_AMBUSH_EVADE_SUCCESS.pre_process = info_letter_preprocess
TEMPLATE_AMBUSH_EVADE_FAILED.pre_process = info_letter_preprocess
TEMPLATE_MAP_WALK_OUT_OF_STEP.pre_process = info_letter_preprocess


class AmbushHandler(Combat):
    """伏击和空袭处理器，通过红色覆盖层透明度检测事件。"""
    MAP_AMBUSH_OVERLAY_TRANSPARENCY_THRESHOLD = 0.40
    MAP_AIR_RAID_OVERLAY_TRANSPARENCY_THRESHOLD = 0.35  # 通常值为 (0.50, 0.53)
    MAP_AIR_RAID_CONFIRM_SECOND = 0.5

    def ambush_color_initial(self):
        """初始化伏击和空袭的颜色参考值。"""
        MAP_AMBUSH.load_color(self.device.image)
        MAP_AIR_RAID.load_color(self.device.image)

    def _ambush_appear(self):
        """检测伏击是否出现。"""
        return red_overlay_transparency(MAP_AMBUSH.color, get_color(self.device.image, MAP_AMBUSH.area)) > \
               self.MAP_AMBUSH_OVERLAY_TRANSPARENCY_THRESHOLD

    def _air_raid_appear(self):
        """检测空袭是否出现。"""
        return red_overlay_transparency(MAP_AIR_RAID.color, get_color(self.device.image, MAP_AIR_RAID.area)) > \
               self.MAP_AIR_RAID_OVERLAY_TRANSPARENCY_THRESHOLD

    def _handle_air_raid(self):
        """
        等待空袭动画消失。
        """
        logger.info('[地图-伏击] 空袭')
        disappear = Timer(self.MAP_AIR_RAID_CONFIRM_SECOND).start()
        timeout = Timer(2.5, count=2).start()

        while 1:
            self.device.screenshot()
            # 超时处理
            if timeout.reached():
                logger.warning('[地图-伏击] 空袭处理超时，假设空袭已消失')
                break
            # 检测是否消失
            if self._air_raid_appear():
                disappear.reset()
            else:
                if disappear.reached():
                    break

    def _handle_ambush_evade(self):
        """处理伏击回避事件。"""
        logger.info('[地图-伏击] 遭遇伏击')
        # 等待 MAP_AMBUSH_EVADE 出现
        self.wait_until_appear(MAP_AMBUSH_EVADE, offset=(30, 30))
        self.handle_info_bar()

        # 点击 MAP_AMBUSH_EVADE
        skip_first_screenshot = True
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 结束条件
            if self.info_bar_count():
                break

            if self.appear_then_click(MAP_AMBUSH_EVADE, offset=(30, 30), interval=3):
                continue

        # 处理回避成功和失败
        image = info_letter_preprocess(self.image_crop(INFO_BAR_DETECT, copy=False))
        if TEMPLATE_AMBUSH_EVADE_SUCCESS.match(image):
            logger.attr('伏击回避', '成功')
        elif TEMPLATE_AMBUSH_EVADE_FAILED.match(image):
            logger.attr('伏击回避', '失败')
            self.combat(expected_end='no_searching', fleet_index=self.fleet_show_index)
        else:
            logger.warning('[地图-伏击] 无法识别的伏击回避信息')
            self.ensure_no_info_bar()
            if self.combat_appear():
                self.combat(fleet_index=self.fleet_show_index)

    def _handle_ambush_attack(self):
        """处理伏击迎击事件。"""
        logger.info('[地图-伏击] 遭遇伏击')
        # 等待 MAP_AMBUSH_ATTACK 出现
        self.wait_until_appear(MAP_AMBUSH_ATTACK, offset=(30, 30))

        # 点击 MAP_AMBUSH_ATTACK
        skip_first_screenshot = True
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 结束条件
            if self.combat_appear():
                break

            if self.appear_then_click(MAP_AMBUSH_ATTACK, offset=(30, 30), interval=3):
                continue
            if self.handle_combat_low_emotion():
                continue
            if self.handle_retirement():
                continue

        # 进入战斗
        logger.attr('伏击回避', '迎击')
        self.combat(expected_end='no_searching', fleet_index=self.fleet_show_index)

    def _handle_ambush(self):
        """根据配置选择回避或迎击。"""
        if self.config.Campaign_AmbushEvade:
            return self._handle_ambush_evade()
        else:
            return self._handle_ambush_attack()

    def handle_ambush(self):
        """统一的伏击/空袭处理入口。"""
        if not self.config.MAP_HAS_AMBUSH:
            return False

        if self._air_raid_appear():
            self._handle_air_raid()
            return True

        if self._ambush_appear():
            self._handle_ambush()
            return True

        if self.appear(MAP_AMBUSH_EVADE, offset=(30, 30)):
            self._handle_ambush()

        return False

    def handle_walk_out_of_step(self):
        """处理舰队步数不足的提示。"""
        if not self.config.MAP_HAS_FLEET_STEP:
            return False
        if not self.info_bar_count():
            return False

        image = info_letter_preprocess(self.image_crop(INFO_BAR_DETECT, copy=False))
        if TEMPLATE_MAP_WALK_OUT_OF_STEP.match(image):
            logger.warning('[地图-伏击] 舰队步数不足')
            self.handle_info_bar()
            return True

        return False
