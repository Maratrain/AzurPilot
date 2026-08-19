"""WebUI 手动停止调度器后的收尾动作。"""

from module.logger import logger, set_file_logger

STOP_ACTIONS = {
    "stay_there",
    "goto_main",
    "close_game",
    "close_emulator",
}


def normalize_stop_action(action: object) -> str:
    """校验停止后的动作，非法配置安全回退为不操作。"""
    if action in STOP_ACTIONS:
        return action

    logger.warning(
        f"[WebUI-停止收尾] 未知的停止后动作 {action!r}，将保持当前游戏状态"
    )
    return "stay_there"


def _get_existing_device(config):
    """连接已有设备，不允许为了停止动作启动模拟器。"""
    from module.device.device import Device

    return Device.for_existing_device(config)


def _close_emulator(config) -> bool:
    """关闭可管理的模拟器实例。"""
    from module.device.platform import Platform

    platform = Platform(config, connect=False)
    if platform.emulator_instance is None:
        logger.warning("[WebUI-停止收尾] 未找到可管理的模拟器实例，跳过关闭模拟器")
        return False

    if platform.emulator_stop():
        logger.info("[WebUI-停止收尾] 模拟器已关闭")
        return True

    logger.warning("[WebUI-停止收尾] 关闭模拟器失败或当前平台不支持该操作")
    return False


def execute_stop_action(config, action: object) -> bool:
    """执行单个停止后动作，所有异常由调用方记录并隔离。"""
    action = normalize_stop_action(action)
    logger.hr("手动停止收尾", level=1)
    logger.attr("停止后操作", action)

    if action == "stay_there":
        logger.info("[WebUI-停止收尾] 保持当前游戏状态")
        return True

    if action == "close_emulator":
        return _close_emulator(config)

    device = _get_existing_device(config)
    if action == "close_game":
        device.app_stop()
        logger.info("[WebUI-停止收尾] 游戏已关闭")
        return True

    if not device.app_is_running():
        logger.info("[WebUI-停止收尾] 游戏未运行，跳过返回主页面")
        return True

    from module.ui.ui import UI

    UI(config=config, device=device).ui_goto_main(recover_unknown=False)
    logger.info("[WebUI-停止收尾] 已返回主页面")
    return True


def run_stop_action(config_name: str) -> None:
    """独立进程入口：加载配置并执行停止后动作。"""
    try:
        # ProcessManager 为轻量 WebUI 场景注入了伪 PIL，此处需要真实图像库。
        from module.webui.fake_pil_module import remove_fake_pil_module

        remove_fake_pil_module()
        set_file_logger(name=config_name)

        from module.config.config import AzurLaneConfig

        config = AzurLaneConfig(config_name=config_name)
        action = getattr(config, "Optimization_WhenSchedulerStopped", "stay_there")
        execute_stop_action(config, action)
    except Exception:
        logger.exception("[WebUI-停止收尾] 执行停止后动作失败")
