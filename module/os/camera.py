"""大世界相机控制模块。

管理大世界（Operation Siren）地图的相机移动和视图更新。

大世界的相机系统与主线战役不同：
- 使用 Homography（单应性变换）而非 Perspective（透视检测）
- 固定的存储参数用于网格检测
- 滑动区域和边界与主线战役不同
- 使用 OSGrid 而非 Grid 进行网格检测

继承自 OSMapOperation 和 Camera，组合了大世界地图操作和相机控制能力。
"""

import cv2
import numpy as np

from module.base.button import Button
from module.base.decorator import cached_property
from module.exception import MapDetectionError
from module.logger import logger
from module.map.camera import Camera
from module.map.map_base import location2node, location_ensure
from module.map_detection.os_grid import OSGrid
from module.map_detection.view import View
from module.os.map_operation import OSMapOperation
from module.os.radar import Radar


class OSCamera(OSMapOperation, Camera):
    """大世界相机控制器。

    管理大世界地图的相机位置、视图更新和坐标转换。

    Attributes:
        radar (Radner): 雷达对象，用于检测大世界中的目标。
        fleet_current (tuple): 当前舰队位置。
    """
    radar: Radar
    fleet_current: tuple

    def _map_swipe(self, vector, box=(239, 128, 993, 628)):
        return super()._map_swipe(vector, box=box)

    def _view_init(self):
        if not hasattr(self, 'view'):
            storage = ((10, 7), [(110.307, 103.657), (1012.311, 103.657), (-32.959, 600.567), (1113.057, 600.567)])
            view = View(self.config, mode='os', grid_class=OSGrid)
            view.detector_set_backend('homography')
            view.backend.load_homography(storage=storage)
            self.view = view

    @cached_property
    def radar(self):
        """
        Returns:
            Radar:
        """
        return Radar(self.config)

    def predict_radar(self):
        """
        Scan radar and merge it into map
        """
        self.radar.predict(self.device.image)
        self.radar.show()

    def grid_is_in_sight(self, grid, camera=None, sight=None):
        location = location_ensure(grid)
        camera = location_ensure(camera) if camera is not None else self.camera
        if sight is None:
            sight = self.map.camera_sight

        diff = np.array(location) - camera
        if diff[1] > sight[3]:
            y = diff[1] - sight[3]
        elif diff[1] < sight[1]:
            y = diff[1] - sight[1]
        else:
            y = 0
        if diff[0] > sight[2]:
            x = diff[0] - sight[2]
        elif diff[0] < sight[0]:
            x = diff[0] - sight[0]
        else:
            x = 0
        return x == 0 and y == 0

    def _limit_camera_location(self, location):
        """将大世界摄像机坐标限制在海域可容纳视角的范围内。

        大世界四角缺少边缘线时，滑动预测可能把摄像机推到图外。
        可容纳范围与 camera_2d() 生成 camera_data 的范围一致。
        """
        shape = getattr(self.map, 'shape', None)
        sight = getattr(self.map, 'camera_sight', None)
        if not shape or not sight:
            return tuple(location)

        location = tuple(location)
        left, upper, right, lower = sight

        def limit(value, size, start, end):
            if size <= -start:
                return size // 2
            return min(max(value, -start), size - end)

        return (
            limit(int(location[0]), int(shape[0]), int(left), int(right)),
            limit(int(location[1]), int(shape[1]), int(upper), int(lower)),
        )

    # def ensure_edge_insight(self, reverse=False, preset=None, swipe_limit=(4, 3)):
    #     return super().ensure_edge_insight(reverse=reverse, preset=preset, swipe_limit=swipe_limit)
    #
    # def focus_to(self, location, swipe_limit=(4, 3)):
    #     return super().focus_to(location, swipe_limit=swipe_limit)

    def _get_map_outside_button(self):
        """
        Returns:
            Button: Click outside of map.
        """
        for _ in range(2):
            if self.view.left_edge:
                edge = self.view.backend.left_edge
                area = (113, 185, edge.get_x(290), 290)
            elif self.view.right_edge:
                edge = self.view.backend.right_edge
                area = (edge.get_x(360), 360, 1280, 560)
            else:
                logger.info('[大世界-相机] 没有左边缘或右边缘')
                self.ensure_edge_insight()
                continue

            button = Button(area=area, color=(), button=area, name='MAP_OUTSIDE')
            return button

    def update_os(self):
        """
        Similar to `Camera.update()`, but for OPSI.
        """
        # self.device.screenshot()
        self._view_init()

        try:
            self.view.load(self.device.image)
        except (MapDetectionError, AttributeError, cv2.error) as e:
            logger.warning(e)
            logger.warning('[大世界-相机] 假设摄像机聚焦在格子中心')

            def empty(*args, **kwargs):
                pass

            backup, self.view.backend.load = self.view.backend.load, empty
            self.view.backend.homo_loca = (53, 60)
            self.view.backend.left_edge = False
            self.view.backend.right_edge = False
            self.view.backend.lower_edge = False
            self.view.backend.upper_edge = False
            self.view.load(self.device.image)
            self.view.backend.load = backup

    def convert_radar_to_local(self, location):
        """
        Converts the coordinate on radar to the coordinate of local map view,
        also handles a rare game bug.

        Usually, OPSI camera focus on current fleet, which is (5, 4) in local view.
        The convert should be `local = view[np.add(radar, view.center_loca)]`
        However, Azur Lane may bugged, not focusing current.
        In this case, the convert should base on fleet position.

        Args:
            location: (x, y), Position on radar.

        Returns:
            OSGrid: Grid instance in self.view
        """
        location = location_ensure(location)

        fleets = self.view.select(is_current_fleet=True)
        if fleets.count == 1:
            center = fleets[0].location
        elif fleets.count > 1:
            logger.warning(f'[大世界-相机] 雷达转换到本地时发现多个当前舰队: {fleets}')
            fleets = fleets.sort_by_camera_distance(self.view.center_loca)
            center = fleets[0].location
            logger.warning(
                f'假设距离摄像机中心最近的舰队为当前舰队: {location2node(center)}')
        else:
            logger.warning(f'[大世界-相机] 雷达转换到本地时未找到当前舰队, '
                           f'假设摄像机中心为当前舰队: {location2node(self.view.center_loca)}')
            center = self.view.center_loca

        try:
            local = self.view[np.add(location, center)]
        except KeyError:
            logger.warning(f'[大世界-相机] 雷达转换到本地时目标格子不在本地视野中, '
                           f'假设摄像机中心为当前舰队: {location2node(self.view.center_loca)}')
            center = self.view.center_loca
            local = self.view[np.add(location, center)]

        logger.info(
            f'[大世界-相机] 雷达 {location} -> 本地 {location2node(local.location)} '
            f'(舰队={location2node(center)})'
        )
        return local
