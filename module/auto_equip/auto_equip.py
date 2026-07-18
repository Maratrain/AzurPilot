from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from module.config.config import TaskEnd
from module.base.button import Button, ButtonGrid
from module.equipment.equipment import SWIPE_AREA, SWIPE_DISTANCE, SWIPE_RANDOM_RANGE
from module.ui.switch import Switch
from module.exception import ScriptError
from module.logger import logger
from module.retire.assets import SHIP_DETAIL_CHECK
from module.retire.dock import Dock
from module.ui.page import page_dock


AUTO_EQUIP_QUICK_CHANGE = Button(
    area=(1035, 86, 1128, 116),
    color=(90, 125, 185),
    button=(1035, 86, 1128, 116),
    name='AUTO_EQUIP_QUICK_CHANGE',
)

AUTO_EQUIP_QUICK_CHANGE_CHECK = Button(
    area=(1048, 90, 1119, 111),
    color=(239, 174, 117),
    button=(1048, 90, 1119, 111),
    name='AUTO_EQUIP_QUICK_CHANGE_CHECK',
)

AUTO_EQUIP_EQUIPPING_CLICK = Button(
    area=(1035, 245, 1127, 272),
    color=(117, 128, 161),
    button=(1035, 245, 1127, 272),
    name='AUTO_EQUIP_EQUIPPING_CLICK',
)

AUTO_EQUIP_EQUIPPING_ON = Button(
    area=(1035, 248, 1059, 268),
    color=(175, 182, 202),
    button=(1035, 245, 1127, 272),
    name='AUTO_EQUIP_EQUIPPING_ON',
)

AUTO_EQUIP_EQUIPPING_OFF = Button(
    area=(1035, 246, 1066, 270),
    color=(187, 187, 187),
    button=(1035, 245, 1127, 272),
    name='AUTO_EQUIP_EQUIPPING_OFF',
)

AUTO_EQUIP_EQUIPMENT_SLOT_ROW = Button(
    area=(695, 127, 1113, 200),
    color=(141, 133, 137),
    button=(695, 127, 1113, 200),
    name='AUTO_EQUIP_EQUIPMENT_SLOT_ROW',
)

AUTO_EQUIP_EQUIPMENT_SLOT_GRID = ButtonGrid(
    origin=(695, 127),
    delta=(86.25, 0),
    button_shape=(73, 73),
    grid_shape=(5, 1),
    name='AUTO_EQUIP_EQUIPMENT_SLOT',
)
AUTO_EQUIP_EQUIPMENT_SLOTS = AUTO_EQUIP_EQUIPMENT_SLOT_GRID.buttons

AUTO_EQUIP_WAREHOUSE_FIRST = Button(
    area=(704, 293, 788, 377),
    color=(196, 176, 123),
    button=(704, 293, 788, 377),
    name='AUTO_EQUIP_WAREHOUSE_FIRST',
)

AUTO_EQUIP_WAREHOUSE_SECOND = Button(
    area=(790, 293, 874, 377),
    color=(196, 176, 122),
    button=(790, 293, 874, 377),
    name='AUTO_EQUIP_WAREHOUSE_SECOND',
)

AUTO_EQUIP_WAREHOUSE_FIRST_DISABLED = Button(
    area=(713, 302, 779, 368),
    color=(63, 56, 42),
    button=(704, 293, 788, 377),
    name='AUTO_EQUIP_WAREHOUSE_FIRST_DISABLED',
)

AUTO_EQUIP_SWIPE = 'AUTO_EQUIP_SWIPE'
AUTO_EQUIP_EMPTY_SLOT_PLUS_TEMPLATE_FILE = Path(__file__).with_name('empty_slot_plus.png')
AUTO_EQUIP_EMPTY_SLOT_PLUS_SIMILARITY = 0.8
AUTO_EQUIP_NO_EQUIPMENT_TEMPLATE_FILE = Path(__file__).with_name('no_equipment.png')
AUTO_EQUIP_NO_EQUIPMENT_SIMILARITY = 0.85
AUTO_EQUIP_NO_EQUIPMENT_SEARCH_AREA = (695, 282, 1238, 622)
AUTO_EQUIP_AFTER_EQUIP_WAIT = 3
AUTO_EQUIP_CLICK_RECORD_NAMES = (
    AUTO_EQUIP_QUICK_CHANGE.name,
    AUTO_EQUIP_EQUIPPING_CLICK.name,
    AUTO_EQUIP_WAREHOUSE_FIRST.name,
    AUTO_EQUIP_WAREHOUSE_SECOND.name,
    AUTO_EQUIP_SWIPE,
    *(slot.name for slot in AUTO_EQUIP_EQUIPMENT_SLOTS),
)


@lru_cache(maxsize=1)
def auto_equip_empty_slot_plus_template():
    template = cv2.imread(str(AUTO_EQUIP_EMPTY_SLOT_PLUS_TEMPLATE_FILE), cv2.IMREAD_GRAYSCALE)
    if template is None:
        raise ScriptError(f'Unable to load {AUTO_EQUIP_EMPTY_SLOT_PLUS_TEMPLATE_FILE}')
    return template


@lru_cache(maxsize=1)
def auto_equip_no_equipment_template():
    template = cv2.imread(str(AUTO_EQUIP_NO_EQUIPMENT_TEMPLATE_FILE), cv2.IMREAD_GRAYSCALE)
    if template is None:
        raise ScriptError(f'Unable to load {AUTO_EQUIP_NO_EQUIPMENT_TEMPLATE_FILE}')
    return template


auto_equip_equipping_filter = Switch('Auto_equip_equipping_filter')
auto_equip_equipping_filter.add_state(
    'on',
    check_button=AUTO_EQUIP_EQUIPPING_ON,
    click_button=AUTO_EQUIP_EQUIPPING_CLICK,
)
auto_equip_equipping_filter.add_state(
    'off',
    check_button=AUTO_EQUIP_EQUIPPING_OFF,
    click_button=AUTO_EQUIP_EQUIPPING_CLICK,
)


class AutoEquip(Dock):
    def _should_stop(self):
        event = getattr(self.config, 'stop_event', None)
        return event is not None and event.is_set()

    def _quick_change_appear(self):
        return self.appear(AUTO_EQUIP_QUICK_CHANGE_CHECK)

    def _open_quick_change(self):
        logger.info('Open quick equipment change')
        for _ in self.loop(timeout=10):
            if self._quick_change_appear():
                return

            if self.appear(SHIP_DETAIL_CHECK, offset=(30, 30), interval=2):
                self.device.click(AUTO_EQUIP_QUICK_CHANGE)
                continue
            if self.handle_popup_confirm('AUTO_EQUIP_QUICK_CHANGE'):
                continue
            if self.handle_game_tips():
                continue
        else:
            raise ScriptError('Unable to open quick equipment change')

    def _quick_equipping_set(self, enable=True):
        target = 'on' if enable else 'off'
        current = auto_equip_equipping_filter.get(main=self)
        logger.attr('Auto_equip_equipping_filter', current)
        if current == target:
            return
        if current == 'unknown':
            logger.warning('Unable to determine quick equipping filter state')
            return

        self.device.click(AUTO_EQUIP_EQUIPPING_CLICK)
        self.wait_until_stable(AUTO_EQUIP_EQUIPPING_CLICK)

    def _auto_equip_click_record_clear(self):
        for name in AUTO_EQUIP_CLICK_RECORD_NAMES:
            self.device.click_record_remove(name)

    def _quick_change_next(self):
        logger.info('Swipe to next ship')
        self._auto_equip_click_record_clear()
        self.device.swipe_vector(
            vector=(-SWIPE_DISTANCE, 0),
            box=SWIPE_AREA.area,
            random_range=SWIPE_RANDOM_RANGE,
            padding=0,
            duration=(0.1, 0.12),
            name=AUTO_EQUIP_SWIPE,
        )
        self._auto_equip_click_record_clear()
        self.wait_until_stable(SWIPE_AREA)

    @staticmethod
    def _warehouse_first_unavailable(image):
        x1, y1, x2, y2 = AUTO_EQUIP_WAREHOUSE_FIRST_DISABLED.area
        crop = image[y1:y2, x1:x2, :3].astype(np.float32)
        luma = 0.299 * crop[:, :, 0] + 0.587 * crop[:, :, 1] + 0.114 * crop[:, :, 2]
        return bool(luma.mean() < 90)

    @staticmethod
    def _empty_slot_plus_score(image, slot):
        x1, y1, x2, y2 = slot.area
        crop = image[y1:y2, x1:x2, :3]
        crop = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        result = cv2.matchTemplate(crop, auto_equip_empty_slot_plus_template(), cv2.TM_CCOEFF_NORMED)
        return float(result.max())

    def _enabled_equipment_slots(self):
        slots = []
        for index, slot in enumerate(AUTO_EQUIP_EQUIPMENT_SLOTS, start=1):
            if getattr(self.config, f'AutoEquip_EnableSlot{index}', True):
                slots.append(slot)

        logger.attr('Enabled equipment slots', [slot.name for slot in slots])
        return slots

    def _quick_empty_equipment_slots(self):
        empty_slots = []
        scores = []
        for slot in self._enabled_equipment_slots():
            score = self._empty_slot_plus_score(self.device.image, slot)
            scores.append(f'{slot.name}:{score:.3f}')
            if score >= AUTO_EQUIP_EMPTY_SLOT_PLUS_SIMILARITY:
                empty_slots.append(slot)

        logger.attr('Empty slot plus scores', scores)
        return empty_slots

    @staticmethod
    def _warehouse_no_equipment_score(image):
        x1, y1, x2, y2 = AUTO_EQUIP_NO_EQUIPMENT_SEARCH_AREA
        crop = image[y1:y2, x1:x2, :3]
        crop = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        result = cv2.matchTemplate(crop, auto_equip_no_equipment_template(), cv2.TM_CCOEFF_NORMED)
        return float(result.max())

    def _warehouse_no_equipment(self):
        score = self._warehouse_no_equipment_score(self.device.image)
        logger.attr('No equipment score', f'{score:.3f}')
        return score >= AUTO_EQUIP_NO_EQUIPMENT_SIMILARITY

    def _quick_fill_slot_from_warehouse(self, slot):
        self.device.click(slot)
        self.wait_until_stable(AUTO_EQUIP_WAREHOUSE_FIRST)
        self.device.screenshot()
        if self._warehouse_no_equipment():
            logger.info(f'No equipment available for {slot.name}')
            return False

        if self._warehouse_first_unavailable(self.device.image):
            logger.info(f'Fill {slot.name} from warehouse second equipment')
            self.device.click(AUTO_EQUIP_WAREHOUSE_SECOND)
        else:
            logger.info(f'Fill {slot.name} from warehouse first equipment')
            self.device.click(AUTO_EQUIP_WAREHOUSE_FIRST)
        self.device.sleep(AUTO_EQUIP_AFTER_EQUIP_WAIT)
        self.wait_until_stable(AUTO_EQUIP_EQUIPMENT_SLOT_ROW)
        return True

    def _fill_current_ship_equipment(self):
        logger.hr('Auto equip current ship', level=2)
        self._auto_equip_click_record_clear()
        try:
            self._open_quick_change()
            self._quick_equipping_set(enable=False)
            self.equipment_change_logic()
        finally:
            self._auto_equip_click_record_clear()

    def equipment_change_logic(self):
        logger.info('Fill empty equipment slots')
        filled = 0
        skipped = 0
        self.device.screenshot()
        empty_slots = self._quick_empty_equipment_slots()
        logger.attr('Empty equipment slots', [slot.name for slot in empty_slots])

        for slot in empty_slots:
            if self._should_stop():
                raise TaskEnd('AutoEquip stopped')

            if self._quick_fill_slot_from_warehouse(slot):
                filled += 1
            else:
                skipped += 1

        logger.attr('Filled equipment slots', filled)
        logger.attr('Skipped empty slots', skipped)

    def _ship_limit(self):
        value = getattr(self.config, 'AutoEquip_ShipLimit', 0)
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = 0
        return max(value, 0)

    def run(self):
        logger.hr('Auto Equip', level=1)
        limit = self._ship_limit()
        logger.attr('Ship limit', 'manual stop' if limit == 0 else limit)
        if limit == 0:
            logger.warning('Ship limit is 0, AutoEquip will continue until manually stopped')

        self.ui_ensure(page_dock)
        if not self.dock_enter_first(non_npc=True):
            logger.info('No ship to equip')
            return

        count = 0
        while 1:
            if self._should_stop():
                raise TaskEnd('AutoEquip stopped')

            count += 1
            logger.attr('Ship', count)
            self._fill_current_ship_equipment()

            if limit and count >= limit:
                logger.info('Reached ship limit')
                break

            self._quick_change_next()
