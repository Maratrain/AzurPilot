import onepush.core
import yaml
from onepush import get_notifier
from onepush.core import Provider
from onepush.exceptions import OnePushException
from onepush.providers.custom import Custom
from requests import Response
from datetime import datetime, timedelta
from module.logger import logger

onepush.core.log = logger

CRASH_COOLDOWN = timedelta(minutes=30)

def crash_notify(config, send_func, *args, **kwargs):
    """
    坠机类通知统一入口（即将坠机 + 已坠机）
    """
    # 逻辑：检查冷却期 -> 调用具体的 send_func 发送 -> 成功则记录时间
    if not can_send_crash_notify(config):
        logger.info("[Notify] crash notify skipped (30min cooldown)")
        return False

    try:
        result = send_func(*args, **kwargs)
    except Exception as e:
        # 不打印敏感变量，仅记录错误信息
        logger.error("Error while sending crash notify")
        logger.debug(e)
        return False

    # 只有成功发送才记录时间
    if result:
        _set_last_time(config)

    return result

def _get_last_time(config):
    t = getattr(config, "OpsiGeneral_LastCrashNotifyTime", None)
    if isinstance(t, str):
        try:
            return datetime.fromisoformat(t)
        except Exception:
            return None
    return t


def _set_last_time(config):
    config.OpsiGeneral_LastCrashNotifyTime = datetime.now()
    try:
        config.save()
    except Exception:
        pass


def can_send_crash_notify(config) -> bool:
    last = _get_last_time(config)
    now = datetime.now()

    if last is None:
        return True

    return (now - last) >= CRASH_COOLDOWN

def handle_notify(_config: str, **kwargs) -> bool:
    """处理推送通知请求。

    解析 YAML 格式的配置，选择通知渠道（如 QQ、微信等），
    并通过 onepush 库发送通知消息。

    Args:
        _config: YAML 格式的通知配置字符串，包含 provider 和渠道参数。
        **kwargs: 附加的通知参数，如 title、content 等。

    Returns:
        通知发送成功返回 True，失败返回 False。
    """
    try:
        config = {}
        for item in yaml.safe_load_all(_config):
            config.update(item)
    except Exception:
        logger.error("Fail to load onepush config, skip sending")
        return False
    try:
        provider_name: str = config.pop("provider", None)
        if provider_name is None:
            logger.info("No provider specified, skip sending")
            return False
        notifier: Provider = get_notifier(provider_name)
        required: list[str] = notifier.params["required"]
        config.update(kwargs)

        # 参数预检查
        for key in required:
            if key not in config:
                logger.warning(
                    f"Notifier {notifier.name} require param '{key}' but not provided"
                )

        if isinstance(notifier, Custom):
            if "method" not in config or config["method"] == "post":
                config["datatype"] = "json"
            if "data" not in config or not isinstance(config.get("data"), dict):
                config["data"] = {}
            if "title" in kwargs:
                config["data"]["title"] = kwargs["title"]
            if "content" in kwargs:
                config["data"]["content"] = kwargs["content"]
                if "data" in config and "message" in config["data"] and '${content}' in config["data"]["message"]:
                    config["data"]["message"] = config["data"]["message"].replace("${content}", config["data"]["content"])
                    
        if provider_name.lower() == "gocqhttp":
            access_token = config.get("access_token")
            if access_token:
                config["token"] = access_token

        resp = notifier.notify(**config)
        if isinstance(resp, Response):
            if resp.status_code != 200:
                logger.warning("Push notify failed!")
                logger.warning(f"HTTP Code:{resp.status_code}")
                return False
            else:
                if provider_name.lower() == "gocqhttp":
                    try:
                        return_data: dict = resp.json()
                    except Exception:
                        logger.warning("Failed to parse gocqhttp response JSON")
                        return False
                    if return_data.get("status") == "failed":
                        logger.warning("Push notify failed!")
                        logger.warning(f"Return message:{return_data.get('wording')}")
                        return False
    except OnePushException:
        logger.error("Push notify failed")
        return False
    except Exception as e:
        # 不打印完整异常栈，避免暴露变量信息
        logger.error(e)
        return False

    logger.info("Push notify success")
    return True


def notify_webui(instance: str, title: str, content: str, **kwargs) -> bool:
    """推送通知到 WebUI 本地端口，供启动器接收。

    向本地 WebUI 服务发送 HTTP POST 请求，传递实例名、标题和内容。
    默认端口为 22267，可通过配置自定义。

    Args:
        instance: 触发通知的实例名称。
        title: 通知标题。
        content: 通知正文内容。
        **kwargs: 其他附加字段，合并到请求体中。

    Returns:
        推送成功返回 True，失败返回 False。
    """
    try:
        from module.webui.setting import State
        wp = getattr(State.deploy_config, "WebuiPort", None)
        if wp is None or str(wp).strip() == "":
            port = 22267
        else:
            port = int(wp)
    except Exception:
        port = 22267
    try:
        import requests
        payload = {"instance": instance, "title": title, "content": content}
        payload.update(kwargs)
        requests.post(
            f"http://127.0.0.1:{port}/api/notify",
            json=payload,
            timeout=2,
        )
        return True
    except Exception:
        return False


def send_crash_messages(config_obj, config_name: str, total_ap: int, onepush_config: str = None, notify_push_func=None, webui_instance: str = "AzurPilot", send_crashed: bool = True, send_warning: bool = True) -> None:
    """格式化并发送坠机类通知给多个渠道。

    Args:
        config_obj: 用于冷却时间记录的配置对象（需要支持属性保存）。
        config_name: 实例名，用于标题中显示。
        total_ap: 当前总行动力，用于内容中显示。
        onepush_config: 可选，onepush 的 YAML 配置字符串，若提供则会调用 `handle_notify` 发送。
        webui_instance: 可选，WebUI 实例名，默认 `AzurPilot`。

    此函数会发送两条通知：
      1) 使用 onepush（若提供 `onepush_config`）发送坠机已发生的通知；
      2) 使用本地 WebUI 发送即将坠机提醒（总行动力提示）。
    函数内部使用 `crash_notify` 做 30 分钟冷却检查，避免频繁推送。
    """
    title = f"AzurPilot <{config_name}> 新消息♥♥♥"
    # 保持原有字符串拼接与换行
    content_crashed = (
        f'当前总行动力：{total_ap}\n'
        f'很遗憾，71已坠机，请下次再来Nanoda！！！'
    )
    content_warning = (
        f'当前总行动力：{total_ap}\n'
        f'雪风大人提醒您，71即将坠机，请及时加仓'
    )

    # 发送两条通知：已坠机（crashed）和即将坠机（warning）
    # 每条通知都会尝试通过 OnePush（若提供）和本地启动器（notify_push_func 或 notify_webui）发送，
    # 并且都受 crash_notify 冷却控制（30 分钟）。

    # 1) 已坠机消息（content_crashed）
    if send_crashed:
        if onepush_config:
            crash_notify(
                config_obj,
                handle_notify,
                onepush_config,
                title=title,
                content=content_crashed,
            )

        if notify_push_func is not None:
            crash_notify(
                config_obj,
                notify_push_func,
                title=title,
                content=content_crashed,
            )
        else:
            crash_notify(
                config_obj,
                lambda *a, **kw: notify_webui(webui_instance, kw.get("title", ""), kw.get("content", "")),
                title=title,
                content=content_crashed,
            )

    # 2) 即将坠机消息（content_warning）
    if send_warning:
        if onepush_config:
            crash_notify(
                config_obj,
                handle_notify,
                onepush_config,
                title=title,
                content=content_warning,
            )

        if notify_push_func is not None:
            crash_notify(
                config_obj,
                notify_push_func,
                title=title,
                content=content_warning,
            )
        else:
            crash_notify(
                config_obj,
                lambda *a, **kw: notify_webui(webui_instance, kw.get("title", ""), kw.get("content", "")),
                title=title,
                content=content_warning,
            )
