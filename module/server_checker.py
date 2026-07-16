from collections import deque
from json import JSONDecodeError

import requests

from module.base.timer import Timer
from module.config.server import VALID_SERVER_LIST as server_list
from module.exception import ScriptError
from module.logger import logger


class ServerChecker:
    # 震惊 ALAS 竟然公然收集用户IP地址
    def __init__(self, server: str) -> None:
        self._base: str = 'http://sc.shiratama.cn'
        self._api: dict = {
            'get_state': '/server/get_state',           # POST 请求
            'get_all_state': '/server/get_all_state',   # POST 请求
            'list': '/server/list'                      # GET 请求
        }

        if server != 'disabled':
            server = server.split('-')
            server = server_list[server[0]][int(server[-1])]

        self._server: str = server
        self._state: deque = deque(maxlen=2)
        self._timestamp: int = 0
        self._expired: int = 0
        self._timer: Timer = Timer(0)

        # 状态标志
        self._recover: bool = False
        self._retry: bool = False

        self.check_now()

    def _load_server(self) -> None:
        """
        通过 API 获取服务器状态。

        若服务器不可用，记录原因。API 出现异常时抛出 ScriptError。
        """
        if self._server == 'disabled':
            self._state.append(True)
            return

        try:
            session = requests.Session()
            session.trust_env = False
            resp = session.post(
                url=f'{self._base}{self._api["get_state"]}',
                params={
                    'server_name': self._server
                },
                timeout=15
            )
            if resp.status_code == 200:
                j = resp.json()
                if j['state'] != 1:
                    self._state.append(True)
                    logger.info(f'Server "{self._server}" is available.')
                else:
                    self._state.append(False)
                    logger.info(f'Server "{self._server}" is under maintenance.')

                # 检查 API 服务端是否已停止更新
                if j['last_update'] > self._timestamp:
                    self._timestamp = j['last_update']
                    self._expired = 0
                else:
                    self._expired += 1
                    if self._expired > 3:
                        logger.warning(f'Timestamp {self._timestamp} has not been updated for 3 times.')
            elif resp.status_code == 404:
                # API 数据库可能未收录新增服务器（如"长弓计划"），
                # 检查本地服务器列表确认该服务器是否真实存在
                if self._server_in_local_list():
                    self._state.append(True)
                    logger.info(f'Server "{self._server}" is available (local verified, API unknown).')
                else:
                    self._state.append(False)
                    raise ScriptError(f'Server "{self._server}" does not exist!')
            else:
                raise ScriptError(f'Get status_code {resp.status_code}. Response is {resp.text}')
        except (requests.exceptions.ConnectionError, requests.exceptions.ConnectTimeout) as e:
            logger.error(e)
            logger.error('Timeout while connecting to server checker API.')
            if self._retry:
                self._state.append(False)
            else:
                self._state.append(self.fast_retry())
        except JSONDecodeError:
            self._state.append(False)
            raise ScriptError(f'Response "{resp.text}" seems not to be a JSON.')
        except Exception as e:
            logger.error(e)
            self._state.append(False)
            raise e

    def wait_until_available(self) -> None:
        while not self.is_available():
            self._timer.wait()
            self.check_now()

    def check_now(self) -> None:
        """
        忽略计时器，立即获取服务器状态。

        若服务器可用，检查器保持静默。否则计时器间隔逐步从 2 分钟递增至 10 分钟。
        若发生 ScriptError，检查器将被临时强制禁用。
        """
        try:
            self._load_server()
            if self._state[-1]:
                self._timer.limit = 0
                # Recover 表示最新状态为可用（state[-1]=True），前一状态为不可用（state[0]=False）
                if not self._state[0]:
                    self._recover = True
            else:
                if self._timer.limit < 600:
                    self._timer.limit += 120
                logger.info(f'Server checker will retry after {self._timer.limit}s')
            self._timer.reset()
        except ScriptError as e:
            logger.warning(str(e))
            logger.warning('There may be something wrong with server checker.')
            logger.warning('Please contact the developer to fix it.')
            logger.warning('Server checker will be temporarily forced off.')
            self.reset()
            self._server = 'disabled'
            self._recover = True
            self._state.append(True)
        except Exception as e:
            raise e

    def _server_in_local_list(self) -> bool:
        """
        检查服务器名称是否存在于本地 VALID_SERVER_LIST 中。

        当 API 返回 404 时，用于区分"服务器不存在"和"API 数据库未收录"两种情况。

        Returns:
            bool: 本地列表中存在该服务器时返回 True。
        """
        for servers in server_list.values():
            if self._server in servers:
                return True
        return False

    def reset(self) -> None:
        self._timestamp = 0
        self._expired = 0
        self._timer.limit = 0
        self._recover = False

    def is_available(self) -> bool:
        """
        使用缓存返回服务器状态。

        Returns:
            bool: 服务器可用时返回 True。
        """
        if self._timer.limit != 0 and self._timer.reached():
            self.check_now()

        return self._state[-1]  # 返回最新状态

    def is_recovered(self) -> bool:
        """
        服务器是否从不可用状态恢复。

        Returns:
            bool: 服务器刚从不可用恢复为可用时返回 True。
        """
        if len(self._state) < 2:
            self._recover = False
            return False

        if self._recover:
            self._recover = False
            return True

        return False

    def fast_retry(self) -> bool:
        """
        快速重试：通过访问百度判断网络是否连通。

        部分国内用户可能无法连接 API，但网络实际可用，因此借助百度进行网络可达性判断。

        Returns:
            bool: 网络可用时返回 True。
        """
        self._retry = True
        try:
            session = requests.Session()
            session.trust_env = False
            _ = session.get('https://www.baidu.com', timeout=5)
            network_available = True
        except Exception as e:
            logger.error(e)
            network_available = False

        logger.attr('network_available', network_available)
        if network_available:
            logger.info('Trigger fast retry.')
            last = self._state.copy()
            for _ in range(3):
                logger.info(f'Retry {_ + 1} times ...')
                self._load_server()
                if self._state[0]:
                    self._retry = False
                    self._state.extend(last)
                    return True

            logger.error('Cannot connect to API. Please check you network or disable server checker.')
            self._retry = False
            self._state.extend(last)
            return False
        else:
            self._retry = False
            logger.error('Network is unavailable. Please check your network status.')
            return False
