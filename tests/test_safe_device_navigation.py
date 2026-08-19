import unittest
from unittest.mock import Mock, patch

from module.device.device import Device
from module.exception import GamePageUnknownError
from module.ui.page import page_main
from module.ui.ui import UI


class TestSafeDeviceNavigation(unittest.TestCase):
    def test_for_existing_device_disables_runtime_side_effects(self):
        config = Mock()

        with patch.object(Device, '__init__', return_value=None) as init:
            Device.for_existing_device(config)

        init.assert_called_once_with(
            config=config,
            auto_start_emulator=False,
            initialize_runtime=False,
        )

    def test_ui_goto_main_forwards_recover_unknown(self):
        ui = object.__new__(UI)

        with patch.object(UI, 'ui_ensure', return_value=True) as ensure:
            self.assertTrue(ui.ui_goto_main(recover_unknown=False))

        ensure.assert_called_once_with(destination=page_main, recover_unknown=False)

    def test_unknown_page_without_recovery_raises(self):
        ui = object.__new__(UI)
        ui.device = Mock()
        ui.device.has_cached_image = True

        with patch('module.ui.ui.Timer') as timer:
            timer.return_value.start.return_value = timer.return_value
            timer.return_value.reached.return_value = True

            with self.assertRaises(GamePageUnknownError):
                ui.ui_get_current_page(recover_unknown=False)


if __name__ == '__main__':
    unittest.main()
