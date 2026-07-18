import os
import cv2
from datetime import datetime
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
            ''''
            self.save_debug(
                image=image,
                index=index,
                name=name,
                level=level,
                favorability=favorability,
            )
            '''
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
    '''
    def save_debug(self, image, index, name, level, favorability):
        folder = "log/secretary_group_debug"
        os.makedirs(folder, exist_ok=True)

        debug = image.copy()

        def get_area(ocr):
            area = ocr.buttons[0]

            # Button
            if hasattr(area, "area"):
                return area.area

            # (x1,y1,x2,y2)
            if isinstance(area, tuple):
                return area

            # [(x1,y1,x2,y2)]
            if isinstance(area, list):
                area = area[0]
                if hasattr(area, "area"):
                    return area.area
                return area

            raise TypeError(type(area))

        def draw(area, color, text):
            x1, y1, x2, y2 = area

            cv2.rectangle(
                debug,
                (x1, y1),
                (x2, y2),
                color,
                2,
            )

            cv2.putText(
                debug,
                text,
                (x1, y1 - 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                color,
                1,
            )

        name_area = get_area(self.name_ocr[index])
        level_area = get_area(self.level_ocr[index])
        favor_area = get_area(self.favorability_ocr[index])

        draw(name_area, (0, 255, 0), "NAME")
        draw(level_area, (255, 0, 0), "LEVEL")
        draw(favor_area, (0, 0, 255), "FAVOR")

        cv2.putText(
            debug,
            f"{name} Lv{level} Fav{favorability}",
            (name_area[0], max(20, name_area[1] - 15)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 255),
            2,
        )

        prefix = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

        cv2.imwrite(
            os.path.join(folder, f"{prefix}_slot{index}.png"),
            debug,
        )

        # 保存三个OCR区域
        for area, suffix in (
            (name_area, "name"),
            (level_area, "level"),
            (favor_area, "favor"),
        ):
            x1, y1, x2, y2 = area

            crop = image[y1:y2, x1:x2]

            cv2.imwrite(
                os.path.join(
                    folder,
                    f"{prefix}_slot{index}_{suffix}.png",
                ),
                crop,
            )
    '''