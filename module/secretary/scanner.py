from dataclasses import dataclass

from module.ocr.ocr import Ocr
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
    [SECRETARY_NAME],
    lang="ppocr_v6",
    name="SECRETARY_NAME",
)

OCR_SECRETARY_LEVEL = SecretaryDigit(
    [SECRETARY_LEVEL],
    lang="ppocr_v6",
    name="SECRETARY_LEVEL",
)

OCR_SECRETARY_FAVORABILITY = SecretaryDigit(
    [SECRETARY_FAVORABILITY],
    name="SECRETARY_FAVORABILITY",
)


class SecretaryScanner:

    def scan(self, image):
        name = OCR_SECRETARY_NAME.ocr(image)

        level = OCR_SECRETARY_LEVEL.ocr(image)

        favorability = OCR_SECRETARY_FAVORABILITY.ocr(image)

        return SecretaryInfo(
            name=name,
            level=level,
            favorability=favorability,
        )