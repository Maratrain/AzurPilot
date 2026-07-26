"""在游戏启动前安全地更新碧蓝航线的本地 PlayerPrefs 设置。"""

import hashlib
import os
import re
import secrets
import shlex
import subprocess
import time
import xml.etree.ElementTree as etree
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from module.exception import RequestHumanTakeover
from module.logger import logger


PACKAGE_PATTERN = re.compile(r'^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$')
PREFS_FILE_PATTERN = re.compile(r'^[A-Za-z0-9_.-]+\.v2\.playerprefs\.xml$')
STANDBY_MODE_KEY_PATTERN = re.compile(r'^STANDBY_MODE_KEY_[0-9]+$')
STORY_SPEED_KEY_PATTERN = re.compile(r'^story_speed_flag[0-9]+$')
METADATA_PATTERN = re.compile(r'^(?P<uid>[0-9]+):(?P<gid>[0-9]+):(?P<mode>[0-7]{3,4})$')
SELINUX_CONTEXT_PATTERN = re.compile(r'^[A-Za-z0-9_:,.-]+$')
LEGACY_TRANSACTION_SUFFIX_PATTERN = re.compile(r'^\.alas-\d{8}-\d{6}-[0-9a-f]{12}\.bak$')
LEGACY_TEMPORARY_TRANSACTION_SUFFIX_PATTERN = re.compile(
    r'^\.alas-\d{8}-\d{6}-[0-9a-f]{12}\.(?:tmp|rollback\.tmp)$'
)
TEMPORARY_TRANSACTION_SUFFIX_PATTERN = re.compile(r'^\.alas-tmp-[0-9a-f]{16}\.tmp$')
STORY_SPEED_VALUE = 9

# 仅维护经过当前五服 Lua 源码验证的设置。不要根据 setting_generated.py 泛化写入。
RECOMMENDED_INT_SETTINGS = {
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
RECOMMENDED_STRING_SETTINGS = {
    '_WorldBossProgressTipFlag_': '',
}


class PlayerPrefsError(Exception):
    """PlayerPrefs 事务的基础异常。"""


class PlayerPrefsUnsupported(PlayerPrefsError):
    """当前设备或文件格式不支持安全写入。"""


class PlayerPrefsWriteError(PlayerPrefsError):
    """写入或写后校验失败。"""


@dataclass(frozen=True)
class PlayerPrefsChanges:
    """一次 XML 更新的变更摘要。"""

    static_changed: int
    story_speed_changed: int
    standby_changed: int
    story_speed_keys: tuple[str, ...]
    standby_keys: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return (
            self.static_changed > 0
            or self.story_speed_changed > 0
            or self.standby_changed > 0
        )


@dataclass(frozen=True)
class PlayerPrefsMetadata:
    """Android 应用私有文件的所有者、权限和 SELinux 上下文。"""

    uid: str
    gid: str
    mode: str
    context: str


@dataclass(frozen=True)
class AdbResult:
    """已执行的 ADB 命令结果。"""

    returncode: int
    stdout: str
    stderr: str


def _is_target_key(name: str | None) -> bool:
    """判断键是否在严格维护的静态或动态设置白名单内。"""
    return isinstance(name, str) and (
        name in RECOMMENDED_INT_SETTINGS
        or name in RECOMMENDED_STRING_SETTINGS
        or STORY_SPEED_KEY_PATTERN.fullmatch(name) is not None
        or STANDBY_MODE_KEY_PATTERN.fullmatch(name) is not None
    )


def _index_target_entries(root: etree.Element) -> dict[str, etree.Element]:
    """索引需要读取或修改的设置项，并拒绝同名目标键。"""
    if root.tag != 'map':
        raise PlayerPrefsUnsupported(f'不支持的 PlayerPrefs 根节点: {root.tag!r}')

    entries = {}
    for element in root:
        name = element.get('name')
        if not _is_target_key(name):
            continue
        if name in entries:
            raise PlayerPrefsUnsupported(f'PlayerPrefs 中存在重复的目标键: {name!r}')
        entries[name] = element
    return entries


def _set_int(root: etree.Element, entries: dict[str, etree.Element], name: str, value: int) -> bool:
    """以 Android SharedPreferences 的 int 格式写入单个白名单键。"""
    expected = str(value)
    element = entries.get(name)
    if element is None:
        element = etree.Element('int', {'name': name, 'value': expected})
        root.append(element)
        entries[name] = element
        return True

    if element.tag != 'int':
        raise PlayerPrefsUnsupported(f'目标键 {name!r} 的 XML 类型不是 int: {element.tag!r}')
    if element.text and element.text.strip():
        raise PlayerPrefsUnsupported(f'目标键 {name!r} 包含无法安全处理的文本值')
    if element.get('value') == expected:
        return False

    element.set('value', expected)
    return True


def _set_string(root: etree.Element, entries: dict[str, etree.Element], name: str, value: str) -> bool:
    """以 Android SharedPreferences 的 string 格式写入单个白名单键。"""
    element = entries.get(name)
    if element is None:
        element = etree.Element('string', {'name': name})
        element.text = value
        root.append(element)
        entries[name] = element
        return True

    if element.tag != 'string':
        raise PlayerPrefsUnsupported(f'目标键 {name!r} 的 XML 类型不是 string: {element.tag!r}')
    if element.get('value') is not None or len(element):
        raise PlayerPrefsUnsupported(f'目标键 {name!r} 的 XML 内容无法安全处理')
    current = '' if element.text is None else element.text
    if current == value:
        return False

    element.text = value
    return True


def _serialize_xml(root: etree.Element) -> bytes:
    """生成 Android 可读取的 UTF-8 SharedPreferences XML。"""
    etree.indent(root, space='    ')
    return etree.tostring(root, encoding='utf-8', xml_declaration=True, short_empty_elements=True)


def update_player_prefs_xml(content: bytes) -> tuple[bytes, PlayerPrefsChanges]:
    """按严格白名单更新 PlayerPrefs XML，保留所有其他设置。

    Args:
        content: 原始 PlayerPrefs XML 字节。

    Returns:
        更新后的 XML 与变更摘要。

    Raises:
        PlayerPrefsUnsupported: XML 格式未知或包含无法安全处理的目标键。
    """
    try:
        root = etree.fromstring(content)
    except etree.ParseError as error:
        raise PlayerPrefsUnsupported(f'PlayerPrefs XML 解析失败: {error}') from None

    entries = _index_target_entries(root)
    static_changed = 0
    for name, value in RECOMMENDED_INT_SETTINGS.items():
        static_changed += _set_int(root, entries, name, value)

    for name, value in RECOMMENDED_STRING_SETTINGS.items():
        static_changed += _set_string(root, entries, name, value)

    # 剧情速度按玩家 ID 分键存储；只更新已存在的键，不能猜测或新建账号后缀。
    story_speed_keys = tuple(sorted(
        name for name in entries if STORY_SPEED_KEY_PATTERN.fullmatch(name)
    ))
    story_speed_changed = 0
    for name in story_speed_keys:
        story_speed_changed += _set_int(root, entries, name, STORY_SPEED_VALUE)

    standby_keys = tuple(sorted(name for name in entries if STANDBY_MODE_KEY_PATTERN.fullmatch(name)))
    standby_changed = 0
    for name in standby_keys:
        standby_changed += _set_int(root, entries, name, 0)

    changes = PlayerPrefsChanges(
        static_changed=static_changed,
        story_speed_changed=story_speed_changed,
        standby_changed=standby_changed,
        story_speed_keys=story_speed_keys,
        standby_keys=standby_keys,
    )
    return _serialize_xml(root), changes


def verify_player_prefs_xml(
        content: bytes,
        standby_keys: tuple[str, ...],
        story_speed_keys: tuple[str, ...] = (),
) -> None:
    """验证目标设置是否全部已写入预期值。"""
    try:
        root = etree.fromstring(content)
    except etree.ParseError as error:
        raise PlayerPrefsWriteError(f'回读的 PlayerPrefs XML 解析失败: {error}') from None

    entries = _index_target_entries(root)
    for name, value in RECOMMENDED_INT_SETTINGS.items():
        element = entries.get(name)
        if element is None or element.tag != 'int' or element.get('value') != str(value):
            raise PlayerPrefsWriteError(f'目标键 {name!r} 的回读值不正确')

    for name, value in RECOMMENDED_STRING_SETTINGS.items():
        element = entries.get(name)
        actual = '' if element is None or element.text is None else element.text
        if element is None or element.tag != 'string' or actual != value:
            raise PlayerPrefsWriteError(f'目标键 {name!r} 的回读值不正确')

    for name in story_speed_keys:
        element = entries.get(name)
        if element is None or element.tag != 'int' or element.get('value') != str(STORY_SPEED_VALUE):
            raise PlayerPrefsWriteError('剧情自动播放速度设置的回读值不正确')

    for name in standby_keys:
        element = entries.get(name)
        if element is None or element.tag != 'int' or element.get('value') != '0':
            raise PlayerPrefsWriteError('待机模式设置的回读值不正确')


@contextmanager
def _device_lock(serial: str, package: str, timeout: float = 10) -> None:
    """用 serial 和包名派生跨进程锁，避免多实例同时替换同一文件。"""
    key = hashlib.sha256(f'{serial}\0{package}'.encode('utf-8')).hexdigest()[:16]
    lock_file = Path('cache') / f'game-settings-{key}.lock'
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    with lock_file.open('a+b') as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.seek(0)
            handle.write(b'0')
            handle.flush()

        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise PlayerPrefsUnsupported('等待另一实例的游戏设置事务超时') from None
                time.sleep(0.1)

        try:
            yield
        finally:
            if os.name == 'nt':
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class PlayerPrefsManager:
    """通过 root ADB 原子更新碧蓝航线的 PlayerPrefs 文件。"""

    def __init__(self, device, wait_for_stop: bool = False):
        self.device = device
        self.wait_for_stop = wait_for_stop
        self.package = str(device.package)
        self._root_enabled_by_transaction = False
        self._use_su = False

    def _run_adb(
            self,
            args: list[str],
            *,
            timeout: float = 15,
            check: bool = True,
            error_type: type[PlayerPrefsError] = PlayerPrefsUnsupported,
    ) -> AdbResult:
        """执行带退出码检查的 host ADB 命令。"""
        command = [str(self.device.adb_binary), '-s', str(self.device.serial), *map(str, args)]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise error_type(f'ADB 命令无法执行: {args[0]}') from None

        result = AdbResult(
            returncode=completed.returncode,
            stdout=completed.stdout.decode('utf-8', errors='replace').strip(),
            stderr=completed.stderr.decode('utf-8', errors='replace').strip(),
        )
        if check and result.returncode != 0:
            raise error_type(f'ADB 命令执行失败: {args[0]}')
        return result

    def _run_adb_bytes(
            self,
            args: list[str],
            *,
            input_data: bytes | None = None,
            timeout: float = 15,
            error_type: type[PlayerPrefsError] = PlayerPrefsUnsupported,
    ) -> bytes:
        """通过 ADB 传输二进制数据，绝不将 PlayerPrefs 写入本地文件。"""
        command = [str(self.device.adb_binary), '-s', str(self.device.serial), *map(str, args)]
        try:
            completed = subprocess.run(
                command,
                input=input_data,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise error_type(f'ADB 二进制传输失败: {args[0]}') from None
        if completed.returncode != 0:
            raise error_type(f'ADB 二进制传输失败: {args[0]}')
        return completed.stdout

    def _shell(
            self,
            args: list[str],
            *,
            timeout: float = 15,
            check: bool = True,
            error_type: type[PlayerPrefsError] = PlayerPrefsUnsupported,
    ) -> AdbResult:
        if self._use_su:
            args = ['su', '-c', shlex.join(map(str, args))]
        return self._run_adb(
            ['shell', *args],
            timeout=timeout,
            check=check,
            error_type=error_type,
        )

    def _ensure_root(self) -> bool:
        """确认 adbd 为 root，或回退到可用的 ``su -c``。"""
        current = self._shell(['id'], check=False)
        if 'uid=0(root)' in current.stdout:
            return True

        self._run_adb(['root'], check=False)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            current = self._shell(['id'], check=False)
            if 'uid=0(root)' in current.stdout:
                self._root_enabled_by_transaction = True
                return True
            time.sleep(0.25)

        su = self._run_adb(['shell', 'su', '-c', 'id'], check=False)
        if 'uid=0(root)' in su.stdout:
            self._use_su = True
            return True
        return False

    def _restore_root_state(self) -> None:
        """仅在本事务提权过时恢复为非 root adbd，避免改变用户原有状态。"""
        if not self._root_enabled_by_transaction:
            return
        try:
            self._run_adb(['unroot'], check=False)
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                current = self._shell(['id'], check=False)
                if 'uid=0(root)' not in current.stdout:
                    return
                time.sleep(0.25)
        except PlayerPrefsError:
            pass
        logger.warning('[GameSettings] 无法恢复 adbd 的原始非 root 状态')

    def _game_is_stopped(self) -> bool | None:
        """确认包及其子进程均不在运行；无法确认时返回 None。"""
        pidof = self._shell(['pidof', self.package], check=False)
        if pidof.stdout:
            return False

        processes = self._shell(['ps', '-A', '-o', 'NAME'], check=False)
        if processes.returncode != 0:
            return None
        for process in processes.stdout.splitlines():
            process = process.strip()
            if process == self.package or process.startswith(f'{self.package}:'):
                return False
        return True

    def _wait_until_game_stopped(self) -> bool:
        """重启流程中短暂轮询应用退出，其他启动路径只检查一次。"""
        deadline = time.monotonic() + (8 if self.wait_for_stop else 0)
        while True:
            stopped = self._game_is_stopped()
            if stopped is not False:
                return stopped is True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)

    def _prefs_path(self) -> str:
        """定位 Unity PlayerPrefs 文件，文件名不符时拒绝猜测。"""
        if not PACKAGE_PATTERN.fullmatch(self.package):
            raise PlayerPrefsUnsupported('游戏包名格式不安全')

        directory = f'/data/user/0/{self.package}/shared_prefs'
        expected = f'{directory}/{self.package}.v2.playerprefs.xml'
        if self._shell(['test', '-f', expected], check=False).returncode == 0:
            return expected

        files = self._shell(['ls', '-1', directory], check=False)
        if files.returncode != 0:
            raise PlayerPrefsUnsupported('未找到游戏的 PlayerPrefs 目录')
        candidates = [
            name for name in files.stdout.splitlines()
            if PREFS_FILE_PATTERN.fullmatch(name)
        ]
        if len(candidates) != 1:
            raise PlayerPrefsUnsupported('无法唯一定位游戏的 PlayerPrefs 文件')
        return f'{directory}/{candidates[0]}'

    def _ensure_no_atomic_backup(self, prefs: str) -> None:
        """避免 Android 未完成的原子写入在下次启动时覆盖主文件。"""
        result = self._shell(['test', '-e', f'{prefs}.bak'], check=False)
        if result.returncode == 0:
            raise PlayerPrefsUnsupported('检测到未完成的应用偏好写入，拒绝覆盖')
        if result.returncode != 1:
            raise PlayerPrefsUnsupported('无法确认应用偏好写入状态')

    def _metadata(
            self,
            remote: str,
            error_type: type[PlayerPrefsError] = PlayerPrefsUnsupported,
    ) -> PlayerPrefsMetadata:
        metadata = self._shell(['stat', '-c', '%u:%g:%a', remote], error_type=error_type).stdout
        match = METADATA_PATTERN.fullmatch(metadata)
        if match is None:
            raise error_type('无法读取 PlayerPrefs 文件权限')

        label = self._shell(['ls', '-Zd', remote], error_type=error_type).stdout.split(maxsplit=1)
        if not label or not SELINUX_CONTEXT_PATTERN.fullmatch(label[0]):
            raise error_type('无法读取 PlayerPrefs 文件的 SELinux 上下文')

        return PlayerPrefsMetadata(
            uid=match.group('uid'),
            gid=match.group('gid'),
            mode=match.group('mode'),
            context=label[0],
        )

    def _restore_metadata(self, remote: str, metadata: PlayerPrefsMetadata) -> None:
        self._shell(
            ['chown', f'{metadata.uid}:{metadata.gid}', remote],
            error_type=PlayerPrefsWriteError,
        )
        self._shell(['chmod', metadata.mode, remote], error_type=PlayerPrefsWriteError)
        self._shell(['chcon', metadata.context, remote], error_type=PlayerPrefsWriteError)
        if self._metadata(remote, error_type=PlayerPrefsWriteError) != metadata:
            raise PlayerPrefsWriteError('PlayerPrefs 临时文件的元数据校验失败')

    def _read_remote_bytes(self, remote: str, error_type: type[PlayerPrefsError]) -> bytes:
        """直接读入内存，不产生本地副本。"""
        args = ['exec-out', 'cat', remote]
        if self._use_su:
            args = ['exec-out', 'su', '-c', shlex.join(['cat', remote])]
        return self._run_adb_bytes(args, timeout=30, error_type=error_type)

    def _write_remote_bytes(
            self,
            remote: str,
            content: bytes,
            error_type: type[PlayerPrefsError],
    ) -> None:
        """从内存写入同目录临时文件，供原子替换使用。"""
        command = ['exec-in', 'sh', '-c', f'cat > {remote}']
        if self._use_su:
            command = ['exec-in', 'su', '-c', shlex.join(['sh', '-c', f'cat > {remote}'])]
        self._run_adb_bytes(
            command,
            input_data=content,
            timeout=30,
            error_type=error_type,
        )

    def _cleanup_stale_transaction_files(self, prefs: str) -> None:
        """清理本模块旧版遗留副本和中断事务的临时文件，不记录文件名。"""
        if self._game_is_stopped() is not True:
            raise PlayerPrefsUnsupported('游戏进程在清理敏感临时数据前启动，已取消本次写入')

        directory, filename = prefs.rsplit('/', maxsplit=1)
        files = self._shell(['ls', '-1', directory], error_type=PlayerPrefsUnsupported).stdout.splitlines()
        prefix = f'{filename}.alas-'
        stale_files = []
        for name in files:
            if not name.startswith(prefix):
                continue
            suffix = name[len(filename):]
            if (
                    LEGACY_TRANSACTION_SUFFIX_PATTERN.fullmatch(suffix)
                    or LEGACY_TEMPORARY_TRANSACTION_SUFFIX_PATTERN.fullmatch(suffix)
                    or TEMPORARY_TRANSACTION_SUFFIX_PATTERN.fullmatch(suffix)
            ):
                stale_files.append(f'{directory}/{name}')

        for remote in stale_files:
            if self._game_is_stopped() is not True:
                raise PlayerPrefsUnsupported('游戏进程在清理敏感临时数据期间启动，已取消本次写入')
            self._shell(['rm', '-f', remote], error_type=PlayerPrefsUnsupported)

    def _restore_original(
            self,
            target: str,
            metadata: PlayerPrefsMetadata,
            original: bytes,
            temporary: str,
    ) -> bool:
        """只用内存中的原文恢复目标，并确认内容与原始文件完全一致。"""
        if self._game_is_stopped() is not True:
            return False
        try:
            self._write_remote_bytes(temporary, original, PlayerPrefsWriteError)
            self._restore_metadata(temporary, metadata)
            if self._read_remote_bytes(temporary, PlayerPrefsWriteError) != original:
                return False
            if self._game_is_stopped() is not True:
                return False
            self._shell(['mv', temporary, target], error_type=PlayerPrefsWriteError)
            return (
                self._read_remote_bytes(target, PlayerPrefsWriteError) == original
                and self._metadata(target, error_type=PlayerPrefsWriteError) == metadata
            )
        except PlayerPrefsError:
            return False

    def _apply_locked(self) -> bool:
        if not self._wait_until_game_stopped():
            raise PlayerPrefsUnsupported('游戏进程仍在运行，已跳过本次写入')
        if not self._ensure_root():
            raise PlayerPrefsUnsupported('ADB 未获得 root 权限')
        if not self._wait_until_game_stopped():
            raise PlayerPrefsUnsupported('游戏进程在提权期间启动，已取消本次写入')

        prefs = self._prefs_path()
        self._cleanup_stale_transaction_files(prefs)
        self._ensure_no_atomic_backup(prefs)
        metadata = self._metadata(prefs)
        temporary = f'{prefs}.alas-tmp-{secrets.token_hex(8)}.tmp'
        replace_attempted = False
        original = b''

        try:
            original = self._read_remote_bytes(prefs, PlayerPrefsUnsupported)
            modified, changes = update_player_prefs_xml(original)
            if not changes.changed:
                logger.info('[GameSettings] 推荐的游戏本地设置已符合，无需写入')
                return True

            if not self._wait_until_game_stopped():
                raise PlayerPrefsUnsupported('游戏进程在写入前启动，已取消本次写入')

            self._write_remote_bytes(temporary, modified, PlayerPrefsWriteError)
            self._restore_metadata(temporary, metadata)
            if self._read_remote_bytes(temporary, PlayerPrefsWriteError) != modified:
                raise PlayerPrefsWriteError('PlayerPrefs 临时写入内容校验失败')

            if not self._wait_until_game_stopped():
                raise PlayerPrefsUnsupported('游戏进程在替换前启动，已取消本次写入')
            replace_attempted = True
            self._shell(['mv', temporary, prefs], error_type=PlayerPrefsWriteError)
            applied = self._read_remote_bytes(prefs, PlayerPrefsWriteError)
            verify_player_prefs_xml(
                applied,
                changes.standby_keys,
                changes.story_speed_keys,
            )
            if self._metadata(prefs, error_type=PlayerPrefsWriteError) != metadata:
                raise PlayerPrefsWriteError('写入后的 PlayerPrefs 文件元数据不正确')
        except PlayerPrefsError:
            if replace_attempted:
                restored = self._restore_original(prefs, metadata, original, temporary)
                if not restored:
                    logger.critical('[GameSettings] 本地设置写入失败且内存恢复失败，已阻止启动游戏')
                    raise RequestHumanTakeover from None
                logger.warning('[GameSettings] 本地设置写入失败，已恢复原始状态')
            else:
                logger.warning('[GameSettings] 本地设置写入未完成，原文件未被替换')
            return False
        finally:
            try:
                self._shell(['rm', '-f', temporary], check=False)
            except PlayerPrefsError:
                logger.warning('[GameSettings] 无法清理本次临时写入数据')

        logger.info(
            '[GameSettings] 已写入 %s 项静态设置、%s 项剧情速度设置和 %s 项待机模式设置',
            changes.static_changed,
            changes.story_speed_changed,
            changes.standby_changed,
        )
        return True

    def apply(self) -> bool:
        """安全应用推荐设置；无法安全执行时不影响常规启动。"""
        if getattr(self.device, 'is_over_http', False):
            logger.warning('[GameSettings] HTTP 设备不支持游戏本地设置自动配置，已跳过')
            return False
        try:
            with _device_lock(str(self.device.serial), self.package):
                return self._apply_locked()
        except PlayerPrefsUnsupported:
            logger.warning('[GameSettings] 已跳过游戏本地设置自动配置（安全检查未通过）')
            return False
        finally:
            self._restore_root_state()


def apply_recommended_game_settings(device, wait_for_stop: bool = False) -> bool:
    """为当前设备应用推荐设置的唯一运行时入口。"""
    return PlayerPrefsManager(device, wait_for_stop=wait_for_stop).apply()
