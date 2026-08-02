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

import module.config.server as server
from module.combat.assets import GET_ITEMS_1
from module.logger import logger
from module.minigame.assets import *
from module.ocr.ocr import Digit
from module.ui.assets import ACADEMY_GOTO_GAME_ROOM, GAME_ROOM_CHECK
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
MINIGAME_SCROLL = Scroll(MINIGAME_SCROLL_AREA, color=(247, 247, 247), name='MINIGAME_SCROLL')

class MinigameRun(UI):
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
            out: page_game_room main_page
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
            # both minigame main and minigame list has GOTO_CHOOSE_GAME
            if self.appear(GAME_ROOM_CHECK, offset=(5, 5)) and not self.appear(GOTO_CHOOSE_GAME, offset=(20, 20)):
                if MINIGAME_SCROLL.appear(main=self):
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


class Minigame(UI):
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
            if self.appear(GAME_ROOM_CHECK, offset=(5, 5)) \
                    and not self.appear(GOTO_CHOOSE_GAME, offset=(5, 5)):
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
            if self.appear(GAME_ROOM_CHECK, offset=(5, 5)) \
                    and not self.appear(GOTO_CHOOSE_GAME, offset=(5, 5)):
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
