from typing import Optional

import requests


IP9_LOCATION_URL = 'https://ip9.com.cn/get'


def get_country_code(timeout=5) -> Optional[str]:
    """查询当前公网 IP 所在国家的 ISO 代码。

    查询失败或响应格式不符合预期时返回 ``None``，调用方应保留当前更新源。
    """
    try:
        response = requests.get(
            IP9_LOCATION_URL,
            timeout=timeout,
            headers={'User-Agent': 'AzurPilot'},
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None

    try:
        country_code = payload['data']['country_code']
    except (KeyError, TypeError):
        return None

    if isinstance(country_code, str):
        return country_code.lower()
    return None
