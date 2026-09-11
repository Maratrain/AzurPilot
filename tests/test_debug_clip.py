"""验证 debug 录屏的设备端命令、产物校验与录像清理策略。

这些测试不依赖模拟器和真实 ffmpeg：设备被 `_FakeAdb` 取代，转码用假实现，
只有纯逻辑（命令拼装、进程号/时长解析、清理规则、会话管理）被真正执行。
"""

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from module.base import debug_clip


class _FakeAdb:
    """假的 adb 设备：记录 shell 命令，并按命令内容给出预设回复。

    只看前台查询（进程存活、文件大小、stderr、清理）；启动 recorder 走的是 adb
    命令行，由 `_run_adb_cli` 的假实现负责。
    """

    def __init__(self, pid='4321', file_size=200_000, error_text='',
                 window=(1280, 720), pull_fails=False, alive=True,
                 stays_alive=False, ls_listing=''):
        self.commands = []
        self.pid = pid
        self.file_size = file_size
        self.error_text = error_text
        self.window = window
        self.pull_fails = pull_fails
        self.alive = alive
        self.stays_alive = stays_alive
        self.ls_listing = ls_listing
        self.pulled = []

    # ---- 设备接口
    def shell(self, cmd):
        self.commands.append(cmd)
        if cmd.startswith('ls '):
            return self.ls_listing
        if cmd.startswith('kill -0'):
            return 'alive' if self.alive else 'gone'
        if 'kill -2' in cmd:
            self.alive = self.stays_alive
            return ''
        if 'stat -c %s' in cmd:
            return '' if self.file_size is None else str(self.file_size)
        if cmd.startswith('cat '):
            return self.error_text
        return ''

    def window_size(self):
        if self.window is None:
            raise RuntimeError('wm size failed')
        return SimpleNamespace(width=self.window[0], height=self.window[1])

    def sync_pull(self, src, dst):
        if self.pull_fails:
            raise RuntimeError('pull failed')
        self.pulled.append((src, dst))
        with open(dst, 'wb') as f:
            f.write(b'x' * 20000)

    def command_starting_with(self, prefix):
        return [c for c in self.commands if c.startswith(prefix)]


def make_fake_sync(adb):
    """给 _FakeAdb 装一个 adbutils 风格的 sync 对象（只用到 pull）。"""
    return SimpleNamespace(pull=adb.sync_pull)


class ClipTestCase(unittest.TestCase):
    """提供临时输出目录并隔离模块级缓存的公共基类。"""

    def setUp(self):
        self.output_dir = tempfile.mkdtemp(prefix='debug_clip_test_')
        self._saved_cleanup = debug_clip._LAST_CLEANUP
        # 避免测试过程中真的去扫录像目录
        debug_clip._LAST_CLEANUP = float('inf')

    def tearDown(self):
        debug_clip._LAST_CLEANUP = self._saved_cleanup
        shutil.rmtree(self.output_dir, ignore_errors=True)

    def make_clip(self, prefix=debug_clip.CLIP_PREFIX_EH1, adb=None):
        rec = debug_clip._ScreenRecordClip(config=None, prefix=prefix)
        rec.output_dir = self.output_dir
        rec.remote_path = '/data/local/tmp/eh1_clip_test.mp4'
        rec.remote_error_path = '/data/local/tmp/eh1_clip_test.err'
        if adb is not None:
            adb.sync = make_fake_sync(adb)
            rec.adb = adb
        return rec


class TestCleanupClips(ClipTestCase):
    def touch(self, name, age_seconds, payload=b'x'):
        path = os.path.join(self.output_dir, name)
        with open(path, 'wb') as f:
            f.write(payload)
        mtime = time.time() - age_seconds
        os.utime(path, (mtime, mtime))
        return path

    def test_retention_zero_keeps_clips_forever(self):
        old = self.touch('eh1_clip_20200101_000000.mp4', 400 * 86400)
        self.assertEqual(debug_clip.cleanup_clips(0, self.output_dir), 0)
        self.assertTrue(os.path.exists(old))

    def test_negative_retention_also_keeps_clips(self):
        old = self.touch('eh1_clip_20200101_000000.mp4', 400 * 86400)
        self.assertEqual(debug_clip.cleanup_clips(-1, self.output_dir), 0)
        self.assertTrue(os.path.exists(old))

    def test_removes_clips_older_than_retention(self):
        old = self.touch('eh1_clip_20200101_000000.mp4', 8 * 86400)
        fresh = self.touch('eh1_clip_20260909_000000.mp4', 60)
        self.assertEqual(debug_clip.cleanup_clips(7, self.output_dir), 1)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(fresh))

    def test_removes_stale_tmp_files_even_when_retention_disabled(self):
        stale = self.touch('_tmp_eh1_20200101_000000.mp4', 2 * 3600)
        fresh = self.touch('_tmp_eh1_20260909_000000.enc.log', 60)
        self.assertEqual(debug_clip.cleanup_clips(0, self.output_dir), 1)
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.exists(fresh))

    def test_removes_meowfficer_clips_too(self):
        """短猫相接的录像用 meow_clip_ 前缀，同样要按保留天数清理。"""
        old = self.touch('meow_clip_20200101_000000.mp4', 8 * 86400)
        fresh = self.touch('meow_clip_20260909_000000.mp4', 60)
        self.assertEqual(debug_clip.cleanup_clips(7, self.output_dir), 1)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(fresh))

    def test_removes_both_tmp_prefixes(self):
        """新前缀与历史遗留的 _tmp_eh1_ 临时文件都要清掉。"""
        legacy = self.touch('_tmp_eh1_20200101_000000.mp4', 2 * 3600)
        current = self.touch('_tmp_clip_20200101_000001.mp4', 2 * 3600)
        self.assertEqual(debug_clip.cleanup_clips(0, self.output_dir), 2)
        self.assertFalse(os.path.exists(legacy))
        self.assertFalse(os.path.exists(current))

    def test_ignores_unrelated_files(self):
        other = self.touch('readme.txt', 400 * 86400)
        self.assertEqual(debug_clip.cleanup_clips(1, self.output_dir), 0)
        self.assertTrue(os.path.exists(other))

    def test_missing_directory_is_safe(self):
        missing = os.path.join(self.output_dir, 'not_created_yet')
        self.assertEqual(debug_clip.cleanup_clips(7, missing), 0)


class TestCleanupClipsIfDue(ClipTestCase):
    def setUp(self):
        super().setUp()
        debug_clip._LAST_CLEANUP = 0.0  # 让首次调用一定执行清理

    def touch_expired_clip(self, name):
        path = os.path.join(self.output_dir, name)
        with open(path, 'wb') as f:
            f.write(b'x')
        mtime = time.time() - 3 * 86400
        os.utime(path, (mtime, mtime))
        return path

    def test_uses_configured_retention_days(self):
        self.touch_expired_clip('eh1_clip_20200101_000000.mp4')
        config = SimpleNamespace(OpsiGeneral_DebugClipRetentionDays=1)
        self.assertEqual(debug_clip.cleanup_clips_if_due(config, self.output_dir), 1)

    def test_second_call_is_throttled(self):
        self.touch_expired_clip('eh1_clip_20200101_000000.mp4')
        config = SimpleNamespace(OpsiGeneral_DebugClipRetentionDays=1)
        self.assertEqual(debug_clip.cleanup_clips_if_due(config, self.output_dir), 1)
        self.touch_expired_clip('eh1_clip_20200102_000000.mp4')
        # 节流期内不再扫描，文件仍然留着
        self.assertEqual(debug_clip.cleanup_clips_if_due(config, self.output_dir), 0)
        self.assertEqual(len(os.listdir(self.output_dir)), 1)

    def test_missing_setting_keeps_everything(self):
        kept = self.touch_expired_clip('eh1_clip_20200101_000000.mp4')
        self.assertEqual(debug_clip.cleanup_clips_if_due(SimpleNamespace(), self.output_dir), 0)
        self.assertTrue(os.path.exists(kept))

    def test_invalid_setting_is_ignored(self):
        kept = self.touch_expired_clip('eh1_clip_20200101_000000.mp4')
        config = SimpleNamespace(OpsiGeneral_DebugClipRetentionDays='abc')
        self.assertEqual(debug_clip.cleanup_clips_if_due(config, self.output_dir), 0)
        self.assertTrue(os.path.exists(kept))


class TestEvenSize(unittest.TestCase):
    def test_even_resolution_unchanged(self):
        self.assertEqual(debug_clip._even_size(1280, 720), (1280, 720))

    def test_odd_edges_rounded_down(self):
        self.assertEqual(debug_clip._even_size(1281, 721), (1280, 720))

    def test_single_odd_edge(self):
        self.assertEqual(debug_clip._even_size(1280, 721), (1280, 720))


class TestFfmpegProbe(unittest.TestCase):
    def setUp(self):
        self._saved = debug_clip._FFMPEG_CACHE
        debug_clip._FFMPEG_CACHE = None

    def tearDown(self):
        debug_clip._FFMPEG_CACHE = self._saved

    def test_rejects_unusable_binary(self):
        self.assertFalse(debug_clip._ffmpeg_works('/definitely/not/a/real/ffmpeg'))
        self.assertFalse(debug_clip._ffmpeg_works(None))
        self.assertFalse(debug_clip._ffmpeg_works(''))

    def test_returns_first_working_candidate(self):
        with patch.object(
            debug_clip, '_ffmpeg_works', side_effect=lambda exe: exe == '/fake/ffmpeg'
        ), patch.object(debug_clip.shutil, 'which', return_value='/fake/ffmpeg'):
            self.assertEqual(debug_clip._ffmpeg_path(), '/fake/ffmpeg')

    def test_returns_none_when_nothing_works(self):
        with patch.object(debug_clip, '_ffmpeg_works', return_value=False), \
                patch.object(debug_clip.shutil, 'which', return_value=None):
            self.assertIsNone(debug_clip._ffmpeg_path())

    def test_probe_result_is_cached(self):
        with patch.object(
            debug_clip, '_ffmpeg_works', side_effect=lambda exe: exe == '/fake/ffmpeg'
        ) as works, patch.object(debug_clip.shutil, 'which', return_value='/fake/ffmpeg'):
            debug_clip._ffmpeg_path()
            first_calls = works.call_count
            debug_clip._ffmpeg_path()
            # 第二次走缓存，不再重复探测子进程
            self.assertEqual(works.call_count, first_calls)


class TestParseHelpers(unittest.TestCase):
    def test_pid_is_read_from_shell_echo(self):
        self.assertEqual(debug_clip._parse_pid('4321\n'), 4321)
        self.assertEqual(debug_clip._parse_pid('  99  '), 99)

    def test_missing_pid_is_detected(self):
        # 设备没有 nohup 时 shell 只会报错，不会有进程号
        self.assertIsNone(debug_clip._parse_pid('sh: nohup: not found'))
        self.assertIsNone(debug_clip._parse_pid(''))

    def test_progress_duration_is_parsed(self):
        progress = (
            b'frame=10\nout_time_us=5170000\nout_time=00:00:05.170000\nprogress=end\n'
        )
        self.assertAlmostEqual(debug_clip._parse_progress_duration(progress), 5.17, places=2)

    def test_progress_duration_handles_hours(self):
        progress = b'out_time=01:02:03.500000\n'
        self.assertAlmostEqual(
            debug_clip._parse_progress_duration(progress), 3723.5, places=2
        )

    def test_broken_progress_is_not_fatal(self):
        self.assertIsNone(debug_clip._parse_progress_duration(b''))
        self.assertIsNone(debug_clip._parse_progress_duration(b'out_time=oops\n'))


class TestStaleDeviceFiles(unittest.TestCase):
    """设备上的残留只按「文件名时间戳」判断，避免误删正在录的那一段。"""

    def name(self, seconds_ago, ext='.mp4'):
        stamp = time.strftime('%Y%m%d_%H%M%S', time.localtime(time.time() - seconds_ago))
        return f'{debug_clip.DEVICE_TMP_DIR}/eh1_clip_{stamp}{ext}'

    def test_fresh_files_are_kept(self):
        self.assertEqual(debug_clip._stale_device_files(self.name(60)), [])

    def test_old_files_are_collected(self):
        stale = self.name(2 * 3600)
        self.assertEqual(debug_clip._stale_device_files(stale), [stale])

    def test_err_files_are_collected_too(self):
        stale = self.name(2 * 3600, ext='.err')
        self.assertEqual(debug_clip._stale_device_files(stale), [stale])

    def test_only_our_prefixes_and_temp_dir_are_considered(self):
        listing = '\n'.join([
            f'{debug_clip.DEVICE_TMP_DIR}/other_20200101_000000.mp4',
            '/sdcard/eh1_clip_20200101_000000.mp4',
            f'{debug_clip.DEVICE_TMP_DIR}/eh1_clip_broken.mp4',
            '',
        ])
        self.assertEqual(debug_clip._stale_device_files(listing), [])

    def test_empty_listing_is_safe(self):
        self.assertEqual(debug_clip._stale_device_files(''), [])
        self.assertEqual(debug_clip._stale_device_files(None), [])


class TestRecorderCommand(ClipTestCase):
    def test_command_contains_all_recorder_options(self):
        rec = self.make_clip()
        rec.size = '1280x720'
        cmd = rec._recorder_command()
        self.assertIn('screenrecord', cmd)
        self.assertIn(f'--time-limit {debug_clip.DEVICE_TIME_LIMIT}', cmd)
        self.assertIn(f'--bit-rate {debug_clip.DEVICE_BITRATE}', cmd)
        self.assertIn('--size 1280x720', cmd)
        self.assertIn(rec.remote_path, cmd)
        # 后台运行 + 回显进程号（收尾时按进程号发 SIGINT）
        self.assertIn('&', cmd)
        self.assertIn('echo $!', cmd)
        # 设备端的 stderr 要留下来，失败时才有真实原因可报
        self.assertIn(f'2>{rec.remote_error_path}', cmd)

    def test_size_is_omitted_when_unknown(self):
        rec = self.make_clip()
        rec.size = None
        self.assertNotIn('--size', rec._recorder_command())


class TestStart(ClipTestCase):
    def setUp(self):
        super().setUp()
        self._saved = (debug_clip.START_TIMEOUT, debug_clip.POLL_INTERVAL)
        debug_clip.START_TIMEOUT = 0.05
        debug_clip.POLL_INTERVAL = 0.01

    def tearDown(self):
        debug_clip.START_TIMEOUT, debug_clip.POLL_INTERVAL = self._saved
        super().tearDown()

    def start(self, adb, launch_output='4321\n', serial='127.0.0.1:16384'):
        rec = debug_clip._ScreenRecordClip(
            config=SimpleNamespace(Emulator_Serial=serial)
        )
        rec.output_dir = self.output_dir
        adb.sync = make_fake_sync(adb)
        rec.adb = adb
        with patch.object(
            debug_clip, '_run_adb_cli', return_value=launch_output
        ) as cli:
            ok = rec.start()
        return rec, ok, cli

    def launched_command(self, cli):
        """取出真正发给设备的那条 shell 命令。"""
        args = cli.call_args[0][0]
        self.assertEqual(args[:3], ['-s', '127.0.0.1:16384', 'shell'])
        return args[3]

    def test_start_success_records_device_path_and_pid(self):
        adb = _FakeAdb()
        rec, ok, cli = self.start(adb)
        self.assertTrue(ok)
        self.assertEqual(rec.pid, 4321)
        self.assertEqual(rec.size, '1280x720')
        self.assertTrue(rec.remote_path.startswith(debug_clip.DEVICE_TMP_DIR))
        self.assertTrue(rec.remote_error_path.endswith('.err'))
        # 开录前要先问一遍设备上的历史临时文件
        self.assertTrue(adb.command_starting_with('ls '))
        # 启动走 adb 命令行（adbutils 的 shell 会连带杀掉后台进程）
        self.assertIn('screenrecord', self.launched_command(cli))

    def test_stale_device_file_is_cleaned_before_recording(self):
        old = time.strftime('%Y%m%d_%H%M%S', time.localtime(time.time() - 7200))
        adb = _FakeAdb(ls_listing=f'/data/local/tmp/eh1_clip_{old}.mp4\n')
        _, ok, _ = self.start(adb)
        self.assertTrue(ok)
        removed = adb.command_starting_with('rm -f')
        self.assertTrue(removed)
        self.assertIn(f'eh1_clip_{old}.mp4', removed[0])

    def test_start_without_pid_reports_failure(self):
        adb = _FakeAdb()
        _, ok, _ = self.start(adb, launch_output='sh: nohup: not found')
        self.assertFalse(ok)

    def test_start_without_serial_is_skipped(self):
        adb = _FakeAdb()
        _, ok, _ = self.start(adb, serial='')
        self.assertFalse(ok)

    def test_recorder_dying_immediately_is_reported(self):
        adb = _FakeAdb(alive=False)  # recorder 起来就崩
        _, ok, _ = self.start(adb)
        self.assertFalse(ok)

    def test_unknown_device_size_still_starts(self):
        adb = _FakeAdb(window=None)
        rec, ok, cli = self.start(adb)
        self.assertTrue(ok)
        self.assertIsNone(rec.size)
        self.assertNotIn('--size', self.launched_command(cli))


class TestFinalize(ClipTestCase):
    def setUp(self):
        super().setUp()
        self._saved = (debug_clip.START_TIMEOUT, debug_clip.POLL_INTERVAL)
        debug_clip.START_TIMEOUT = 0.05
        debug_clip.POLL_INTERVAL = 0.01

    def tearDown(self):
        debug_clip.START_TIMEOUT, debug_clip.POLL_INTERVAL = self._saved
        super().tearDown()

    def started_clip(self, adb=None, prefix=debug_clip.CLIP_PREFIX_EH1):
        adb = adb or _FakeAdb()
        rec = self.make_clip(prefix=prefix, adb=adb)
        rec.pid = int(adb.pid)
        rec.alive = True
        adb.alive = True
        rec._started_at = time.perf_counter() - 12
        return rec

    def test_successful_clip_is_saved_and_device_is_cleaned(self):
        adb = _FakeAdb()
        rec = self.started_clip(adb)
        with patch.object(rec, '_transcode', side_effect=self.fake_transcode):
            path = rec.finalize(keep=True)

        self.assertIsNotNone(path)
        self.assertTrue(os.path.exists(path))
        self.assertTrue(os.path.basename(path).startswith(debug_clip.CLIP_PREFIX_EH1))
        # 产物目录里只应留下 mp4，本地临时文件要清掉
        self.assertEqual(
            [n for n in os.listdir(self.output_dir) if n.startswith('_tmp')], []
        )
        # 设备上的录像与 stderr 都要删掉；SIGINT 前要确认进程还是 recorder
        stopped = [c for c in adb.commands if 'kill -2' in c]
        self.assertTrue(stopped)
        self.assertIn('/proc/', stopped[0])
        self.assertTrue(adb.command_starting_with('rm -f'))

    def test_meow_prefix_is_used_for_output_name(self):
        rec = self.started_clip(prefix=debug_clip.CLIP_PREFIX_MEOW)
        with patch.object(rec, '_transcode', side_effect=self.fake_transcode):
            path = rec.finalize(keep=True)
        self.assertTrue(os.path.basename(path).startswith(debug_clip.CLIP_PREFIX_MEOW))

    def test_discarded_clip_leaves_no_file_and_still_stops_recorder(self):
        adb = _FakeAdb()
        rec = self.started_clip(adb)
        self.assertIsNone(rec.finalize(keep=False))
        self.assertEqual([n for n in os.listdir(self.output_dir) if n.endswith('.mp4')], [])
        self.assertTrue([c for c in adb.commands if 'kill -2' in c])
        self.assertEqual(adb.pulled, [])

    def test_missing_device_file_is_reported_not_faked(self):
        adb = _FakeAdb(file_size=None, error_text='Fatal: cannot create encoder')
        rec = self.started_clip(adb)
        self.assertIsNone(rec.finalize(keep=True))
        self.assertEqual([n for n in os.listdir(self.output_dir) if n.endswith('.mp4')], [])

    def test_too_small_device_file_is_rejected(self):
        rec = self.started_clip(_FakeAdb(file_size=100))
        self.assertIsNone(rec.finalize(keep=True))
        self.assertEqual([n for n in os.listdir(self.output_dir) if n.endswith('.mp4')], [])

    def test_short_clip_without_recording_is_skipped(self):
        """录制时间过短时设备端还没写出文件，这属于正常跳过而不是失败。"""
        rec = self.started_clip(_FakeAdb(file_size=None))
        rec._started_at = time.perf_counter() - 0.5
        self.assertIsNone(rec.finalize(keep=True))

    def test_pull_failure_is_reported(self):
        rec = self.started_clip(_FakeAdb(pull_fails=True))
        with patch.object(rec, '_transcode', side_effect=self.fake_transcode):
            self.assertIsNone(rec.finalize(keep=True))
        self.assertEqual([n for n in os.listdir(self.output_dir) if n.endswith('.mp4')], [])

    def test_finalize_never_raises_on_broken_internals(self):
        rec = self.make_clip(adb=_FakeAdb())
        rec.adb = object()  # 故意塞一个没有 shell / sync 的对象
        self.assertIsNone(rec.finalize(keep=False))
        self.assertIsNone(rec.finalize(keep=True))

    @staticmethod
    def fake_transcode(src, dst, elapsed):
        """替代真实转码：写出目标文件并返回时长。"""
        with open(dst, 'wb') as f:
            f.write(b'x' * 20000)
        return 12.0


class TestTranscode(ClipTestCase):
    def test_transcode_command_targets_realtime_30fps(self):
        rec = self.make_clip()
        captured = {}

        def fake_run(cmd, **kwargs):
            captured['cmd'] = cmd
            with open(cmd[-1], 'wb') as f:
                f.write(b'x' * 20000)
            return subprocess.CompletedProcess(
                cmd, 0, stdout=b'out_time=00:00:12.000000\n', stderr=b''
            )

        with patch.object(debug_clip, '_ffmpeg_path', return_value='/fake/ffmpeg'), \
                patch.object(debug_clip.subprocess, 'run', side_effect=fake_run):
            src = os.path.join(self.output_dir, 'src.mp4')
            with open(src, 'wb') as f:
                f.write(b'x' * 20000)
            duration = rec._transcode(src, os.path.join(self.output_dir, 'out.mp4'), 30)

        self.assertAlmostEqual(duration, 12.0, places=2)
        cmd = captured['cmd']
        # 抽帧到目标帧率（不改变时长）+ CRF 压缩，避免设备端高码率文件堆积
        self.assertEqual(cmd[cmd.index('-r') + 1], str(debug_clip.RECORD_FPS))
        self.assertIn('-crf', cmd)
        self.assertIn('+faststart', cmd)
        # yuv420p 不接受奇数边长
        self.assertIn('scale=trunc(iw/2)*2:trunc(ih/2)*2', cmd)

    def test_transcode_failure_keeps_raw_recording(self):
        rec = self.make_clip()
        src = os.path.join(self.output_dir, 'src.mp4')
        dst = os.path.join(self.output_dir, 'out.mp4')
        with open(src, 'wb') as f:
            f.write(b'x' * 20000)

        with patch.object(debug_clip, '_ffmpeg_path', return_value='/fake/ffmpeg'), \
                patch.object(
                    debug_clip.subprocess, 'run',
                    return_value=subprocess.CompletedProcess([], 1, stdout=b'', stderr=b'boom'),
                ):
            self.assertIsNone(rec._transcode(src, dst, 30))

        # 转码失败也要保住画面：原始录像比没有强
        self.assertTrue(os.path.exists(dst))
        self.assertFalse(os.path.exists(src))

    def test_missing_ffmpeg_keeps_raw_recording(self):
        rec = self.make_clip()
        src = os.path.join(self.output_dir, 'src.mp4')
        dst = os.path.join(self.output_dir, 'out.mp4')
        with open(src, 'wb') as f:
            f.write(b'x' * 20000)

        with patch.object(debug_clip, '_ffmpeg_path', return_value=None):
            self.assertIsNone(rec._transcode(src, dst, 30))

        self.assertTrue(os.path.exists(dst))
        self.assertFalse(os.path.exists(src))


class TestClipSession(unittest.TestCase):
    def setUp(self):
        self._saved = debug_clip._ACTIVE
        debug_clip._ACTIVE = None

    def tearDown(self):
        debug_clip._ACTIVE = self._saved

    def test_stale_session_is_finalized_before_restart(self):
        stale = SimpleNamespace(finalize=lambda keep: '/stale.mp4')
        debug_clip._ACTIVE = stale
        with patch.object(debug_clip, '_ScreenRecordClip') as clip_cls:
            clip_cls.return_value.start.return_value = False
            self.assertIsNone(debug_clip.clip_start(config=None))
        # 旧会话被收尾、_ACTIVE 被清空，不会永久泄漏导致之后再也录不了
        self.assertIsNone(debug_clip._ACTIVE)

    def test_clip_end_clears_active_even_if_finalize_raises(self):
        def boom(keep):
            raise RuntimeError('finalize exploded')

        debug_clip._ACTIVE = SimpleNamespace(finalize=boom)
        self.assertIsNone(debug_clip.clip_end(keep=True))
        self.assertIsNone(debug_clip._ACTIVE)

    def test_clip_end_without_session_returns_none(self):
        self.assertIsNone(debug_clip.clip_end(keep=True))


class TestClipRecordingContext(unittest.TestCase):
    """clip_recording 是各任务接入录像的统一入口（短猫相接有 4 处调用）。"""

    def test_disabled_does_not_start_or_save(self):
        with patch.object(debug_clip, 'clip_start') as start, \
                patch.object(debug_clip, 'clip_end') as end:
            with debug_clip.clip_recording(config='cfg', enabled=False) as clip:
                self.assertIsNone(clip)
        start.assert_not_called()
        end.assert_not_called()

    def test_enabled_starts_with_prefix_and_saves(self):
        handle = object()
        with patch.object(debug_clip, 'clip_start', return_value=handle) as start, \
                patch.object(debug_clip, 'clip_end') as end:
            with debug_clip.clip_recording(
                config='cfg', enabled=True, prefix=debug_clip.CLIP_PREFIX_MEOW
            ) as clip:
                self.assertIs(clip, handle)
        start.assert_called_once_with('cfg', prefix=debug_clip.CLIP_PREFIX_MEOW)
        end.assert_called_once_with(keep=True)

    def test_saves_even_when_body_raises(self):
        with patch.object(debug_clip, 'clip_start', return_value=object()), \
                patch.object(debug_clip, 'clip_end') as end:
            with self.assertRaises(ValueError):
                with debug_clip.clip_recording(config='cfg', enabled=True):
                    raise ValueError('boom')
        end.assert_called_once_with(keep=True)

    def test_start_failure_is_not_fatal(self):
        """设备上录不了时优雅降级，不影响任务本身。"""
        with patch.object(debug_clip, 'clip_start', return_value=None), \
                patch.object(debug_clip, 'clip_end') as end:
            with debug_clip.clip_recording(config='cfg', enabled=True) as clip:
                self.assertIsNone(clip)
        end.assert_not_called()


class TestMeowfficerClipWiring(unittest.TestCase):
    """短猫相接的录像上下文必须读对配置键、用对自己的文件名前缀。"""

    def make_fake_task(self, enabled):
        from module.os.tasks.meowfficer_farming import OpsiMeowfficerFarming

        fake = SimpleNamespace(
            config=SimpleNamespace(OpsiMeowfficerFarming_DebugClip=enabled)
        )
        return OpsiMeowfficerFarming._meow_debug_clip(fake)

    def test_disabled_switch_yields_no_clip(self):
        with self.make_fake_task(False) as clip:
            self.assertIsNone(clip)

    def test_enabled_switch_starts_clip_with_meow_prefix(self):
        handle = object()
        with patch.object(debug_clip, 'clip_start', return_value=handle) as start, \
                patch.object(debug_clip, 'clip_end') as end:
            with self.make_fake_task(True) as clip:
                self.assertIs(clip, handle)
        self.assertEqual(start.call_args.kwargs['prefix'], debug_clip.CLIP_PREFIX_MEOW)
        end.assert_called_once_with(keep=True)


if __name__ == '__main__':
    unittest.main()
