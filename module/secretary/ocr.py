from module.logger import logger
from module.ocr.ocr import Digit, crop
import cv2


class SecretaryDigit(Digit):

    def ocr(self, image, direct_ocr=False):

        if direct_ocr:
            image_list = [
                self.pre_process(i)
                for i in image
            ]
        else:
            image_list = [
                self.pre_process(crop(image, area))
                for area in self.buttons
            ]

        # 关键：跳过 crop_to_text
        # image_list = [crop_to_text(i) for i in image_list]

        result_list = self.cnocr.atomic_ocr_for_single_lines(
            image_list,
            self.alphabet
        )

        result_list = [
            ''.join(result)
            for result in result_list
        ]

        result_list = [
            self.after_process(result)
            for result in result_list
        ]

        if len(self.buttons) == 1:
            result_list = result_list[0]

        return result_list


class SecretaryFavorabilityDigit(SecretaryDigit):
    """秘书舰好感度识别，双通道交叉校验。

    移植自船坞卡片侧的 FavorabilityDigit：
    第一遍常规灰度预处理识别，第二遍彩色 2x 放大识别，
    交叉校验修复 10→100 等丢位误读与 I/D/L 等字母乱码。

    与船坞侧的差异：
    - 秘书组槽位排列与好感度排序无关，不做整页排序一致性修正（fix_order）
    - 不含船坞情绪 OCR 的 '044' 专项修正
    """

    def normalize_ocr(self, result):
        """OCR 判断用，不直接转 int，保留归一化前的原始信息。"""
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

    def pre_process_color(self, image):
        """第二遍通道：彩色图像 2x 放大，不提取字母颜色。"""
        return cv2.resize(
            image,
            None,
            fx=2,
            fy=2,
            interpolation=cv2.INTER_CUBIC,
        )

    def reconcile_ocr(self, r1, r2):
        """双通道结果交叉校验，返回采纳的识别值（字符串）。"""
        n1 = self.normalize_ocr(r1)
        n2 = self.normalize_ocr(r2)

        logger.debug(
            f'SecretaryFavorability OCR raw: r1={r1}, r2={r2}'
        )

        # 1. 特殊低值丢 0 修复
        #
        # 10 -> 100
        # 10 -> 400
        #
        # 只处理 10~19
        # 防止:
        # 53 -> 153
        # 53 -> 753
        if n1.isdigit() and 10 <= int(n1) < 20:
            if n2.isdigit() and int(n2) >= 100:
                return '100'

        # 2. 第一遍识别为纯数字，优先相信原 OCR
        if n1.isdigit() and r1 == n1 and 0 <= int(n1) <= 200:
            return n1

        # 3. 第一遍乱码，采用第二遍彩色 OCR
        if n2.isdigit() and 0 <= int(n2) <= 200:
            return n2

        # 4. 兜底
        if n1.isdigit():
            return n1
        if n2.isdigit():
            return n2
        return ''

    def after_process(self, result):
        result = super().after_process(result)
        # 三位数以上误读兜底：1000 -> 100
        if result > 200:
            result //= 10
        return result

    def ocr(self, image, direct_ocr=False):
        # ---------- 第一遍：常规灰度 OCR ----------
        if direct_ocr:
            image_list = [self.pre_process(i) for i in image]
            color_list = [self.pre_process_color(i) for i in image]
        else:
            image_list = [
                self.pre_process(crop(image, area))
                for area in self.buttons
            ]
            color_list = [
                self.pre_process_color(crop(image, area))
                for area in self.buttons
            ]

        result1 = [
            ''.join(r)
            for r in self.cnocr.atomic_ocr_for_single_lines(
                image_list,
                self.alphabet
            )
        ]

        # ---------- 第二遍：彩色 OCR ----------
        result2 = [
            ''.join(r)
            for r in self.cnocr.atomic_ocr_for_single_lines(
                color_list,
                self.alphabet
            )
        ]

        result = [
            self.reconcile_ocr(r1, r2)
            for r1, r2 in zip(result1, result2)
        ]

        result = [
            self.after_process(x)
            for x in result
        ]

        if len(self.buttons) == 1:
            result = result[0]

        return result
