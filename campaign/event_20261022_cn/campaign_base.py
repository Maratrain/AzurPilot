from module.campaign.campaign_base import CampaignBase as CampaignBase_
from module.exception import CampaignNameError


class Config:
    MAP_CHAPTER_SWITCH_20241219 = True
    STAGE_ENTRANCE = ['half', '20240725']
    MAP_HAS_MODE_SWITCH = False
    MAP_WALK_USE_CURRENT_FLEET = True
    # 普通关的 type=95 精英共用小型橙色图标，不依赖各自的立绘。
    MAP_SIREN_TEMPLATE = []
    MAP_SIREN_HAS_BOSS_ICON_SMALL = True


class CampaignBase(CampaignBase_):
    def campaign_set_chapter_20241219(self, chapter, stage, mode='combat'):
        """按上下篇切换 T 关卡，SP 使用独立分区。

        Pages:
            in: 任意页面
            out: page_sp
        """
        if mode == 'story' or not self.config.MAP_CHAPTER_SWITCH_20241219:
            return super().campaign_set_chapter_20241219(chapter, stage, mode)

        if chapter == 't':
            if stage in ('1', '2', '3'):
                aside = 'part1'
            elif stage in ('4', '5', '6'):
                aside = 'part2'
            else:
                raise CampaignNameError(f'活动没有关卡 {chapter}{stage}')
        elif chapter == 'ex_sp':
            aside = 'sp'
        else:
            return super().campaign_set_chapter_20241219(chapter, stage, mode)

        self.ui_goto_sp()
        self.campaign_ensure_mode_20241219('combat')
        self.campaign_ensure_aside_20241219(aside)
        self.campaign_ensure_chapter(chapter)
        return True
