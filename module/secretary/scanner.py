from dataclasses import dataclass
import os
import time
import cv2

from module.ocr.ocr import Ocr,Digit
from module.secretary.ocr import SecretaryDigit
from module.secretary.assets import (
    SECRETARY_NAME,
    SECRETARY_LEVEL,
    SECRETARY_FAVORABILITY,
)


@dataclass
class SecretaryInfo:
    name: str
    level: int
    favorability: int


OCR_SECRETARY_NAME = Ocr(
    SECRETARY_NAME,
    name="SECRETARY_NAME",
)

OCR_SECRETARY_LEVEL = Digit(
    SECRETARY_LEVEL,
    name="SECRETARY_LEVEL",
)

OCR_SECRETARY_FAVORABILITY = SecretaryDigit(
    SECRETARY_FAVORABILITY,
    name="SECRETARY_FAVORABILITY",
)


class SecretaryScanner:

    def save_debug(self, image):
        path = "log/secretary_debug"
        os.makedirs(path, exist_ok=True)

        timestamp = int(time.time())

        # 保存完整截图
        cv2.imwrite(
            f"{path}/{timestamp}_full.png",
            image
        )

        crops = {
            "name": SECRETARY_NAME.area,
            "level": SECRETARY_LEVEL.area,
            "favorability": SECRETARY_FAVORABILITY.area,
        }

        for key, area in crops.items():
            x1, y1, x2, y2 = area

            crop = image[y1:y2, x1:x2]

            cv2.imwrite(
                f"{path}/{timestamp}_{key}.png",
                crop
            )

    def scan(self, image, debug=False):

        if debug:
            self.save_debug(image)

        name = OCR_SECRETARY_NAME.ocr(image)

        level = OCR_SECRETARY_LEVEL.ocr(image)

        favorability = OCR_SECRETARY_FAVORABILITY.ocr(image)

        return SecretaryInfo(
            name=name,
            level=level,
            favorability=favorability,
        )