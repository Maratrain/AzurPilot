import re

import yaml

from module.base.timer import Timer
from module.device.method.utils import HierarchyButton
from module.equipment.assets import *
from module.exception import EmulatorNotRunningError, RequestHumanTakeover
from module.logger import logger
from module.retire.assets import TEMPLATE_BOGUE, TEMPLATE_HERMES, TEMPLATE_RANGER, TEMPLATE_LANGLEY
from module.storage.assets import EQUIPMENT_FULL
from module.storage.storage import StorageHandler

EMPTY_CODE = "MC8wLzAvMC8wXDA="
EQUIPMENT_CODE_PATTERN = re.compile(r'[A-Za-z0-9+/=]{%d,}' % len(EMPTY_CODE))
U2_CONTROL_METHODS = {'uiautomator2', 'minitouch', 'MaaTouch'}
EQUIPMENT_PREVIEW = list([
    EQUIPMENT_CODE_EQUIP_0,
    EQUIPMENT_CODE_EQUIP_1,
    EQUIPMENT_CODE_EQUIP_2,
    EQUIPMENT_CODE_EQUIP_3,
    EQUIPMENT_CODE_EQUIP_4,
    EQUIPMENT_CODE_EQUIP_5,
])


class EquipmentCodeHandler(StorageHandler):
    last_code: str = None
    FASTINPUT_IME = 'com.github.uiautomator/.FastInputIME'

    @property
    def equipment_code_config_key(self):
        return None

    @property
    def equipment_code_export_to_config(self):
        if self.equipment_code_config_key:
            return True
        return getattr(self.config, 'EquipmentCode_ExportToConfig', False)

    def _code_config_load(self):
        key = self.equipment_code_config_key
        if key:
            raw = self.config.cross_get(keys=key)
        else:
            raw = getattr(self.config, 'EquipmentCode_Config', '')

        config = {}
        try:
            for item in yaml.safe_load_all(raw or ''):
                if item:
                    config.update(item)
        except Exception:
            logger.error("Fail to load equipment code config")
        return config

    def _code_config_save(self, config):
        value = yaml.safe_dump(config)
        key = self.equipment_code_config_key
        if key:
            self.config.cross_set(keys=key, value=value)
        elif hasattr(self.config, 'EquipmentCode_Config'):
            self.config.EquipmentCode_Config = value
        else:
            logger.warning("No equipment code config target, skip saving")

    def equipment_code_supported(self):
        method = self.config.Emulator_ControlMethod
        if method in U2_CONTROL_METHODS:
            return True

        logger.warning(
            f"Equipment code requires uiautomator2 based control method, "
            f"current control method is {method}, skip equipment change"
        )
        return False

    def get_code(self, name):
        config = self._code_config_load()
        code = config.get(name)
        if code is None:
            logger.error(f"Config does not contain equipment code for {name}")
        return code

    def set_code(self, name, code):
        config = self._code_config_load()
        try:
            config.update({name: code})
            self._code_config_save(config)
        except Exception:
            logger.error("Fail to set equipment code config")

    def current_ship(self):
        """
        Currently, only supports common CV recognization

        Pages:
            in: equipment_code
        """
        for _ in self.loop():
            if not self.appear(EMPTY_SHIP_R):
                break
        if TEMPLATE_BOGUE.match(self.device.image, scaling=1.46):  # image has rotation
            logger.info("Bogue detected")
            return 'bogue'
        elif TEMPLATE_HERMES.match(self.device.image, scaling=124 / 89):
            logger.info("Hermes detected")
            return 'hermes'
        elif TEMPLATE_RANGER.match(self.device.image, scaling=4 / 3):
            logger.info("Ranger detected")
            return 'ranger'
        elif TEMPLATE_LANGLEY.match(self.device.image, scaling=25 / 21):
            logger.info("Langley detected")
            return 'langley'
        else:
            logger.warning("Unknown ship detected, assuming DD")
            return 'DD'

    def _code_enter(self):
        """
        Pages:
            in: ship_detail
            out: equipment_code
        """
        for _ in self.loop():
            if self.appear(EQUIPMENT_CODE_PAGE_CHECK, offset=(5, 5)):
                break

            if self.appear_then_click(EQUIPMENT_CODE_ENTRANCE, offset=(5, 5), interval=1):
                continue

    def _code_exit(self):
        """
        Pages:
            in: equipment_code
            out: ship_detail
        """
        self.ui_back(check_button=EQUIPMENT_CODE_ENTRANCE)

    def is_code_preview_loaded(self):
        if self.appear(EQUIPMENT_CODE_EQUIP_5_LOCKED, offset=(5, 5)):
            max_index = 5
        else:
            max_index = 6
        for index in range(max_index):
            if not self.appear(EQUIPMENT_PREVIEW[index], offset=(5, 5)):
                return True

        return False

    def _code_preview_clear(self):
        for _ in self.loop(timeout=2):
            if not self.is_code_preview_loaded():
                return True

            if self.appear_then_click(EQUIPMENT_CODE_CLEAR, offset=(5, 5), interval=1):
                continue
        else:
            return False

    def fastinput_ime_enable(self):
        self.device.adb_shell(['am', 'start', '-a', 'android.settings.INPUT_METHOD_SETTINGS'])
        timeout = Timer(10).start()
        while 1:
            if timeout.reached():
                logger.warning("Enable FastInputIME timeout")
                break

            h = self.device.dump_hierarchy_adb()

            def appear(xpath):
                return bool(HierarchyButton(h, xpath))

            def appear_then_click(xpath):
                b = HierarchyButton(h, xpath)
                if b:
                    self.device.click(b)
                    return True
                else:
                    return False

            if appear_then_click('//*[@resource-id="android:id/title" and @text="FastInputIME"]/following-sibling::*[@resource-id="android:id/switch_widget" and @checked="false"]'):
                continue
            if appear_then_click('//*[@resource-id="android:id/button1"]'):
                continue
            # Disable one other enabled IME at a time
            if appear_then_click('(//*[@resource-id="android:id/title" and @text!="FastInputIME"]/following-sibling::*[@resource-id="android:id/switch_widget" and @enabled="true" and @checked="true"])[1]'):
                continue
            if appear('//*[@resource-id="android:id/title" and @text="FastInputIME"]/following-sibling::*[@resource-id="android:id/switch_widget" and @checked="true"]') \
                    and not appear('//*[@resource-id="android:id/title" and @text!="FastInputIME"]/following-sibling::*[@resource-id="android:id/switch_widget" and @enabled="true" and @checked="true"]'):
                break

        self.device.adb_shell(['input', 'keyevent', '4'])

    def set_fastinput_ime(self):
        d = self.device.u2
        failed = False
        try:
            for command in ('enable', 'set'):
                result = d.shell(['ime', command, self.FASTINPUT_IME])
                exit_code = getattr(result, 'exit_code', 0)
                output = getattr(result, 'output', result)
                if exit_code:
                    failed = True
                    logger.warning(f"Unable to {command} FastInputIME: {output.strip()}")
        except Exception as e:
            failed = True
            logger.warning(f"Unable to set FastInputIME: {e}")

        if failed:
            logger.warning("FastInputIME not enabled by adb shell, trying to enable it in settings")
            self.fastinput_ime_enable()

    @staticmethod
    def _adb_input_text_escape(text):
        text = str(text).replace('%', '%s')
        for char in ['\\', '"', "'", '`', '$', '&', '|', '<', '>', ';', '(', ')', '*']:
            text = text.replace(char, f'\\{char}')
        return text

    def _code_input_adb(self, code):
        try:
            text = self._adb_input_text_escape(code)
            clear_keys = ' '.join(['KEYCODE_DEL'] * (len(code) + 10))
            self.device.adb_shell(f'input keyevent KEYCODE_MOVE_END {clear_keys}', timeout=5)
            self.device.adb_shell(f'input text {text}', timeout=5)
            self.device.adb_shell('input keyevent KEYCODE_ENTER', timeout=1)
            logger.info("通过 ADB 输入装备码")
            return True
        except (EmulatorNotRunningError, RequestHumanTakeover):
            raise
        except Exception as e:
            logger.warning(f"通过 ADB 输入装备码失败: {e}")
            return False

    def _code_input_uiautomator2(self, code):
        try:
            d = self.device.u2
            d.send_keys(text=code, clear=True)
            d.send_action(code="done")
            logger.info("通过 uiautomator2 输入装备码")
            return True
        except Exception as e:
            logger.warning(f"通过 uiautomator2 输入装备码失败: {e}")
            return False

    def _code_input(self, code):
        logger.info(f"Code input: {code}")
        d = self.device.u2
        click_timer = Timer(1, count=3)
        for _ in self.loop():
            name, shown = d.current_ime()
            if shown:
                if name != self.FASTINPUT_IME:
                    self.set_fastinput_ime()
                    continue
                else:
                    break
                if click_timer.reached_and_reset():
                    self.device.click(EQUIPMENT_CODE_TEXTBOX)
                    textbox_clicked = True
            else:
                continue

            for _ in self.loop(timeout=10, skip_first=False):
                if self.is_code_preview_loaded():
                    return True
                if self.appear_then_click(EQUIPMENT_CODE_ENTER, offset=(5, 5), interval=3):
                    continue

        if self._code_input_uiautomator2(code):
            for _ in self.loop(timeout=10, skip_first=False):
                if self.is_code_preview_loaded():
                    return True
                if self.appear_then_click(EQUIPMENT_CODE_ENTER, offset=(5, 5), interval=3):
                    continue

        logger.warning("Equipment code load failed")
        return False

    def _code_confirm(self):
        logger.info("Code apply")
        for _ in self.loop(timeout=10):
            if self.appear(EQUIPMENT_CODE_ENTRANCE, offset=(5, 5)):
                return True
            if self.appear(EQUIPMENT_FULL, offset=(30, 30)):
                return False
            if self.handle_popup_confirm("EQUIPMENT_CODE"):
                continue
            if self.appear_then_click(EQUIPMENT_CODE_CONFIRM, offset=(5, 5), interval=3):
                continue
        else:
            return False

    def _code_apply(self, code=None):
        for _ in range(5):
            self._code_preview_clear()
            if code is not None and code != EMPTY_CODE:
                success = self._code_input(code)
                if not success:
                    continue
            success = self._code_confirm()
            if success:
                logger.info("Equipment code apply complete.")
                return True
            else:
                self.handle_storage_full()
        else:
            return False

    @staticmethod
    def _is_equipment_code(code):
        code = code.strip().strip('\'"')
        if len(code) < len(EMPTY_CODE):
            return False
        if len(code) % 4 != 0:
            return False
        if not EQUIPMENT_CODE_PATTERN.fullmatch(code):
            return False
        if '=' in code.rstrip('='):
            return False
        return True

    @staticmethod
    def _code_from_text(text):
        for line in reversed(str(text).splitlines()):
            line = line.strip().strip('\'"')
            if not line:
                continue

            lowered = line.lower()
            if any(keyword in lowered for keyword in [
                'not found',
                'unknown command',
                'no shell command implementation',
                'no primary clip',
                'exception',
                'error:',
                'security exception',
            ]):
                continue

            for prefix in ['text:', 'clipboard text:']:
                if lowered.startswith(prefix):
                    line = line[len(prefix):].strip().strip('\'"')
                    break

            if line.startswith('ClipData') and ':' in line:
                line = line.rsplit(':', 1)[1].strip().strip('\'"')

            if EquipmentCodeHandler._is_equipment_code(line):
                return line

            for match in EQUIPMENT_CODE_PATTERN.finditer(line):
                code = match.group(0).strip('=')
                padding = '=' * (-len(code) % 4)
                code = code + padding
                if EquipmentCodeHandler._is_equipment_code(code):
                    return code

        return None

    @staticmethod
    def _parcel_bytes(output):
        data = bytearray()
        for raw in str(output).splitlines():
            line = raw.strip()
            if line.startswith('0x') and ':' in line:
                line = line.split(':', 1)[1]
            elif 'Parcel(' in line:
                line = line.split('Parcel(', 1)[1]
            else:
                continue
            line = line.split("'", 1)[0]
            for word in re.findall(r'\b[0-9a-fA-F]{8}\b', line):
                data.extend(int(word, 16).to_bytes(4, 'little'))
        return bytes(data)

    @staticmethod
    def _code_from_parcel_output(output):
        data = EquipmentCodeHandler._parcel_bytes(output)
        if not data:
            return None

        for text in [
            data.decode('utf-8', errors='ignore'),
            data.decode('utf-16le', errors='ignore'),
        ]:
            code = EquipmentCodeHandler._code_from_text(text.replace('\x00', '\n'))
            if code is not None:
                return code

        return None

    @staticmethod
    def _code_from_clipboard_output(output):
        if output is None:
            return None
        if isinstance(output, bytes):
            output = output.decode('utf-8', errors='ignore')

        code = EquipmentCodeHandler._code_from_parcel_output(output)
        if code is not None:
            return code

        return EquipmentCodeHandler._code_from_text(output)

    def _clipboard_adb(self):
        for command in [
            ['cmd', 'clipboard', 'get'],
            ['cmd', 'clipboard', 'get-primary-clip'],
            ['service', 'call', 'clipboard', '4', 's16', 'com.android.shell', 's16', '', 'i32', '0', 'i32', '0'],
        ]:
            try:
                output = self.device.adb_shell(command, timeout=3)
            except (EmulatorNotRunningError, RequestHumanTakeover):
                raise
            except Exception as e:
                logger.debug(f"通过 ADB 读取剪贴板失败: {e}")
                continue

            code = self._code_from_clipboard_output(output)
            if code is not None:
                logger.info("通过 ADB 读取装备码剪贴板成功")
                return code

        return None

    def _clipboard_uiautomator2(self):
        try:
            output = self.device.clipboard
        except (EmulatorNotRunningError, RequestHumanTakeover):
            raise
        except Exception as e:
            logger.warning(f"通过 uiautomator2 读取剪贴板失败: {e}")
            return None

        code = self._code_from_clipboard_output(output)
        if code is not None:
            logger.info("通过 uiautomator2 读取装备码剪贴板成功")
        return code

    def _clipboard_get(self):
        code = self._clipboard_adb()
        if code is not None:
            return code

        code = self._clipboard_uiautomator2()
        if code is not None:
            return code

        logger.warning("读取装备码剪贴板失败")
        return None

    def _code_export(self):
        self.handle_info_bar()
        for _ in self.loop(timeout=10):
            if self.info_bar_count():
                break
            if self.appear_then_click(EQUIPMENT_CODE_EXPORT, offset=(5, 5), interval=3):
                continue
        return self._clipboard_get()

    def code_clear(self, name=None):
        if not self.equipment_code_supported():
            return False

        self._code_enter()
        if name is None:
            name = self.current_ship()
        if self.equipment_code_export_to_config and self.get_code(name=name) is None:
            self.last_code = self._code_export()
            if self.last_code is None:
                logger.warning("装备码导出失败，跳过清空装备")
                return False
            self.set_code(name=name, code=self.last_code)
        return self._code_apply(code=None)

    def code_apply(self, name=None):
        if not self.equipment_code_supported():
            return False

        self._code_enter()
        if name is None:
            name = self.current_ship()
        code = self.get_code(name=name)
        if code is None:
            code = self.last_code
        if code is None:
            logger.warning("没有可用装备码，跳过装备应用")
            return False
        return self._code_apply(code=code)
