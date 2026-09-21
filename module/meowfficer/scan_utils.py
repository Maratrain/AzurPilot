"""指挥喵扫描用到的纯图像/文本工具函数。

从 :mod:`module.meowfficer.scan` 拆出来，一是让扫描主流程保持在 500 行以内
（仓库约定），二是这些函数不依赖设备，可以单独测试。
"""

import re

import numpy as np

# 相关能测到的最大位移（超过面板高度就没有重叠可对了）
MAX_SCROLL_SHIFT = 300
# 猫名 OCR 结果里需要排除的界面词
NAME_NOISE = ('空闲中', '后勤', '指挥', '战术', '加成', '一览', '技能', '天赋',
              '陪玩', '锁定', '使用', '成长', '确认', '消耗', '容量', '猫窝',
              '订购', '训练', '排行', '等级')


def _crop(image: np.ndarray, area: tuple) -> np.ndarray:
    """按 ``(x0, y0, x1, y1)`` 裁剪图像。"""
    x0, y0, x1, y1 = area
    return image[y0:y1, x0:x1]


def _mean_diff(a: np.ndarray, b: np.ndarray) -> float:
    """两张同尺寸图像的平均像素差，用于判断画面是否还在变化。"""
    if a is None or b is None or a.shape != b.shape:
        return 255.0
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())


def scroll_offset(before: np.ndarray, after: np.ndarray, max_shift: int = MAX_SCROLL_SHIFT) -> int:
    """估算 ``after`` 相对 ``before`` 内容向上滚动了多少像素。

    内容向上滚（看到更后面的猫）时，``before`` 里 y 处的图像会出现在 ``after`` 的 y-dy 处，
    所以拿 ``before[dy:]`` 和 ``after[:h-dy]`` 逐 dy 比较，取平均差最小的那个 dy。

    Args:
        before: 滚动前裁剪的面板图像。
        after: 滚动后同区域的面板图像。
        max_shift: 最大可测位移（超出面板高度就没有重叠可对了）。

    Returns:
        int: 向上滚动的像素数；画面基本没动时返回 0。
    """
    if before is None or after is None or before.shape != after.shape or before.size == 0:
        return 0
    height = before.shape[0]
    best_dy, best_diff = 0, None
    for dy in range(0, min(max_shift, height - 1) + 1):
        a = before[dy:]
        b = after[:len(a)]
        if a.size == 0:
            break
        diff = float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())
        if best_diff is None or diff < best_diff:
            best_dy, best_diff = dy, diff
    return best_dy


def pick_cat_name(texts) -> str:
    """从一组 OCR 文本里挑出猫名。

    猫名可能是玩家自定义的，所以不强行匹配天赋库，只做去噪：
    取 2~6 个字、含汉字、且不含界面词的最长候选。

    Args:
        texts: OCR 出来的文本序列。

    Returns:
        str: 猫名；没有合格候选时返回空串。
    """
    best = ''
    for text in texts:
        text = (text or '').strip()
        if not 2 <= len(text) <= 6:
            continue
        if not any('\u4e00' <= char <= '\u9fff' for char in text):
            continue
        if any(word in text for word in NAME_NOISE):
            continue
        if len(text) > len(best):
            best = text
    return best


def parse_level(texts):
    """从一组 OCR 文本里取指挥喵等级。

    等级条在左下角，OCR 常见结果是 ``LV30`` / ``LV:30`` / ``IV:30``，
    所以优先认带 ``v``/``lv`` 的候选，其余只收「纯数字或单字母+数字」的形态，
    避免把「后勤 101」之类的属性值当成等级。

    Args:
        texts: OCR 出来的文本序列。

    Returns:
        int | None: 等级；识别不到返回 ``None``。
    """
    weak = None
    for text in texts:
        text = (text or '').strip()
        match = re.search(r'(\d{1,3})', text)
        if not match:
            continue
        value = int(match.group(1))
        if not 1 <= value <= 60:
            continue
        lowered = text.lower()
        # OCR 常把 Lv 认成 LV / IV / WV，都会带上 v
        if 'v' in lowered:
            return value
        if weak is None and re.fullmatch(r'[a-z:. ]*\d{1,3}', lowered):
            weak = value
    return weak
