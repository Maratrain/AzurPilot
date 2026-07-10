import os
import socket
import struct
import threading
import time as time_
from datetime import datetime, timezone


NTP_EPOCH_DELTA = 2208988800
NTP_PORT = 123
NTP_PACKET = b'\x1b' + b'\0' * 47
NTP_SERVERS_ENV = 'AZURPILOT_NTP_SERVERS'
NTP_DISABLE_ENV = 'AZURPILOT_NTP_DISABLE'
DEFAULT_NTP_SERVERS = (
    'ntp.ntsc.ac.cn',
    'ntp.aliyun.com',
    'ntp.tencent.com',
    'cn.pool.ntp.org',
    'pool.ntp.org',
)


class NetworkTimeSource:
    """通过 NTP 服务器校准本地时间。

    只缓存本机时间与 NTP 时间的偏移量，后续读取不会频繁访问网络。
    """

    def __init__(self):
        self.offset = 0.0
        self.base_timestamp = 0.0
        self.base_monotonic = 0.0
        self.server = None
        self.synced = False
        self.last_sync_monotonic = 0.0
        self.retry_after_monotonic = 0.0
        self.refresh_interval = 30 * 60
        self.retry_interval = 10 * 60
        self.timeout = 1.0
        self._lock = threading.RLock()
        self._warned = False

    @property
    def enabled(self):
        value = os.environ.get(NTP_DISABLE_ENV, '').strip().lower()
        return value not in {'1', 'true', 'yes', 'on'}

    @property
    def servers(self):
        value = os.environ.get(NTP_SERVERS_ENV, '').strip()
        if value:
            servers = [item.strip() for item in value.replace(';', ',').split(',')]
            servers = [item for item in servers if item]
            if servers:
                return servers

        return list(DEFAULT_NTP_SERVERS)

    def _query_server(self, host):
        last_error = None
        addresses = socket.getaddrinfo(host, NTP_PORT, type=socket.SOCK_DGRAM)
        for family, socktype, proto, _, sockaddr in addresses:
            with socket.socket(family, socktype, proto) as sock:
                sock.settimeout(self.timeout)
                try:
                    sent = time_.time()
                    sock.sendto(NTP_PACKET, sockaddr)
                    data, _ = sock.recvfrom(48)
                    received = time_.time()
                except OSError as e:
                    last_error = e
                    continue

            if len(data) < 48:
                continue

            mode = data[0] & 0b00000111
            stratum = data[1]
            if mode not in {4, 5} or not 1 <= stratum <= 15:
                continue

            seconds, fraction = struct.unpack('!II', data[40:48])
            ntp_timestamp = seconds - NTP_EPOCH_DELTA + fraction / 2 ** 32
            local_timestamp = (sent + received) / 2
            if ntp_timestamp < 1577836800:  # 2020-01-01
                continue

            return ntp_timestamp - local_timestamp

        if last_error is not None:
            raise last_error
        raise OSError(f'无效的 NTP 响应: {host}')

    def refresh(self, force=False):
        """刷新 NTP 偏移量，失败时保留已有偏移并退回本机时间。"""
        if not self.enabled:
            return False

        current = time_.monotonic()
        with self._lock:
            if not force and self.synced and current - self.last_sync_monotonic < self.refresh_interval:
                return True
            if not force and current < self.retry_after_monotonic:
                return self.synced

            errors = []
            for server in self.servers:
                try:
                    offset = self._query_server(server)
                except OSError as e:
                    errors.append(f'{server}: {e}')
                    continue

                self.offset = offset
                self.base_timestamp = time_.time() + offset
                self.base_monotonic = time_.monotonic()
                self.server = server
                self.synced = True
                self.last_sync_monotonic = time_.monotonic()
                self.retry_after_monotonic = 0.0
                self._warned = False
                self._log_info(f'网络时间已校准: {server}, offset={offset:.3f}s')
                return True

            self.retry_after_monotonic = time_.monotonic() + self.retry_interval
            if not self._warned:
                detail = '; '.join(errors) if errors else '没有可用服务器'
                self._log_warning(f'NTP 校时失败，暂时使用本机时间: {detail}')
                self._warned = True
            return self.synced

    def timestamp(self):
        self.refresh()
        if self.synced:
            return self.base_timestamp + (time_.monotonic() - self.base_monotonic)
        return time_.time() + self.offset

    def now(self, tz=None):
        return datetime.fromtimestamp(self.timestamp(), tz=tz)

    def status(self):
        self.refresh()
        return {
            'enabled': self.enabled,
            'synced': self.synced,
            'server': self.server or '-',
            'offset': self.offset,
            'refresh_interval': self.refresh_interval,
            'last_sync_elapsed': (
                time_.monotonic() - self.last_sync_monotonic
                if self.synced else None
            ),
        }

    @staticmethod
    def monotonic():
        return time_.monotonic()

    @staticmethod
    def sleep(seconds):
        time_.sleep(seconds)

    @staticmethod
    def _log_info(message):
        try:
            from module.logger import logger
            logger.info(message)
        except Exception:
            pass

    @staticmethod
    def _log_warning(message):
        try:
            from module.logger import logger
            logger.warning(message)
        except Exception:
            pass


network_time = NetworkTimeSource()


def refresh_time(force=False):
    return network_time.refresh(force=force)


def now(tz=None):
    return network_time.now(tz=tz)


def utcnow():
    return now(timezone.utc)


def timestamp():
    return network_time.timestamp()


def status():
    return network_time.status()


def monotonic():
    return network_time.monotonic()


def sleep(seconds):
    network_time.sleep(seconds)
