import os
import time
from abc import ABCMeta, abstractmethod

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple, Union

import cv2
import numpy as np

import module.config.server as server
from module.base.button import ButtonGrid
from module.base.utils import (color_similar, crop, extract_letters, get_color,
                               image_color_count, limit_in,
                               random_normal_distribution_int,
                               random_rectangle_point,
                               float2str)
from module.combat.level import LevelOcr
from module.logger import logger
from module.ocr.ocr import Digit
from module.secretary.dock import (CARD_GRIDS,CARD_LEVEL_GRIDS, CARD_RARITY_GRIDS, CARD_FAVORABILITY_GRIDS)

class Scanner(metaclass=ABCMeta):
    _results: List = None
    _enabled: bool = True
    _disabled_value: List[None] = [None] * 14
    grids: ButtonGrid = None

    @property
    def results(self) -> List:
        return self._results

    @abstractmethod
    def _scan(self, image) -> List:
        pass

    @abstractmethod
    def limit_value(self, value) -> Any:
        pass

    def clear(self) -> None:
        """清除所有缓存的扫描结果。"""
        self._results.clear()

    def scan(self, image, cached=False, output=False) -> Union[List, None]:
        """执行扫描，返回结果列表。

        启用时返回真实扫描结果，禁用时返回全 None 列表。
        多次扫描场景建议使用 cached=True 缓存结果。

        Args:
            image: 截图图像。
            cached: 是否将结果追加到缓存。
            output: 是否将结果逐条输出到日志。

        Returns:
            list 或 None: cached=False 时返回结果列表，cached=True 时返回 None。
        """
        results: List = self._scan(image) if self._enabled else self._disabled_value

        if output:
            for result in results:
                logger.info(f'{result}')

        if cached:
            self._results.extend(results)
        else:
            return results

    def move(self, vector) -> None:
        """移动网格坐标，同步更新内部 ButtonGrid。"""
        self.grids = self.grids.move(vector)

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

class RarityScanner(Scanner):
    def __init__(self) -> None:
        super().__init__()
        self._results = []
        self.grids = CARD_RARITY_GRIDS
        self.value_list: List[str] = ['common', 'rare', 'elite', 'super_rare','unknown']

    def color_to_rarity(self, color: Tuple[int, int, int]) -> str:
        """将卡片颜色转换为舰船稀有度。

        稀有度分为 common、rare、elite、super_rare、unknown 五种。
        彩虹（ultra）稀有度因颜色差异过大，标记为 'unknown'。

        Args:
            color: RGB 颜色元组 (r, g, b)。

        Returns:
            str: 稀有度字符串。
        """
        if color_similar(color, (171, 174, 186)):
            return 'common'
        elif color_similar(color, (106, 194, 248)):
            return 'rare'
        elif color_similar(color, (151, 134, 254)):
            return 'elite'
        elif color_similar(color, (247, 221, 101)):
            return 'super_rare'
        else:
            # 彩虹稀有度颜色差异过大，无法统一识别
            return 'unknown'

    def _scan(self, image) -> List:
        return [self.color_to_rarity(get_color(image, button.area))
                for button in self.grids.buttons]

    def limit_value(self, value) -> str:
        return value if value in self.value_list else 'any'

class DHash:
    EQ_THRES: int = 30

    def __init__(self, image, size=8) -> None:
        self.code = DHash.gen_hash(image, size)

    @staticmethod
    def gen_hash(image, size=8) -> str:
        if len(image.shape) > 2:
            image = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        image = cv2.resize(image, (size + 1, size + 1))
        row_diff = np.packbits(image[:-1, :-1] > image[1:, :-1])
        col_diff = np.packbits(image[:-1, :-1] > image[:-1, 1:])
        row_hash: str = ''.join([f'{i:>02x}' for i in row_diff])
        col_hash: str = ''.join([f'{i:>02x}' for i in col_diff])

        return f'{row_hash}{col_hash}'

    @staticmethod
    def distance(__x, __y) -> int:
        if isinstance(__x, DHash) and isinstance(__y, DHash):
            __x, __y = int(__x.code, 16), int(__y.code, 16)
        elif isinstance(__x, str) and isinstance(__y, str):
            __x, __y = int(__x, 16), int(__y, 16)

        return bin(__x ^ __y).count('1')

    def __eq__(self, __o: object) -> bool:
        return type(self) == type(__o) and DHash.distance(self, __o) < DHash.EQ_THRES

    def __repr__(self) -> str:
        return self.code

class HashGenerator(Scanner):
    def __init__(self, length=8) -> None:
        super().__init__()
        self._results = []
        self.length = length
        self.grids = CARD_GRIDS

    def _scan(self, image) -> List:
        image_list = [crop(image, button.area) for button in self.grids.buttons]

        return [DHash(image, self.length) for image in image_list]

    def limit_value(self, value) -> Any:
        pass

class LevelScanner(Scanner):
    def __init__(self) -> None:
        super().__init__()
        self._results = []
        self.grids = CARD_LEVEL_GRIDS
        self.ocr_model = LevelOcr(self.grids.buttons,
                                  name='DOCK_LEVEL_OCR', threshold=64)

    def _scan(self, image) -> List:
        return self.ocr_model.ocr(image)

    def limit_value(self, value) -> int:
        return limit_in(value, 1, 125)
    
    def move(self, vector) -> None:
        super().move(vector)
        self.ocr_model.buttons = [button.area for button in self.grids.buttons]

class FavorabilityScanner(Scanner):
    def __init__(self, descending=True):
        super().__init__()
        self._results = []
        self.grids = CARD_FAVORABILITY_GRIDS
        self.descending = descending
        if server.server != 'jp':
            self.ocr_model = FavorabilityDigit(
                self.grids.buttons,
                name='SECRETARY_FAVORABILITY_OCR',
                threshold=64,
                descending=descending,
            )
        else:
            self.ocr_model = FavorabilityDigit(
                self.grids.buttons,
                name='SECRETARY_FAVORABILITY_OCR',
                letter=(201, 201, 201), 
                threshold=176,
                descending=descending,
            )
    def _scan(self, image):
        return self.ocr_model.ocr(image)

    def limit_value(self, value):
        return limit_in(value, 0, 200)

    def move(self, vector):
        super().move(vector)
        self.ocr_model.buttons = [button.area for button in self.grids.buttons]

class FavorabilityDigit(Digit):
    def __init__(self, *args, descending=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.descending = descending

    def pre_process(self, image):
        if server.server == 'jp':
            image_gray = extract_letters(image, letter=(255, 255, 255), threshold=self.threshold)
            right_side = np.nonzero(image_gray[0:16, :].max(axis=0) > 192)[-1]
            for i, col in enumerate(right_side):
                if i < col:
                    break
            image = image[:, :i]
        image = super().pre_process(image)
        return image

    def pre_process_color(self, image):
        return cv2.resize(
            image,
            None,
            fx=2,
            fy=2,
            interpolation=cv2.INTER_CUBIC
        )

    def normalize_ocr(self, result):
        """
        OCR判断用，不直接int
        保留信息
        """
        if not result:
            return ''
        return (
            result
            .replace('I', '1')
            .replace('D', '0')
            .replace('L', '0')
            .replace('S', '5')
            .replace('B', '8')
            .replace('C', '0')
        )

    def ocr(self, image, direct_ocr=False):
        start_time = time.time()

        # ---------- 第一次：原来的 OCR ----------
        image_list = [
            self.pre_process(crop(image, area))
            for area in self.buttons
        ]
        result1 = self.cnocr.atomic_ocr_for_single_lines(
            image_list,
            self.alphabet
        )
        result1 = [''.join(r) for r in result1]


        # ---------- 第二次：彩色 OCR ----------
        image_list = [
            self.pre_process_color(crop(image, area))
            for area in self.buttons
        ]
        result2 = self.cnocr.atomic_ocr_for_single_lines(
            image_list,
            self.alphabet
        )
        result2 = [''.join(r) for r in result2]

        ocr_pairs = []
        result = []

        for r1, r2 in zip(result1, result2):

            n1 = self.normalize_ocr(r1)
            n2 = self.normalize_ocr(r2)

            ocr_pairs.append((n1, n2))

            logger.info(
                f'Favorability OCR raw: r1={r1}, r2={r2}'
            )

            # ==================================================
            # 1. 特殊低值丢0修复
            #
            # 10 -> 100
            # 10 -> 400
            #
            # 只处理10~19
            # 防止:
            # 53 -> 153
            # 53 -> 753
            # ==================================================

            if (
                n1.isdigit()
                and 10 <= int(n1) < 20
            ):
                if (
                    n2.isdigit()
                    and (
                        100 <= int(n2) <= 200
                        or int(n2) > 200
                    )
                ):
                    result.append('100')
                    continue



            # ==================================================
            # 2. r1 正常数字
            #
            # 例如:
            # 53
            # 91
            # 100
            # 200
            #
            # 优先相信原OCR
            # ==================================================

            if (
                n1.isdigit()
                and r1 == n1
                and 0 <= int(n1) <= 200
            ):
                result.append(n1)
                continue



            # ==================================================
            # 3. r1乱码
            #
            # 例如:
            # 1DI
            # 10L
            # D0
            #
            # 使用彩色OCR
            # ==================================================

            if (
                n2.isdigit()
                and 0 <= int(n2) <= 200
            ):
                result.append(n2)
                continue



            # ==================================================
            # 4. fallback
            # ==================================================

            if n1.isdigit():
                result.append(n1)
            elif n2.isdigit():
                result.append(n2)
            else:
                result.append('0')


        result = [self.after_process(x) for x in result]

        result = self.fix_order(
            result,
            ocr_pairs,
            descending=self.descending,
        )
        if len(result) == 1:
            result = result[0]

        logger.attr(
            name='%s %ss' % (
                self.name,
                float2str(time.time() - start_time)
            ),
            text=str(result)
        )

        return result
    def after_process(self, result):
        # 唐斯头发区域的随机 OCR 误识别
        # DOCK_EMOTION_OCR 识别结果 "044" 修正为 "44"
        if result == '044' or result == 'D44':
            result = '0'

        result = super().after_process(result)
        if result > 200:
            result //=10

        return result

    def fix_order(self, result, ocr_pairs, descending=True):
        """
        根据整页排序关系修正 OCR。

        Args:
            result: 当前OCR结果(list[int])
            ocr_pairs: [(r1,r2), ...]
            descending: True=降序 False=升序
        """
        values = result[:]

        if len(values) < 2:
            return values

        changed = True

        # 最多迭代3轮，直到没有变化
        for _ in range(3):

            if not changed:
                break
            changed = False

            for i in range(len(values)):

                current = values[i]

                r1, r2 = ocr_pairs[i]

                candidates = []
                # ---------- 特殊规则 ----------
                if r1.isdigit() and r2.isdigit():
                    r1v = int(r1)
                    r2v = int(r2)

                    # r1<100，r2<100：直接采用r2
                    if r1v < 100 and r2v < 100:
                        best = r2v
                        if best != current:
                            logger.info(
                                f'Favorability reorder: {current} -> {best} (both <100, use r2)'
                            )
                            values[i] = best
                            changed = True
                        continue

                    # r1<100，r2>100：去掉百位
                    if r1v < 100 and r2v > 100:
                        best = r2v % 100
                        if best != current:
                            logger.info(
                                f'Favorability reorder: {current} -> {best} (r2 remove hundred)'
                            )
                            values[i] = best
                            changed = True
                        continue
                for raw in (r1, r2):
                    if raw.isdigit():
                        v = int(raw)
                        if 0 <= v <= 200 and v not in candidates:
                            candidates.append(v)

                if not candidates:
                    continue

                best = current
                best_score = -1

                for candidate in candidates:

                    score = 0

                    # ---------- 前一张 ----------
                    if i > 0:
                        prev = values[i - 1]

                        if descending:
                            if prev >= candidate:
                                score += 1
                            else:
                                score -= 1
                        else:
                            if prev <= candidate:
                                score += 1
                            else:
                                score -= 1

                    # ---------- 后一张 ----------
                    if i < len(values) - 1:
                        nxt = values[i + 1]

                        if descending:
                            if candidate >= nxt:
                                score += 1
                            else:
                                score -= 1
                        else:
                            if candidate <= nxt:
                                score += 1
                            else:
                                score -= 1

                    # ---------- 与当前值一致奖励 ----------
                    if candidate == current:
                        score += 0.1

                    if score > best_score:
                        best_score = score
                        best = candidate

                if best != current:
                    logger.info(
                        f'Favorability reorder: {current} -> {best}'
                    )
                    values[i] = best
                    changed = True

        return values

@dataclass(frozen=True)
class SecretaryShip:
    rarity: str = ''
    level: int = 0
    favorability: int = 0
    button: Any = None
    hash_: str = field(default='', repr=False)

    def satisfy_limitation(self, limitation) -> bool:
        """检查舰船是否满足筛选条件。

        遍历舰船的所有属性，与 limitation 中的限制逐一比对。
        str/int 类型要求精确匹配，tuple 表示范围，list 表示枚举。

        Args:
            limitation: 筛选条件字典，key 为属性名，value 为限制值。

        Returns:
            bool: 是否满足所有限制条件。
        """
        for key in self.__dict__:
            value = limitation.get(key)
            if self.__dict__[key] is not None and value is not None:
                # str 和 int 要求精确匹配
                if isinstance(value, (str, int)):
                    if value == 'any':
                        continue
                    if self.__dict__[key] != value:
                        return False
                # tuple 表示范围限制
                elif isinstance(value, tuple):
                    if not (value[0] <= self.__dict__[key] <= value[1]):
                        return False
                # list 表示枚举限制
                elif isinstance(value, list):
                    if self.__dict__[key] not in value:
                        return False

        return True



class ShipScanner(Scanner):
    """舰船扫描器，用于扫描船坞页面中所有舰船的属性信息。

    必须在船坞初始页面使用（设置筛选器后不能有滚动操作），否则结果不可靠。
    如需跨页扫描，请使用 DockScanner。

    Args:
        rarity: 稀有度筛选，取值 'any'、'common'、'rare'、'elite'、'super_rare'，支持 str 或 list。
        level: 等级范围 (下限, 上限)，自动限制在 [1, 125]。
        emotion: 情绪范围 (下限, 上限)，自动限制在 [0, 150]。
        fleet: 舰队编号，0 表示不在任何编队，自动限制在 [0, 6]。
        status: 状态筛选，取值 'free'、'battle'、'commission'、'in_hard_fleet'、'in_event_fleet'。

    属性支持两个特殊值 False 和 None：

    使用 False:
        跳过该属性的扫描，结果中对应字段为 None。
        设置为 False 后只能通过 enable() 重新启用，
        disable() 的效果与设为 False 相同。

    使用 None:
        正常扫描该属性，但筛选时忽略该属性的限制。
        调用 set_limitation(property=...) 可重置限制（包括设为 None）。

    Examples:
        ShipScanner(rarity=False) 扫描时忽略稀有度，结果中 rarity 为 None。
    """
    def __init__(
        self,
        rarity: str = 'any',    
        level: Tuple[int, int] = (1, 125),
        favorability: Tuple[int, int] = (0, 200),
        descending=True,
    ) -> None:
        super().__init__()
        self._results = []
        self.grids = CARD_GRIDS
        self.limitation: Dict[str, Union[str, int, Tuple[int, int]]] = {
            'level': (1, 125),
            'favorability': (0, 200),
            'rarity': 'any',
        }

        # 每个舰船属性绑定一个独立的子扫描器
        self.sub_scanners: Dict[str, Scanner] = {
            'level': LevelScanner(),
            'rarity': RarityScanner(),
            'favorability': FavorabilityScanner(descending=descending),
            'hash': HashGenerator(),
        }

        self.set_limitation(
            level=level, favorability=favorability, rarity=rarity)

    def _scan(self, image) -> List:
        for scanner in self.sub_scanners.values():
            scanner.scan(image, cached=True)

        candidates: List[SecretaryShip] = []

        for level, favorability, rarity, button, hash_ in zip(
            self.sub_scanners['level'].results,
            self.sub_scanners['favorability'].results,
            self.sub_scanners['rarity'].results,
            self.grids.buttons,
            self.sub_scanners['hash'].results,
        ):
            # 空卡片，强制清零好感度
            if level == 0:
                favorability = 0

            candidates.append(
                SecretaryShip(
                    level=level,
                    favorability=favorability,
                    rarity=rarity,
                    button=button,
                    hash_=hash_,
                )
            )

        for scanner in self.sub_scanners.values():
            scanner.clear()

        return candidates

    def scan(self, image, cached=False, output=True) -> Union[List, None]:
        ships = super().scan(image, cached, output)
        if not cached:
            return [ship for ship in ships if ship.satisfy_limitation(self.limitation)]

    def move(self, vector) -> None:
        """移动网格坐标，同步更新所有子扫描器和自身的网格位置。"""
        for scanner in self.sub_scanners.values():
            scanner.move(vector)

        super().move(vector)

    def limit_value(self, key, value) -> None:
        if value is None:
            self.limitation[key] = None
        elif isinstance(value, tuple):
            lower, upper = value
            lower = self.sub_scanners[key].limit_value(lower)
            upper = self.sub_scanners[key].limit_value(upper)
            self.limitation[key] = (lower, upper)
        elif isinstance(value, list):
            self.limitation[key] = [self.sub_scanners[key].limit_value(v) for v in value]
        else:
            self.limitation[key] = self.sub_scanners[key].limit_value(value)

    def enable(self, *args) -> None:
        """启用指定属性的子扫描器。

        支持的属性：'level'、'favorability'、'rarity'。
        """
        for name, scanner in self.sub_scanners.items():
            if name in args:
                scanner.enable()

    def disable(self, *args) -> None:
        """禁用指定属性的子扫描器。

        支持的属性：'level'、'emotion'、'rarity'、'fleet'、'status'。
        """
        for name, scanner in self.sub_scanners.items():
            if name in args:
                scanner.disable()

    def set_limitation(self, **kwargs):
        """设置舰船筛选条件。

        Args:
            rarity: 稀有度，取值 'any'、'common'、'rare'、'elite'、'super_rare'。
            level: 等级范围 (下限, 上限)，自动限制在 [1, 125]。
            favorability: 好感度范围 (下限, 上限)，自动限制在 [0, 200]。
        """
        for attr in self.limitation.keys():
            value = kwargs.get(attr, self.limitation[attr])
            self.limit_value(key=attr, value=value)
            if value is False:
                self.sub_scanners[attr].disable()

        logger.info(f'Limitations set to {self.limitation}')


class DockScanner(ShipScanner):
    """船坞扫描器，支持跨页扫描。

    与 ShipScanner 相同，必须从船坞初始页面开始扫描。
    扫描过程会自动滚动船坞，扫描完成后自动停止。
    """
    SCAN_ZONES: Dict[str, Tuple[int, int, int, int]] = {
        'dock': (93, 55, 1219, 719),
    }
    def __init__(self, zone: str = 'dock', test_name: str = '') -> None:
        self._results = []
        self.scan_zone: Tuple[int, int, int, int] = self.SCAN_ZONES[zone]
        self.zone_top: int = self.scan_zone[1]
        self.zone_height: int = self.scan_zone[3] - self.scan_zone[1]
        self.grids_top: int = 76
        # 用于重新定位和滚动计算
        self.mean_color_set = deque(maxlen=2)
        self.moving_distance: int = 0
        self.bound = []
        # 用于扫描稳定性判断
        self._stable: bool = False
        self._no_change: int = 0
        self.last_results = []
        self.retry: int = 0

        self.scanner = ShipScanner()

        # 以下为调试信息相关
        self.save_debug_info = False
        self.debug_folder = f'./log/dock_scan_test/{test_name}_{int(time.time()*1000):x}'
        if self.save_debug_info:
            if not os.path.exists('./log/dock_scan_test'):
                os.mkdir('./log/dock_scan_test')
            if not os.path.exists(self.debug_folder):
                os.mkdir(self.debug_folder)
        self.debug_info = {
            'time' : 0,
            'ship_count' : 0,
            'dock_size' : 0,
            'ocr_mistake' : 0,
            'reposition_retry' : 0,
        }
        self.ocr_mistake_image = []
        self.extend_log = []
        self.moving_distance_log = []

    def limit_value(self, value) -> Any:
        pass

    @property
    def stable(self) -> bool:
        if self._stable:
            self._stable = False
            return True
        else:
            return False

    @property
    def mean_color(self):
        return self.mean_color_set[-1] if self.mean_color_set else None

    @mean_color.setter
    def mean_color(self, value):
        self.mean_color_set.append(value)

    def no_change(self) -> bool:
        return self._no_change > 3

    def _find_bound(self, image) -> List[int]:
        """粗略定位舰船卡片间的空白行位置。

        空白行的标准差会出现明显波谷，通过定位波谷位置即可获得
        空白行的大致位置。精度不高，但只需其中心点即可。
        """
        image = crop(image, self.scan_zone)
        image = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        std = np.std(image, axis=1)
        move_avg = np.convolve(std, np.ones((5, )) / 5, mode='valid')
        gap_seq = list(np.nonzero(move_avg < 20)[0]) + [1000]

        bound = []
        start = 0
        for i in range(len(gap_seq) - 1):
            if gap_seq[i + 1] - gap_seq[i] > 50 and i + 1 - start > 10:
                bound.append(np.mean(gap_seq[start : i + 1]).astype(int))
                start = i + 1
        if len(bound) > 1:
            # 最后一行不可靠，限制其与上一行的最大间距
            bound[-1] = min(bound[-2] + 225, bound[-1])

        return bound

    def reset_position(self) -> None:
        offset = 76 - self.grids_top
        self.grids_top += offset
        self.scanner.move((0, offset))
        self.mean_color_set.append(self.mean_color_set[0])

    def reposition(self, image, bound) -> None:
        """精确调整网格位置。

        从 bound 给出的空白行中心点向下搜索，第一个颜色与 mean_color
        差异较大的行即为新 CARD_GRIDS 的顶部位置。
        """
        scan_image = crop(image, self.scan_zone)
        if self.mean_color is not None:
            for y in range(0, 20):
                if not color_similar(np.mean(scan_image[bound[0] + y], axis=0), self.mean_color, 60):
                    break
            offset = y + self.zone_top + bound[0] + 1 - self.grids_top
            self.grids_top += offset
            self.scanner.move((0, offset))

        self.mean_color = np.mean(scan_image[bound[-1]], axis=0)

    def _remove_duplicate(self, results) -> int:
        """去除重复扫描结果，返回新增条目数。

        两种重复情况：
            整页重复：新结果与上一次完全相同。
            半页重复：新结果前半部分与上次后半部分相同。
        两种情况下，len(results) < 14 表示已到达底部。
        """
        if self._results:
            if all([old.hash_ == new.hash_ for new, old in zip(results, self._results[-len(results):])]):
                self._no_change += 999 if len(results) < 14 else 1
                return 0
            elif all([old.hash_ == new.hash_ for new, old in zip(results[:7], self._results[-7:])]):
                self._results.extend(results[7-len(results):])
                self._no_change = 999 if len(results) < 14 else 0
                return len(results)-7

        self._no_change = 0
        self._results.extend(results)
        return len(results)

    def ensure_in_dock(self, main) -> None:
        if main.appear(SHIP_DETAIL_CHECK, offset=(30, 30)):
            main.ui_back(DOCK_CHECK)

    def _scan(self, image) -> None:
        bound = self._find_bound(image)
        if len(bound) == 1:
            # 没有舰船出现，页面已稳定
            self._stable = True
            return
        elif len(bound) == 2:
            if self.bound != bound:
                self._stable = False
                self.bound = bound
                return
        else:
            self.bound.clear()

        self.moving_distance = bound[-1] - (self.zone_height - 204 * 2 - 23 * 3) / 2 * 1.5
        self.moving_distance_log.append(self.moving_distance)
        self.reposition(image, bound)
        results = self.scanner.scan(image, cached=False, output=False)
        if not results:
            self.retry += 1
            self.debug_info['reposition_retry'] += 1
            logger.info(f'No ship was detected, reset the position. Retry {self.retry} time(s)')
            self.reset_position()
            self.reposition(image, bound)
            results = self.scanner.scan(image, cached=False, output=False)
            if self.retry > 3:
                self.moving_distance = random_normal_distribution_int(10, 20)
                self.retry = 0
        else:
            self.retry = 0

        if all([old.hash_ == new.hash_ for new, old in zip(results, self.last_results)]):
            self._stable = True
            inc = self._remove_duplicate(results)
            if inc:
                level = [ship.level for ship in results]
                self.extend_log.append((inc, self.grids_top, level, cv2.cvtColor(image, cv2.COLOR_BGR2RGB)))

                level = [ship.level for ship in results]
                greater_equal = [level[i-1] >= level[i] for i in range(1, len(level))]
                in_order = all(x == greater_equal[0] for x in greater_equal)
                if not in_order:
                    interrupt = np.where(np.array(greater_equal)==False)[0].tolist()
                    values = [level[i] for i in interrupt]
                    level_info = '_'.join([f'{p,v}' for p,v in zip(interrupt,values)])
                    self.ocr_mistake_image.append((
                        f"{self.debug_info['ocr_mistake']}_{self.grids_top}_{level_info}.png", cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                    ))
                    self.debug_info['ocr_mistake'] += 1

        self.last_results = results

    def multi_scan(self, main) -> None:
        """执行船坞多页扫描，自动滚动并收集所有舰船信息。

        扫描原理示意：
            □ | □ | □                          --------- (*)
            ---------                          ■ | □ | □
            □ | □ | □       --- 滚动 --->      ---------
            --------- (*)                      □ | □ | □
            ■ | □ | □                          ---------
        □ 和 ■ 为舰船，| 和 - 为舰船间的空白间隔。
        需要计算 (*) 移动的距离来检测滚动。

        舰船间空白区域的颜色变化很小，将图像灰度化后用 np.std
        过滤即可获得空白行的位置。
        """
        from module.retire.enhancement import OCR_DOCK_AMOUNT
        self.debug_info['dock_size'], _, _ = OCR_DOCK_AMOUNT.ocr(main.device.image)

        if DOCK_SCROLL.appear(main):
            # 预先滚动到底部再回到顶部，可部分预加载舰船图像，
            # 降低扫描过程中卡住的可能性
            DOCK_SCROLL.set_bottom(main)
            DOCK_SCROLL.set_top(main)

        start_time = time.time()
        while True:
            while not self.stable:
                main.device.screenshot()
                self.ensure_in_dock(main)
                self._scan(main.device.image)

            click_zone_index = random_normal_distribution_int(0, 6)
            start = random_rectangle_point((
                240 + click_zone_index * 165, 555, 250 + click_zone_index * 165, 719
            ))
            end = (start[0], start[1] - self.moving_distance)
            sharp_end = (end[0] - 165, end[1])
            main.device.swipe(start, end)
            main.device.click_record.pop()
            main.device.swipe(end, sharp_end)
            main.device.click_record.pop()

            if not DOCK_SCROLL.appear(main) or (DOCK_SCROLL.at_bottom(main) and self.no_change()):
                break
        end_time = time.time()
        self.debug_info['time'] = end_time - start_time
        self.debug_info['ship_count'] = len(self._results)

        if self.save_debug_info:
            # 保存哈希相似度数据
            hashs = [ship.hash_ for ship in self.results]
            sims = []
            for i in range(len(hashs)):
                for j in range(i+1, len(hashs)):
                    sims.append(DHash.distance(hashs[i],hashs[j]))
            np.save(f'{self.debug_folder}/{len(sims)}.npy', np.array(sims))
            # 保存 OCR 识别错误的图像
            for name, image in self.ocr_mistake_image:
                cv2.imwrite(f'{self.debug_folder}/{name}.png', image)
            # 保存去重异常的图像
            self.extend_log.append((0, None))
            for i in range(len(self.extend_log) - 1):
                cnt, top, level, image = self.extend_log[i]
                if cnt != 14 and cnt != 7 and self.extend_log[i+1][0] != 0:
                    cv2.imwrite(f'{self.debug_folder}/len={cnt}_top={top}_id={i}.png', image)
                    self.debug_info[f'len={cnt}_top={top}_id={i}'] = level
            # 保存调试信息摘要
            self.debug_info['moving_mean'] = np.mean(self.moving_distance_log)
            with open(f'{self.debug_folder}/debug_info.txt', 'w', encoding='utf-8') as f:
                for k,v in self.debug_info.items():
                    f.write(f'{k} = {v}\n')

            logger.info(f'debug info has been saved in {self.debug_folder}')

    def scan(self, image, cached=False, output=True) -> Union[List, None]:
        """请使用 multi_scan() 代替。"""
        pass

    def scan_one_fleet(self, fleet: int = None) -> List[Ship]:
        """扫描指定舰队中的所有舰船。

        Args:
            fleet: 舰队编号，未指定时使用 self.fleet。

        Returns:
            list[Ship]: 舰船列表。
        """
        pass


    def scan_whole_dock(self) -> List[Ship]:
        """扫描整个船坞。"""
        pass