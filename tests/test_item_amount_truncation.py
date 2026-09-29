"""AmountOcr.ocr_with_validation 超读截断回归测试。

覆盖残影拼在数字左侧（高位）的超读场景：重试 3 次仍超上限时，
逐位削去首位直到落入上限或只剩一位，确保订正到真实末位值，
而不是停留在残影部分（如 721→72、7221→722）。
"""
import unittest
from unittest.mock import patch

import numpy as np

from module.statistics.item import AMOUNT_OCR


class TestAmountTruncation(unittest.TestCase):
    """残影在前超读的截断订正。"""

    def _ocr_with_fixed_reading(self, reading, item_name):
        """固定 OCR 读数，跑完整 ocr_with_validation（含重试与截断兜底）。"""
        image = np.zeros((20, 20, 3), dtype=np.uint8)
        ocr = AMOUNT_OCR
        with patch.object(ocr, "pre_process",
                          return_value=np.zeros((10, 10), dtype=np.uint8)):
            with patch.object(ocr.cnocr, "atomic_ocr_for_single_lines",
                              return_value=[reading]):
                return ocr.ocr_with_validation(
                    image, item_name=item_name, direct_ocr=True, trim=False
                )

    def test_two_digit_leading_noise(self):
        """1 被读成 71（残影 7 在高位），应订正到末位真实值 1。"""
        result = self._ocr_with_fixed_reading("71", "GearDesignPlanGunT5")
        self.assertEqual(result, 1)

    def test_three_digit_leading_noise(self):
        """1 被读成 721，逐位削首位到 1，而不是停在残影 72。"""
        result = self._ocr_with_fixed_reading("721", "OrdnanceTestingReportT3")
        self.assertEqual(result, 1)

    def test_four_digit_leading_noise(self):
        """1 被读成 7221，逐位削到 1，而不是停在残影 722。"""
        result = self._ocr_with_fixed_reading("7221", "OrdnanceTestingReportT3")
        self.assertEqual(result, 1)

    def test_just_over_limit_truncates(self):
        """51 超 OrdnanceTestingReport 上限 50，订正到末位 1。"""
        result = self._ocr_with_fixed_reading("51", "OrdnanceTestingReportT4")
        self.assertEqual(result, 1)

    def test_within_limit_not_truncated(self):
        """读数在上限内不触发截断，原值返回。"""
        result = self._ocr_with_fixed_reading("3", "GearDesignPlanGunT5")
        self.assertEqual(result, 3)


if __name__ == "__main__":
    unittest.main()
