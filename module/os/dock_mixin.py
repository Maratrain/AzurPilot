from module.logger import logger
from module.retire.dock import CARD_GRIDS, Dock


class DockMixin(Dock):
    """
    船坞操作Mixin，用于大世界舰队配队
    
    提供船坞界面中的舰船选择功能，支持通过网格索引选择指定位置的舰船
    继承自Dock类，提供dock_favourite_set等方法
    """
    
    def dock_select_ship_at_grid(self, grid_index):
        """
        选择船坞网格中指定位置的舰船
        
        Args:
            grid_index: 网格索引 0-13
                       0-6: 第一排 (从左到右)
                       7-13: 第二排 (从左到右)
                       
        Returns:
            bool: 是否成功选择舰船
        """
        if grid_index < 0 or grid_index >= 14:
            logger.warning(f"[大世界] 无效的网格索引: {grid_index}")
            return False
        
        if grid_index < 7:
            x = grid_index
            y = 0
        else:
            x = grid_index - 7
            y = 1
        
        button = CARD_GRIDS[(x, y)]
        self.device.click(button)
        logger.info(f"[大世界] 选择船坞第 {y+1} 排第 {x+1} 艘舰船")
        return True
