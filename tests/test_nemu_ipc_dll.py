"""NemuIpc SDK DLL 选择逻辑的单元测试。

覆盖实例版本推断（vms 目录名）与 DLL 候选路径顺序：
实例版本精确匹配的 SDK 必须排在通用路径之前，避免同机多版本
并存时（如 nx_device/12.0 与 15.0）错用旧版 DLL 连新版实例。
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

import numpy as np

from module.device.method.nemu_ipc import NemuIpcImpl
from module.exception import EmulatorNotRunningError


def build_fake_install(root, versions=('12.0',), instance_names=('MuMuPlayer-12.0-0',)):
    """构造一个仿真的 MuMu 安装目录结构。

    Args:
        root (str): 临时目录。
        versions (tuple): nx_device 下存在的版本目录。
        instance_names (tuple): vms 下存在的实例目录名。

    Returns:
        str: 安装根目录。
    """
    for version in versions:
        folder = os.path.join(root, 'nx_device', version, 'shell', 'sdk')
        os.makedirs(folder)
        with open(os.path.join(folder, 'external_renderer_ipc.dll'), 'w') as f:
            f.write('fake')
    folder = os.path.join(root, 'nx_main', 'sdk')
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, 'external_renderer_ipc.dll'), 'w') as f:
        f.write('fake')
    for name in instance_names:
        os.makedirs(os.path.join(root, 'vms', name))
    return root


class TestDetectVersion(unittest.TestCase):
    def test_detect_from_vms_name(self):
        with tempfile.TemporaryDirectory() as root:
            build_fake_install(
                root, versions=('12.0', '15.0'),
                instance_names=('MuMuPlayer-12.0-1', 'MuMuPlayer-15.0-0'))
            self.assertEqual(
                NemuIpcImpl.detect_version(root, 0), '15.0')
            self.assertEqual(
                NemuIpcImpl.detect_version(root, 1), '12.0')

    def test_detect_yxarknights_instance(self):
        with tempfile.TemporaryDirectory() as root:
            build_fake_install(
                root, versions=('12.0',), instance_names=('YXArkNights-12.0-1',))
            self.assertEqual(
                NemuIpcImpl.detect_version(root, 1), '12.0')

    def test_detect_missing_vms_returns_none(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(NemuIpcImpl.detect_version(root, 0))


class TestDllPathOrder(unittest.TestCase):
    """验证加载的 DLL 来自实例版本对应的 nx_device/<版本> 目录。

    __init__ 会真实加载 DLL，这里用写入假内容的 dll 文件会让
    ctypes.CDLL 抛 OSError 并继续尝试下一个候选；因此只对
    "最终选中路径" 的语义做间接验证：让仅实例版本的路径真实可加载
    是不可能的（假 DLL），改为验证候选列表顺序 via detect + 文件系统。
    """

    def _probe_selected(self, root, instance_id, version=None):
        """拦截 ctypes.CDLL，记录第一个存在的候选路径。"""
        import ctypes as _ctypes

        selected = []

        class _FakeLib:
            pass

        original = _ctypes.CDLL

        def fake_cdll(path, *args, **kwargs):
            if path not in selected:
                selected.append(path)
            return _FakeLib()

        _ctypes.CDLL = fake_cdll
        try:
            NemuIpcImpl(
                nemu_folder=root, instance_id=instance_id,
                version=version)
        finally:
            _ctypes.CDLL = original
        return selected[0]

    def test_explicit_version_wins(self):
        with tempfile.TemporaryDirectory() as root:
            build_fake_install(
                root, versions=('12.0', '15.0'),
                instance_names=('MuMuPlayer-12.0-1', 'MuMuPlayer-15.0-0'))
            selected = self._probe_selected(root, 0)
            self.assertIn('nx_device/15.0', selected.replace('\\', '/'))
            self.assertNotIn('nx_device/12.0', selected.replace('\\', '/'))

    def test_version_inferred_from_vms(self):
        with tempfile.TemporaryDirectory() as root:
            build_fake_install(
                root, versions=('12.0', '15.0'),
                instance_names=('MuMuPlayer-12.0-1', 'MuMuPlayer-15.0-0'))
            # 不传 version，从 vms/MuMuPlayer-15.0-0 自动推断
            selected = self._probe_selected(root, 0)
            self.assertIn('nx_device/15.0', selected.replace('\\', '/'))

    def test_fallback_prefers_newer_version(self):
        with tempfile.TemporaryDirectory() as root:
            # 只有 12.0/15.0 目录，vms 为空（无法推断版本）
            build_fake_install(root, versions=('12.0', '15.0'), instance_names=())
            selected = self._probe_selected(root, 0)
            normalized = selected.replace('\\', '/')
            # 经典 shell/sdk 路径不存在时，兜底应选更高版本 15.0
            self.assertIn('nx_device/15.0', normalized)
            self.assertNotIn('nx_device/12.0', normalized)


class TestBgraLayout(unittest.TestCase):
    """像素通道序按 SDK 来源架构区分：新架构（nx_device/nx_main）为 BGRA，
    经典布局（shell/sdk）为 RGBA。实测 12.0 与 15.0 的 nx_device SDK 均为 BGRA。
    """

    def _build(self, version):
        import ctypes as _ctypes

        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        folder = os.path.join(root, 'nx_device', version, 'shell', 'sdk')
        os.makedirs(folder)
        with open(os.path.join(folder, 'external_renderer_ipc.dll'), 'w') as f:
            f.write('fake')
        original = _ctypes.CDLL

        class _FakeLib:
            pass

        _ctypes.CDLL = lambda path, *a, **k: _FakeLib()
        try:
            impl = NemuIpcImpl(
                nemu_folder=root, instance_id=0, version=version)
        finally:
            _ctypes.CDLL = original
        return impl

    def test_nx_device_layout_is_bgra(self):
        self.assertTrue(self._build('15.0').bgra_layout)
        self.assertTrue(self._build('12.0').bgra_layout)

    def test_classic_and_nx_main_is_not_handled_here(self):
        # 经典 shell/sdk 布局无法用 nx_device 目录构造，直接验证路径判定语义
        impl = self._build('12.0')
        impl.ipc_dll = r'C:\MuMu\shell\sdk\external_renderer_ipc.dll'
        self.assertFalse(impl.bgra_layout)
        impl.ipc_dll = r'C:\MuMu\nx_main\sdk\external_renderer_ipc.dll'
        self.assertTrue(impl.bgra_layout)


class TestTouchArgumentTypes(unittest.TestCase):
    """触控坐标必须先转成 Python int 再交给 ctypes。

    click/drag/swipe 的坐标来自 numpy（np.int64），而 nemu 的 DLL 函数没有声明
    argtypes，直接传 numpy 标量会抛
    ArgumentError: Don't know how to convert parameter 3；被 retry 包装成
    EmulatorNotRunningError 后 Alas 会误判掉线并重启模拟器（大舰队作战拖拽时实锤）。
    """

    class _Lib:
        """记录调用参数的假 DLL。"""

        def __init__(self):
            self.touch_down_calls = []
            self.nemu_input_event_touch_down = self._touch_down
            self.nemu_input_event_touch_up = self._touch_up

        def _touch_down(self, *args):
            self.touch_down_calls.append(args)
            return 0

        def _touch_up(self, *args):
            return 0

    def _build(self):
        import ctypes as _ctypes

        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        folder = os.path.join(root, 'nx_device', '15.0', 'shell', 'sdk')
        os.makedirs(folder)
        with open(os.path.join(folder, 'external_renderer_ipc.dll'), 'w') as f:
            f.write('fake')

        lib = self._Lib()
        original = _ctypes.CDLL
        _ctypes.CDLL = lambda path, *a, **k: lib
        try:
            impl = NemuIpcImpl(nemu_folder=root, instance_id=0, version='15.0')
        finally:
            _ctypes.CDLL = original
        impl.connect_id = 1  # 跳过真实连接
        impl.display_id = 0
        return impl, lib

    def test_down_converts_numpy_int(self):
        impl, lib = self._build()
        impl.down(np.int64(331), np.int64(396))
        self.assertTrue(lib.touch_down_calls, "没有调用 nemu_input_event_touch_down")
        args = tuple(lib.touch_down_calls[-1])
        self.assertEqual(args, (1, 0, 331, 396))
        for value in args:
            self.assertIsInstance(value, int)
            self.assertNotIsInstance(value, np.integer)

    def test_down_accepts_plain_numbers(self):
        impl, lib = self._build()
        impl.down(120, 240)
        impl.down(120.7, 240.2)  # 截断成 int，不应抛异常
        self.assertEqual(tuple(lib.touch_down_calls[-1]), (1, 0, 120, 240))

    def test_invalid_point_never_reaches_dll(self):
        """None / nan / inf 之类无效坐标不落到 DLL，也不被当成模拟器掉线。

        转换失败抛出的 TypeError / ValueError / OverflowError 会被 retry 包装按
        「调用方参数错误」直接抛出（见 TestRetryErrorClassification），
        而不是重试后包装成 EmulatorNotRunningError 去重启模拟器。
        """
        impl, lib = self._build()
        for value in (None, float("nan"), float("inf")):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError, OverflowError)):
                    impl.down(value, 10)
        self.assertFalse(lib.touch_down_calls, "无效坐标不应调用底层 DLL")


class TestRetryErrorClassification(unittest.TestCase):
    """参数类型错误不是模拟器掉线，不能被包装成 EmulatorNotRunningError。

    否则一次调用方传错类型就会触发模拟器重启，并把真实原因埋进重启日志。
    """

    class _Impl:
        def reconnect(self):
            pass

    def _run(self, func):
        with mock.patch('module.device.method.nemu_ipc.RETRY_TRIES', 2):
            with mock.patch('module.device.method.nemu_ipc.retry_sleep', lambda _: 0):
                return func(self._Impl(), 1, 2)

    def test_argument_error_is_reraised(self):
        import ctypes

        from module.device.method.nemu_ipc import retry as nemu_retry

        @nemu_retry
        def down(self, x, y):
            raise ctypes.ArgumentError(
                "argument 3: TypeError: Don't know how to convert parameter 3")

        with self.assertRaises(ctypes.ArgumentError):
            self._run(down)

    def test_nemu_ipc_error_still_means_emulator_dead(self):
        from module.device.method.nemu_ipc import NemuIpcError, retry as nemu_retry

        @nemu_retry
        def down(self, x, y):
            raise NemuIpcError("nemu_input_event_touch_down failed")

        with self.assertRaises(EmulatorNotRunningError):
            self._run(down)


if __name__ == '__main__':
    unittest.main()
