import os
import socket
import ipaddress
from urllib.parse import urlparse

import requests
import yaml

from module.logger import logger


def get_wecom_webhook(onepush_config):
    try:
        config = {}

        for item in yaml.safe_load_all(onepush_config):
            if item:
                config.update(item)

        provider = config.get("provider")

        if provider != "wechatworkbot":
            return None

        return config.get("key")

    except Exception:
        logger.exception("Parse OnePushConfig failed")
        return None


def send_template_card(webhook, title, content_lines):
    # Basic validation of webhook URL to reduce SSRF / misuse risk.
    def is_valid_webhook(url: str) -> bool:
        if not url or not isinstance(url, str):
            return False
        try:
            parsed = urlparse(url)
        except Exception:
            return False

        # Only allow http or https schemes (prefer https)
        if parsed.scheme not in ("http", "https"):
            return False

        hostname = parsed.hostname
        if not hostname:
            return False

        # Allowed hosts can be provided via environment variable
        allowed = os.environ.get("REPORT_ALLOWED_HOSTS")
        if allowed:
            allowed_hosts = {h.strip() for h in allowed.split(",") if h.strip()}
        else:
            # Default to official WeCom API host for enterprise robot
            allowed_hosts = {"qyapi.weixin.qq.com"}

        if hostname not in allowed_hosts:
            return False

        # Resolve hostname and ensure it does not point to private/loopback addresses
        try:
            infos = socket.getaddrinfo(hostname, None)
            for info in infos:
                addr = info[4][0]
                ip = ipaddress.ip_address(addr)
                if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_multicast:
                    return False
        except Exception:
            # If DNS resolution fails, reject
            return False

        return True

    if not is_valid_webhook(webhook):
        logger.warning(f"Rejected webhook by validation: {webhook}")
        return False

    # Ensure all content lines are strings
    try:
        content_lines = [str(x) for x in content_lines]
    except Exception:
        logger.exception("Invalid content_lines for template card")
        return False

    payload = {
        "msgtype": "template_card",
        "template_card": {
            "card_type": "text_notice",
            "source": {
                "desc": "AzurPilot"
            },
            "main_title": {
                "title": title,
                "desc": ""
            },
            "horizontal_content_list": [
                {
                    "keyname": "统计",
                    "value": "\n".join(content_lines)
                }
            ],
            "card_action": {
                "type": 1,
                "url": "https://github.com/LmeSzinc/AzurLaneAutoScript"
            }
        }
    }

    try:
        r = requests.post(
            webhook,
            json=payload,
            timeout=10,
        )

        logger.info(f"Daily report sent: {r.status_code} {r.text}")

        # Prefer checking provider-specific success code in JSON response
        try:
            data = r.json()
            if isinstance(data, dict):
                # WeCom bot returns errcode == 0 on success
                return data.get("errcode", 0) == 0
        except ValueError:
            # Not JSON, fallback to HTTP status
            pass

        return r.status_code == 200

    except Exception:
        logger.exception("Send template card failed")
        return False
