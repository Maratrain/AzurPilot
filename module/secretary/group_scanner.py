from dataclasses import dataclass
from module.ocr.ocr import Ocr
from module.secretary.ocr import SecretaryDigit
from module.secretary.assets import (
    SECRETARY_NAME,
    SECRETARY_LEVEL,
    SECRETARY_FAVORABILITY,
)
from module.secretary.slot import (
    SECRETARY_SLOT,
    SECRETARY_SLOT_OFFSET,
    move_button
)

@dataclass
class SecretaryGroupInfo:
    index: int
    name: str
    level: int
    favorability: int
    button: object
    is_main: bool


class SecretaryGroupScanner:

    def __init__(self):
        self.name_ocr = []
        self.level_ocr = []
        self.favorability_ocr = []

        for index in range(5):

            offset_x, offset_y = SECRETARY_SLOT_OFFSET[index]

            name_btn = move_button(
                SECRETARY_NAME,
                offset_x,
                offset_y,
            )

            level_btn = move_button(
                SECRETARY_LEVEL,
                offset_x,
                offset_y,
            )

            favor_btn = move_button(
                SECRETARY_FAVORABILITY,
                offset_x,
                offset_y,
            )

            self.name_ocr.append(
                Ocr(
                    name_btn,
                    lang="ppocr_v6",
                    name=f"SECRETARY_NAME_{index}",
                )
            )

            self.level_ocr.append(
                SecretaryDigit(
                    level_btn,
                    lang="ppocr_v6",
                    name=f"SECRETARY_LEVEL_{index}",
                )
            )

            self.favorability_ocr.append(
                SecretaryDigit(
                    favor_btn,
                    name=f"SECRETARY_FAVORABILITY_{index}",
                )
            )

    def scan(self, image):
        ships = []

        for index in range(5):
            name = self.name_ocr[index].ocr(image)
            level = self.level_ocr[index].ocr(image)
            favorability = self.favorability_ocr[index].ocr(image)

            try:
                level = int(level)
            except (ValueError, TypeError):
                level = 0

            try:
                favorability = int(favorability)
            except (ValueError, TypeError):
                favorability = 0
            ships.append(
                SecretaryGroupInfo(
                    index=index,
                    name=name,
                    level=level,
                    favorability=favorability,
                    button=SECRETARY_SLOT[index],
                    is_main=index == 0,
                )
            )

        return ships
