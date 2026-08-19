import unittest
from unittest.mock import Mock, patch

from module.exception import RequestHumanTakeover
from module.webui.scheduler_stop import execute_stop_action, normalize_stop_action


class TestSchedulerStopAction(unittest.TestCase):
    def test_invalid_action_falls_back_without_connecting_device(self):
        config = Mock()

        with patch('module.webui.scheduler_stop._get_existing_device') as device:
            self.assertTrue(execute_stop_action(config, 'unexpected'))

        device.assert_not_called()
        self.assertEqual('stay_there', normalize_stop_action('unexpected'))

    def test_stay_there_does_not_connect_device(self):
        config = Mock()

        with patch('module.webui.scheduler_stop._get_existing_device') as device:
            self.assertTrue(execute_stop_action(config, 'stay_there'))

        device.assert_not_called()

    def test_close_game_uses_existing_device(self):
        config = Mock()
        device = Mock()

        with patch(
            'module.webui.scheduler_stop._get_existing_device', return_value=device
        ):
            self.assertTrue(execute_stop_action(config, 'close_game'))

        device.app_stop.assert_called_once_with()

    def test_close_game_keeps_device_guard_exception(self):
        config = Mock()
        device = Mock()
        device.app_stop.side_effect = RequestHumanTakeover

        with patch(
            'module.webui.scheduler_stop._get_existing_device', return_value=device
        ):
            with self.assertRaises(RequestHumanTakeover):
                execute_stop_action(config, 'close_game')

    def test_goto_main_does_not_start_game(self):
        config = Mock()
        device = Mock()
        device.app_is_running.return_value = False

        with (
            patch(
                'module.webui.scheduler_stop._get_existing_device', return_value=device
            ),
            patch('module.ui.ui.UI') as ui,
        ):
            self.assertTrue(execute_stop_action(config, 'goto_main'))

        device.app_is_running.assert_called_once_with()
        device.app_start.assert_not_called()
        ui.assert_not_called()

    def test_goto_main_disables_unknown_page_recovery(self):
        config = Mock()
        device = Mock()
        device.app_is_running.return_value = True
        ui = Mock()

        with (
            patch(
                'module.webui.scheduler_stop._get_existing_device', return_value=device
            ),
            patch('module.ui.ui.UI', return_value=ui) as ui_class,
        ):
            self.assertTrue(execute_stop_action(config, 'goto_main'))

        ui_class.assert_called_once_with(config=config, device=device)
        ui.ui_goto_main.assert_called_once_with(recover_unknown=False)
        device.app_start.assert_not_called()

    def test_close_emulator_requires_managed_instance(self):
        config = Mock()
        platform = Mock()
        platform.emulator_instance = None

        with patch('module.device.platform.Platform', return_value=platform):
            self.assertFalse(execute_stop_action(config, 'close_emulator'))

        platform.emulator_stop.assert_not_called()

    def test_close_emulator_uses_platform_without_adb_connection(self):
        config = Mock()
        platform = Mock()
        platform.emulator_instance = Mock()
        platform.emulator_stop.return_value = True

        with patch('module.device.platform.Platform', return_value=platform) as cls:
            self.assertTrue(execute_stop_action(config, 'close_emulator'))

        cls.assert_called_once_with(config, connect=False)
        platform.emulator_stop.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
