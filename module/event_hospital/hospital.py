"""医院活动模块。

提供碧蓝航线医院活动的自动化处理功能，包括：
- 每日奖励的红点检测与自动领取
- 线索系统的标签页切换（地点 / 角色）
- 旁白（aside）列表的遍历与选择
- 调查（invest）入口的进入与战斗执行
- 调查奖励的自动领取
- 旁白列表的滑动翻页处理
- 体力不足时的优雅退出与延迟重试

医院活动是一个探索型活动，玩家通过选择不同地点和角色的旁白展开调查，
每个调查包含线索收集和战斗环节。
"""
from module.base.timer import Timer
from module.base.utils import random_rectangle_vector
from module.config.config import TaskEnd
from module.event_hospital.assets import *
from module.event_hospital.clue import HospitalClue
from module.event_hospital.combat import HospitalCombat
from module.exception import OilExhausted, ScriptEnd
from module.logger import logger
from module.ui.page import page_hospital, page_campaign_menu
from module.ui.switch import Switch


class HospitalSwitch(Switch):
    """医院活动标签页切换器。"""

    def get(self, main):
        """获取当前标签页状态。

        通过检测标签按钮的高亮颜色判断当前选中的标签。

        Args:
            main: 模块实例，用于图像颜色检测。

        Returns:
            str: 状态名称，未匹配时返回 'unknown'。
        """
        for data in self.state_list:
            if main.image_color_count(data['check_button'], color=(33, 77, 189), threshold=221, count=100):
                return data['state']

        return 'unknown'


HOSPITAL_TAB = HospitalSwitch('HOSPITAL_ASIDE', is_selector=True)
HOSPITAL_TAB.add_state('LOCATION', check_button=TAB_LOCATION)
HOSPITAL_TAB.add_state('CHARACTER', check_button=TAB_CHARACTER)


class Hospital(HospitalClue, HospitalCombat):
    """医院活动主控制器。

    组合线索处理（HospitalClue）和战斗处理（HospitalCombat）能力，
    实现医院活动的完整自动化流程。

    工作流程：
    1. 检查活动可用性，导航至活动页面
    2. 领取每日奖励（检测红点 -> 进入奖励界面 -> 领取 -> 退出）
    3. 进入线索系统，遍历地点和角色标签页的所有旁白
    4. 对每个旁白执行调查（进入 -> 战斗 -> 领取奖励）
    5. 角色标签页支持滑动翻页以访问更多旁白

    Attributes:
        HOSPITAL_TAB (HospitalSwitch): 标签页切换器，支持 LOCATION 和 CHARACTER 两种状态。
    """

    def daily_red_dot_appear(self):
        """检测每日奖励红点是否出现。"""
        return self.image_color_count(DAILY_RED_DOT, color=(189, 69, 66), threshold=221, count=35)

    def daily_reward_receive_appear(self):
        """检测每日奖励领取按钮是否可点击。"""
        return self.image_color_count(DAILY_REWARD_RECEIVE, color=(41, 73, 198), threshold=221, count=200)

    def is_in_daily_reward(self, interval=0):
        """检测当前是否在每日奖励界面。"""
        return self.match_template_color(HOSIPITAL_CLUE_CHECK, offset=(30, 30), interval=interval)

    def daily_reward_receive(self):
        """领取每日奖励。

        检测红点后进入奖励界面，点击领取并退出。

        Returns:
            bool: 是否成功领取。

        Pages:
            in: page_hospital
        """
        if self.daily_red_dot_appear():
            logger.info('每日红点出现')
        else:
            logger.info('无每日红点')
            return False

        logger.hr('领取每日奖励', level=2)
        # 进入奖励界面
        logger.info('进入每日奖励')
        skip_first_screenshot = True
        self.interval_clear(page_hospital.check_button)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()
            if self.is_in_daily_reward():
                break
            if self.ui_page_appear(page_hospital, interval=2):
                logger.info(f'{page_hospital} -> {HOSPITAL_GOTO_DAILY}')
                self.device.click(HOSPITAL_GOTO_DAILY)
                continue

        # 领取奖励
        logger.info('领取每日奖励')
        skip_first_screenshot = True
        self.interval_clear(HOSIPITAL_CLUE_CHECK)
        timeout = Timer(1.5, count=6).start()
        clicked = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()
            if timeout.reached():
                logger.warning('每日奖励领取超时')
                break
            if clicked and self.is_in_daily_reward():
                if not self.daily_reward_receive_appear():
                    break
            if self.is_in_daily_reward(interval=2):
                if self.daily_reward_receive_appear():
                    self.device.click(DAILY_REWARD_RECEIVE)
                    continue
            if self.handle_get_items():
                timeout.reset()
                clicked = True
                continue

        # 退出奖励界面
        logger.info('退出每日奖励')
        skip_first_screenshot = True
        self.interval_clear(HOSIPITAL_CLUE_CHECK)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.ui_page_appear(page_hospital):
                break
            if self.is_in_daily_reward(interval=2):
                self.device.click(HOSIPITAL_CLUE_CHECK)
                logger.info(f'is_in_daily_reward -> {HOSIPITAL_CLUE_CHECK}')
                continue

        return True

    def loop_invest(self):
        """遍历当前页面所有调查并执行战斗。

        战斗结束后旁白会重置，需要重新选择。
        """
        self.config.override(Fleet_FleetOrder='fleet1_all_fleet2_standby')
        while 1:
            logger.hr('循环医院投资', level=2)
            # 调度器检查，可能抛出 ScriptEnd
            self.emotion.check_reduce(battle=1)

            entered = self.invest_enter()
            if not entered:
                break
            self.hospital_combat()

            # 调度器检查，可能抛出 TaskEnd
            if self.config.task_switched():
                self.config.task_stop()

            # 战斗后旁白重置，跳出重新选择
            break

        self.claim_invest_reward()
        logger.info('循环医院投资 end')

    def invest_reward_appear(self) -> bool:
        """检测调查奖励领取按钮是否出现。"""
        return self.image_color_count(INVEST_REWARD_RECEIVE, color=(33, 77, 189), threshold=221, count=100)

    def claim_invest_reward(self):
        """领取调查奖励。"""
        if self.invest_reward_appear():
            logger.info('投资奖励出现')
        else:
            logger.info('无投资奖励')
            return False
        # 领取奖励
        skip_first_screenshot = True
        clicked = True
        self.interval_clear(HOSIPITAL_CLUE_CHECK)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if clicked:
                if self.is_in_clue() and not self.invest_reward_appear():
                    return True
            if self.handle_get_items():
                clicked = True
                continue
            if self.is_in_clue(interval=2):
                if self.invest_reward_appear():
                    self.device.click(INVEST_REWARD_RECEIVE)
                    continue

    def loop_aside(self):
        """遍历所有标签页的旁白并执行调查。"""
        while 1:
            logger.hr('循环医院旁白', level=1)
            HOSPITAL_TAB.set('LOCATION', main=self)
            selected = self.select_aside()
            if not selected:
                break
            self.loop_invest()

        while 1:
            logger.hr('循环医院旁白', level=1)
            HOSPITAL_TAB.set('CHARACTER', main=self)
            selected = self.select_aside()
            if not selected:
                break
            self.loop_invest()

        while 1:
            logger.hr('循环医院旁白', level=1)
            HOSPITAL_TAB.set('CHARACTER', main=self)
            self.aside_swipe_down()
            selected = self.select_aside()
            if not selected:
                break
            self.loop_invest()

        logger.info('循环医院旁白 end')

    def aside_swipe_down(self, skip_first_screenshot=True):
        """向下滑动旁白列表直到没有翻页标识。"""
        logger.info('旁白下滑')
        swiped = False
        interval = Timer(2, count=6)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if swiped and not self.appear(ASIDE_NEXT_PAGE, offset=(20, 20)):
                logger.info('旁白到达终点')
                break
            if interval.reached():
                p1, p2 = random_rectangle_vector(
                    vector=(0, -200), box=CLUE_LIST.area, random_range=(-20, -10, 20, 10))
                self.device.swipe(p1, p2)
                interval.reset()
                swiped = True
                continue

    def run(self):
        """医院活动主入口。"""
        # 检查活动是否可用
        if self.event_time_limit_triggered():
            self.config.task_stop()
        self.ui_ensure(page_campaign_menu)
        if self.is_event_entrance_available():
            self.ui_goto(page_hospital)

        # 领取每日奖励
        self.daily_reward_receive()

        # 执行活动
        self.clue_enter()
        try:
            self.loop_aside()
            # Scheduler
            self.config.task_delay(server_update=True)
        except OilExhausted:
            self.clue_exit()
            logger.hr('触发停止条件: 石油上限')
            self.config.task_delay(minute=(120, 240))
        except ScriptEnd as e:
            logger.hr('脚本结束')
            logger.info(str(e))
            self.clue_exit()
        except TaskEnd:
            self.clue_exit()
            raise


if __name__ == '__main__':
    self = Hospital('alas')
    self.device.screenshot()
    self.loop_aside()
