"""
API 客户端模块
负责与 API 服务器进行HTTP交互（公告获取）
支持主域名(nanoda.work)和备用域名(xf-sama.xyz)的自动故障转移
"""
from typing import Any, Dict, List, Tuple, Optional

import requests

from module.logger import logger


class ApiClient:
    """统一的API客户端，支持双域名故障转移"""

    # 主域名和备用域名列表
    PRIMARY_DOMAIN = 'https://alas-apiv2.nanoda.work'
    FALLBACK_DOMAIN = 'https://alas-apiv2.nanoda.work'

    # API端点路径
    ANNOUNCEMENT_PATH = '/api/get/announcement'

    # 公告检查间隔（秒），5分钟 = 300秒
    ANNOUNCEMENT_CHECK_INTERVAL = 300

    @classmethod
    def _get_endpoints(cls, path: str) -> List[str]:
        """
        获取指定路径的所有端点URL（主域名+备用域名）

        Args:
            path: API路径

        Returns:
            端点URL列表
        """
        return [
            f'{cls.PRIMARY_DOMAIN}{path}',
            f'{cls.FALLBACK_DOMAIN}{path}'
        ]

    @classmethod
    def _request_with_fallback(cls, method: str, path: str, params: Dict[str, Any] = None,
                             json_data: Dict[str, Any] = None, timeout: int = 10,
                             success_codes: List[int] = None) -> Tuple[bool, int, str]:
        """
        通用请求方法，支持故障转移
        """
        if success_codes is None:
            success_codes = [200]

        endpoints = cls._get_endpoints(path)
        last_error = None

        for i, endpoint in enumerate(endpoints):
            try:
                domain_type = "主域名" if i == 0 else "备用域名"
                logger.debug(f'[基础-API] 尝试使用{domain_type}: {endpoint}')

                if method == 'GET':
                    response = requests.get(
                        endpoint,
                        params=params,
                        timeout=timeout,
                        headers={'User-Agent': 'alas AzurPilot'}
                    )
                else:
                    response = requests.post(
                        endpoint,
                        json=json_data,
                        timeout=timeout,
                        headers={
                            'Content-Type': 'application/json',
                            'User-Agent': 'alas AzurPilot'
                        }
                    )

                if response.status_code in success_codes:
                    if i > 0:
                        logger.info(f'[基础-API] 使用{domain_type}请求成功')
                    return True, response.status_code, response.text
                else:
                    logger.warning(f'[基础-API] {domain_type}返回错误状态: {response.status_code}')
                    last_error = f'HTTP {response.status_code}'

            except requests.exceptions.Timeout:
                logger.warning(f'[基础-API] {domain_type if i > 0 else "主域名"}请求超时')
                last_error = 'Timeout'
            except requests.exceptions.RequestException as e:
                logger.warning(f'[基础-API] {domain_type if i > 0 else "主域名"}请求失败: {e}')
                last_error = str(e)
            except Exception as e:
                logger.warning(f'[基础-API] {domain_type if i > 0 else "主域名"}发生异常: {e}')
                last_error = str(e)

        return False, 0, last_error or 'Unknown error'

    @classmethod
    def get_announcement(cls, timeout: int = 1, current_id: int = None) -> Optional[Dict[str, Any]]:
        """
        获取公告信息（同步）

        Args:
            timeout: 请求超时时间（秒），默认10秒
            current_id: 当前公告ID，如果提供，用于增量检查

        Returns:
            公告数据字典，如果为None表示无更新或获取失败
        """
        import time
        try:
            # 添加时间戳参数以绕过缓存
            timestamp = int(time.time())
            params = {'t': timestamp}
            if current_id is not None:
                params['id'] = current_id
            # 允许 200 (OK) 和 304 (Not Modified)
            success, status_code, response_text = cls._request_with_fallback(
                'GET',
                cls.ANNOUNCEMENT_PATH,
                params=params,
                timeout=timeout,
                success_codes=[200, 304]
            )
            if success:
                # 304 或空内容表示无更新
                if status_code == 304 or not response_text.strip():
                    return None

                import json
                try:
                    data = json.loads(response_text)

                    # 如果返回空字典或无ID，也视为无更新
                    if not data or not data.get('announcementId'):
                        logger.info('[Base] 公告数据为空或无ID')
                        return None

                    # 只要有标题，且有内容 OR 链接，就是有效公告
                    if data.get('title') and (data.get('content') or data.get('url')):
                        return data
                    else:
                        return None
                except json.JSONDecodeError as e:
                    logger.warning(f'[Base] 解析公告JSON失败: {e}, response={response_text[:100]}')
                    return None
            else:
                logger.warning(f'[Base] 获取公告失败: {response_text}')
                return None

        except Exception as e:
            logger.warning(f'[Base] 获取公告异常: {e}')
            return None
