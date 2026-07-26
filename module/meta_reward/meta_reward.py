"""
META 奖励收取模块。

自动化 META 系统中的奖励收取流程，包括余烬信标（Beacon）奖励
和档案（Dossier）奖励两种类型。

主要功能：
    - 检测并收取 META 同步奖励（sync reward）：累积 META 点数达到 100%
      后获取 META 舰船
    - 检测并收取 META 信标奖励（beacon reward）：余烬信标战斗后的奖励
    - 检测并收取档案奖励（dossier reward）：已完成的旧 META 档案奖励
    - 处理新 META 舰船锁定确认、物品获取弹窗

META 系统机制：
    - 余烬信标：玩家通过余烬信标战斗累积点数，达到 100% 可获取 META 舰船
    - 同步（Sync）：累积点数的过程，完成后可获取舰船
    - 档案（Dossier）：已完成的旧 META 活动，可领取遗留奖励

继承关系：
    - BeaconReward: 继承 Combat + UI，处理信标奖励和同步奖励
    - DossierReward: 继承 Combat + UI，处理档案奖励
    - MetaReward: 继承 BeaconReward + DossierReward，统一入口

服务器支持：CN、EN、JP（TW 不支持）

Pages:
    META 页面：page_meta
    档案 META 页面：dossier meta page
"""

from module.base.timer import Timer
from module.combat.combat import Combat
from module.logger import logger
from module.meta_reward.assets import *
from module.os_ash.assets import DOSSIER_LIST
from module.ui.page import page_meta
from module.ui.ui import UI


class BeaconReward(Combat, UI):
    """
    余烬信标奖励处理器。

    处理 META 页面中的信标战斗奖励和同步奖励收取。
    同步奖励是玩家累积 META 点数达到 100% 后获得 META 舰船的过程；
    信标奖励是余烬信标战斗后的常规奖励。

    核心流程：
        1. 导航至 META 页面，等待页面加载完成
        2. 检测同步奖励红点，收取同步奖励（获取 META 舰船）
        3. 检测信标奖励红点，收取信标奖励（物品/经验）

    属性:
        无额外实例属性

    配置项:
        OpsiAshBeacon_AutoCollectShip: 是否自动收取 META 舰船
    """
    def meta_reward_notice_appear(self):
        """
        Returns:
            bool: If appear.

        Page:
            in: page_meta
        """
        if self.appear(META_REWARD_NOTICE, threshold=30):
            return True
        else:
            return False

    def meta_reward_receive(self, skip_first_screenshot=True):
        """
        Args:
            skip_first_screenshot:

        Returns:
            bool: If received.

        Pages:
            in: page_meta or REWARD_CHECK
            out: REWARD_CHECK
        """
        logger.hr('领取META奖励', level=1)
        confirm_timer = Timer(1, count=3).start()
        received = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # End
            # REWARD_CHECK appears and REWARD_RECEIVE gets gray
            if self.appear(REWARD_CHECK, offset=(20, 20)) and \
                    self.image_color_count(REWARD_RECEIVE, color=(49, 52, 49), threshold=221, count=400):
                break

            if self.appear_then_click(REWARD_ENTER, offset=(20, 20), interval=3):
                continue
            if self.match_template_color(REWARD_RECEIVE, offset=(20, 20), interval=3):
                self.device.click(REWARD_RECEIVE)
                confirm_timer.reset()
                continue
            if self.handle_popup_confirm('META_REWARD'):
                # Lock new META ships
                confirm_timer.reset()
                continue
            if self.handle_get_items():
                received = True
                confirm_timer.reset()
                continue
            if self.handle_get_ship():
                received = True
                confirm_timer.reset()
                continue

        logger.info(f'[META-奖励] META奖励领取完成, 领取={received}')
        return received

    def meta_sync_notice_appear(self, interval=0):
        """
        "sync" is the period that you gather meta points to 100% and get a meta ship

        Returns:
            bool: If appear.

        Page:
            in: page_meta
        """
        if self.appear(SYNC_REWARD_NOTICE, threshold=30, interval=interval):
            return True
        elif self.appear(SYNC_TAP, threshold=30, interval=interval):
            return True
        else:
            return False

    def meta_sync_receive(self, skip_first_screenshot=True):
        """
        Args:
            skip_first_screenshot:

        Returns:
            bool: If received.

        Pages:
            in: SYNC_ENTER
            out: SYNC_ENTER if meta ship synced < 100%
                REWARD_ENTER if meta ship synced >= 100%
        """
        logger.hr('Meta同步领取', level=1)
        received = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # End
            # Sync progress >= 100%
            if self.appear(REWARD_ENTER, offset=(20, 20)):
                logger.info('[META-同步] 同步领取在REWARD_ENTER结束')
                break

            if self.config.SERVER == 'en':
                if self.appear(SYNC_ENTER, offset=(20, 20)):
                    logger.info(f'meta_sync_receive ends at SYNC_ENTER')
                    break
                elif self.appear(SYNC_ENTER2, offset=(20, 20)):
                    if not self.meta_sync_notice_appear():
                        logger.info(f'meta_sync_receive ends at SYNC_ENTER2')
                        break
            else:
                if self.appear(SYNC_ENTER, offset=(20, 20)):
                    if not self.meta_sync_notice_appear():
                        logger.info('[META-同步] 同步领取在SYNC_ENTER结束')
                        break

            # Click
            if self.handle_popup_confirm('META_REWARD'):
                # Lock new META ships
                continue
            if self.handle_get_items():
                received = True
                continue
            if self.handle_get_ship():
                received = True
                continue
            if self.appear(SYNC_REWARD_NOTICE, threshold=30, interval=3):
                logger.info(f'[META-同步] 同步奖励通知出现 -> {SYNC_ENTER}')
                self.device.click(SYNC_ENTER)
                received = True
                continue
            if self.config.OpsiAshBeacon_AutoCollectShip:
                # Collect ship automatically
                if self.appear_then_click(SYNC_TAP, offset=(20, 20), interval=3):
                    received = True
                    continue
            else:
                # Collect ship manually, just skip SYNC_TAP
                if self.appear(SYNC_TAP, offset=(20, 20)):
                    logger.info(f"[META-奖励] 跳过舰船收集，因为自动收集舰船已禁用")
                    received = False
                    break

        logger.info(f'[META-同步] META同步领取完成, 领取={received}')
        return received

    def meta_wait_reward_page(self, skip_first_screenshot=True):
        """
        Wait the circle loading animation
        """
        timeout = Timer(2, count=6).start()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning(f'[META-同步] 等待奖励页面超时')
                break
            if self.appear(REWARD_ENTER, offset=(20, 20)):
                logger.info(f'[META-同步] 等待奖励页面在 {REWARD_ENTER} 结束')
                break
            if self.config.SERVER == 'en':
                if self.appear(SYNC_ENTER, offset=(20, 20)):
                    logger.info(f'[META-同步] 等待奖励页面在 {SYNC_ENTER} 结束')
                    break
                elif self.appear(SYNC_ENTER2, offset=(20, 20)):
                    logger.info(f'[META-同步] 等待奖励页面在 {SYNC_ENTER2} 结束')
                    break
            else:
                if self.appear(SYNC_ENTER, offset=(20, 20)):
                    logger.info(f'[META-同步] 等待奖励页面在 {SYNC_ENTER} 结束')
                    break
            if self.appear(SYNC_TAP, offset=(20, 20)):
                logger.info(f'[META-同步] 等待奖励页面在 {SYNC_TAP} 结束')
                break
            if self.meta_sync_notice_appear():
                logger.info('[META-同步] 等待奖励页面在同步红点结束')
                break
            if self.meta_reward_notice_appear():
                logger.info('[META-同步] 等待奖励页面在奖励红点结束')
                break

    def run(self):
        if self.config.SERVER in ['cn', 'en', 'jp']:
            pass
        else:
            logger.info(f'[META-同步] MetaReward不支持 {self.config.SERVER} 服务器，请联系服务器维护者')
            return

        self.ui_ensure(page_meta)
        self.meta_wait_reward_page()

        # Sync rewards
        # "sync" is the period that you gather meta points to 100% and get a meta ship
        if self.meta_sync_notice_appear():
            logger.info('[META-同步] 找到META同步红点或同步按钮')
            self.meta_sync_receive()
        else:
            logger.info('[META-同步] 未找到META同步红点或同步按钮')

        # Meta rewards
        if self.meta_reward_notice_appear():
            logger.info('[META-同步] 找到META奖励红点')
            self.meta_reward_receive()
        else:
            logger.info('[META-同步] 未找到META奖励红点')


class DossierReward(Combat, UI):
    """
    META 档案奖励处理器。

    处理已完成的旧 META 档案中遗留的奖励收取。档案是已结束的
    META 活动，玩家可从中领取之前未收取的奖励。

    核心流程：
        1. 导航至档案 META 页面
        2. 检测是否有可收取的档案奖励红点
        3. 进入奖励界面并逐个收取奖励
    """
    def meta_reward_notice_appear(self):
        """
        Returns:
            bool: If appear.

        Page:
            in: dossier meta page
        """
        self.device.screenshot()
        if self.appear(DOSSIER_REWARD_RECEIVE, offset=(-40, 10, -10, 40), similarity=0.7):
            logger.info('[META-同步] 找到档案奖励红点')
            return True
        else:
            logger.info('[META-同步] 未找到档案奖励红点')
            return False

    def meta_reward_enter(self, skip_first_screenshot=True):
        """
        Pages:
            in: dossier meta page
            out: DOSSIER_REWARD_CHECK
        """
        logger.info('[META-同步] 进入档案奖励')
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(DOSSIER_LIST, offset=(20, 20)):
                self.device.click(DOSSIER_REWARD_ENTER)
                continue

            # End
            if self.appear(DOSSIER_REWARD_CHECK, offset=(20, 20)):
                break

    def meta_reward_receive(self, skip_first_screenshot=True):
        """
        Args:
            skip_first_screenshot:

        Returns:
            bool: If received.

        Pages:
            in: DOSSIER_REWARD_CHECK
            out: DOSSIER_REWARD_CHECK
        """
        logger.hr('档案奖励领取', level=1)
        confirm_timer = Timer(1, count=3).start()
        received = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.match_template_color(DOSSIER_REWARD_RECEIVE, offset=(20, 20), interval=3):
                self.device.click(DOSSIER_REWARD_RECEIVE)
                confirm_timer.reset()
                continue
            if self.handle_popup_confirm('DOSSIER_REWARD'):
                # Lock new META ships
                confirm_timer.reset()
                continue
            if self.handle_get_items():
                received = True
                confirm_timer.reset()
                continue
            if self.handle_get_ship():
                received = True
                confirm_timer.reset()
                continue

            # End
            if not self.appear(DOSSIER_REWARD_RECEIVE, offset=(20, 20)):
                if confirm_timer.reached():
                    break
            else:
                confirm_timer.reset()

        logger.info(f'[META-同步] 档案奖励领取完成, 领取={received}')
        return received

    def run(self):
        if self.config.SERVER in ['cn', 'en', 'jp']:
            pass
        else:
            logger.info(f'[META-同步] MetaReward不支持 {self.config.SERVER} 服务器，请联系服务器维护者')
            return

        from module.os_ash.meta import OpsiAshBeacon
        OpsiAshBeacon(self.config, self.device).ensure_dossier_page()
        if self.meta_reward_notice_appear():
            self.meta_reward_enter()
            self.meta_reward_receive()


class MetaReward(BeaconReward, DossierReward):
    """
    META 奖励统一入口。

    组合 BeaconReward 和 DossierReward，根据 category 参数
    分发到对应的奖励处理流程。

    Args:
        category (str): 奖励类型，'beacon' 表示余烬信标奖励，
                        'dossier' 表示档案奖励
    """
    def run(self, category="beacon"):
        if category == "beacon":
            BeaconReward(self.config, self.device).run()
        elif category == "dossier":
            DossierReward(self.config, self.device).run()
        else:
            logger.info(f'[META-同步] 可能的错误参数 {category}，请联系开发者')
