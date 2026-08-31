import time
from abc import ABCMeta, abstractmethod

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple, Union

import cv2
import numpy as np

import module.config.server as server
from module.base.button import ButtonGrid
from module.base.utils import (color_similar, crop, extract_letters, get_color,
                               limit_in,
                               float2str)
from module.combat.level import LevelOcr
from module.logger import logger
from module.ocr.ocr import Digit
from module.secretary.dock import (CARD_GRIDS,CARD_LEVEL_GRIDS, CARD_RARITY_GRIDS, CARD_FAVORABILITY_GRIDS)
from module.secretary.assets import SECRETARY_SELECTED

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

            logger.debug(
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
                            logger.debug(
                                f'Favorability reorder: {current} -> {best} (both <100, use r2)'
                            )
                            values[i] = best
                            changed = True
                        continue

                    # r1 未识别，r2>100：去掉百位
                    if (r1 in ("", None) or r1v is None) and r2v is not None and r2v > 100:
                        best = r2v % 100
                        if best != current:
                            logger.debug(
                                f'Favorability reorder: {current} -> {best} (r1 empty, r2 remove hundred)'
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
                    logger.debug(
                        f'Favorability reorder: {current} -> {best}'
                    )
                    values[i] = best
                    changed = True

        return values

class SelectedDetector:

    def __init__(self):
        self.grids = CARD_GRIDS

        SECRETARY_SELECTED.ensure_template()

        self.template = SECRETARY_SELECTED.image

        if len(self.template.shape) == 3:
            self.template = cv2.cvtColor(
                self.template,
                cv2.COLOR_BGR2GRAY,
            )

        _, self.template = cv2.threshold(
            self.template,
            0,
            255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU,
        )
        base = CARD_GRIDS.buttons[0]
        self.offset = (
            125 - base.area[0],
            165 - base.area[1],
            201 - base.area[0],
            191 - base.area[1],
        )

    def _scan(self, image):
        result = []
        for button in self.grids.buttons:
            area = (
                button.area[0] + self.offset[0],
                button.area[1] + self.offset[1],
                button.area[0] + self.offset[2],
                button.area[1] + self.offset[3],
            )

            img = crop(image, area)

            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            _, binary = cv2.threshold(
                gray,
                0,
                255,
                cv2.THRESH_BINARY + cv2.THRESH_OTSU,
            )

            res = cv2.matchTemplate(
                binary,
                self.template,
                cv2.TM_SQDIFF_NORMED,
            )
            _, sim, _, _ = cv2.minMaxLoc(res)
            SELECTED_THRESHOLD = 0.4
            selected = sim < SELECTED_THRESHOLD
            logger.debug(
                f"{button.name} "
                f"selected={selected} "
                f"sim={sim:.3f}"
            )

            result.append(selected)

        return result
    def scan(self, image):
        return self._scan(image)

@dataclass(frozen=True)
class SecretaryShip:
    rarity: str = ''
    level: int = 0
    favorability: int = 0
    selected: bool = False
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
    """舰船扫描器，用于扫描船坞页面第一页所有舰船的属性信息。

    必须在船坞初始页面使用（设置筛选器后不能有滚动操作），否则结果不可靠。

    Args:
        rarity: 稀有度筛选，取值 'any'、'common'、'rare'、'elite'、'super_rare'，支持 str 或 list。
        level: 等级范围 (下限, 上限)，自动限制在 [1, 125]。
        favorability: 好感度范围 (下限, 上限)，自动限制在 [0, 200]。
        descending: 好感度降序，用于 OCR 结果排序一致性修正。

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
        self.selected_detector = SelectedDetector()

        self.set_limitation(
            level=level, favorability=favorability, rarity=rarity)

    def _scan(self, image) -> List:
        for scanner in self.sub_scanners.values():
            scanner.scan(image, cached=True)

        candidates: List[SecretaryShip] = []
        selected_list = self.selected_detector.scan(image)
        for level, favorability, rarity, selected, button, hash_ in zip(
            self.sub_scanners['level'].results,
            self.sub_scanners['favorability'].results,
            self.sub_scanners['rarity'].results,
            selected_list,
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
                    selected=selected,
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

        支持的属性：'level'、'favorability'、'rarity'、'hash'。
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
