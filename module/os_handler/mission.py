"""大世界任务管理模块。

处理大世界（Operation Siren）的任务系统，包括任务提交、
任务奖励领取、任务结算界面导航以及月度 Boss 任务的特殊处理。
通过 OCR 和图像匹配检测任务状态，自动完成任务流程。
"""
from datetime import datetime, timedelta

from module.base.decorator import cached_property
from module.base.template import Template
from module.base.timer import Timer
from module.base.utils import *
from module.config.time_source import now as current_time
from module.config.utils import DEFAULT_TIME, get_os_next_reset, get_server_next_update
from module.exception import MapDetectionError
from module.logger import logger
from module.map_detection.utils import fit_points
from module.os.assets import GLOBE_GOTO_MAP
from module.os.globe_detection import GLOBE_MAP_SHAPE
from module.os.globe_operation import GlobeOperation, OSExploreError
from module.os.globe_zone import Zone, ZoneManager
from module.os_handler.assets import *


class MissionAtCurrentZone(Exception):
    """当前海域有任务异常。"""
    pass


class MissionHandler(GlobeOperation, ZoneManager):
    _os_mission_submitted = False
    _os_mission_index = 0

    @cached_property
    def _os_mission_checkout_template(self):
        template = Template(MISSION_CHECKOUT.file)
        MISSION_CHECKOUT.ensure_template()
        template.image = MISSION_CHECKOUT.image
        return template

    def _os_mission_checkout_offsets(self):
        """从当前页匹配任务按钮，避免滚动后仍按固定行号点击。"""
        offsets = []
        for button in self._os_mission_checkout_template.match_multi(self.device.image, similarity=0.78):
            x, y, _, bottom = button.area
            if (abs(x - MISSION_CHECKOUT.area[0]) <= 20 and 190 <= y and bottom <= 650
                    and color_similar(button.color, MISSION_CHECKOUT.color, threshold=30)):
                offsets.append(area_offset((-20, -20, 20, 20),
                                           (x - MISSION_CHECKOUT.area[0], y - MISSION_CHECKOUT.area[1])))
        return sorted(offsets, key=lambda offset: offset[1])

    def _os_find_checkout_offset_skip_monthly_boss(self, checkout_offset, skip=0):
        """
        查找非月度Boss的任务结算行。

        Args:
            checkout_offset (tuple): 初始结算按钮偏移量。
            skip (int): 跳过本轮已延期的未完成委托数量，不计月度 Boss。

        Returns:
            tuple | None: 跳过指定数量的未完成委托和月度 Boss 后，下一任务按钮的偏移。
        """
        if not skip:
            # 没有延期行时保持原有选择逻辑，避免改变其他任务。
            # 直接扫描全屏定位「查看」，不依赖固定行距。
            # 旧实现按 110 像素步进，行距不同时会漏掉后面的任务行。
            for y, _ in self._os_mission_scan_rows(MISSION_CHECKOUT, similarity=0.78):
                row_offset = area_offset(checkout_offset, (0, y - MISSION_CHECKOUT.area[1]))
                if not self.appear(MISSION_MONTHLY_BOSS, offset=row_offset):
                    return row_offset
            return None

        # 延期委托仍留在列表中，超过一页时继续查找后面的委托。
        scrolling = None
        stable = Timer(0.3, count=2)
        timeout = Timer(5, count=10)
        scroll_distance = None
        for _ in self.loop():
            if scrolling is not None:
                anchor, anchor_y, previous = scrolling
                image = self.image_crop((600, 170, 1000, 650), copy=False)
                result = cv2.matchTemplate(image, anchor, cv2.TM_CCOEFF_NORMED)
                _, similarity, _, point = cv2.minMaxLoc(result)
                if similarity >= 0.90:
                    distance = anchor_y - (170 + point[1])
                    if scroll_distance is None or abs(distance - scroll_distance) > 2:
                        scroll_distance = distance
                        stable.reset()
                    elif stable.reached():
                        if distance > 2:
                            skip -= sum(MISSION_CHECKOUT.area[1] + offset[1] + 20 - distance < 190
                                        for offset in previous)
                            skip = max(skip, 0)
                            scrolling = None
                            continue
                        if abs(distance) <= 2:
                            # 拖动后任务行稳定停在原位置，已到列表底部。
                            return None
                else:
                    stable.reset()
                if timeout.reached():
                    raise MapDetectionError('无法确认大世界任务列表滚动位置')
                continue

            offsets = self._os_mission_checkout_offsets()
            eligible = [offset for offset in offsets if not self.appear(MISSION_MONTHLY_BOSS, offset=offset)]
            if skip < len(eligible):
                return eligible[skip]
            if not offsets:
                return None
            y = MISSION_CHECKOUT.area[1] + offsets[-1][1] + 20
            anchor_y = max(170, y - 35)
            anchor = self.image_crop((600, anchor_y, 1000, min(650, y + 45)))
            scrolling = (anchor, anchor_y, eligible)
            scroll_distance = None
            stable.reset()
            timeout.reset()
            self.device.drag((820, 550), (820, 330), name='MISSION_SCROLL')

    def _os_deferred_mission_zones(self):
        """延期记录只保存进不了的委托目标海域，到下次日更自动失效。"""
        state = self.config.cross_get('OpsiDaily.OpsiDaily.DeferredMissions')
        if not isinstance(state, dict):
            return set()
        try:
            until = datetime.fromisoformat(state['until'])
        except (KeyError, TypeError, ValueError):
            return set()
        if until.tzinfo is not None:
            return set()
        if current_time() >= until:
            self.config.cross_set('OpsiDaily.OpsiDaily.DeferredMissions', None)
            return set()
        zones = state.get('zones', [])
        return {zone for zone in zones if type(zone) is int} if isinstance(zones, list) else set()

    def _os_defer_mission_zone(self, zone):
        zones = self._os_deferred_mission_zones() | {zone.zone_id}
        until = get_server_next_update(
            self.config.cross_get('OpsiDaily.Scheduler.ServerUpdate', default='00:00'))
        self.config.cross_set('OpsiDaily.OpsiDaily.DeferredMissions',
                              dict(until=until.isoformat(), zones=sorted(zones)))
        logger.warning(f'每日委托目标海域 {zone} 无法进入，仅延期该海域的委托至 {until}，继续其他委托')

    def _os_return_from_unavailable_mission(self):
        self.ensure_no_zone_pinned()
        self.os_globe_goto_map()


    def get_mission_zone(self):
        """
        获取任务所在的海域。

        Returns:
            Zone: 任务海域对象。
        """
        area = (341, 72, 1217, 648)
        # 黄色 `!` 的点
        image = color_similarity_2d(self.image_crop(area, copy=False), color=(255, 207, 66))
        points = np.array(np.where(image > 235)).T[:, ::-1]
        if not len(points):
            logger.warning('无法在大世界任务地图中找到任务')

        point = fit_points(points, mod=(1000, 1000), encourage=5) + (0, 11)
        # 海域位置
        # (2570, 1694) 是 os_globe_map.png 的形状
        point *= np.array(GLOBE_MAP_SHAPE) / np.subtract(area[2:], area[:2])

        zone = self.camera_to_zone(tuple(point))
        return zone

    def is_in_os_mission(self):
        return self.appear(MISSION_CHECK, offset=(20, 20))

    def os_mission_enter(self, skip_siren_mission=False, skip_first_screenshot=True):
        """
        进入任务列表并领取任务奖励。

        Args:
            skip_siren_mission (bool): 是否跳过塞壬研究任务。
            skip_first_screenshot (bool): 是否跳过第一次截图。

        Returns:
            tuple: MISSION_CHECKOUT 的按钮偏移量。

        Pages:
            in: MISSION_ENTER
            out: MISSION_CHECK
        """
        logger.info('[大世界处理-任务] 进入大世界任务')
        self._os_mission_submitted = False
        checkout_offset = (-20, -20, 20, 20)
        confirm_timer = Timer(2, count=6).start()
        for _ in self.loop():
            # 结束
            # 跳过塞壬研究时按全屏扫描判断剩余任务：任务行可能不含「领取」
            # 或只剩塞壬研究行，按固定窗口判断会误判成「没有任务」而提前退出。
            if self.is_in_os_mission():
                if skip_siren_mission:
                    has_finish = bool(self._os_mission_scan_rows(MISSION_FINISH))
                    has_siren = bool(self._os_mission_scan_rows(MISSION_SIREN_RESEARCH))
                else:
                    has_finish = self.appear(MISSION_FINISH, offset=checkout_offset)
                    has_siren = False
                no_task = (not has_finish and not has_siren
                           and not self.match_template_color(
                               MISSION_CHECKOUT, offset=checkout_offset, similarity=0.78))
            else:
                no_task = False
            if no_task:
                # 未找到任务，等待确认。任务可能加载较慢。
                if confirm_timer.reached():
                    logger.info('[大世界处理-任务] 未找到大世界任务')
                    break
            elif self.is_in_os_mission() \
                    and self.match_template_color(MISSION_CHECKOUT, offset=checkout_offset, similarity=0.78):
                # 找到至少一个任务
                logger.info('[大世界处理-任务] 至少找到一个大世界任务')
                break
            else:
                confirm_timer.reset()

            # 点击
            # 保留海域中断自律并退出战斗后，结算「合计获得奖励」会延迟弹出，
            # 可能到进入任务列表这一步才盖住界面；不处理会一直等不到任务列表
            if self.appear_then_click(AUTO_SEARCH_REWARD, offset=(50, 50), interval=3):
                confirm_timer.reset()
                continue
            if self.appear_then_click(MISSION_ENTER, offset=(200, 5), interval=5):
                confirm_timer.reset()
                continue
            if skip_siren_mission:
                # 扫描全屏比对行坐标，不依赖固定行距，避免漏判导致误领塞壬研究
                if self._os_mission_claim_skip_siren():
                    confirm_timer.reset()
                    continue
                # 剩余任务全是塞壬研究：没有可领取项，退出循环避免空转
                if self._os_mission_scan_rows(MISSION_SIREN_RESEARCH):
                    logger.info('[大世界处理-任务] 剩余任务均为塞壬研究，跳过不领取')
                    break
            else:
                if self.appear_then_click(MISSION_FINISH, offset=checkout_offset, interval=2):
                    self._os_mission_submitted = True
                    confirm_timer.reset()
                    continue

            # 弹窗与结算处理对所有分支生效，跳过塞壬研究时同样需要处理
            if self.handle_popup_confirm('MISSION_FINISH'):
                confirm_timer.reset()
                continue
            if self.handle_map_get_items():
                confirm_timer.reset()
                continue
            if self.handle_info_bar():
                confirm_timer.reset()
                continue

            if self.appear_then_click(GLOBE_GOTO_MAP, offset=(20, 20), interval=2):
                # 意外进入地球仪
                confirm_timer.reset()
                continue
        return checkout_offset

    @cached_property
    def _os_mission_scan_templates(self):
        """任务行扫描用的模板缓存（按钮名 → 已加载图片的 Template）。"""
        templates = {}
        for button in (MISSION_CHECKOUT, MISSION_FINISH, MISSION_SIREN_RESEARCH):
            template = Template(button.file)
            button.ensure_template()
            template.image = button.image
            templates[button.name] = template
        return templates

    def _os_mission_scan_rows(self, button, similarity=0.85):
        """扫描全屏，返回某个按钮所有匹配位置，按 y 升序排列。

        用于在不依赖固定行距的前提下，一次性定位任务列表中的全部目标行，
        避免逐行累加固定偏移量时因行距变化而漏判。

        Args:
            button (Button): 待扫描的按钮，需在 _os_mission_scan_templates 中注册。
            similarity (float): 模板匹配相似度阈值。

        Returns:
            list[tuple[int, int]]: 每个匹配位置的 (y, x)。

        Pages:
            in: MISSION_CHECK
            out: MISSION_CHECK
        """
        template = self._os_mission_scan_templates[button.name]
        rows = [(b.area[1], b.area[0])
                for b in template.match_multi(self.device.image, similarity=similarity)]
        return sorted(rows)

    def _os_mission_claim_skip_siren(self):
        """只领取非塞壬研究任务，跳过所有带「研究」的任务行。

        塞壬研究任务使用黄币兑换紫币，开启「不做塞壬研究任务」后应保持不领取。
        旧实现依赖「命中塞壬行后偏移 +110 像素」的逐行推进，行距变化或塞壬研究
        不在首行时都会漏判，导致误领。本方法扫描全屏后按 y 坐标比对：
        与任意塞壬研究行纵向重叠的「领取」按钮一律跳过。

        Returns:
            bool: 是否点击了某个领取按钮。

        Pages:
            in: MISSION_CHECK
            out: MISSION_CHECK
        """
        siren_rows = self._os_mission_scan_rows(MISSION_SIREN_RESEARCH)
        finish_rows = self._os_mission_scan_rows(MISSION_FINISH)

        for y, _ in finish_rows:
            # 「研究」文字与同一行「领取」按钮的中心纵向相差约 21 像素，取 45 像素容差
            if any(abs(y - siren_y) < 45 for siren_y, _ in siren_rows):
                logger.info('[大世界处理-任务] 跳过塞壬研究任务')
                continue
            # 名称带行号，避免多行领取共用同名按钮时触发点击洪水保护
            target = MISSION_FINISH.move((0, y - MISSION_FINISH.area[1]), name=f'MISSION_FINISH_Y{y}')
            # 补给兑换等可重复任务领取后按钮原样保留，点击需限速：
            # 未到间隔时返回 False，让外层循环先处理确认弹窗与奖励结算
            timer = self.get_interval_timer(target, interval=2, renew=True)
            if not timer.reached():
                continue
            self.device.click(target)
            timer.reset()
            self._os_mission_submitted = True
            logger.info('[大世界处理-任务] 领取非塞壬研究任务')
            return True

        return False

    def os_mission_quit(self):
        """
        退出任务列表。
        """
        logger.info('[大世界处理-任务] 退出大世界任务')
        for _ in self.loop():
            # 结束
            # 有时任务弹窗没有黑色模糊背景
            # MISSION_QUIT 和 is_in_map 同时出现
            if not self.appear(MISSION_QUIT, offset=(20, 20)):
                if self.is_in_map():
                    break
            # 点击
            if self.appear_then_click(MISSION_QUIT, offset=(20, 20), interval=3):
                continue

    def os_get_next_mission(self, skip_siren_mission=False, skip_unavailable=False, mission_index=0):
        """
        获取下一个大世界任务。

        点击 MISSION_CHECKOUT 后，AL 会直接切换到目标海域，而非显示无意义的地图。
        如果已在目标海域，则显示信息栏并关闭任务列表。

        Args:
            skip_siren_mission (bool): 是否跳过塞壬研究任务。
            skip_unavailable (bool): 仅每日任务使用，延期进不了海域的委托并继续后续委托。
            mission_index (int): 本轮已经跳过的未完成委托数量。

        Returns:
            str: pinned_at_mission_zone、already_at_mission_zone、pinned_at_archive_zone，
                mission_zone_unavailable 表示仅延期本条委托；没有更多任务则返回 False。
        """
        checkout_offset = self.os_mission_enter(skip_siren_mission=skip_siren_mission)
        skip_unavailable = skip_unavailable and self.config.task.command == 'OpsiDaily'
        self._os_mission_index = 0 if self._os_mission_submitted else mission_index
        if skip_unavailable:
            checkout_offset = self._os_find_checkout_offset_skip_monthly_boss(
                checkout_offset, skip=self._os_mission_index)
        else:
            checkout_offset = self._os_find_checkout_offset_skip_monthly_boss(checkout_offset)
        if checkout_offset is None:
            logger.info('[大世界处理-任务] 没有更多非月度Boss的大世界任务')
            self.os_mission_quit()
            return False

        if self.is_in_opsi_explore():
            logger.info('[大世界处理-任务] 每月开荒+正在运行，仅接取任务并领取奖励')
            self.os_mission_quit()
            return False

        logger.info('[大世界处理-任务] 接取大世界任务')
        for _ in self.loop():
            # 结束
            if self.is_zone_pinned():
                if self.get_zone_pinned_name() == 'ARCHIVE':
                    logger.info('[大世界处理-任务] 固定在档案区域')
                    self.globe_enter(zone=self.name_to_zone(72))
                    return 'pinned_at_archive_zone'
                else:
                    logger.info('[大世界处理-任务] 固定在任务区域')
                    if skip_unavailable:
                        # 从当前固定海域定位真实目标，不把历史占位海域 72 当成委托身份。
                        self.globe_update()
                        zone = self.get_globe_pinned_zone()
                        if zone.zone_id in self._os_deferred_mission_zones():
                            logger.info(f'每日委托目标海域 {zone} 仍在延期中，继续其他委托')
                            self._os_return_from_unavailable_mission()
                            return 'mission_zone_unavailable'
                        try:
                            self.globe_enter(zone=zone)
                        except OSExploreError:
                            self._os_defer_mission_zone(zone)
                            self._os_return_from_unavailable_mission()
                            return 'mission_zone_unavailable'
                        return 'pinned_at_mission_zone'
                    self.globe_enter(zone=self.name_to_zone(72))
                    return 'pinned_at_mission_zone'
            if self.is_in_map() and self.info_bar_count():
                logger.info('[大世界处理-任务] 已在任务区域')
                return 'already_at_mission_zone'

            # 保留海域中断自律后的结算弹窗也会延迟到这一步才弹出，先点掉
            if self.appear_then_click(AUTO_SEARCH_REWARD, offset=(50, 50), interval=3):
                continue
            if self.appear_then_click(MISSION_CHECKOUT, offset=checkout_offset, interval=2, similarity=0.78):
                continue
            if self.handle_popup_confirm('OS_MISSION_CHECKOUT'):
                # 弹窗：退出当前海域后潜艇将撤退
                continue

    def os_mission_overview_accept(self, skip_siren_mission=False, skip_first_screenshot=True):
        """
        在任务总览中接受所有任务。

        Args:
            skip_siren_mission (bool): 是否跳过塞壬研究任务。
            skip_first_screenshot (bool): 是否跳过第一次截图。

        Returns:
            bool: 所有任务已接受或未找到任务时返回 True，无法接受更多任务时返回 False。

        Pages:
            in: is_in_map
            out: is_in_map
        """
        logger.hr('大世界任务总览接取', level=1)
        # is_in_map
        self.os_map_goto_globe(unpin=False)
        # is_in_globe
        self.ui_click(MISSION_OVERVIEW_ENTER, check_button=MISSION_OVERVIEW_CHECK,
                      offset=(200, 20), retry_wait=3, additional=self.handle_manjuu,
                      skip_first_screenshot=True)

        timeout = 5
        accept_button_timer = Timer(timeout)
        self.interval_timer[MISSION_OVERVIEW_ACCEPT_SINGLE.name] = accept_button_timer
        self.interval_timer[MISSION_OVERVIEW_ACCEPT.name] = accept_button_timer
        # MISSION_OVERVIEW_CHECK
        success = True
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 结束
            if self.handle_manjuu():
                continue
            if self.info_bar_count():
                if skip_siren_mission:
                    logger.info('[大世界处理-任务] 无法接受任务，存在多个同名塞壬研究任务')
                    success = True
                else:
                    logger.info('[大世界处理-任务] 无法接受任务，已达任务数量上限')
                    success = False
                break
            if self.appear(MISSION_OVERVIEW_EMPTY, offset=(20, 20)):
                logger.info('[大世界处理-任务] 无更多任务可接受')
                success = True
                break

            if self.appear_then_click(MISSION_OVERVIEW_ACCEPT, offset=(20, 20), interval=2):
                self.interval_reset(MISSION_OVERVIEW_ACCEPT_SINGLE)
                continue
            if self.appear_then_click(MISSION_OVERVIEW_ACCEPT_SINGLE, offset=(20, 20), interval=2):
                self.interval_reset(MISSION_OVERVIEW_ACCEPT)
                continue

        # is_in_globe
        self.ui_back(appear_button=MISSION_OVERVIEW_CHECK, check_button=self.is_in_globe,
                     skip_first_screenshot=True)
        # is_in_map
        self.os_globe_goto_map()
        return success

    def is_in_opsi_explore(self):
        """
        判断任务每月开荒+是否正在调度中。

        Returns:
            bool: 每月开荒+是否正在调度中。
        """
        enable = self.config.is_task_enabled('OpsiExplore')
        next_run = self.config.cross_get(keys='OpsiExplore.Scheduler.NextRun', default=DEFAULT_TIME)
        next_reset = get_os_next_reset()
        logger.attr('大世界下次重置', next_reset)
        logger.attr('每月开荒+', (enable, next_run))
        # -12 小时以处理夏令时
        # `next_run` 可能在夏令时之前计算，但现在是夏令时
        # 2023-03-14 11:15:28.423 | INFO | [OpsiNextReset] 2023-04-01 03:00:00
        # 2023-03-14 11:15:28.425 | INFO | [OpsiExplore] (True, datetime.datetime(2023, 4, 1, 2, 0))
        # 2023-03-14 11:15:28.426 | INFO | 每月开荒+仍在运行，仅接取任务...
        if enable and next_run < next_reset - timedelta(hours=12):
            logger.info('每月开荒+仍在运行，仅接取任务。每月开荒+访问所有区域时会完成这些任务，不必担心遗漏。')
            return True
        else:
            logger.info('未处于每月开荒+，可以执行大世界每日+')
            return False
