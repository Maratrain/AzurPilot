"""大世界战后 debug 录屏（设备端 screenrecord 直录，30fps 输出，真实时间戳）。

当前由侵蚀1练级与短猫相接两个任务使用，各自有独立的开关，共用同一套录制实现
和同一份保留天数设置。

用户要求：录「游戏真实画面」，30fps，**既不加速也不跳帧**。

管线：

    设备端 screenrecord ──SIGINT──▶ 设备上的 mp4 ──adb pull──▶ 本地临时文件 ──ffmpeg──▶ 30fps mp4

**为什么不继续用 scrcpy**（2026-09 更换）：旧实现走 scrcpy-server 1.20 的裸 H.264 视频流，
因为该流不带时间戳，必须自己解码、按 1/30s 墙钟补帧再编码，才能保证播放速度等于真实速度。
但 scrcpy 1.20 靠 `SurfaceControl.createDisplay()` 镜像画面，而 AOSP 在 Android 14 QPR3 /
Android 15 上移除了这个隐藏 API（上游直到 scrcpy 2.4 才适配，见 Genymobile/scrcpy#4657）。
后果是在新模拟器上（例如 MuMu 的安卓 15 实例）握手全部成功、却**一个字节视频都收不到**，
旧实现只能把它误报成「画面完全静止所以没有帧」，现象就是 clip 目录一个文件都没有。

Android 自带的 `screenrecord`（Android 4.4+）没有这个问题：它由系统自己把画面镜像给编码器，
写出的 mp4 自带真实时间戳，播放速度天然等于真实速度——「不加速」不再需要靠补帧实现，
也不再依赖任何随安卓版本变动的隐藏接口。代价是设备端会临时存一个高码率文件
（Android 对 720p60 有约 11Mbps 的下限），所以录完立刻 pull 回来、用 ffmpeg 转成
30fps CRF 26 的小文件，然后删掉设备上的临时文件。

用法（不变，由各任务的战后处理代码驱动，推荐用上下文管理器）：

    with clip_recording(self.config, self.config.OpsiMeowfficerFarming_DebugClip,
                        prefix=CLIP_PREFIX_MEOW):
        ... 重扫地图 / 处理事件 / 强制移动 ...

进入 with 时开录，退出时（含异常路径）保存。每一轮都保留，不管这一轮有没有遇到事件，
方便逐轮回看实际过程。也可手动 `clip_start()` / `clip_end(keep=...)` 控制得更细，
`keep=False` 用于调用方确实想丢弃某一段的场景。

文件输出到 `./log/clips/`，一段一个 mp4，文件名前缀区分任务
（`eh1_clip_*` = 侵蚀1、`meow_clip_*` = 短猫相接），按 `OpsiGeneral` 里的
`DebugClipRetentionDays` 保留天数自动清理（0 表示永久保留）。产物无效时
**不会**留下文件，也不会谎报「已保存」。

已知限制（都不影响游戏逻辑，失败时优雅降级为不录）：
- 单段最长 180 秒（screenrecord 的 `--time-limit` 上限），超时会截断并打 warning；
- 设备端 recorder 启动约 0.5 秒，这一段录不到（旧实现建立 scrcpy 连接也是这个量级）；
- 本段结束时要做 pull + 转码，占用时间大致是片段长度的 0.15 倍，只发生在收尾；
- 进程被强杀时设备上会留下临时文件，超过一小时的下次开录会清掉（不长于录像本身的价值）；
- ALAS 截图方式为 scrcpy 时不再跳过录制（已经不存在 socket 冲突），但设备上会有两路
  视频编码，截图或录像出现异常时优先怀疑这里。
"""

import contextlib
import os
import re
import shutil
import subprocess
import time

from adbutils import AdbClient, AdbDevice

from module.logger import logger

DEFAULT_OUTPUT_DIR = "./log/clips"
# 输出帧率。设备按刷新率（MuMu 实测 47~68fps）录，收尾转码时降到这个帧率
RECORD_FPS = 30
# 录像文件名前缀，用于区分是哪个任务录的
CLIP_PREFIX_EH1 = "eh1_clip_"  # 侵蚀1练级
CLIP_PREFIX_MEOW = "meow_clip_"  # 短猫相接（耄耋相接）
CLIP_PREFIXES = (CLIP_PREFIX_EH1, CLIP_PREFIX_MEOW)
# 本地临时文件前缀（转码中途）；保留旧前缀以便清理历史残留
TMP_PREFIX = "_tmp_clip_"
TMP_PREFIXES = (TMP_PREFIX, "_tmp_eh1_")
# 产物小于此字节数视为无效（正常 720p 首帧就在 10KB 以上）
MIN_VALID_BYTES = 4096
# 残留临时文件（本地）的保留秒数
TMP_MAX_AGE = 3600
# 清理节流：两次扫描目录至少间隔这么久
CLEANUP_INTERVAL = 3600

# ------------------------------------------------ 设备端录制参数
# 设备上的临时目录；用 /data/local/tmp 是因为它一定可写、且能直接 pull
DEVICE_TMP_DIR = "/data/local/tmp"
# 请求码率。Android 对 720p60 有 width*height*fps/5 ≈ 11Mbps 的下限，
# 传更小的值也会被抬高，所以这里只是个声明性的下限
DEVICE_BITRATE = 4_000_000
# screenrecord 的单段时长上限（--time-limit 的默认值与最大值都是 180）
DEVICE_TIME_LIMIT = 180
# 启动后等待进程存活的时间（秒）：立刻退出说明设备上根本跑不起来。
# 这里只拦「一行命令都跑不起来」的情况（例如设备没有 nohup），编码器起不来
# 会在收尾时通过设备端 stderr 报出来，所以不必在这里等太久。
START_TIMEOUT = 0.6
# SIGINT 后等待 recorder 写完文件并退出的上限（秒）
STOP_TIMEOUT = 6.0
# 轮询设备状态的间隔（秒）。抽成常量是为了让单测不必真的等
POLL_INTERVAL = 0.2
# 单条 adb 命令的超时（秒）
ADB_TIMEOUT = 20
# 转码超时：按片段长度放宽，但不超过这个上限（秒）
TRANSCODE_BASE_TIMEOUT = 60.0
TRANSCODE_MAX_TIMEOUT = 600.0

_ACTIVE = None  # 当前活动的录制会话
_FFMPEG_CACHE = None  # ffmpeg 探测结果缓存，None 表示尚未探测
_LAST_CLEANUP = 0.0  # 上次清理录像目录的时间戳


def _ffmpeg_works(exe):
    """校验 ffmpeg 可执行且能跑起来。

    仅用 shutil.which 找到路径并不能说明它可用（可能是残缺文件或缺 DLL），
    这里实跑一次 -version 确认。

    Args:
        exe (str): 候选可执行文件路径。

    Returns:
        bool: 可用返回 True。
    """
    if not exe:
        return False
    try:
        proc = subprocess.run(
            [exe, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and b"ffmpeg" in proc.stdout.lower()


def _ffmpeg_path():
    """按 环境变量 → imageio-ffmpeg 自带 → 系统 PATH 的顺序找可用的 ffmpeg。

    结果会缓存，避免每段录像都跑一次探测子进程。

    Returns:
        str: ffmpeg 可执行文件路径；都不可用时返回 None。
    """
    global _FFMPEG_CACHE
    if _FFMPEG_CACHE is not None:
        return _FFMPEG_CACHE or None

    candidates = []
    env_exe = os.getenv("IMAGEIO_FFMPEG_EXE")
    if env_exe:
        candidates.append(env_exe)
    try:
        import imageio_ffmpeg

        candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        # 依赖缺失或未安装，继续尝试系统 ffmpeg
        pass
    candidates.append(shutil.which("ffmpeg"))

    for exe in candidates:
        if _ffmpeg_works(exe):
            _FFMPEG_CACHE = exe
            return exe

    _FFMPEG_CACHE = ""
    return None


def _even_size(width, height):
    """把分辨率向下取偶。

    H.264 的 yuv420p 要求宽高均为偶数，否则 libx264 直接报错、产出 0 字节文件。

    Args:
        width (int): 原始宽度。
        height (int): 原始高度。

    Returns:
        tuple: (偶数宽度, 偶数高度)。
    """
    return width - width % 2, height - height % 2


def _adb_device(serial):
    """取当前实例对应的 adb 设备句柄。

    和 `module.device.connection_attr` 一样从 127.0.0.1 的 adb server 连，
    端口允许用 ANDROID_ADB_SERVER_PORT 环境变量覆盖。

    Args:
        serial (str): 设备序列号。

    Returns:
        AdbDevice: 与 serial 绑定的设备对象。
    """
    port = 5037
    env = os.environ.get("ANDROID_ADB_SERVER_PORT")
    if env is not None:
        try:
            port = int(env)
        except ValueError:
            logger.warning(f"[录屏] 无效的环境变量 ANDROID_ADB_SERVER_PORT={env}，使用默认端口")
    return AdbDevice(AdbClient("127.0.0.1", port), serial)


def _parse_pid(text):
    """从 shell 回显里取出 recorder 的进程号。

    Args:
        text (str): `echo $!` 的输出。

    Returns:
        int | None: 解析失败返回 None（例如 nohup 不存在时 shell 报的错）。
    """
    for token in str(text).split():
        if token.isdigit():
            return int(token)
    return None


def _parse_progress_duration(stdout):
    """从 ffmpeg `-progress pipe:1` 的输出里取转码后的时长。

    Args:
        stdout (bytes): ffmpeg 写往 stdout 的进度流。

    Returns:
        float | None: 时长（秒）；解析不出来返回 None。
    """
    text = stdout.decode("utf-8", errors="replace") if stdout else ""
    seconds = None
    for line in text.splitlines():
        if not line.startswith("out_time="):
            continue
        try:
            hour, minute, second = line.split("=", 1)[1].strip().split(":")
            seconds = int(hour) * 3600 + int(minute) * 60 + float(second)
        except ValueError:
            continue
    return seconds


def _stale_device_files(listing, now=None, max_age=TMP_MAX_AGE):
    """从设备目录列表里挑出过期的临时文件。

    录像文件名里带的是**本机**时间戳（`<前缀>YYYYMMDD_HHMMSS.mp4`），所以可以直接
    和本机时钟比：只有超过 max_age 的才算残留（进程被强杀留下的）。这样就不会误删
    别的实例正在录的那一段。

    Args:
        listing (str): `ls <前缀>*.mp4 <前缀>*.err` 的输出。
        now (float): 当前时间戳，默认取本机时间。
        max_age (float): 超过这么多秒视为残留。

    Returns:
        list: 需要删除的设备端路径。
    """
    now = time.time() if now is None else now
    stale = []
    for line in str(listing).replace("\r", "\n").split("\n"):
        path = line.strip()
        if not path.startswith(f"{DEVICE_TMP_DIR}/") or not path.endswith((".mp4", ".err")):
            continue
        # 只认自己的前缀：删的是用户设备上的文件，宁可漏删也不能误删
        if not os.path.basename(path).startswith(CLIP_PREFIXES):
            continue
        match = re.search(r"_(\d{8})_(\d{6})\.(?:mp4|err)$", path)
        if not match:
            continue
        try:
            stamp = time.mktime(time.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S"))
        except ValueError:
            continue
        if now - stamp > max_age:
            stale.append(path)
    return stale


def _adb_binary():
    """adb 可执行文件路径。

    优先用 ALAS 已经探测/配置好的那个（adbutils 的全局 adb_path），
    拿不到再退回 PATH 里的 adb。

    Returns:
        str | None: 找不到返回 None。
    """
    try:
        import adbutils

        exe = adbutils.adb_path()
    except Exception:
        exe = None
    return exe or shutil.which("adb")


def _run_adb_cli(args, timeout=ADB_TIMEOUT):
    """用 adb 命令行执行一条命令并取回 stdout。

    只用于「必须在设备上留下一个独立进程」的场景（启动 screenrecord），
    见 `_ScreenRecordClip._launch()` 的说明。

    Args:
        args (list): adb 子命令，例如 ['shell', 'echo hi']。
        timeout (float): 超时秒数。

    Returns:
        str | None: 成功返回 stdout 文本；失败返回 None 并记录原因。
    """
    exe = _adb_binary()
    if not exe:
        logger.error("[录屏] 找不到 adb 可执行文件，无法执行设备命令")
        return None
    cmd = [exe] + list(args)
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.error(f"[录屏] 执行 adb 失败: {e}")
        return None
    if proc.returncode != 0:
        reason = proc.stderr.decode("utf-8", errors="replace").strip()
        logger.error(f"[录屏] adb 返回码 {proc.returncode}: {reason[-400:]}")
        return None
    return proc.stdout.decode("utf-8", errors="replace")


def cleanup_clips(retention_days, output_dir=DEFAULT_OUTPUT_DIR):
    """清理录像目录。

    规则：
    - `eh1_clip_*.mp4` / `meow_clip_*.mp4`：修改时间超过 retention_days 天的删除；
      retention_days <= 0 表示永久保留。
    - `_tmp_clip_*` / `_tmp_eh1_*`：录制中途被中断留下的临时文件/日志，
      超过 TMP_MAX_AGE 即删除。
    - 其它文件一律不动。

    Args:
        retention_days (int): 录像保留天数，小于等于 0 表示永久保留。
        output_dir (str): 录像目录。

    Returns:
        int: 实际删除的文件数。
    """
    try:
        names = os.listdir(output_dir)
    except OSError:
        return 0

    now = time.time()
    removed = 0
    for name in names:
        path = os.path.join(output_dir, name)
        try:
            if not os.path.isfile(path):
                continue
            mtime = os.path.getmtime(path)
        except OSError:
            continue

        if name.startswith(TMP_PREFIXES):
            deadline = TMP_MAX_AGE
        elif name.startswith(CLIP_PREFIXES) and name.endswith(".mp4"):
            if retention_days <= 0:
                continue
            deadline = retention_days * 86400
        else:
            continue

        if now - mtime < deadline:
            continue
        try:
            os.remove(path)
            removed += 1
        except OSError:
            # 文件被占用/权限不足时跳过，下次清理再试
            pass

    if removed:
        if retention_days <= 0:
            keep_desc = "录像永久保留"
        else:
            keep_desc = f"录像保留 {retention_days} 天"
        logger.info(f"[录屏] 已清理 {removed} 个过期录像/临时文件（{keep_desc}）")
    return removed


def cleanup_clips_if_due(config, output_dir=DEFAULT_OUTPUT_DIR):
    """按配置清理过期录像（带节流，不必每轮战斗都真的扫目录）。

    保留天数取自「大世界通用设置」的 DebugClipRetentionDays，侵蚀一与短猫相接共用。
    任何 Opsi 任务都会自动绑定 OpsiGeneral，所以这里可以直接读属性。

    Args:
        config: 当前运行实例的 AzurLaneConfig。
        output_dir (str): 录像目录。

    Returns:
        int: 本次实际删除的文件数；未到清理时间返回 0。
    """
    global _LAST_CLEANUP
    now = time.time()
    if now - _LAST_CLEANUP < CLEANUP_INTERVAL:
        return 0
    _LAST_CLEANUP = now

    # 配置缺失时按「永久保留」处理：删除是不可逆操作，默认不删任何东西
    days = getattr(config, "OpsiGeneral_DebugClipRetentionDays", 0)
    try:
        days = int(days)
    except (TypeError, ValueError):
        logger.warning(f"[录屏] 录像保留天数配置无效: {days!r}，本次跳过清理")
        return 0
    return cleanup_clips(days, output_dir)


class _ScreenRecordClip:
    """一个基于设备端 screenrecord 的 debug 录屏段。

    录制在设备上完成，本类的职责只有三件事：把 recorder 拉起来、按需把它停下来、
    把产物拉回本地并转码。因此除了 `start()` / `finalize()`，其余方法都是围绕
    「问设备要信息」展开的，全部失败路径都只记日志、不抛异常。
    """

    _scrcpy_warned = False  # 截图方式为 scrcpy 的提示只打一次

    def __init__(self, config, fps=RECORD_FPS, prefix=CLIP_PREFIX_EH1):
        self.config = config
        self.fps = fps
        self.prefix = prefix
        self.output_dir = DEFAULT_OUTPUT_DIR
        self.adb = None  # start() 里建立；测试可直接注入假的
        self.serial = None  # start() 里从配置读取
        self.remote_path = None  # 设备上的录像文件
        self.remote_error_path = None  # 设备上 recorder 的 stderr
        self.pid = None
        self.size = None  # 传给 screenrecord 的 --size，None 表示让设备自己决定
        self.alive = False
        self._started_at = None
        self._stopped_at = None
        self._error = None

    # ------------------------------------------------ 启动
    def start(self):
        """拉起设备端 recorder。

        Returns:
            bool: 成功返回 True；失败会记录具体原因并返回 False（不影响游戏逻辑）。
        """
        try:
            os.makedirs(self.output_dir, exist_ok=True)
        except OSError as e:
            logger.error(f"[录屏] 创建输出目录失败: {e}")
            return False

        self.serial = str(getattr(self.config, "Emulator_Serial", "") or "")
        if not self.serial:
            logger.warning("[录屏] 配置里没有 Emulator_Serial，本次不录制")
            return False
        method = str(getattr(self.config, "Emulator_ScreenshotMethod", "") or "")
        if method.lower().startswith("scrcpy") and not _ScreenRecordClip._scrcpy_warned:
            # 旧实现会和 scrcpy 截图抢同一个 abstract socket 而必须跳过；
            # 现在没有 socket 冲突，只是设备上会多一路视频编码，出问题先怀疑这里。
            # 每轮都提醒会变成噪音，所以一个进程只提醒一次。
            _ScreenRecordClip._scrcpy_warned = True
            logger.warning(
                "[录屏] 当前截图方式为 scrcpy，设备上会同时存在两路视频编码，"
                "若截图或录像出现异常请临时关掉录像"
            )
        try:
            self.adb = self.adb or _adb_device(self.serial)
        except Exception as e:
            logger.error(f"[录屏] 连接 ADB 失败，本次不录制: {e}")
            return False

        ts = time.strftime("%Y%m%d_%H%M%S")
        self.remote_path = f"{DEVICE_TMP_DIR}/{self.prefix}{ts}.mp4"
        self.remote_error_path = f"{DEVICE_TMP_DIR}/{self.prefix}{ts}.err"
        self.size = self._device_size()
        self._remove_stale_device_files()
        if not self._launch():
            self._remove_device_files()
            return False

        self._started_at = time.perf_counter()
        self.alive = True
        size_desc = f", {self.size}" if self.size else ""
        logger.info(
            f"[录屏] 开始录制（设备端 screenrecord{size_desc}，收尾转 {self.fps}fps）: {self.remote_path}"
        )
        return True

    def _device_size(self):
        """读取设备分辨率，作为 screenrecord 的 --size。

        拿不到就让设备自己决定（screenrecord 默认用主显示器的分辨率），
        这比猜一个尺寸更安全：--size 不被编码器支持时 recorder 会直接失败。

        Returns:
            str | None: 形如 "1280x720"；读取失败返回 None。
        """
        try:
            window = self.adb.window_size()
            width, height = _even_size(int(window.width), int(window.height))
        except Exception as e:
            logger.warning(f"[录屏] 读取设备分辨率失败，由 screenrecord 自行决定尺寸: {e}")
            return None
        if width <= 0 or height <= 0:
            return None
        return f"{width}x{height}"

    def _recorder_command(self):
        """拼出后台启动 screenrecord 的 shell 命令。

        末尾 `echo $!` 回显进程号：收尾时按进程号发 SIGINT，比 `pidof screenrecord`
        精确（设备上可能同时有别的进程在录屏）。stderr 落到设备上的文件，
        失败时才有真实原因可报。
        """
        size = f"--size {self.size} " if self.size else ""
        return (
            f"nohup screenrecord --time-limit {DEVICE_TIME_LIMIT} "
            f"--bit-rate {DEVICE_BITRATE} {size}{self.remote_path} "
            f">/dev/null 2>{self.remote_error_path} </dev/null & echo $!"
        )

    def _launch(self):
        """启动 recorder，并确认它没有立刻退出。

        这一步刻意绕开 adbutils、直接用 adb 命令行：adbutils 的 shell 会话结束时，
        设备上的后台子进程会被一起带走（nohup / setsid 都保不住，实测），而
        screenrecord 必须活到本段结束。其余的前台查询仍然走 adbutils。

        Returns:
            bool: 成功返回 True。
        """
        output = _run_adb_cli(["-s", self.serial, "shell", self._recorder_command()])
        if output is None:
            return False

        self.pid = _parse_pid(output)
        if self.pid is None:
            logger.error(f"[录屏] 启动 screenrecord 失败，设备未回显进程号: {output!r}")
            return False

        deadline = time.time() + START_TIMEOUT
        while time.time() < deadline:
            if not self._pid_alive():
                logger.error(
                    f"[录屏] screenrecord 启动后立刻退出，本段不录制"
                    f"{self._device_error()}"
                )
                return False
            time.sleep(POLL_INTERVAL)
        return True

    def _pid_alive(self):
        """recorder 进程是否还在。"""
        try:
            output = self.adb.shell(
                f"kill -0 {self.pid} 2>/dev/null && echo alive || echo gone"
            )
        except Exception:
            return False
        return "alive" in str(output)

    def _device_error(self):
        """读取设备端 recorder 的 stderr，把真实失败原因带给用户。

        Returns:
            str: 形如「｜设备端: xxx」；没有内容时返回空串。
        """
        if not self.remote_error_path:
            return ""
        try:
            text = str(self.adb.shell(f"cat {self.remote_error_path} 2>/dev/null")).strip()
        except Exception:
            return ""
        if not text:
            return ""
        return f"｜设备端: {text[-400:]}"

    # ------------------------------------------------ 收尾
    def _stop_recorder(self):
        """发 SIGINT 让 recorder 收尾。

        screenrecord 只有在收到中断、写完 moov atom 之后才会得到可播放的 mp4，
        直接杀进程只会留下一堆无法播放的碎片。
        """
        if self.pid is None:
            return
        try:
            # 先确认这个进程号还是 recorder：screenrecord 自己到点退出后，
            # 进程号可能已经被别的进程复用，不能盲杀
            self.adb.shell(
                f"grep -q screenrecord /proc/{self.pid}/cmdline 2>/dev/null"
                f" && kill -2 {self.pid}"
            )
        except Exception as e:
            self._set_error(f"停止 screenrecord 失败: {e}")
        stopped = False
        deadline = time.time() + STOP_TIMEOUT
        while time.time() < deadline:
            if not self._pid_alive():
                stopped = True
                break
            time.sleep(POLL_INTERVAL)
        if not stopped:
            self._set_error("等待 screenrecord 退出超时，录像可能不完整")
        self._stopped_at = time.perf_counter()

    def _file_size(self):
        """设备上录像文件的字节数。

        Returns:
            int | None: 文件不存在或取不到时返回 None。
        """
        try:
            output = str(self.adb.shell(f"stat -c %s {self.remote_path} 2>/dev/null")).strip()
        except Exception:
            return None
        return int(output) if output.isdigit() else None

    def _remove_device_files(self):
        """删掉设备上的录像与它的 stderr（可重复调用，失败不抛异常）。"""
        if not self.remote_path:
            return
        try:
            self.adb.shell(f"rm -f {self.remote_path} {self.remote_error_path}")
        except Exception as e:
            logger.warning(f"[录屏] 清理设备上的临时录像失败: {e}")

    def _remove_stale_device_files(self):
        """清理设备上遗留的录像/err 文件。

        进程被强杀时来不及删，它们会一直占着设备存储，所以每次开录前扫一遍。
        只删超过 TMP_MAX_AGE 的（按文件名里的时间戳判断），避免误删正在录的那一段。
        """
        pattern = " ".join(f"{DEVICE_TMP_DIR}/{p}*" for p in CLIP_PREFIXES)
        try:
            listing = self.adb.shell(f"ls {pattern} 2>/dev/null")
        except Exception as e:
            logger.warning(f"[录屏] 查询设备上的历史临时文件失败: {e}")
            return
        stale = _stale_device_files(listing)
        if not stale:
            return
        try:
            self.adb.shell(f"rm -f {' '.join(stale)}")
            logger.info(f"[录屏] 已清理设备上 {len(stale)} 个历史临时文件")
        except Exception as e:
            logger.warning(f"[录屏] 清理设备上的历史临时文件失败: {e}")

    def _pull(self, local_path):
        """把设备上的录像拉到本地。

        Args:
            local_path (str): 本地目标路径。

        Returns:
            bool: 成功返回 True。
        """
        try:
            self.adb.sync.pull(self.remote_path, local_path)
        except Exception as e:
            self._fail(f"从设备拉取录像失败: {e}")
            return False
        return True

    def _transcode(self, src, dst, elapsed):
        """把设备录像转成 30fps 的小文件。

        设备按刷新率录、码率有下限（720p60 约 11Mbps），直接留下来既大也没必要：
        转成 30fps CRF 26 之后体积和旧的 scrcpy 管线是一个量级，时间轴仍是真实速度
        （`-r` 只做抽帧/复制，不改变时长）。没有 ffmpeg 就保留原始文件，只是更大。

        Args:
            src (str): 拉回来的原始录像。
            dst (str): 最终产物路径。
            elapsed (float): 本段实际经过的秒数，用于放宽转码超时。

        Returns:
            float | None: 转码后的时长（秒）。返回 None 有两种情况：彻底没有产物，
                或者产物有效但转码没成功（此时留下的是未转码的原始录像）。
        """
        ffmpeg = _ffmpeg_path()
        if not ffmpeg:
            logger.warning("[录屏] 未找到 ffmpeg，跳过转码，直接保留原始录像（文件会明显更大）")
            try:
                os.replace(src, dst)
            except OSError as e:
                self._fail(f"保存录像失败: {e}")
                return None
            return None

        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostats",
            "-progress", "pipe:1",
            "-i", src,
            "-an",
            # 宽高都取偶：yuv420p 的 H.264 不接受奇数边长
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-r", str(self.fps),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "26",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            dst,
        ]
        timeout = min(TRANSCODE_MAX_TIMEOUT, TRANSCODE_BASE_TIMEOUT + elapsed * 2)
        reason = None
        try:
            proc = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout
            )
        except (OSError, subprocess.SubprocessError) as e:
            reason = f"转码失败: {e}"
            proc = None
        if proc is not None and proc.returncode != 0:
            output = proc.stderr.decode("utf-8", errors="replace").strip()
            reason = f"转码失败（返回码 {proc.returncode}）｜{output[-400:]}"
        elif proc is not None:
            try:
                if os.path.getsize(dst) >= MIN_VALID_BYTES:
                    return _parse_progress_duration(proc.stdout)
            except OSError:
                pass
            reason = "转码产物无效"

        # 转码失败也尽量保住原始画面：它本身是有效录像，只是更大、帧率更高
        logger.error(f"[录屏] {reason}，改为保留未转码的原始录像（文件会明显更大）")
        try:
            os.replace(src, dst)
        except OSError as e:
            self._fail(f"{reason}，原始录像也无法保留: {e}")
            return None
        return None

    def _report(self, path, duration):
        """汇报保存结果，并把「录像比实际短」这类偏差显式提示出来。"""
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        desc = f"{duration:.1f}s" if duration else "时长未解析"
        logger.info(f"[录屏] 已保存: {path} ({size / 1024 / 1024:.1f} MB, 时长 {desc})")

        if duration:
            if duration >= DEVICE_TIME_LIMIT - 2:
                logger.warning(
                    f"[录屏] 本段达到设备端 {DEVICE_TIME_LIMIT} 秒上限，之后的画面没有录到；"
                    f"需要更长的录像请反馈"
                )
            elapsed = 0.0
            if self._started_at is not None and self._stopped_at is not None:
                elapsed = max(0.0, self._stopped_at - self._started_at)
            if elapsed > 10 and duration < elapsed * 0.8:
                logger.warning(
                    f"[录屏] 录像时长 {duration:.1f}s 明显短于实际经过的 {elapsed:.1f}s，请留意"
                )
        if self._error:
            logger.warning(f"[录屏] 录制期间出现异常: {self._error}")

    @staticmethod
    def _remove_file(path):
        """尽力删除本地文件（Windows 上可能被占用，重试几次）。"""
        if not path:
            return
        for _ in range(3):
            try:
                os.remove(path)
                break
            except FileNotFoundError:
                break
            except OSError:
                time.sleep(0.2)

    def _set_error(self, reason):
        """记录首个异常（只保留第一条，便于定位）。"""
        if self._error is None:
            self._error = reason

    def _fail(self, reason):
        """产物无效时统一报错，绝不谎报「已保存」。"""
        logger.error(f"[录屏] 本段录像未保存: {reason}")

    # ------------------------------------------------ 对外入口
    def finalize(self, keep):
        """结束录制：停流、拉取转码、校验产物，决定保留还是删除。

        本函数保证不抛异常（异常也只会退化成「没有产物」），避免调用方的
        finally 里再炸一次。

        Args:
            keep (bool): 是否保留本段录像。

        Returns:
            str: 保留且产物有效时返回最终 mp4 路径；丢弃或无效时返回 None。
        """
        try:
            return self._finalize(keep)
        except Exception as e:
            logger.error(f"[录屏] 结束录制时发生异常，本段录像丢弃: {e}")
            try:
                self._remove_device_files()
            except Exception:
                pass
            return None

    def _finalize(self, keep):
        """finalize 的实际实现。"""
        self._stop_recorder()
        self.alive = False
        try:
            if not keep:
                return None
            elapsed = 0.0
            if self._started_at is not None and self._stopped_at is not None:
                elapsed = max(0.0, self._stopped_at - self._started_at)

            size = self._file_size()
            if not size:
                if elapsed < 2:
                    logger.warning("[录屏] 本段录制时间过短，设备端没有产出录像，本段跳过")
                else:
                    self._fail(f"设备端 screenrecord 没有产出录像文件{self._device_error()}")
                return None
            if size < MIN_VALID_BYTES:
                self._fail(
                    f"设备端录像只有 {size} 字节，判定为无效{self._device_error()}"
                )
                return None

            ts = time.strftime("%Y%m%d_%H%M%S")
            tmp_path = os.path.join(self.output_dir, f"{TMP_PREFIX}{ts}.mp4")
            if not self._pull(tmp_path):
                # 拉取失败时本地可能留下半截文件，不能让它占着目录
                self._remove_file(tmp_path)
                return None

            final_path = os.path.join(self.output_dir, f"{self.prefix}{ts}.mp4")
            duration = self._transcode(tmp_path, final_path, elapsed)
            self._remove_file(tmp_path)
            if not os.path.exists(final_path):
                return None
            self._report(final_path, duration)
            return final_path
        finally:
            # 无论成功失败，设备上的临时文件都不能留
            self._remove_device_files()


def clip_start(config, fps=RECORD_FPS, prefix=CLIP_PREFIX_EH1):
    """打开录屏。

    Args:
        config: 当前运行实例的 AzurLaneConfig（含 serial 配置）。
        fps (int): 输出帧率（设备端录制帧率由设备决定，收尾时转成这个帧率）。
        prefix (str): 输出文件名前缀，用于区分是哪个任务录的。

    Returns:
        _ScreenRecordClip: 录制句柄；启动失败返回 None。
    """
    global _ACTIVE
    if _ACTIVE is not None:
        # 正常情况下走不到这里（调用方保证 start/end 成对）。真发生了说明上一段
        # 没有被正常结束，先把它收尾，避免会话永久泄漏、之后再也录不了。
        logger.warning("[录屏] 上一段录制未正常结束，先收尾再开始新的一段")
        _finalize_active(keep=True)

    rec = _ScreenRecordClip(config, fps=fps, prefix=prefix)
    if not rec.start():
        return None
    _ACTIVE = rec
    return rec


def _finalize_active(keep):
    """收尾当前会话并清空 _ACTIVE（即使 finalize 抛错也不会泄漏会话）。

    Args:
        keep (bool): 是否保留该段录像。

    Returns:
        str: 保留时的视频路径；无产物返回 None。
    """
    global _ACTIVE
    rec, _ACTIVE = _ACTIVE, None
    if rec is None:
        return None
    try:
        return rec.finalize(keep=keep)
    except Exception as e:
        # finalize 自身已兜底，这里是最后一道保险
        logger.error(f"[录屏] 结束录制时发生异常: {e}")
        return None


def clip_end(keep=True):
    """结束当前录屏。

    Args:
        keep (bool): 是否把该段保存为 mp4。默认 True（每一轮都保留）；
            传 False 会直接丢弃该段，不留下任何文件。

    Returns:
        str: 保留时的视频路径；无录制或产物无效时返回 None。
    """
    return _finalize_active(keep=keep)


@contextlib.contextmanager
def clip_recording(config, enabled, prefix=CLIP_PREFIX_EH1):
    """在 with 块内录制一段 debug 录像（进入时开录，退出时保存）。

    异常路径也会正常收尾，不会把会话留在活动状态。同一个进程内不会同时存在
    两段录制，因此调用方应避免嵌套。

    Args:
        config: 当前运行实例的 AzurLaneConfig。
        enabled (bool): 是否开启录制；False 时整个块不产生任何录像。
        prefix (str): 输出文件名前缀，用于区分是哪个任务录的。

    Yields:
        _ScreenRecordClip | None: 录制句柄；未开启或启动失败时为 None。
    """
    clip = clip_start(config, prefix=prefix) if enabled else None
    try:
        yield clip
    finally:
        if clip is not None:
            clip_end(keep=True)
