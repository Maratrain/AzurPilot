"""指挥喵天赋板截图 -> 天赋名识别（复用仓库自带 OCR）。

设计要点：

- 用 ``AlOcr.det()`` 拿整图的多行文本（``AlOcr.ocr()`` 只返回第一行，不适合整块面板）。
- 天赋名在游戏内只有 11~14px，实拍校准结论：**放大 3 倍**后识别率明显提升；
  天赋名是浅蓝字配浅底，**CLAHE 灰度增强**能把低对比度的那几条救回来。
  因此对同一张图跑 ``plain`` / ``clahe`` 两个变体，取并集。
- 识别结果用天赋库白名单匹配，天然过滤掉「天赋 / 陪玩 / 成长」等 UI 噪声。
- 不依赖坐标与分辨率：整图识别 + 白名单匹配，所以 1280×720 截图与其它尺寸截图都能用。
"""

import cv2
import numpy as np

from module.logger import logger
from module.meowfficer.score import Talent, match_cat, match_talent

# 放大倍数：实拍校准的结论值（1280×720 下天赋名约 11px）
DEFAULT_SCALE = 3.0
# 放大后的长边上限，避免 4K 截图把内存吃满
MAX_SIDE = 3840


def build_variants(image: np.ndarray, scale: float = DEFAULT_SCALE) -> dict[str, np.ndarray]:
    """构造用于 OCR 的预处理变体。

    Args:
        image: BGR 图像（``device.image`` 或 ``cv2.imread`` 的结果）。
        scale: 放大倍数，放大后长边受 :data:`MAX_SIDE` 限制。

    Returns:
        变体名 -> BGR 图像。``plain`` 为仅放大，``clahe`` 为灰度增强后转回 BGR。
    """
    height, width = image.shape[:2]
    factor = max(1.0, float(scale))
    if max(height, width) * factor > MAX_SIDE:
        factor = MAX_SIDE / max(height, width)
    if factor > 1.01:
        big = cv2.resize(image, None, fx=factor, fy=factor,
                         interpolation=cv2.INTER_LANCZOS4)
    else:
        big = image

    variants = {'plain': big}
    gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray)
    variants['clahe'] = cv2.cvtColor(clahe, cv2.COLOR_GRAY2BGR)
    return variants


def _iter_det_results(results):
    """兼容 ``det()`` 可能返回的多种元素形态，统一产出 ``(text, score)``。"""
    for item in results or []:
        text, score = '', 0.0
        if isinstance(item, dict):
            text = item.get('text', '')
            score = item.get('score', 0.0)
        elif isinstance(item, (tuple, list)):
            if item:
                text = item[0] or ''
            if len(item) >= 3:
                score = item[2] or 0.0
        else:
            text = str(item)
        if text:
            yield text, float(score)


def recognize(image: np.ndarray, ocr=None, scale: float = DEFAULT_SCALE,
              variants: tuple[str, ...] = ('plain', 'clahe')):
    """识别一张截图里的指挥喵天赋与猫名。

    Args:
        image: BGR 图像。既可以是整块天赋面板（一次识别多条），
            也可以是单条天赋的详情面板（只识别那一条）。
        ocr: 已初始化的 OCR 实例；为 ``None`` 时内部创建 ``AlOcr(name='cn')``。
        scale: 放大倍数。
        variants: 参与识别的预处理变体，取并集。

    Returns:
        ``(talents, cat)``：天赋列表（按天赋线去重并保留最高等级）与识别到的猫名
        （没识别到则为 ``None``）。
    """
    if ocr is None:
        from module.ocr.al_ocr import AlOcr
        ocr = AlOcr(name='cn')
        ocr.init()

    all_variants = build_variants(image, scale)
    found: dict[str, Talent] = {}
    cat = None
    raw_hits: list[str] = []

    for variant_name in variants:
        variant = all_variants.get(variant_name)
        if variant is None:
            continue
        try:
            results = ocr.det(variant)
        except Exception as e:
            logger.warning(f'[指挥喵-评分] OCR 变体 {variant_name} 识别失败: {e}')
            continue
        for text, _score in _iter_det_results(results):
            if cat is None:
                cat = match_cat(text)
            ref = match_talent(text)
            if ref is None:
                continue
            raw_hits.append(f'{text}->{ref.name}')
            current = found.get(ref.line)
            if current is None or ref.level > current.level:
                found[ref.line] = Talent(name=ref.name, line=ref.line, level=ref.level,
                                         kind=ref.kind, raw=text)

    if found:
        logger.info(f'[指挥喵-评分] 识别到天赋 {len(found)} 条：'
                    f'{"、".join(t.name for t in found.values())}')
    else:
        logger.info('[指挥喵-评分] 未识别到天赋（画面里可能没有天赋面板）')
    logger.debug(f'[指挥喵-评分] OCR 原始命中：{raw_hits}')
    return list(found.values()), cat


def recognize_talents(image: np.ndarray, ocr=None, scale: float = DEFAULT_SCALE,
                      variants: tuple[str, ...] = ('plain', 'clahe')) -> list[Talent]:
    """只取天赋，不关心猫名时的便捷封装（参数含义见 :func:`recognize`）。"""
    talents, _cat = recognize(image, ocr=ocr, scale=scale, variants=variants)
    return talents

