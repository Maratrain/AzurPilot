from dataclasses import dataclass
import os
import time
import cv2

from module.ocr.ocr import Ocr,Digit
from module.secretary.ocr import SecretaryDigit
from module.secretary.assets import (
    SECRETARY_NAME,
    SECRETARY_LEVEL,
    SECRETARY_EMOTION,
)


@dataclass
class SecretaryInfo:
    name: str
    level: int
    emotion: int


OCR_SECRETARY_NAME = Ocr(
    SECRETARY_NAME,
    name="SECRETARY_NAME",
)

OCR_SECRETARY_LEVEL = Digit(
    SECRETARY_LEVEL,
    name="SECRETARY_LEVEL",
)

OCR_SECRETARY_EMOTION = SecretaryDigit(
    SECRETARY_EMOTION,
    name="SECRETARY_EMOTION",
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
            "emotion": SECRETARY_EMOTION.area,
        }

        for key, area in crops.items():
            x1, y1, x2, y2 = area

            crop = image[y1:y2, x1:x2]

            cv2.imwrite(
                f"{path}/{timestamp}_{key}.png",
                crop
            )

    def scan(self, image):

        self.save_debug(image)

        name = OCR_SECRETARY_NAME.ocr(image)

        level = OCR_SECRETARY_LEVEL.ocr(image)

        emotion = OCR_SECRETARY_EMOTION.ocr(image)

        return SecretaryInfo(
            name=name,
            level=level,
            emotion=emotion,
        )