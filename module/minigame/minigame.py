"""
小游戏自动化模块。

管理学院游戏室（Game Room）中的小游戏自动化流程。
负责游戏券/代币的收集、小游戏选择、游玩和退出。

主要功能：
    - OCR 识别当前代币数量
    - 自动收集游戏代币
    - 导航至游戏室主页
    - 选择并游玩特定小游戏（如新年挑战）
    - 处理弹窗（代币已满、游戏券不足等）

代币机制：
    - 最大代币上限为 40，OCR 超过 40 时截断
    - 代币数量 <= 30 时尝试自动收集
    - 代币为 0 时结束游玩循环
    - 每局游玩后根据配置决定下次游玩时间

依赖关系：
    - MinigameRun: 小游戏运行基类，定义选择/游玩/退出模板方法
    - Minigame: 主任务类，组合代币管理、游戏室导航和游玩循环

Pages:
    游戏室页面：page_game_room
    学院页面：page_academy
"""

import numpy as np

import module.config.server as server
from module.base.timer import Timer
from module.base.utils import random_rectangle_point
from module.combat.assets import GET_ITEMS_1
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.minigame.assets import *
from module.ocr.ocr import Digit
from module.ui.assets import ACADEMY_GOTO_GAME_ROOM
from module.ui.page import page_academy, page_game_room
from module.ui.scroll import Scroll
from module.ui.ui import UI

if server.server != 'jp':
    OCR_COIN = Digit(COIN_HOLDER,
                    name='OCR_COIN',
                    letter=(255, 235, 115),
                    threshold=128)
else:
    OCR_COIN = Digit(COIN_HOLDER,
                    name='OCR_COIN',
                    letter=(211, 196, 95),
                    threshold=128)


class MinigameScroll(Scroll):
    """
    小游戏专用滚动条，避免残留开始按钮的白色边框干扰定位。
    """

    def cal_position(self, main):
        """
        按滑块模板计算位置，不再使用白色像素的平均位置。

        Args:
            main (MinigameUI): 持有当前截图的小游戏界面。

        Returns:
            float | None: 归一化位置，0 为顶部，1 为底部；滑块被遮挡时为 None。
        """
        y = main.minigame_scroll_y()
        if y is None:
            return None
        # 参考资源中的滑块位于轨道顶部，用其中心反推滑块长度
        reference_y = (MINIGAME_SCROLL_KNOB.area[1] + MINIGAME_SCROLL_KNOB.area[3]) / 2
        self.length = 2 * (reference_y - self.area[1])
        position = (y - reference_y) / (self.total - self.length)
        position = min(max(position, 0.0), 1.0)
        logger.attr(self.name, f'{position:.2f} (knob y={y})')
        return position

    def set(self, position, main, random_range=(-0.05, 0.05), distance_check=True, skip_first_screenshot=True):
        """
        定位到指定百分比，滑块被遮挡时通过滑动卡片恢复。

        Args:
            position (float): 目标位置，0 为顶部，1 为底部。
            main (MinigameUI): 持有当前截图的小游戏界面。
            random_range (tuple): 目标滑块位置的随机偏移范围。
            distance_check (bool): 是否检查滑动距离。
            skip_first_screenshot (bool): 是否复用已有截图。

        Returns:
            int: 实际执行的滑动次数，已在目标位置时为 0。

        Raises:
            RequestHumanTakeover: 达到 8 次滑动或 20 秒仍未确认定位完成。

        Pages:
            in: page_game_room 选游戏界面
            out: page_game_room 选游戏界面
        """
        logger.info(f'{self.name} set to {position}')
        timeout = Timer(20).start()
        interval = Timer(1)
        dragged = 0
        previous = None
        stationary = 0
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                main.device.screenshot()
            current = self.cal_position(main)
            if current is not None and abs(position - current) < self.drag_threshold:
                return dragged
            if dragged >= 8 or timeout.reached():
                raise RequestHumanTakeover('无法定位小游戏滚动条')
            if not interval.reached():
                continue
            if position >= 1 - self.edge_threshold:
                # 底部滑块可能被遮挡，通过连续滑动后卡片不再移动确认到底
                cards = main.image_crop((300, 110, 600, 500))
                if previous is not None and np.mean(np.abs(cards.astype(float) - previous)) < 1:
                    stationary += 1
                    if stationary >= 2:
                        return dragged
                else:
                    stationary = 0
                previous = cards.astype(float)
                p1, p2 = (600, 550), (600, 250)
            elif current is None:
                # 通过卡片向顶部滚动，直到重新识别到滑块
                p1, p2 = (600, 250), (600, 550)
            else:
                p1 = random_rectangle_point(self.position_to_screen(current), n=1)
                p2 = random_rectangle_point(self.position_to_screen(position, random_range=random_range), n=1)
            main.device.swipe(p1, p2, name=self.name, distance_check=distance_check)
            dragged += 1
            interval.reset()


MINIGAME_SCROLL = MinigameScroll(MINIGAME_SCROLL_AREA, color=(247, 247, 247), name='MINIGAME_SCROLL')


class MinigameUI(UI):
    """
    小游戏任务和具体游戏共享的页面识别。
    """

    def minigame_scroll_y(self):
        """
        Returns:
            int | None: 当前截图中的滑块中心纵坐标，滑块被遮挡时为 None。
        """
        if self.appear(MINIGAME_SCROLL_KNOB, offset=(-9, -73, 12, 495)):
            return int((MINIGAME_SCROLL_KNOB.button[1] + MINIGAME_SCROLL_KNOB.button[3]) // 2)
        return None

    def minigame_list_appear(self):
        """
        用滚动条与卡片标记共同确认当前截图为游戏列表。
        列表页面上可能残留 GOTO_CHOOSE_GAME 开始按钮，不能以该按钮消失作为就绪条件。

        Returns:
            bool: 是否在选游戏界面。
        """
        # 两列分别使用自身模板作为横向锚点，并沿纵向搜索滚动后的卡片
        return MINIGAME_SCROLL.appear(main=self) and (
            self.appear(GAME_LIST_CHECK_LEFT, offset=(5, 500))
            or self.appear(GAME_LIST_CHECK_RIGHT, offset=(5, 500))
        )


class MinigameRun(MinigameUI):
    """
    小游戏运行基类。

    定义小游戏的通用运行流程模板：导航至游戏列表、选择游戏、
    投入代币、游玩、退出。具体游戏逻辑由子类实现。

    子类需要重写以下方法：
        - choose_game(): 从游戏列表中选择目标游戏
        - use_coin(): 投入代币并准备游玩
        - play_game(): 执行游戏的具体操作
        - exit_game(): 退出当前游戏
        - deal_specific_popup(): 处理特定游戏的弹窗

    属性:
        无额外属性，所有状态通过方法参数传递
    """

    def minigame_run(self, skip_first_screenshot=True):
        """
        Pages:
            in: page_game_room main_page
            out: page_game_room main_page or choose_game
        Return:
            False if unable or unnecessary to play
        """
        logger.hr('[小游戏] 运行', level=1)

        # page_game_room main_page -> MINIGAME_SCROLL
        logger.info("[小游戏] 进入小游戏")
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()
            # End
            # 列表页可能残留开始按钮，用卡片与滚动条共同确认列表
            if self.minigame_list_appear():
                break
            # unable to get more ticket popup
            if self.deal_popup():
                continue
            if self.appear_then_click(GOTO_CHOOSE_GAME, offset=(5, 5), interval=3):
                # note: GOTO_CHOOSE_GAME is some where safe to click
                # that won't enter any minigame on the minigame list page
                continue

        logger.info("[小游戏] 选择小游戏")
        self.choose_game()
        # try to add coins, if failed, skip play
        add_coin_result = self.use_coin()
        if add_coin_result:
            logger.hr("[小游戏] 游玩", level=2)
            self.play_game()
        logger.info("[小游戏] 退出小游戏")
        self.exit_game()
        return add_coin_result

    def deal_popup(self):
        """
            deal possible popups
            need re-screenshot if return true
        """
        # specific
        if self.deal_specific_popup():
            return True
        if self.handle_popup_confirm('TICKETS_FULL'):
            self.interval_reset(COIN_POPUP, interval=3)
            return True
        # coins more than 31, deal popup
        if self.appear_then_click(COIN_POPUP, offset=(5, 5), interval=3):
            return True
        # coins/tickets received
        if self.appear_then_click(GET_ITEMS_1, offset=(5, 5), interval=3):
            return True
        return False

    def deal_specific_popup(self):
        return False

    def choose_game(self, skip_first_screenshot=True):
        """
        Pages:
            in: page_game_room choosing_game
            out: page_game_room game_entrance
        """
        pass

    def use_coin(self, skip_first_screenshot=True):
        return False

    def play_game(self, skip_first_screenshot=True):
        pass

    def exit_game(self, skip_first_screenshot=True):
        """
        Pages:
            in: page_game_room new_year_challenge_end
            out: page_game_room choose_game
        """
        pass


class Minigame(MinigameUI):
    """
    小游戏主任务类。

    管理小游戏任务的完整生命周期：从学院页面导航到游戏室，
    收集代币，选择并游玩小游戏，直到代币耗尽或达到游玩上限。

    流程概要：
        1. 从任意页面导航至学院 -> 游戏室主页
        2. OCR 读取代币数量
        3. 代币 <= 30 时尝试自动收集
        4. 代币 > 0 时选择小游戏并游玩（最多 10 次）
        5. 代币耗尽后调度下次运行

    配置项:
        通过 self.config.task_delay(server_update=True) 调度下次运行

    Pages:
        任务入口页面：任意页面
        任务结束页面：page_game_room
    """

    def get_coin_amount(self, skip_first_screenshot=True):
        """
        Pages:
            in: page_game_room main_page
            out: page_game_room main_page
        Returns:
            int: Coin amount
        """
        if not skip_first_screenshot:
            self.device.screenshot()
        amount = OCR_COIN.ocr(self.device.image)
        if amount >= 40:
            amount = 40
        return amount

    def go_to_main_page(self, skip_first_screenshot=True):
        """
        Pages:
            in: page_game_room main_page/choose_game_page
            out: page_game_room main_page
        """
        logger.info('[小游戏] 前往主页')
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()
            if self.ui_additional():
                continue
            if self.appear_then_click(COIN_POPUP, offset=(5, 5), interval=2):
                continue
            if self.minigame_list_appear():
                self.appear_then_click(BACK, offset=(5, 5), interval=2)
                continue
            if self.appear(GOTO_CHOOSE_GAME, offset=(5, 5)):
                break

    def collect_coin(self, skip_first_screenshot=True):
        """
        Pages:
            in: page_game_room main_page/choose_game_page
            out: page_game_room main_page
        """
        coin_collected = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()
            if self.ui_additional():
                continue
            if self.appear_then_click(COIN_POPUP, offset=(5, 5), interval=3):
                continue
            # game room and choose game have same header, go to game room first
            if self.minigame_list_appear():
                self.appear_then_click(BACK, offset=(5, 5), interval=3)
                continue
            # collect coins
            if not coin_collected and self.appear(COIN, offset=(5, 5)):
                self.appear_then_click(COIN, offset=(5, 5), interval=3)
                coin_collected = True
                continue
            if self.appear(GOTO_CHOOSE_GAME, offset=(5, 5)):
                break
        return coin_collected

    def run(self):
        """
        Pages:
            in: Any page
            out: page_game_room
        """
        # TEMP: 2026.02.18 separate self.ui_ensure(page_game_room) into 2 steps
        # EN has different page_academy detection, to use ui_ensure(page_game_room),
        # ui_goto must use `if self.ui_page_appear(page)` instead of `if self.appear(page.check_button)`
        # But that would cause page_main/page_main_white clicking a static switch button
        self.ui_ensure(page_academy)
        # page_academy -> page_game_room
        for _ in self.loop():
            if self.ui_page_appear(page_game_room):
                break
            if self.ui_page_appear(page_academy, interval=5):
                self.device.click(ACADEMY_GOTO_GAME_ROOM)
                continue
            # You've reached your monthly limit of Game Tickets, and will not be able to earn any more.
            # Continue playing the minigame?
            if self.handle_popup_confirm('MINIGAME_ENTER'):
                continue

        # game room and choose game have same header, go to game room first
        self.go_to_main_page()
        coin_collected = False
        play_count = 0

        # choose game
        specific_game_name = "new_year_challenge"
        minigame_instance = None
        if specific_game_name == "new_year_challenge":
            from module.minigame.new_year_challenge import NewYearChallenge
            minigame_instance = NewYearChallenge(config=self.config, device=self.device)

        while 1:
            # play count limit
            if play_count >= 10:
                break
            # ocr to get coin count and ticket count
            coin_count = self.get_coin_amount()
            logger.info(f"[小游戏] 硬币数量: {coin_count}")
            # collect coins
            if coin_count <= 30 and not coin_collected:
                coin_collected = True
                if self.collect_coin():
                    continue
            # no coin left
            if coin_count == 0:
                logger.info(f"[小游戏] 硬币数量: {coin_count}, 游玩结束")
                break
            logger.info("[小游戏] 硬币数量 > 0，消费")
            # specific game logic
            if minigame_instance is not None and minigame_instance.minigame_run():
                play_count += 1
                continue
            elif minigame_instance is None:
                logger.error(f"[小游戏] 未知的游戏名称 {specific_game_name}")
                break
            else:
                break

        self.config.task_delay(server_update=True)
