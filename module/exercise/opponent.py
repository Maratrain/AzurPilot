"""
演习对手分析与选择模块。

通过 OCR 识别对手的等级和战力信息，并根据配置的策略对对手进行排序。

支持的对手选择策略：
- max_exp: 按等级总和排序，优先攻击经验最高的对手
- easiest: 按综合难度排序，优先攻击最容易击败的对手
    （等级越低、战力越低的对手优先级越高）
- leftmost: 按默认顺序（最左优先）

常量说明：
    MAX_LVL_SUM: 最大等级总和（6 艘船 * 125 级 = 750）
    PWR_FACTOR: 战力归一化因子，用于将战力缩放到可比较范围
"""
import numpy as np

from module.base.button import ButtonGrid
from module.base.utils import image_left_strip
from module.exercise.assets import *
from module.logger import logger
from module.ocr.ocr import Digit
from module.ui.assets import BACK_ARROW
from module.ui.ui import UI

OPPONENT = ButtonGrid(origin=(104, 77), delta=(244, 0), button_shape=(212, 304), grid_shape=(4, 1))

# Mode 'easiest' constants
# MAX_LVL_SUM = Max Fleet Size (6) * Max Lvl (125)
# PWR_FACTOR used to make overall PWR manageable
MAX_LVL_SUM = 750
PWR_FACTOR = 100


class Level(Digit):
    """
    演习对手等级 OCR 识别器。

    对数字 OCR 进行预处理，去除左侧多余空白并添加白色边距，
    以提高演习准备界面中等级数字的识别准确率。
    """

    def pre_process(self, image):
        image = super().pre_process(image)
        image = image_left_strip(image, threshold=85, length=22)

        image = np.pad(image, ((5, 6), (0, 5)), mode='constant', constant_values=255)
        return image.astype(np.uint8)


class Opponent:
    """
    演习对手数据类，包含对手的等级和战力信息。

    从演习页面截图中 OCR 识别对手的两支舰队的等级和战力，
    并根据指定策略计算对手的优先级分数。

    Attributes:
        index (int): 对手索引（0-3，从左到右）。
        power (list[int]): 两支舰队的战力，如 [14848, 13477]。
        level (list[int]): 六艘舰船的等级，如 [120, 120, 120, 120, 120, 120]。
    """

    def __init__(self, main_image, fleet_image, index):
        self.index = index
        self.power = self.get_power(image=main_image)
        self.level = self.get_level(image=fleet_image)

        # [OPPONENT_1] ( 8256) 120 120 120 | (12356) 100  80  80
        level = [str(x).rjust(3, ' ') for x in self.level]
        power = ['(' + str(x).rjust(5, ' ') + ')' for x in self.power]
        logger.attr(
            '对手_%s' % index,
            ' '.join([power[0]] + level[:3] + ['|'] + [power[1]] + level[3:])
        )

    @staticmethod
    def get_level(image):
        """
        Args:
            image: Screenshot in EXERCISE_PREPARATION.

        Returns:
            list[int]: Fleet level, such as [120, 120, 120, 120, 120, 120].
        """
        level = []
        level += ButtonGrid(origin=(130, 259), delta=(168, 0), button_shape=(58, 21), grid_shape=(3, 1), name='LEVEL').buttons
        level += ButtonGrid(origin=(832, 259), delta=(168, 0), button_shape=(58, 21), grid_shape=(3, 1), name='LEVEL').buttons

        level = Level(level, name='LEVEL', letter=(255, 255, 255), threshold=128)
        result = level.ocr(image)
        return result

    def get_power(self, image):
        """
        Args:
            image: Screenshot in page_exercise.

        Returns:
            list[int]: Fleet power, such as [14848, 13477].
        """
        grids = ButtonGrid(origin=(222, 257), delta=(244, 30), button_shape=(72, 28), grid_shape=(4, 2), name='POWER')
        power = [grids[self.index, 0], grids[self.index, 1]]

        power = Digit(power, name='POWER', letter=(255, 223, 57), threshold=128)
        result = power.ocr(image)
        return result

    def get_priority(self, method="max_exp"):
        """
        Args:
            method: EXERCISE_CHOOSE_MODE

        Returns:
            np.ndarray: Priority of 4 opponents, such as [120, 113.2, 120, 95.3].
                        Higher priority means attack first.
        """
        if "easiest" in method:
            level = (1 - (np.sum(self.level) / MAX_LVL_SUM)) * 100
            team_pwr_div = np.count_nonzero(self.level) * PWR_FACTOR
            avg_team_pwr = np.sum(self.power) / team_pwr_div
            priority = level - avg_team_pwr
        else:
            priority = np.sum(self.level) / 6
        return priority


class OpponentChoose(UI):
    """
    对手选择器，负责检查和排序演习对手。

    依次进入每个对手的准备界面，OCR 识别其舰队等级和战力，
    然后根据策略对手进行排序，返回攻击优先级列表。

    Attributes:
        main_image (numpy.ndarray): 演习主页面截图，用于识别战力。
        opponents (list[Opponent]): 4 个对手的 Opponent 数据对象列表。
    """

    main_image = None
    opponents = []

    def _opponent_fleet_check_all(self):
        """
        依次检查所有 4 个对手的舰队信息。

        通过点击每个对手进入演习准备界面，OCR 识别等级和战力后返回。
        """
        self.opponents = []
        self.main_image = self.device.image

        for index in range(4):
            self.ui_click(click_button=OPPONENT[index, 0], check_button=EXERCISE_PREPARATION,
                          appear_button=NEW_OPPONENT, skip_first_screenshot=True)

            self.opponents.append(Opponent(main_image=self.main_image, fleet_image=self.device.image, index=index))

            self.ui_click(click_button=BACK_ARROW, check_button=NEW_OPPONENT,
                          appear_button=EXERCISE_PREPARATION, skip_first_screenshot=True)

    def _opponent_sort(self, method="max_exp"):
        """
        Args:
            method: EXERCISE_CHOOSE_MODE

        Returns:
            list[int]: List of opponent index, such as [2, 1, 0, 3].
                       Attack one by one.
        """
        order = np.argsort([- x.get_priority(method) for x in self.opponents])
        logger.attr('出战顺序', str(order))
        return order
