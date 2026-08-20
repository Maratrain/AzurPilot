"""装备码输入确认流程的回归测试。"""

import unittest
from unittest.mock import Mock

from module.equipment.assets import EQUIPMENT_CODE_ENTER
from module.equipment.equipment_code import EquipmentCodeHandler


class TestEquipmentCodeInputConfirmation(unittest.TestCase):
    """确保预览只在实际点击输入确认后才会被校验。"""

    @staticmethod
    def _handler():
        return object.__new__(EquipmentCodeHandler)

    def test_preview_is_not_checked_before_input_confirmation(self):
        handler = self._handler()
        handler.loop = Mock(return_value=iter([None, None]))
        handler.appear = Mock(return_value=False)
        handler.appear_then_click = Mock()
        handler.device = Mock()
        handler.device.ime_shown.return_value = True
        handler.is_code_preview_loaded = Mock(return_value=True)

        self.assertFalse(handler._code_wait_preview_loaded())
        handler.appear_then_click.assert_not_called()
        handler.is_code_preview_loaded.assert_not_called()

    def test_preview_is_not_checked_without_input_confirmation(self):
        handler = self._handler()
        handler.loop = Mock(return_value=iter([None]))
        handler.appear = Mock(return_value=False)
        handler.appear_then_click = Mock()
        handler.device = Mock()
        handler.device.ime_shown.return_value = False
        handler.is_code_preview_loaded = Mock(return_value=True)

        self.assertFalse(handler._code_wait_preview_loaded())
        handler.is_code_preview_loaded.assert_not_called()

    def test_preview_is_checked_on_frame_after_input_confirmation(self):
        handler = self._handler()
        handler.loop = Mock(return_value=iter([None, None]))
        handler.appear = Mock(side_effect=[True, False])
        handler.appear_then_click = Mock(return_value=True)
        handler.device = Mock()
        handler.device.ime_shown.return_value = False
        handler.is_code_preview_loaded = Mock(return_value=True)

        self.assertTrue(handler._code_wait_preview_loaded())
        handler.appear_then_click.assert_called_once_with(
            EQUIPMENT_CODE_ENTER, offset=(5, 5), interval=3
        )
        handler.is_code_preview_loaded.assert_called_once_with()

    def test_empty_preview_after_confirmation_times_out(self):
        handler = self._handler()
        handler.loop = Mock(return_value=iter([None, None]))
        handler.appear = Mock(side_effect=[True, False])
        handler.appear_then_click = Mock(return_value=True)
        handler.device = Mock()
        handler.device.ime_shown.return_value = False
        handler.is_code_preview_loaded = Mock(return_value=False)

        self.assertFalse(handler._code_wait_preview_loaded())
        handler.is_code_preview_loaded.assert_called_once_with()

    def test_visible_button_during_cooldown_does_not_validate_preview(self):
        handler = self._handler()
        handler.loop = Mock(return_value=iter([None, None, None]))
        handler.appear = Mock(side_effect=[True, True, False])
        handler.appear_then_click = Mock(side_effect=[True, False])
        handler.device = Mock()
        handler.device.ime_shown.return_value = False
        handler.is_code_preview_loaded = Mock(return_value=True)

        self.assertTrue(handler._code_wait_preview_loaded())
        handler.is_code_preview_loaded.assert_called_once_with()

    def test_ime_after_confirmation_does_not_validate_preview(self):
        handler = self._handler()
        handler.loop = Mock(return_value=iter([None, None]))
        handler.appear = Mock(side_effect=[True, False])
        handler.appear_then_click = Mock(return_value=True)
        handler.device = Mock()
        handler.device.ime_shown.return_value = True
        handler.is_code_preview_loaded = Mock(return_value=True)

        self.assertFalse(handler._code_wait_preview_loaded())
        handler.is_code_preview_loaded.assert_not_called()


if __name__ == '__main__':
    unittest.main()
