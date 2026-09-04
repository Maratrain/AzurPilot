"""侵蚀1漏猫复盘 debug 录屏（真实游戏画面，scrcpy 设备直录）。

用户要录的是**游戏真实画面**（30fps），而不是 ALAS 处理过的截图帧，因此不再
复用 ALAS 的 device.screenshot()。本模块在录屏开启时，用项目自带的 scrcpy
v1.20 客户端另起一路设备视频流（H.264，max_fps=30），把原始流直接交给 ffmpeg
封装成 mp4 文件。scrcpy 走独立 adb 通道，不影响 ALAS 自身的控制与截图。

用法（由侵蚀1战后处理代码驱动）：
    clip = clip_start(self.config)    # 打完开始找事件时打开（提前几秒预录）
    ... 重扫地图 / 处理事件 / 强制移动 ...
    clip_end(keep=bool(事件已解决))    # 事件处理完、进入下一循环前结束

文件输出到 ./log/clips/，一段一个 mp4（默认全部保留、不自动清理）。
限制：ALAS 自身截图方式若正使用 scrcpy，本模块会跳过并告警（同一 abstract
socket 无法并存）。scrcpy 启动失败会优雅降级为不录，不影响游戏逻辑。
"""

import os
import shutil
import socket
import struct
import subprocess
import threading
import time

from adbutils import AdbError, Network

from module.logger import logger

DEFAULT_OUTPUT_DIR = "./log/clips"
_ACTIVE = None  # 当前活动的录制会话


def _ffmpeg_path():
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


class _ScrcpyClip:
    """一个基于 scrcpy 设备视频流的 debug 录屏段。"""

    def __init__(self, config, fps=30, width=1280, bitrate_scale=1.0):
        self.config = config
        self.fps = fps
        self.width = width
        self.bitrate_scale = bitrate_scale
        self.output_dir = DEFAULT_OUTPUT_DIR
        self.tmp_path = None
        self._core = None
        self.video_socket = None
        self.control_socket = None
        self.server_stream = None
        self.alive = False
        self.resolution = (1280, 720)
        self._proc = None
        self._reader = None
        self._stop = threading.Event()

    # ------------------------------------------------ scrcpy 视频流
    @property
    def _bitrate(self):
        # scrcpy 1.20 超过 20Mbps 会回落，保守限制在 20Mbps 内
        base = max(1, self.width * int(self.width * 9 / 16) * self.fps)
        bitrate = int(base * 0.20 * self.bitrate_scale)
        return max(300_000, min(bitrate, 20_000_000))

    def _open_scrcpy(self):
        from module.device.method.scrcpy.core import ScrcpyCore
        from module.device.method.scrcpy.options import ScrcpyOptions

        core = ScrcpyCore(self.config)
        core.adb_push(self.config.SCRCPY_FILEPATH_LOCAL, self.config.SCRCPY_FILEPATH_REMOTE)

        original_frame_rate = ScrcpyOptions.frame_rate
        try:
            ScrcpyOptions.frame_rate = self.fps
            commands = ScrcpyOptions.command_v120(jar_path=self.config.SCRCPY_FILEPATH_REMOTE)
        finally:
            ScrcpyOptions.frame_rate = original_frame_rate
        # scrcpy-server 1.20 参数位置：max_size、bitrate、max_fps
        commands[6] = str(self.width)
        commands[7] = str(self._bitrate)
        commands[8] = str(self.fps)

        server_stream = core.adb.shell(commands, stream=True)
        server_stream.conn.settimeout(3)

        ret = server_stream.read(10)
        if b"Aborted" in ret:
            raise RuntimeError("scrcpy-server 启动失败：Aborted")
        if ret == b"[server] E":
            ret += self._receive_more(server_stream)
            raise RuntimeError(ret.decode("utf-8", errors="replace"))
        ret += self._receive_more(server_stream)
        if ret:
            logger.info(f"[录屏] scrcpy-server: {ret.strip()}")

        video_socket = self._connect_scrcpy_socket(core)
        if video_socket.recv(1) != b"\x00":
            raise RuntimeError("scrcpy 视频流握手失败")
        control_socket = self._connect_scrcpy_socket(core)
        device_name = video_socket.recv(64).decode("utf-8", errors="replace").rstrip("\x00")
        if device_name:
            logger.attr("[录屏] 设备", device_name)
        resolution = video_socket.recv(4)
        if len(resolution) != 4:
            raise RuntimeError("scrcpy 未返回视频分辨率")
        self.resolution = struct.unpack(">HH", resolution)
        video_socket.settimeout(1)

        self._core = core
        self.server_stream = server_stream
        self.video_socket = video_socket
        self.control_socket = control_socket
        self.alive = True
        logger.attr("[录屏] 分辨率", self.resolution)

    @staticmethod
    def _receive_more(server_stream):
        try:
            return server_stream.conn.recv(4096)
        except Exception:
            return b""

    def _connect_scrcpy_socket(self, core):
        deadline = time.time() + 3
        while time.time() < deadline:
            try:
                sock = core.adb.create_connection(Network.LOCAL_ABSTRACT, "scrcpy")
                sock.settimeout(3)
                return sock
            except AdbError:
                time.sleep(0.1)
        raise RuntimeError("连接 scrcpy socket 超时")

    # ------------------------------------------------ 生命周期
    def start(self):
        """启动 scrcpy 视频流 + ffmpeg 输出到临时文件。成功返回 True。"""
        ffmpeg = _ffmpeg_path()
        if not ffmpeg:
            logger.warning("[录屏] 未找到 ffmpeg，录屏功能不可用")
            return False
        if str(self.config.Emulator_ScreenshotMethod).lower().startswith("scrcpy"):
            logger.warning("[录屏] 截图方式为 scrcpy，无法并存第二条视频流，本次跳过录制")
            return False
        try:
            os.makedirs(self.output_dir, exist_ok=True)
        except OSError as e:
            logger.warning(f"[录屏] 创建输出目录失败: {e}")
            return False

        ts = time.strftime("%Y%m%d_%H%M%S")
        self.tmp_path = os.path.join(self.output_dir, f"_tmp_eh1_{ts}.mp4")
        cmd = [
            ffmpeg, "-y",
            "-f", "h264",
            "-framerate", str(self.fps),
            "-i", "pipe:0",
            "-c:v", "copy",
            "-movflags", "+faststart",
            self.tmp_path,
        ]
        try:
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, bufsize=0,
            )
        except OSError as e:
            logger.warning(f"[录屏] 启动 ffmpeg 失败: {e}")
            return False

        try:
            self._open_scrcpy()
        except Exception as e:
            logger.warning(f"[录屏] scrcpy 启动失败，本次不录制: {e}")
            self._proc.terminate()
            self._proc = None
            if self.tmp_path and os.path.exists(self.tmp_path):
                try:
                    os.remove(self.tmp_path)
                except OSError:
                    pass
            return False

        self._reader = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader.start()
        logger.info(f"[录屏] 开始录制（真实画面 {self.fps}fps）: {self.tmp_path}")
        return True

    def _reader_loop(self):
        """把 scrcpy 原始 H.264 流写入 ffmpeg。"""
        try:
            while not self._stop.is_set() and self.alive:
                try:
                    data = self.video_socket.recv(0x10000)
                except socket.timeout:
                    continue
                except (ConnectionError, OSError):
                    break
                if not data:
                    break
                try:
                    self._proc.stdin.write(data)
                    self._proc.stdin.flush()
                except Exception:
                    break
        finally:
            try:
                if self._proc is not None and self._proc.stdin:
                    self._proc.stdin.close()
            except Exception:
                pass

    def _close_scrcpy(self):
        self.alive = False
        for obj in (self.control_socket, self.video_socket, self.server_stream):
            if obj is None:
                continue
            try:
                obj.close()
            except Exception:
                pass
        self.control_socket = None
        self.video_socket = None
        self.server_stream = None

    def finalize(self, keep):
        """结束录制，决定保留还是删除。

        Returns:
            str: 保留时返回最终 mp4 路径，丢弃或失败返回 None。
        """
        self._stop.set()
        self._close_scrcpy()
        if self._reader is not None:
            self._reader.join(timeout=6)
        if self._proc is not None:
            try:
                self._proc.wait(timeout=6)
            except Exception:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
                try:
                    self._proc.wait(timeout=3)
                except Exception:
                    pass

        if not keep or not self.tmp_path or not os.path.exists(self.tmp_path):
            if self.tmp_path and os.path.exists(self.tmp_path):
                for _ in range(3):
                    try:
                        os.remove(self.tmp_path)
                        break
                    except OSError:
                        time.sleep(0.2)
            return None

        final_name = f"eh1_clip_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
        final_path = os.path.join(self.output_dir, final_name)
        try:
            os.replace(self.tmp_path, final_path)
        except OSError as e:
            logger.warning(f"[录屏] 保存视频失败: {e}")
            try:
                os.remove(self.tmp_path)
            except OSError:
                pass
            return None
        size_mb = os.path.getsize(final_path) / 1024 / 1024
        logger.info(f"[录屏] 已保存: {final_path} ({size_mb:.1f} MB)")
        return final_path


def clip_start(config, fps=30):
    """打开录屏（线程安全：重复调用返回 None）。

    Args:
        config: 当前运行实例的 AzurLaneConfig（含 serial / scrcpy 路径配置）。

    Returns:
        _ScrcpyClip: 录制句柄；启动失败返回 None。
    """
    global _ACTIVE
    if _ACTIVE is not None:
        return None
    rec = _ScrcpyClip(config, fps=fps)
    if not rec.start():
        return None
    _ACTIVE = rec
    return rec


def clip_end(keep=True):
    """结束当前录屏。

    Args:
        keep (bool): 是否把该段保存为 mp4（无事件轮传 False 会自动丢弃）。

    Returns:
        str: 保留时的视频路径；无录制或已丢弃返回 None。
    """
    global _ACTIVE
    rec = _ACTIVE
    _ACTIVE = None
    if rec is None:
        return None
    return rec.finalize(keep=keep)
