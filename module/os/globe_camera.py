"""
大世界全球地图摄像机控制模块。

负责全球地图（Globe Map）上的摄像机操作，包括视角平移、海域定位、
坐标转换以及塞壬要塞搜索等功能。

主要类:
    GlobeCamera: 全球地图摄像机控制类，整合全球地图操作和海域管理。

坐标系说明:
    globe 坐标系: 全球地图的二维坐标系，原点为地图左上角。
    screen 坐标系: 屏幕像素坐标系。
    zone.location: 海域在全球地图坐标系中的位置。

术语:
    全球地图 (Globe Map): 大世界的整体地图视图，包含所有海域。
    海域 (Zone): 全球地图上的一个可进入区域。
    塞壬要塞 (Siren Stronghold): 全球地图上的特殊海域类型，
        以红色漩涡标记，完成可获得特殊奖励。
"""
from module.base.timer import Timer
from module.base.utils import *
from module.exception import GameStuckError
from module.logger import logger
from module.os.assets import *
from module.os.globe_detection import GLOBE_MAP_SHAPE, GlobeDetection
from module.os.globe_operation import GlobeOperation
from module.os.globe_zone import Zone, ZoneManager
from module.os_ash.assets import ASH_QUIT, ASH_SHOWDOWN
from module.os_handler.assets import ACTION_POINT_CANCEL, ACTION_POINT_USE, AUTO_SEARCH_REWARD


class GlobeCamera(GlobeOperation, ZoneManager):
    """全球地图摄像机控制类。

    提供全球地图上的摄像机操控能力，包括视角平移 (swipe)、
    海域聚焦 (focus)、坐标系转换以及塞壬要塞搜索。

    通过组合 GlobeOperation（全球地图操作）和 ZoneManager（海域管理），
    实现从全球地图层面的完整海域导航。

    Attributes:
        globe (GlobeDetection): 全球地图检测器实例。
        globe_camera (tuple[float, float]): 当前摄像机在全球地图坐标系中的位置。
    """
    globe: GlobeDetection
    globe_camera: tuple

    def _globe_init(self):
        """初始化全球地图检测器。

        在进行任何全球地图操作前必须调用此方法。
        仅在首次调用时加载全球地图资源。
        """
        if not hasattr(self, 'globe'):
            self.globe = GlobeDetection(self.config)
            self.globe.load_globe_map()

    def globe_update(self):
        """更新全球地图状态。

        确保当前处于全球地图视图，然后加载全球地图数据并更新
        摄像机位置。自动处理各种弹窗和意外页面跳转。

        Raises:
            GameStuckError: 5 秒内无法进入全球地图视图时抛出。
        """
        timeout = Timer(5, count=10).start()
        while 1:
            if timeout.reached():
                raise GameStuckError

            self.device.screenshot()

            # End
            if self.is_in_globe():
                break

            # A copy of os_map_goto_globe()
            # May accidentally enter map
            if self.appear_then_click(MAP_GOTO_GLOBE, offset=(200, 5), interval=3):
                # Just to initialize interval timer of MAP_GOTO_GLOBE_FOG
                self.appear(MAP_GOTO_GLOBE_FOG, interval=3)
                timeout.reset()
                continue
            # Encountered only in strongholds; AL will not prevent
            # zone exit even with left over exploration rewards in map
            if self.appear_then_click(MAP_GOTO_GLOBE_FOG, interval=3):
                self.interval_reset(MAP_GOTO_GLOBE)
                timeout.reset()
                continue
            if self.handle_map_event():
                timeout.reset()
                continue
            # Popup: AUTO_SEARCH_REWARD appears slowly
            if self.appear_then_click(AUTO_SEARCH_REWARD, offset=(50, 50), interval=3):
                timeout.reset()
                continue
            # Popup: Leaving current zone will terminate meowfficer searching.
            # Popup: Leaving current zone will retreat submarines
            # Searching reward will be shown after entering another zone.
            if self.handle_popup_confirm('GOTO_GLOBE'):
                timeout.reset()
                continue
            # Don't know why but AL just entered META page
            if self.appear(ASH_SHOWDOWN, offset=(20, 20), interval=3):
                self.device.click(ASH_QUIT)
                timeout.reset()
                continue
            # Action point popup
            if self.appear(ACTION_POINT_USE, offset=(20, 20), interval=3):
                self.device.click(ACTION_POINT_CANCEL)
                timeout.reset()
                continue

            logger.warning('[大世界-地球仪] 尝试执行globe_update()，但不在大世界地球仪地图中')
            continue

        self._globe_init()
        self.globe.load(self.device.image)
        self.globe_camera = self.globe.center_loca
        center = self.camera_to_zone(self.globe.center_loca)
        logger.attr('地球仪中心', center.zone_id)

    def globe_swipe(self, vector, box=(20, 220, 980, 620)):
        """
        Args:
            vector (tuple, np.ndarray): float
            box (tuple): Area that allows to swipe.

        Returns:
            bool: if camera moved.
        """
        name = 'GLOBE_SWIPE_' + '_'.join([str(int(round(x))) for x in vector])
        if np.linalg.norm(vector) <= 25:
            logger.warning(f'地球仪滑动过短: {vector}')
            vector = np.sign(vector) * 25

        if self.config.DEVICE_CONTROL_METHOD == 'minitouch':
            distance = self.config.MAP_SWIPE_MULTIPLY_MINITOUCH
        elif self.config.DEVICE_CONTROL_METHOD == 'MaaTouch':
            distance = self.config.MAP_SWIPE_MULTIPLY_MAATOUCH
        else:
            distance = self.config.MAP_SWIPE_MULTIPLY
        vector = np.array(distance) * vector

        vector = -vector
        self.device.swipe_vector(vector, name=name, box=box)
        self.device.sleep(0.3)

        self.globe_update()

    def globe_wait_until_stable(self):
        """等待全球地图摄像机稳定。

        持续更新全球地图状态，直到摄像机位置不再发生变化。
        期间会处理海域固定弹窗。
        """
        prev = self.globe_camera
        interval = Timer(1)
        confirm = Timer(0.5, count=1).start()
        for _ in range(10):
            if not interval.reached():
                interval.wait()
            interval.reset()

            self.globe_update()

            # End
            if np.linalg.norm(np.subtract(self.globe_camera, prev)) < 10:
                if confirm.reached():
                    logger.info('[大世界-地球仪] 地球仪地图已稳定')
                    break
            else:
                confirm.reset()

            if self.handle_zone_pinned():
                continue

            prev = self.globe_camera

    def globe2screen(self, points):
        """将全球地图坐标转换为屏幕坐标。

        Args:
            points (np.ndarray): 全球地图坐标点数组。

        Returns:
            np.ndarray: 对应的屏幕坐标点数组。
        """
        points = np.array(points) - self.globe_camera + self.globe.homo_center
        return self.globe.globe2screen(points).round()

    def screen2globe(self, points):
        """将屏幕坐标转换为全球地图坐标。

        Args:
            points (np.ndarray): 屏幕坐标点数组。

        Returns:
            np.ndarray: 对应的全球地图坐标点数组。
        """
        points = self.globe.screen2globe(points).round()
        return points - self.globe.homo_center + self.globe_camera

    def zone_to_button(self, zone):
        """
        Args:
            zone (Zone):

        Returns:
            Button:
        """
        pinned = self.globe2screen([zone.location])[0]
        # pinned is the bottom left corner of where its actually pinned.
        area = area_offset((0, -10, 16, 0), offset=pinned)
        button = Button(area=area, color=(), button=area, name=f'ZONE_{zone.zone_id}')
        return button

    def globe_in_sight(self, zone, swipe_limit=(620, 340), sight=(20, 220, 980, 620)):
        """将目标海域平移到屏幕视野范围内。

        如果目标海域不在指定视野区域内，通过反复平移摄像机直到其可见。

        Args:
            zone (str, int, Zone): 海域名称（CN/EN/JP/TW）、海域 ID 或 Zone 实例。
            swipe_limit (tuple[int, int]): 单次平移的最大像素距离限制。
            sight (tuple[int, int, int, int]): 屏幕上的有效视野区域 (x1, y1, x2, y2)。
        """
        zone = self.name_to_zone(zone)
        # logger.info(f'Globe in_sight: {zone}')

        while 1:
            if point_in_area(self.globe2screen([zone.location])[0], area=sight):
                break

            area = (400, 200, GLOBE_MAP_SHAPE[0] - 400, GLOBE_MAP_SHAPE[1] - 250)
            loca = point_limit(zone.location, area=area)
            vector = np.array(loca) - self.globe_camera
            vector = vector / self.config.OS_GLOBE_SWIPE_MULTIPLY
            swipe = tuple(np.min([np.abs(vector), swipe_limit], axis=0) * np.sign(vector))
            self.globe_swipe(swipe)

    def get_globe_pinned_zone(self):
        """
        Returns:
            Zone:
        """
        location = self.screen2globe([ZONE_PINNED.button[:2]])[0] + (0, 5)
        return self.camera_to_zone(location)

    def globe_wait_until_zone_pinned(self, zone, skip_first_screenshot=True):
        """
        Args:
            zone (str, int, Zone): Name in CN/EN/JP/TW, zone id, or Zone instance.
            skip_first_screenshot:

        Returns:
            bool: True if zone pinned, False if timeout
        """
        zone = self.name_to_zone(zone)
        timeout = Timer(5, count=5).start()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()
                self.globe_update()

            if self.is_zone_pinned():
                if self.get_globe_pinned_zone() == zone:
                    logger.attr('固定海域', zone)
                    return True
            if timeout.reached():
                logger.warning('[大世界-地球仪] 等待区域固定超时')
                return False

    def globe_focus_to(self, zone):
        """
        Focus to a zone in globe view
        self.globe_update() needs to be called first

        Args:
            zone (str, int, Zone): Name in CN/EN/JP/TW, zone id, or Zone instance.

        Pages:
            in: IN_GLOBE
            out: IN_GLOBE, zone selected, ZONE_ENTRANCE
        """
        zone = self.name_to_zone(zone)
        logger.info(f'[大世界-地球仪] 聚焦到: {zone.zone_id}')

        while 1:
            if self.handle_zone_pinned():
                self.globe_update()
                continue

            # Insight
            self.globe_in_sight(zone)
            # Click zone
            button = self.zone_to_button(zone)
            self.device.click(button)
            # Wait until zone pinned
            if self.globe_wait_until_zone_pinned(zone):
                break

    def _globe_predict_stronghold(self, zone):
        """
        Predict if this zone has siren stronghold.
        `self.globe_in_sight(zone)` must be called before calling this method.

        Args:
            zone (str, int, Zone): Name in CN/EN/JP/TW, zone id, or Zone instance.

        Returns:
            bool:
        """
        zone = self.name_to_zone(zone)
        # The center of red whirlpool, on 2D map.
        location = zone.location + (-9.5, -12.5)
        # Area around the center, on 2D map.
        location = [location - (4, 4), location + (4, 4)]
        # Area around the center, on screen.
        screen = self.globe2screen(location).flatten().round()
        screen = np.round(screen).astype(int).tolist()
        # Average color of whirlpool center
        center = self.image_crop(screen, copy=False)
        center = np.array([[cv2.mean(center), ], ]).astype(np.uint8)
        h, s, v = rgb2hsv(center)[0][0]
        # hsv usually to be (338, 74.9, 100)
        if 285 < h <= 360 and s > 45 and v > 45:
            return True
        else:
            return False

    def _find_siren_stronghold(self, zones):
        """
        self.globe_update() needs to be called first

        Args:
            zones (SelectGrids): A group of zones to search from.

        Returns:
            zone: Zone that has siren stronghold, or None if not found.

        Pages:
            in: in_globe
            out: in_globe, is_zone_pinned() if found.
        """
        sight = (20, 220, 980, 620)
        while zones:
            prev = self.camera_to_zone(self.globe_camera)
            zone = zones.sort_by_camera_distance(prev.location)[0]
            logger.info(f'[大世界-地球仪] 查找塞壬要塞 around {zone}')
            self.globe_in_sight(zone, sight=sight)

            to_check = zones.filter(lambda z: point_in_area(self.globe2screen([z.location])[0], area=sight))
            for zone in to_check:
                if self._globe_predict_stronghold(zone):
                    logger.info(f'[大世界-地球仪] 区域 {zone.zone_id} 是塞壬要塞')
                    self.globe_focus_to(zone)
                    if self.get_zone_pinned_name() == 'STRONGHOLD':
                        logger.info('[大世界-地球仪] 确认为塞壬要塞')
                        return zone
                    else:
                        logger.warning('[大世界-地球仪] 不是塞壬要塞，继续搜索')
                        self.ensure_no_zone_pinned()
                else:
                    logger.info(f'[大世界-地球仪] 区域 {zone.zone_id} 不是塞壬要塞')

            zones = zones.delete(to_check)

        logger.info('[大世界-地球仪] 查找塞壬要塞完成')
        return None

    def find_siren_stronghold(self):
        """
        Returns:
            zone: Zone that has siren stronghold, or None if not found.

        Pages:
            in: in_globe
            out: in_globe, is_zone_pinned() if found.
        """
        logger.hr(f'[大世界-地球仪] 查找塞壬要塞', level=1)
        region = self.camera_to_zone(self.globe_camera).region
        order = [1, 2, 4, 3]
        if region not in order:
            # Camera may focus on region 5, select the nearest non-region-5 zone
            zones = self.zones.delete(self.zones.select(region=5)) \
                .delete(self.zones.select(is_port=True)) \
                .sort_by_camera_distance(self.globe_camera)
            region = zones[0].region

        index = order.index(region)
        order = order * 2
        order = order[index:index + 4]
        for region in order:
            logger.hr(f'[大世界-地球仪] 查找塞壬要塞 in region {region}', level=2)
            zones = self.zones.select(region=region, is_port=False)
            result = self._find_siren_stronghold(zones)
            if result is not None:
                return result

        logger.info('[大世界-地球仪] 没有更多塞壬要塞')
        return None
