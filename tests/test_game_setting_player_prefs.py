import unittest
import xml.etree.ElementTree as etree
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from module.game_setting.player_prefs import (
    AdbResult,
    PlayerPrefsManager,
    PlayerPrefsMetadata,
    RECOMMENDED_INT_SETTINGS,
    RECOMMENDED_STRING_SETTINGS,
    PlayerPrefsUnsupported,
    PlayerPrefsWriteError,
    update_player_prefs_xml,
    verify_player_prefs_xml,
)


class TestPlayerPrefsXml(unittest.TestCase):
    EXPECTED_SETTINGS = {
        'fps_limit': 60,
        'world_flag_story_tips': 1,
        'world_flag_consume_item': 1,
        'world_flag_auto_save_area': 0,
        'story_autoplay_flag': 1,
        'display_ship_get_effect': 0,
        'QUICK_CHANGE_EQUIP': 0,
        'BATTLERESULT_DISPAY_PAINTING': 0,
        'world_sub_auto_call': 0,
    }

    def test_updates_only_whitelisted_settings(self):
        content = b'''<?xml version="1.0" encoding="utf-8"?>
<map>
    <int name="fps_limit" value="30" />
    <int name="story_speed_flag123456" value="0" />
    <int name="STANDBY_MODE_KEY_123456" value="1" />
    <int name="world_sub_call_line" value="9" />
    <string name="auto_switch_difficult_safe">all</string>
    <string name="unrelated_setting">keep me</string>
</map>'''

        updated, changes = update_player_prefs_xml(content)
        root = etree.fromstring(updated)
        entries = {element.get('name'): element for element in root}

        self.assertTrue(changes.changed)
        self.assertEqual(1, changes.story_speed_changed)
        self.assertEqual(('story_speed_flag123456',), changes.story_speed_keys)
        self.assertEqual(1, changes.standby_changed)
        self.assertEqual(('STANDBY_MODE_KEY_123456',), changes.standby_keys)
        self.assertEqual(self.EXPECTED_SETTINGS, RECOMMENDED_INT_SETTINGS)
        self.assertEqual({'_WorldBossProgressTipFlag_': ''}, RECOMMENDED_STRING_SETTINGS)
        for name, value in self.EXPECTED_SETTINGS.items():
            self.assertEqual('int', entries[name].tag)
            self.assertEqual(str(value), entries[name].get('value'))
        self.assertEqual('9', entries['story_speed_flag123456'].get('value'))
        self.assertEqual('string', entries['_WorldBossProgressTipFlag_'].tag)
        self.assertEqual('', entries['_WorldBossProgressTipFlag_'].text or '')
        self.assertEqual('0', entries['STANDBY_MODE_KEY_123456'].get('value'))
        self.assertEqual('9', entries['world_sub_call_line'].get('value'))
        self.assertEqual('all', entries['auto_switch_difficult_safe'].text)
        self.assertEqual('keep me', entries['unrelated_setting'].text)
        self.assertNotIn('STANDBY_MODE_KEY', entries)
        verify_player_prefs_xml(updated, changes.standby_keys, changes.story_speed_keys)

    def test_second_update_is_idempotent(self):
        content = b'<map><int name="STANDBY_MODE_KEY_7" value="1" /></map>'
        updated, _ = update_player_prefs_xml(content)
        _, changes = update_player_prefs_xml(updated)

        self.assertFalse(changes.changed)
        self.assertEqual((), changes.story_speed_keys)
        self.assertEqual(('STANDBY_MODE_KEY_7',), changes.standby_keys)

    def test_does_not_create_dynamic_key_without_player_id(self):
        updated, changes = update_player_prefs_xml(b'<map />')
        root = etree.fromstring(updated)

        self.assertEqual((), changes.standby_keys)
        self.assertEqual((), changes.story_speed_keys)
        self.assertFalse(any(
            element.get('name', '').startswith('STANDBY_MODE_KEY')
            for element in root
        ))
        self.assertFalse(any(
            element.get('name', '').startswith('story_speed_flag')
            for element in root
        ))

    def test_verification_rejects_wrong_target_values(self):
        updated, changes = update_player_prefs_xml(
            b'<map><int name="story_speed_flag7" value="0" /><int name="STANDBY_MODE_KEY_7" value="1" /></map>'
        )
        root = etree.fromstring(updated)
        entries = {element.get('name'): element for element in root}
        entries['story_speed_flag7'].set('value', '5')
        wrong_story_speed = etree.tostring(root, encoding='utf-8')

        with self.assertRaises(PlayerPrefsWriteError):
            verify_player_prefs_xml(wrong_story_speed, changes.standby_keys, changes.story_speed_keys)

        entries['story_speed_flag7'].set('value', '9')
        entries['_WorldBossProgressTipFlag_'].text = '100&200'
        wrong_beacon_tip = etree.tostring(root, encoding='utf-8')

        with self.assertRaises(PlayerPrefsWriteError):
            verify_player_prefs_xml(wrong_beacon_tip, changes.standby_keys, changes.story_speed_keys)

        entries['_WorldBossProgressTipFlag_'].text = None
        entries['STANDBY_MODE_KEY_7'].set('value', '1')
        wrong_standby = etree.tostring(root, encoding='utf-8')
        with self.assertRaises(PlayerPrefsWriteError):
            verify_player_prefs_xml(wrong_standby, changes.standby_keys, changes.story_speed_keys)

    def test_rejects_target_key_with_wrong_xml_type(self):
        with self.assertRaises(PlayerPrefsUnsupported):
            update_player_prefs_xml(b'<map><string name="fps_limit">60</string></map>')

        with self.assertRaises(PlayerPrefsUnsupported):
            update_player_prefs_xml(b'<map><int name="_WorldBossProgressTipFlag_" value="0" /></map>')

    def test_rejects_unknown_root_node(self):
        with self.assertRaises(PlayerPrefsUnsupported):
            update_player_prefs_xml(b'<preferences />')


class TestPlayerPrefsRollback(unittest.TestCase):
    def test_rollback_uses_memory_and_atomic_replace(self):
        manager = PlayerPrefsManager(
            SimpleNamespace(package='com.example.game', adb_binary='adb', serial='serial')
        )
        metadata = PlayerPrefsMetadata(uid='10000', gid='10000', mode='660', context='u:object_r:test:s0')
        original = b'<map />'
        manager._game_is_stopped = Mock(return_value=True)
        manager._shell = Mock(return_value=AdbResult(0, '', ''))
        manager._write_remote_bytes = Mock()
        manager._restore_metadata = Mock()
        manager._read_remote_bytes = Mock(side_effect=[original, original])
        manager._metadata = Mock(return_value=metadata)

        restored = manager._restore_original(
            '/remote/prefs.xml',
            metadata,
            original,
            '/remote/prefs.rollback.tmp',
        )

        self.assertTrue(restored)
        manager._write_remote_bytes.assert_called_once_with(
            '/remote/prefs.rollback.tmp', original, PlayerPrefsWriteError
        )
        self.assertEqual(
            [call(['mv', '/remote/prefs.rollback.tmp', '/remote/prefs.xml'], error_type=PlayerPrefsWriteError)],
            manager._shell.call_args_list,
        )
        manager._restore_metadata.assert_called_once_with('/remote/prefs.rollback.tmp', metadata)

    def test_rollback_refuses_to_replace_when_game_restarts(self):
        manager = PlayerPrefsManager(
            SimpleNamespace(package='com.example.game', adb_binary='adb', serial='serial')
        )
        manager._game_is_stopped = Mock(return_value=False)
        manager._shell = Mock()
        manager._write_remote_bytes = Mock()

        restored = manager._restore_original(
            '/remote/prefs.xml',
            PlayerPrefsMetadata(uid='10000', gid='10000', mode='660', context='u:object_r:test:s0'),
            b'<map />',
            '/remote/prefs.rollback.tmp',
        )

        self.assertFalse(restored)
        manager._shell.assert_not_called()
        manager._write_remote_bytes.assert_not_called()


class TestPlayerPrefsPrivacy(unittest.TestCase):
    def test_cleanup_ignores_android_atomic_backup(self):
        manager = PlayerPrefsManager(
            SimpleNamespace(package='com.example.game', adb_binary='adb', serial='serial')
        )
        prefs = '/remote/prefs.xml'
        manager._game_is_stopped = Mock(return_value=True)
        manager._shell = Mock(side_effect=[
            AdbResult(0, '\n'.join([
                'prefs.xml.bak',
                'prefs.xml.alas-20260726-120000-0123456789ab.bak',
                'prefs.xml.alas-tmp-0123456789abcdef.tmp',
                'prefs.xml.unrelated.bak',
            ]), ''),
            AdbResult(0, '', ''),
            AdbResult(0, '', ''),
        ])

        manager._cleanup_stale_transaction_files(prefs)

        self.assertEqual(
            [
                call(['ls', '-1', '/remote'], error_type=PlayerPrefsUnsupported),
                call(['rm', '-f', '/remote/prefs.xml.alas-20260726-120000-0123456789ab.bak'], error_type=PlayerPrefsUnsupported),
                call(['rm', '-f', '/remote/prefs.xml.alas-tmp-0123456789abcdef.tmp'], error_type=PlayerPrefsUnsupported),
            ],
            manager._shell.call_args_list,
        )

    def test_skip_log_does_not_contain_exception_details(self):
        manager = PlayerPrefsManager(
            SimpleNamespace(package='com.example.game', adb_binary='adb', serial='serial')
        )
        manager._apply_locked = Mock(side_effect=PlayerPrefsUnsupported('/sensitive/path/uuid.xml'))

        with (
            patch('module.game_setting.player_prefs._device_lock', return_value=nullcontext()),
            patch('module.game_setting.player_prefs.logger.warning') as warning,
        ):
            result = manager.apply()

        self.assertFalse(result)
        warning.assert_called_once_with('[GameSettings] 已跳过游戏本地设置自动配置（安全检查未通过）')


class TestPlayerPrefsRootModes(unittest.TestCase):
    def test_falls_back_to_su_when_adb_root_is_unavailable(self):
        manager = PlayerPrefsManager(
            SimpleNamespace(package='com.example.game', adb_binary='adb', serial='serial')
        )
        manager._shell = Mock(return_value=AdbResult(1, 'uid=2000(shell)', ''))
        manager._run_adb = Mock(side_effect=[
            AdbResult(1, '', ''),
            AdbResult(0, 'uid=0(root)', ''),
        ])

        with patch('module.game_setting.player_prefs.time.monotonic', side_effect=[0, 6]):
            self.assertTrue(manager._ensure_root())

        self.assertTrue(manager._use_su)
        self.assertEqual(
            [
                call(['root'], check=False),
                call(['shell', 'su', '-c', 'id'], check=False),
            ],
            manager._run_adb.call_args_list,
        )

    def test_su_mode_wraps_shell_and_binary_transfers(self):
        manager = PlayerPrefsManager(
            SimpleNamespace(package='com.example.game', adb_binary='adb', serial='serial')
        )
        manager._use_su = True
        manager._run_adb = Mock(return_value=AdbResult(0, '', ''))
        manager._run_adb_bytes = Mock(return_value=b'<map />')

        manager._shell(['stat', '-c', '%u:%g:%a', '/remote/prefs.xml'])
        content = manager._read_remote_bytes('/remote/prefs.xml', PlayerPrefsUnsupported)
        manager._write_remote_bytes('/remote/prefs.tmp', content, PlayerPrefsWriteError)

        manager._run_adb.assert_called_once_with(
            ['shell', 'su', '-c', 'stat -c %u:%g:%a /remote/prefs.xml'],
            timeout=15,
            check=True,
            error_type=PlayerPrefsUnsupported,
        )
        self.assertEqual(
            [
                call(
                    ['exec-out', 'su', '-c', 'cat /remote/prefs.xml'],
                    timeout=30,
                    error_type=PlayerPrefsUnsupported,
                ),
                call(
                    ['exec-in', 'su', '-c', "sh -c 'cat > /remote/prefs.tmp'"],
                    input_data=b'<map />',
                    timeout=30,
                    error_type=PlayerPrefsWriteError,
                ),
            ],
            manager._run_adb_bytes.call_args_list,
        )
