"""WebUIASGI生命周期管理"""

from module.webui.app_dependencies import (
    ProcessManager,
    RemoteAccess,
    State,
    close_discord_rpc,
    init_discord_rpc,
    lang,
    logger,
    os,
    start_ocr_server_process,
    stop_ocr_server_process,
    task_handler,
    updater,
)

from module.webui.app_helpers import (
    is_demo_mode,
)


def startup() -> None:
    """初始化 WebUI 进程级后台服务。"""
    State.init()
    lang.reload()
    updater.event = State.manager.Event()
    if State.deploy_config.AutoUpdate:
        if updater.delay > 0:
            task_handler.add(updater.check_update, updater.delay)
        task_handler.add(updater.schedule_update(), 86400)
    task_handler.start()
    if State.deploy_config.DiscordRichPresence:
        init_discord_rpc()
    if State.deploy_config.StartOcrServer and not is_demo_mode():
        start_ocr_server_process(State.deploy_config.OcrServerPort)
    if State.deploy_config.EnableRemoteAccess and (
        State.deploy_config.Password is not None or os.environ.get("DEMO") == "1"
    ):
        task_handler.add(RemoteAccess.keep_ssh_alive(), 60)


def clearup() -> None:
    """停止 WebUI 进程级资源，避免热重载遗留子进程。"""
    logger.info("Start clearup")
    RemoteAccess.kill_ssh_process()
    close_discord_rpc()
    stop_ocr_server_process()
    for alas in ProcessManager._processes.values():
        alas.stop()
    State.clearup()
    task_handler.stop()
    logger.info("Alas closed.")
