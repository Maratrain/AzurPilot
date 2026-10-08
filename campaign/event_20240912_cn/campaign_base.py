from module.campaign.assets import SWITCH_20241219_COMBAT, SWITCH_20241219_STORY
from module.campaign.campaign_base import CampaignBase as CampaignBase_
from module.campaign.campaign_ui import MODE_SWITCH_1, MODE_SWITCH_2, MODE_SWITCH_20241219, ModeSwitch
from module.exception import CampaignNameError
from module.logger import logger
from module.ui.ui import page_event

MODE_SWITCH_20240912 = ModeSwitch('Mode_switch_20240912', is_selector=True)
MODE_SWITCH_20240912.add_state('combat', SWITCH_20241219_COMBAT, offset=(444, 4))
MODE_SWITCH_20240912.add_state('story', SWITCH_20241219_STORY, offset=(444, 4))


class CampaignBase(CampaignBase_):
    def campaign_ensure_mode(self, mode='normal'):
        """
        Args:
            mode (str): 'normal', 'hard', 'ex', 'story'

        Returns:
            bool: If mode changed.
        """
        # event_20240912_cn has two mode switches at bottom
        # The classic one, MODE_SWITCH_* is at bottom-left,
        # and MODE_SWITCH_20240912 is at bottom-middle
        if mode == "story":
            MODE_SWITCH_20240912.set('story', main=self)
        elif mode in ['normal', 'hard', 'ex']:
            # First switch to combat mode and then select Hard or Normal.
            MODE_SWITCH_20240912.set('combat', main=self)
            super().campaign_ensure_mode(mode)

    def campaign_set_chapter_20241219(self, chapter, stage, mode='combat'):
        """按当前选关页选择首发或复刻的导航布局。

        同一地图目录会被不同服务器和复刻活动复用，不能由服务器决定布局。
        新布局的模式按钮位于左下角；首发布局保留普通／困难开关。

        Args:
            chapter (str): 章节标识，如 'a'、'c'、'ex_sp'。
            stage (str): 关卡编号。
            mode (str): 战役模式。

        Returns:
            bool: 新布局导航已处理时返回 True；旧布局交给后续活动分支。

        Raises:
            CampaignNameError: 布局尚未识别，交给选关循环获取新截图重试。

        Pages:
            in: 任意页面
            out: page_event
        """
        self.ui_goto_event()
        if MODE_SWITCH_20241219.appear(main=self):
            has_aside = True
        elif MODE_SWITCH_1.appear(main=self) or MODE_SWITCH_2.appear(main=self):
            has_aside = False
        else:
            # 页面动画中不猜测布局，避免把新版作战模式当作旧困难开关反复点击。
            raise CampaignNameError

        logger.attr('活动选关布局', '侧边栏' if has_aside else '旧版模式开关')
        self.config.override(
            MAP_CHAPTER_SWITCH_20241219=has_aside,
            MAP_HAS_MODE_SWITCH=has_aside and chapter in ['a', 'b', 'c', 'd'],
        )
        return super().campaign_set_chapter_20241219(chapter, stage, mode)

    def handle_exp_info(self):
        # Random background of hits EXP_INFO_B
        if self.ui_page_appear(page_event):
            return False
        return super().handle_exp_info()
