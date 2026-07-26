"""大世界地图基础数据结构。

定义大世界（Operation Siren）的地图数据模型。
大世界的地图使用 OSCampaignMap 类，继承自主线战役的 CampaignMap，
但有以下差异：
- 使用 OSGridInfo 而非 GridInfo 作为格子类型
- 相机视野范围不同（camera_sight = (-4, -1, 3, 3)）
- 地图尺寸通过节点名称动态设置
- 所有格子默认权重为 10

继承自 CampaignMap，复用寻路和地图管理的核心逻辑。
"""

from module.base.utils import *
from module.map.map_base import CampaignMap, camera_2d
from module.map_detection.os_grid import OSGridInfo


class OSCampaignMap(CampaignMap):
    """大世界地图数据结构。

    管理大世界海域的格子信息和寻路逻辑。

    Attributes:
        camera_sight (tuple): 相机视野范围。
        camera_data (list): 相机位置数据列表。
    """
    def __init__(self, name=None):
        super().__init__(name)
        self.camera_sight = (-4, -1, 3, 3)

    @property
    def shape(self):
        return self._shape

    @shape.setter
    def shape(self, scale):
        self._shape = node2location(scale.upper())
        for y in range(self._shape[1] + 1):
            for x in range(self._shape[0] + 1):
                grid = OSGridInfo()
                grid.location = (x, y)
                self.grids[(x, y)] = grid

        # camera_data can be generate automatically, but it's better to set it manually.
        self.camera_data = [location2node(loca) for loca in camera_2d((0, 0, *self._shape), sight=self.camera_sight)]
        self.camera_data_spawn_point = []
        # weight_data set to 10.
        for grid in self:
            grid.weight = 10.

    def update(self, grids, camera, mode='normal'):
        """
        Args:
            grids:
            camera (tuple):
            mode (str): Scan mode, such as 'normal', 'carrier', 'movable'
        """
        offset = np.array(camera) - np.array(grids.center_loca)
        grids.show()

        for grid in grids.grids.values():
            loca = tuple(offset + grid.location)
            if loca in self.grids:
                self.grids[loca].merge(grid)
