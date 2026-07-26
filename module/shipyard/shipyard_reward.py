"""
船坞蓝图购买模块。

自动化船坞（Shipyard）中的科研蓝图购买与使用流程。
支持 PR（科研船）和 DR（决战方案）两种稀有度的蓝图管理。

主要功能：
    - 从主页面读取当前金币余额，用于购买计算
    - 根据金币余额和蓝图价格阶梯计算可购买数量
    - 自动进入船坞界面，定位目标舰船并执行蓝图购买
    - 使用目标舰船的多余蓝图
    - 每日防重复执行（基于服务器更新时间 04:00）

价格阶梯：
    - PR 蓝图：第1-2张免费，之后阶梯递增至 1500 金币/张
    - DR 蓝图：第1-2张免费，之后阶梯递增至 6000 金币/张

依赖关系：
    继承自 ShipyardUI（船坞界面操作），调用船坞导航、蓝图购买确认等底层方法。

Pages:
    任务入口页面：任意页面
    任务结束页面：page_shipyard
"""

from module.base.timer import Timer
from module.config.time_source import now as current_time
from module.config.utils import get_server_last_update
from module.exception import ScriptError
from module.logger import logger
from module.shipyard.ui import ShipyardUI
from module.ui.page import page_main, page_shipyard

# PR 蓝图价格阶梯：键为已购蓝图序号范围，值为对应单价（金币）
PRBP_BUY_PRIZE = {
    (1, 2):               0,
    (3, 4):               150,
    (5, 6, 7):            300,
    (8, 9, 10):           600,
    (11, 12, 13, 14, 15): 1050,
}
# DR 蓝图价格阶梯：键为已购蓝图序号范围，值为对应单价（金币）
DRBP_BUY_PRIZE = {
    (1, 2):               0,
    (3, 4, 5, 6):         600,
    (7, 8, 9, 10):        1200,
    (11, 12, 13, 14, 15): 3000,
}


class RewardShipyard(ShipyardUI):
    """
    船坞蓝图购买任务处理器。

    负责执行船坞中科研蓝图的购买和使用逻辑。支持 PR（普通科研船）
    和 DR（决战方案）两种稀有度类型，按价格阶梯计算最优购买方案。

    属性:
        _shipyard_bp_rarity (str): 当前蓝图稀有度，'PR' 或 'DR'
        _coin_count (int): 当前金币余额，从主页面 OCR 获取

    配置项:
        Shipyard_ShipIndex: PR 舰船索引
        Shipyard_BuyAmount: PR 购买数量
        Shipyard_ResearchSeries: PR 科研系列
        ShipyardDr_ShipIndex: DR 舰船索引
        ShipyardDr_BuyAmount: DR 购买数量
        ShipyardDr_ResearchSeries: DR 科研系列

    Pages:
        任务入口页面：任意页面
        任务结束页面：page_shipyard
    """
    _shipyard_bp_rarity = 'PR'
    _coin_count = 0

    @staticmethod
    def _shipyard_task_enabled(index, count):
        return index > 0 and count > 0

    def _shipyard_get_cost(self, amount, rarity=None):
        """
        根据已购蓝图数量和稀有度计算购买单价。

        Args:
            amount (int): 已购买的蓝图序号
            rarity (str): 稀有度，'DR' 或 'PR'

        Returns:
            int: 购买价格

        Raises:
            ScriptError: 稀有度无效时抛出
        """
        if rarity is None:
            rarity = self._shipyard_bp_rarity

        if rarity == 'PR':
            cost = [v for k, v in PRBP_BUY_PRIZE.items() if amount in k]
            if len(cost):
                return cost[0]
            else:
                return 1500
        elif rarity == 'DR':
            cost = [v for k, v in DRBP_BUY_PRIZE.items() if amount in k]
            if len(cost):
                return cost[0]
            else:
                return 6000
        else:
            raise ScriptError(f'Invalid rarity in _shipyard_get_cost: {rarity}')

    def _shipyard_calculate(self, start, count, pay=False):
        """
        计算当前金币下可购买的最大蓝图数量。

        根据起始位置、剩余数量和金币余额，计算可购买的
        蓝图总数。若 pay 为 True 则扣除对应金币。

        Args:
            start (int): 起始购买序号
            count (int): 剩余待购买总数
            pay (bool): 是否实际扣除金币

        Returns:
            tuple: (下次起始序号, 本次可购买数量)
        """
        if start <= 0 or count <= 0:
            return start, count

        total = 0
        i = start
        for i in range(start, (start + count)):
            cost = self._shipyard_get_cost(i)

            if (total + cost) > self._coin_count:
                if pay:
                    self._coin_count -= total
                else:
                    logger.info(f'最多只能购买 {(i - start)} '
                                f'/ {count} 张蓝图')
                return i, i - start
            total += cost

        if pay:
            self._coin_count -= total
        else:
            logger.info(f'可以购买全部 {count} 张蓝图')
        return i + 1, count

    def _shipyard_buy_calc(self, start, count):
        """计算可购买数量，不扣除金币。"""
        return self._shipyard_calculate(start, count, pay=False)

    def _shipyard_pay_calc(self, start, count):
        """计算并扣除已购蓝图的金币消耗。"""
        return self._shipyard_calculate(start, count, pay=True)

    def _shipyard_buy(self, count):
        """
        购买指定数量的蓝图。

        支持在 DEV 和 FATE 阶段购买。循环进入购买界面、
        调整数量并确认购买，直到数量用尽或无法继续。

        Args:
            count (int): 待购买总数
        """
        logger.hr('船坞购买')
        prev = 1
        start, count = self._shipyard_buy_calc(prev, count)
        while count > 0:
            if not self._shipyard_buy_enter() or \
                    self._shipyard_cannot_strengthen():
                break

            remain = self._shipyard_ensure_index(count)
            if remain is None:
                break

            if self._shipyard_bp_rarity == 'DR':
                self.config.ShipyardDr_LastRun = current_time().replace(microsecond=0)
            else:
                self.config.Shipyard_LastRun = current_time().replace(microsecond=0)

            self._shipyard_buy_confirm('BP_BUY')

            # 根据实际购买量（remain）扣除金币，同时更新 start
            # 保存到 prev 供下次 _shipyard_pay_calc 使用
            start, _ = self._shipyard_pay_calc(prev, (count - remain))
            prev = start

            start, count = self._shipyard_buy_calc(start, remain)

    def _shipyard_use(self, index):
        """
        使用指定舰船的所有剩余多余蓝图。

        支持在 DEV 和 FATE 阶段使用蓝图。

        Args:
            index (int): 目标舰船索引
        """
        logger.hr('船坞使用')
        count = self._shipyard_get_bp_count(index)
        while count > 0:
            if not self._shipyard_buy_enter() or \
                    self._shipyard_cannot_strengthen():
                break

            remain = self._shipyard_ensure_index(count)
            if remain is None:
                break
            self._shipyard_buy_confirm('BP_USE')

            count = self._shipyard_get_bp_count(index)

    def shipyard_run(self, series, index, count):
        """
        执行船坞蓝图购买流程。

        Pages: in: page_main, out: page_shipyard

        Args:
            series (int): 科研系列，1-4（部分系列限制为 1-5）
            index (int): 舰船索引，1-6
            count (int): 使用后待购买的数量

        Returns:
            bool: 是否执行了购买流程
        """
        if count <= 0:
            logger.info('船坞购买数量为0，跳过')
            return False
        if index <= 0:
            logger.info('船坞舰船索引为0，跳过')
            return False

        # 船坞页面中金币 OCR 困难（文字和数字右对齐导致混淆）
        # 改从主页面获取金币信息
        self.ui_ensure(page_main)
        timeout = Timer(1, count=1).start()
        skip_first_screenshot = True
        while True:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            self._coin_count = self._shipyard_get_coin()

            if self._coin_count > 0:
                break
            if timeout.reached():
                logger.warning('假设OCR_COIN在正确位置')
                break

        self.ui_goto(page_shipyard)
        if not self.shipyard_set_focus(series=series, index=index) \
                or not self._shipyard_buy_enter() \
                or self._shipyard_cannot_strengthen():
            return True

        self._shipyard_use(index=index)
        self._shipyard_buy(count=count)

        return True

    def run(self):
        """
        Pages:
            in: Any page
            out: page_shipyard
        """
        dr_enabled = self._shipyard_task_enabled(
            self.config.ShipyardDr_ShipIndex,
            self.config.ShipyardDr_BuyAmount,
        )
        pr_enabled = self._shipyard_task_enabled(
            self.config.Shipyard_ShipIndex,
            self.config.Shipyard_BuyAmount,
        )
        if not dr_enabled and not pr_enabled:
            self.config.Scheduler_Enable = False
            self.config.task_stop()

        logger.hr('船坞DR', level=1)
        logger.attr('船坞DR上次运行', self.config.ShipyardDr_LastRun)
        if not dr_enabled:
            logger.info('船坞DR任务未配置，跳过')
        elif self.config.ShipyardDr_LastRun > get_server_last_update('04:00'):
            logger.warning('船坞DR任务今天已运行，跳过')
        else:
            self._shipyard_bp_rarity = 'DR'
            self.shipyard_run(series=self.config.ShipyardDr_ResearchSeries,
                              index=self.config.ShipyardDr_ShipIndex,
                              count=self.config.ShipyardDr_BuyAmount)

        logger.hr('船坞PR', level=1)
        logger.attr('船坞PR上次运行', self.config.Shipyard_LastRun)
        if not pr_enabled:
            logger.info('船坞PR任务未配置，跳过')
        elif self.config.Shipyard_LastRun > get_server_last_update('04:00'):
            logger.warning('船坞PR任务今天已运行，停止')
            self.config.task_delay(server_update=True)
            self.config.task_stop()
        else:
            self._shipyard_bp_rarity = 'PR'
            self.shipyard_run(series=self.config.Shipyard_ResearchSeries,
                              index=self.config.Shipyard_ShipIndex,
                              count=self.config.Shipyard_BuyAmount)

        self.config.task_delay(server_update=True)
