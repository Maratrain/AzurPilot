"""扫描全部指挥喵的天赋（工具Plus 评分任务的第四种取图方式）。

与 ``screenshot`` / ``device`` 两种方式不同，本模式**自己操作游戏**：
进入指挥喵页面的猫窝列表，逐只选中猫 -> 打开「天赋」页 -> 截图识别 -> 返回，
再小步滑动列表覆盖后面的猫，最后把每只猫的天赋交给评分引擎。

实机量测结论（1280×720）：

- 猫窝列表 4 列 × 3 行，列中心 784/914/1044/1174（列距 130），
  行中心 185/331/477（行距 146）。
- 点卡片即选中该猫，「天赋」页签随后显示**这只猫**的天赋。
- 「天赋」页签未激活时模板匹配 1.000，激活后降到 0.44，
  因此可以用它的出现/消失判断页面切换是否完成，不需要额外的页面资源。
- 天赋名自带等级（每条天赋线的 1/2/3 级名字不同），所以只 OCR 天赋名即可，
  不必识别图标左下角的罗马数字徽章。
- 返回箭头可以从天赋页回到猫窝列表，且**选中状态与滚动位置都会保留**。

注意：左侧「陪玩」页是消耗材料给猫涨经验的功能页，底部有破坏性的「确认」按钮，
本模块**不进入该页、也不点击任何确认按钮**。
"""

import time

import numpy as np

from module.base.button import ButtonGrid
from module.base.timer import Timer
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.meowfficer.assets import MEOWFFICER_TALENT_TAB
from module.meowfficer.base import MeowfficerBase
from module.meowfficer.score import Talent
from module.meowfficer.scan_utils import _crop, _mean_diff, parse_level, pick_cat_name, scroll_offset
from module.meowfficer.score_ocr import recognize
from module.ui.assets import MEOWFFICER_GOTO_DORMMENU

# 猫窝卡片网格（实机量测）
MEOWFFICER_CATTERY_GRID = ButtonGrid(
    origin=(784, 185), delta=(130, 146), button_shape=(80, 80), grid_shape=(4, 3),
    name='MEOWFFICER_CATTERY_GRID')

# 左下角「当前显示的猫」名字与等级区域。
# 右边界刻意停在 640：再往右会和右侧天赋面板的左缘（约 690）重叠，
# 实测会把天赋名（如「不动如山」）读成猫名。
CURRENT_CAT_AREA = (100, 565, 640, 618)
# 天赋面板的完整范围（含边距）。识别天赋时只在这一块上跑 OCR：
# recognize() 会把图放大 3 倍再跑两个变体，整屏 1280×720 要 4 秒左右，
# 只取面板能省掉约 2/3 的计算量。范围要比面板稍大，别把最下面半截那行切掉。
TALENT_OCR_AREA = (686, 78, 1279, 615)
# 猫窝列表面板 / 天赋列表面板：只取面板做画面稳定性比较，避免立绘动画干扰
CATTERY_PANEL_AREA = (700, 110, 1275, 565)
TALENT_PANEL_AREA = (690, 90, 1275, 585)

# 「天赋」页签模板匹配的搜索偏移
TALENT_TAB_OFFSET = 10
# 单只猫最多滑动几次以抓全天赋
MAX_TALENT_SWIPES = 4
# 猫窝一屏可见的内容高度：3 行 × 行距 146。整屏前进才能做到「每只猫只访问一次」，
# 从而不需要按内容去重（指挥喵可以重名，按内容合并会丢猫）
CATTERY_SCREEN_HEIGHT = 438
# 猫窝单次小步滑动距离：实机滚动量约为滑动距离的 1.4~1.5 倍，
# 所以一屏要滑 3 次左右；这里按**实测位移**累加，不依赖这个倍率
CATTERY_SWIPE_STEP = 120
# 两次截图的平均像素差小于该值即认为画面已稳定
STABLE_TOLERANCE = 3.0


class MeowfficerScanner(MeowfficerBase):
    """自动遍历猫窝并识别每只猫的天赋。

    只负责「取图 + 识别」，评分与报告由 :class:`~module.meowfficer.score_task.MeowfficerScore`
    负责，保持这个类没有配置耦合。

    Attributes:
        scanned (list): ``(猫名, [Talent, ...])``，按扫描顺序。
    """

    def __init__(self, config, device=None, task=None):
        super().__init__(config, device, task)
        self.scanned = []

    # ------------------------------------------------------------------
    # 基础等待
    # ------------------------------------------------------------------

    def _wait_talent_tab(self, appear: bool, timeout: float = 10.0) -> bool:
        """等待「天赋」页签出现或消失。

        页签未激活（还在猫窝列表）时模板得分 1.000，激活（已进天赋页）后降到 0.44，
        所以「消失」就代表天赋页已经打开。

        Args:
            appear: True 等它出现（回到列表），False 等它消失（进入天赋页）。
            timeout: 超时秒数。

        Returns:
            bool: 是否等到了期望状态。
        """
        timer = Timer(timeout, count=int(timeout / 0.3) + 3).start()
        while 1:
            self.device.screenshot()
            matched = self.appear(MEOWFFICER_TALENT_TAB, offset=TALENT_TAB_OFFSET)
            if matched == appear:
                return True
            if timer.reached():
                logger.warning(f'[指挥喵-扫描] 等待天赋页签{"出现" if appear else "消失"}超时')
                return False

    def _wait_stable(self, area: tuple, timeout: float = 4.0, tolerance: float = STABLE_TOLERANCE) -> bool:
        """等待画面稳定：连续两次截图的面板区域平均差小于阈值。

        Args:
            area: 参与比较的面板区域。
            timeout: 超时秒数。
            tolerance: 平均像素差阈值。

        Returns:
            bool: 是否判定为稳定。
        """
        timer = Timer(timeout, count=int(timeout / 0.2) + 3).start()
        last = None
        while 1:
            self.device.screenshot()
            current = _crop(self.device.image, area).copy()
            if last is not None and _mean_diff(last, current) < tolerance:
                return True
            last = current
            if timer.reached():
                logger.debug('[指挥喵-扫描] 画面稳定等待超时，按当前画面继续')
                return False
            time.sleep(0.2)

    # ------------------------------------------------------------------
    # 页面操作
    # ------------------------------------------------------------------

    def _ensure_cattery(self) -> None:
        """确保停在猫窝列表。

        刻意**不使用** ``ui_ensure``：实测部分客户端的主界面资源与仓库不一致
        （``MAIN_GOTO_FLEET`` 只有 0.24），而 ``ui_ensure`` 在页面未知时会
        **停掉并重启游戏**，对用户极不友好。这里只用「指挥喵页」与「生活区页」
        自身的资源做两步导航（实测 0.998 / 0.990），都识别不到就交给用户手动打开。

        Raises:
            RequestHumanTakeover: 无法识别当前页面时给出可操作的提示。
        """
        from module.ui.assets import (DORMMENU_CHECK, DORMMENU_GOTO_MEOWFFICER, MAIN_GOTO_DORMMENU,
                                      MEOWFFICER_CHECK)

        timer = Timer(45, count=60).start()
        blind_clicks = 0
        self.device.stuck_record_clear()
        with self.device.stuck_timeout_override(image_stuck=180):
            while 1:
                self.device.screenshot()
                # 已在猫窝列表：天赋页签可见即代表在列表上
                if self.appear(MEOWFFICER_TALENT_TAB, offset=TALENT_TAB_OFFSET):
                    return
                # 在指挥喵页的其它视图（天赋/陪玩）。注意不能用 MEOWFFICER_CHECK 判断：
                # 它匹配的是页面标题文字，猫窝页是「指挥喵」、天赋页变成「天赋」，会识别不到。
                # 返回箭头才是所有指挥喵子视图都有的标志（实测 0.995）。
                if self.appear(MEOWFFICER_GOTO_DORMMENU, offset=TALENT_TAB_OFFSET):
                    if self.appear_then_click(MEOWFFICER_GOTO_DORMMENU, offset=TALENT_TAB_OFFSET, interval=3):
                        logger.info('[指挥喵-扫描] 从子视图返回猫窝列表')
                        time.sleep(1.0)
                        continue
                    # 页面首入可能弹出说明弹窗
                    if self.meow_additional():
                        continue
                elif self.appear(DORMMENU_CHECK, offset=(30, 30)):
                    # 在生活区页，点「指挥喵」卡片进入
                    if self.appear_then_click(DORMMENU_GOTO_MEOWFFICER, offset=(30, 30), interval=3):
                        logger.info('[指挥喵-扫描] 从生活区进入指挥喵')
                        time.sleep(1.5)
                        continue
                elif blind_clicks < 2:
                    # 页面未知：大概率在主界面，但部分客户端主界面资源与仓库不一致
                    # （实测 MAIN_GOTO_FLEET 只有 0.24，识别不出来），只能盲点一次底部「生活区」。
                    # 限制次数，避免在别的页面上乱点。
                    logger.info('[指挥喵-扫描] 未识别到已知页面，尝试点击底部「生活区」')
                    self.device.click(MAIN_GOTO_DORMMENU)
                    blind_clicks += 1
                    time.sleep(1.5)
                    continue
                if timer.reached():
                    logger.warning('[指挥喵-扫描] 页面识别情况：'
                                   f'猫窝页签={self.appear(MEOWFFICER_TALENT_TAB, offset=TALENT_TAB_OFFSET)} '
                                   f'返回箭头={self.appear(MEOWFFICER_GOTO_DORMMENU, offset=TALENT_TAB_OFFSET)} '
                                   f'指挥喵页={self.appear(MEOWFFICER_CHECK, offset=TALENT_TAB_OFFSET)} '
                                   f'生活区页={self.appear(DORMMENU_CHECK, offset=(30, 30))}')
                    raise RequestHumanTakeover(
                        '没能进入「指挥喵」页面：请先在游戏里打开 生活区 → 指挥喵，再运行本任务')
                time.sleep(0.5)

    def _read_current_cat(self, ocr) -> tuple:
        """读左下角「当前显示的猫」名字与等级。

        猫名可能是玩家自定义的，所以这里不强行匹配天赋库里的猫名，
        只做去噪后原样返回，用于去重与日志。等级用来区分**同名猫**
        （这一窝就有 3 只「潜艇参谋」、4 只「潜艇火猫」）。

        Args:
            ocr: 已初始化的 OCR 实例。

        Returns:
            ``(猫名, 等级)``；猫名读不到返回空串，等级读不到返回 ``None``。
        """
        from module.meowfficer.score_ocr import _iter_det_results

        image = _crop(self.device.image, CURRENT_CAT_AREA)
        image = self._crop_scale(image)
        try:
            results = ocr.det(image)
        except Exception as e:
            logger.warning(f'[指挥喵-扫描] 猫名识别失败：{e}')
            return '', None

        texts = [text for text, _score in _iter_det_results(results)]
        name = pick_cat_name(texts)
        level = parse_level(texts)
        # 注意：指挥喵**可以自定义名字**，自定义名完全可能和天赋重名
        # （用户就有一只猫叫「不动如山」），所以这里绝不能用天赋库过滤猫名
        logger.debug(f'[指挥喵-扫描] 当前猫 OCR -> {texts}，取 {name!r} Lv{level}')
        return name, level

    @staticmethod
    def _crop_scale(image: np.ndarray, scale: float = 3.0) -> np.ndarray:
        """放大图像以提升小字识别率（天赋名实拍校准用的就是 3 倍）。"""
        import cv2
        return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_LANCZOS4)

    def _select_card(self, button, ocr, previous: str = '') -> str:
        """点击一张猫窝卡片并返回选中后的猫名。

        猫窝列表不满时，空卡片点击后画面不变、左下角仍显示上一只猫的名字。
        仅凭「名字是否变化」无法区分空卡片和同名猫（指挥喵可以重名），
        所以同时比较左下角猫名区域的前后图像：名字没变但画面变了 → 同名猫，
        画面也没变 → 空卡片，返回空串跳过。

        Args:
            button: 卡片按钮。
            ocr: 已初始化的 OCR 实例。
            previous: 点击前显示的猫名。

        Returns:
            str: 选中后的猫名；空卡片或读不到返回空串。
        """
        name = previous
        for attempt in range(2):
            self.device.screenshot()
            before = _crop(self.device.image, CURRENT_CAT_AREA).copy()
            self.device.click(button)
            time.sleep(0.35)
            self._wait_stable(CATTERY_PANEL_AREA, timeout=3)
            after = _crop(self.device.image, CURRENT_CAT_AREA).copy()
            self.device.stuck_record_clear()
            name, _level = self._read_current_cat(ocr)
            # 名字变了 → 新猫
            if name and name != previous:
                return name
            # 名字没变但左下角画面变了 → 同名猫（不同个体），仍然处理
            if name and _mean_diff(before, after) > STABLE_TOLERANCE:
                return name
            if attempt == 0:
                logger.debug(f'[指挥喵-扫描] 点击后猫名未变（{previous}），重试一次')
        # 两次都选不到新猫 → 空卡片
        return ''

    def _open_talent(self) -> bool:
        """点开「天赋」页签。"""
        if not self.appear(MEOWFFICER_TALENT_TAB, offset=TALENT_TAB_OFFSET):
            logger.warning('[指挥喵-扫描] 猫窝列表上找不到天赋页签，跳过这只')
            return False
        self.device.stuck_record_clear()
        self.device.click(MEOWFFICER_TALENT_TAB)
        if not self._wait_talent_tab(appear=False):
            return False
        # 这里不再额外等画面稳定：页签状态变化本身已经同步了页面切换，
        # 而紧接着的 _reset_talent_scroll 还要滑两次（约 1.3 秒），面板足够时间渲染完
        return True

    def _back_to_cattery(self) -> bool:
        """从天赋页返回猫窝列表。"""
        if not self.appear(MEOWFFICER_GOTO_DORMMENU, offset=TALENT_TAB_OFFSET):
            logger.warning('[指挥喵-扫描] 天赋页上找不到返回箭头')
            return False
        self.device.stuck_record_clear()
        self.device.click(MEOWFFICER_GOTO_DORMMENU)
        if not self._wait_talent_tab(appear=True):
            return False
        self._wait_stable(CATTERY_PANEL_AREA, timeout=4)
        return True

    def _read_talents(self, ocr) -> list:
        """抓取当前猫的天赋：整屏识别 + 小步上滑，直到画面不再变化。

        整屏识别是刻意的：裁到面板会把最下面那条只露出一半的天赋切掉，
        而 ``recognize`` 里的天赋库白名单会把说明文字、按钮文字自动过滤掉。

        Args:
            ocr: 已初始化的 OCR 实例。

        Returns:
            list[Talent]: 按天赋线去重、保留最高等级的天赋列表。
        """
        found: dict[str, Talent] = {}
        # 面板会沿用上次的滚动位置，先回顶部，否则第一条天赋会被裁掉
        self._reset_talent_scroll()
        # OCR 期间画面本来就是静止的，放宽卡死检测，避免被误判成模拟器卡死
        with self.device.stuck_timeout_override(image_stuck=180):
            for attempt in range(MAX_TALENT_SWIPES + 1):
                self.device.screenshot()
                try:
                    # 只把天赋面板交给 OCR（整屏要慢 3 倍）
                    talents, _cat = recognize(_crop(self.device.image, TALENT_OCR_AREA), ocr=ocr)
                except Exception as e:
                    logger.warning(f'[指挥喵-扫描] 天赋识别失败：{e}')
                    talents = []
                known = len(found)
                for talent in talents:
                    old = found.get(talent.line)
                    if old is None or talent.level > old.level:
                        found[talent.line] = talent
                if attempt >= MAX_TALENT_SWIPES:
                    break
                # 这次滑动没带来任何新天赋 -> 已经到底。比「等画面不再变化」少滑一次、
                # 少识别一次（每只猫约省 2 秒），扫描速度主要就靠这个。
                if attempt > 0 and len(found) == known:
                    logger.debug('[指挥喵-扫描] 滑动后没有新天赋，认为已到底')
                    break
                self._swipe_panel(TALENT_PANEL_AREA)
        logger.info(f'[指挥喵-扫描] 本只猫识别到 {len(found)} 条天赋')
        return list(found.values())

    def _swipe_panel(self, area: tuple, distance: int = 150, duration: float = 0.9) -> None:
        """在指定面板内竖直滑动。

        刻意用「小步 + 慢速」：实测大幅快滑会带惯性一次冲过好几行，
        有整行猫被跳过的风险。

        Args:
            area: 面板区域。
            distance: 滑动距离（像素）；**正数向上滑（看后面的内容），负数向下滑（回到顶部）**。
            duration: 滑动耗时（秒），越长越不容易触发惯性。
        """
        x0, y0, x1, y1 = area
        x = (x0 + x1) // 2
        if distance >= 0:
            # 向上滑：从面板下方向上拖，看到后面的内容
            y_from = y1 - 60
            y_to = max(y0 + 40, y_from - distance)
        else:
            # 向下滑（回顶部）：必须从面板上方往下拖，否则起止点会被钳到几乎不动
            y_from = y0 + 60
            y_to = min(y1 - 40, y_from - distance)
        self.device.swipe((x, y_from), (x, y_to), duration=duration)
        time.sleep(0.5)
        self.device.stuck_record_clear()

    def _reset_talent_scroll(self) -> None:
        """把天赋列表滑回顶部。

        实测天赋面板会**沿用上一次的滚动位置**：上一只猫读完时列表是往下滚过的，
        直接读下一只猫会把第一条天赋裁掉（漏识别）。

        注意**不能用一次大幅度下拉**（试过 -700）：从面板上部往下拖近 400px
        会被系统/游戏当成长滑手势，实测直接把游戏导航出了指挥喵页面。
        小步多次是安全的。
        """
        for _ in range(2):
            self._swipe_panel(TALENT_PANEL_AREA, distance=-180, duration=0.5)

    def _reset_cattery_scroll(self) -> None:
        """把猫窝列表滑回顶部。

        列表同样会沿用上次的滚动位置；不复位就从中间开始扫，**上方的猫会整批漏掉**。
        多滑几次确保到底，滑到顶后继续往下滑是无害的（只会回弹）。
        """
        for _ in range(4):
            self._swipe_panel(CATTERY_PANEL_AREA, distance=-320, duration=0.6)

    def _advance_cattery_screen(self) -> int:
        """把猫窝列表整屏前进，供「不重叠逐屏扫描」使用。

        滑动距离和实际滚动量不是 1:1（实机约 1.4~1.5 倍，还有惯性），
        所以这里不猜滑动量，而是**每一步都量出实际位移并累加**，累到一屏为止。
        这样每只猫只会被访问一次，就不需要按内容去重了
        （指挥喵可以重名，按名字/天赋合并会丢猫）。

        Returns:
            int: 本次实际向上滚动的像素总数；到底滑不动时接近 0。
        """
        self.device.screenshot()
        prev = _crop(self.device.image, CATTERY_PANEL_AREA).copy()
        total = 0
        for _ in range(8):
            self._swipe_panel(CATTERY_PANEL_AREA, distance=CATTERY_SWIPE_STEP, duration=0.7)
            self.device.screenshot()
            current = _crop(self.device.image, CATTERY_PANEL_AREA).copy()
            moved = scroll_offset(prev, current)
            prev = current
            total += moved
            if moved == 0:
                break
            if total >= CATTERY_SCREEN_HEIGHT - 20:
                break
        logger.debug(f'[指挥喵-扫描] 本屏向前滚动 {total}px（一屏按 {CATTERY_SCREEN_HEIGHT}px 计）')
        return total

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def scan_all(self, limit: int = 0, passes: int = 12) -> list:
        """遍历猫窝列表，返回每只猫的天赋。

        Args:
            limit: 最多扫描多少只猫；``0`` 表示不限。
            passes: 最多翻几屏（每屏 12 张卡片）。

        Returns:
            list[tuple[str, list[Talent]]]: ``(猫名, 天赋列表)``。

        指挥喵**可以自定义名字**，自定义名甚至可能和天赋名一样（用户就有一只猫叫
        「不动如山」），而且**可以重名**，所以这里刻意**不做任何按内容的去重**：
        改成每屏整屏前进、不重叠地扫，保证每只猫只被访问一次。
        """
        self.scanned = []
        ocr = self._load_ocr()

        self._ensure_cattery()
        self._reset_cattery_scroll()
        self.device.stuck_record_clear()
        logger.hr('扫描全部指挥喵', level=2)
        logger.info(f'[指挥喵-扫描] 最多 {passes} 屏，上限 '
                    f'{limit if limit > 0 else "不限"} 只')

        for page in range(1, passes + 1):
            read_in_page = 0
            previous = ''
            for index, button in enumerate(MEOWFFICER_CATTERY_GRID.buttons, 1):
                if limit > 0 and len(self.scanned) >= limit:
                    logger.info(f'[指挥喵-扫描] 已达到上限 {limit} 只，结束')
                    return self.scanned

                cat = self._select_card(button, ocr, previous=previous)
                if cat:
                    previous = cat
                if not cat:
                    logger.warning(f'[指挥喵-扫描] 第 {page} 屏第 {index} 张卡片没读到猫名，跳过')
                    continue

                logger.hr(f'第 {page} 屏 第 {index} 张：{cat}', level=3)
                if not self._open_talent():
                    continue
                # 防串数据：面板切换有一瞬间可能还显示上一只猫的内容，
                # 用左下角猫名核对，不一致就跳过这只（宁可漏也不要错配）
                shown, _level = self._read_current_cat(ocr)
                if shown and shown != cat:
                    logger.warning(f'[指挥喵-扫描] 天赋页显示的是 {shown}，与选中的 {cat} 不一致，跳过')
                    self._back_to_cattery()
                    previous = shown
                    continue
                talents = self._read_talents(ocr)
                if not self._back_to_cattery():
                    # 回不去就重进页面，避免后面所有操作都落在错误页面上
                    logger.warning('[指挥喵-扫描] 返回猫窝列表失败，重新进入指挥喵页面')
                    self._ensure_cattery()
                    previous = ''
                if not talents:
                    logger.warning(f'[指挥喵-扫描] {cat} 没识别到天赋，跳过')
                    continue

                read_in_page += 1
                self.scanned.append((cat, talents))
                logger.attr('[指挥喵-扫描] 已扫描', f'{len(self.scanned)} 只')

            logger.info(f'[指挥喵-扫描] 第 {page} 屏结束，读到 {read_in_page} 只，'
                        f'累计 {len(self.scanned)} 只')
            if read_in_page == 0:
                logger.info('[指挥喵-扫描] 本屏一张卡片都没读到，结束')
                break
            # 整屏前进，保证不重叠（每只猫只访问一次，所以不需要去重）
            if self._advance_cattery_screen() < 40:
                logger.info('[指挥喵-扫描] 列表没有继续滚动，结束')
                break

        logger.info(f'[指挥喵-扫描] 扫描完成，共 {len(self.scanned)} 只猫')
        return self.scanned

    def _load_ocr(self):
        """加载中文 OCR 模型（与评分任务一致的失败提示）。"""
        from module.exception import RequestHumanTakeover
        from module.ocr.al_ocr import AlOcr
        try:
            ocr = AlOcr(name='cn')
            ocr.init()
        except Exception as e:
            raise RequestHumanTakeover(
                f'OCR 模型加载失败（首次运行需联网下载，请检查网络后重试）：{e}') from e
        return ocr
