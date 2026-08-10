"""大世界敌人搜索处理器。

继承标准敌人搜索处理器，针对大世界地图场景进行适配。
提供大世界地图内状态检测（含雾天地图识别）以及地图按钮
滑入动画的等待逻辑，确保 UI 元素就绪后再进行后续操作。
"""
from module.handler.enemy_searching import EnemySearchingHandler as EnemySearchingHandler_
from module.logger import logger
from module.os.assets import MAP_GOTO_GLOBE_FOG
from module.os_handler.assets import AUTO_SEARCH_REWARD, IN_MAP, ORDER_ENTER


class EnemySearchingHandler(EnemySearchingHandler_):
    def is_in_map(self):
        if IN_MAP.match_luma(self.device.image, offset=(200, 5)):
            return True

        if self.appear(IN_MAP, offset=(200, 5), interval=0, similarity=0.8):
            return True

        if self.match_template_color(MAP_GOTO_GLOBE_FOG, offset=(5, 5)):
            return True

        return False

    def wait_os_map_buttons(self):
        """
        When entering a os map, radar and buttons slide out from the right.
        Wait until they slide to the final position.
        """
        for _ in self.loop(timeout=1):
            if self.appear(ORDER_ENTER, offset=(20, 20)):
                break
            # A game bug that AUTO_SEARCH_REWARD from the last cleared zone popups
            if self.appear_then_click(AUTO_SEARCH_REWARD, offset=(50, 50), interval=3):
                continue
        else:
            logger.warning('[大世界处理-搜索] 大世界地图按钮等待超时，假设已等待完成')
