"""WebUI调试工具和远程访问"""

from deploy.atomic import atomic_write
from module.logger import logger

from module.webui.app_dependencies import (
    DEFAULT_CONFIG_NAME,
    Optional,
    ProcessManager,
    RemoteAccess,
    State,
    Switch,
    alas_instance,
    clear,
    load_config,
    os,
    put_button,
    put_buttons,
    put_html,
    put_link,
    put_loading,
    put_row,
    put_scope,
    put_text,
    put_warning,
    raise_exception,
    t,
    toast,
    use_scope,
)
from module.webui.app_lifecycle import clearup


from module.webui.app_types import WebUIMixinBase


def prepare_webui_restart() -> bool:
    """保存当前运行实例，供新 WebUI 在重启后恢复。"""
    try:
        names = [
            f"{alas.config_name}\n" for alas in ProcessManager.running_instances()
        ]
        atomic_write("./config/reloadalas", "".join(names))
    except Exception as exc:
        logger.exception_context(
            title='无法准备 WebUI 手动重启',
            exc=exc,
            impact='继续重启会导致当前运行的 AzurPilot 实例无法自动恢复。',
            action='检查 config 目录写入权限后重试。',
            level=50,
        )
        return False
    return True


def request_webui_restart() -> bool:
    """请求手动重启，且不打断正在执行的更新事务。"""
    if State.restart_event is None:
        return False
    if not State.restart_lock.acquire(blocking=False):
        logger.info("自动更新事务正在进行，忽略本次手动重启请求")
        return False

    try:
        if State._restart_requested:
            return True
        if not prepare_webui_restart():
            return False

        State._restart_requested = True
        try:
            if not clearup():
                logger.warning("WebUI 清理未完成，将由父进程终止完整进程树")
        except Exception as exc:
            logger.exception_context(
                title='WebUI 手动重启清理失败',
                exc=exc,
                impact='父进程仍会终止旧 WebUI 进程树。',
                action='检查 WebUI 清理日志，确认是否有残留资源。',
                level=50,
            )

        try:
            State.restart_event.set()
        except Exception as exc:
            State._restart_requested = False
            logger.exception_context(
                title='无法通知父进程执行 WebUI 手动重启',
                exc=exc,
                impact='当前 WebUI 不会退出，已保存的实例恢复标记将保留。',
                action='检查父子进程事件状态后重新发起重启。',
                level=50,
            )
            return False
        return True
    finally:
        State.restart_lock.release()


class DeveloperToolsMixin(WebUIMixinBase):
    """WebUI调试工具和远程访问"""

    @use_scope("content", clear=True)
    def dev_utils(self) -> None:
        self.init_menu(name="Utils", skip_clear=True)
        self.set_title(t("Gui.MenuDevelop.Utils"))
        put_scope("develop_detail")
        put_button(
            label=t("GUI测试 抛出异常事件"),
            onclick=raise_exception,
            scope="develop_detail",
        )
        put_button(
            label=t("预览更新提示"),
            onclick=self._preview_update_notice,
            scope="develop_detail",
        )

        def _get_debug_target_instance() -> Optional[str]:
            if getattr(self, "alas_name", ""):
                return self.alas_name
            all_instances = alas_instance()
            if all_instances:
                return all_instances[0]
            return None

        def _refresh_debug_status():
            self.set_aside_status()
            if hasattr(self, "state_switch"):
                try:
                    self.state_switch.switch()
                except Exception:
                    pass

        def _mock_icon_state(state: int, seconds: int = 10):
            target = _get_debug_target_instance()
            if not target:
                toast("未找到可用实例，无法模拟图标状态", color="warning")
                return
            ProcessManager.get_manager(target).set_state_override(
                state, duration=seconds
            )
            _refresh_debug_status()
            toast(f"已为 {target} 模拟状态 {state}（{seconds}s）", color="info")

        def _clear_mock_icon_state():
            target = _get_debug_target_instance()
            if not target:
                toast("未找到可用实例，无法清除模拟状态", color="warning")
                return
            ProcessManager.get_manager(target).clear_state_override()
            _refresh_debug_status()
            toast(f"已清除 {target} 的图标状态模拟", color="success")

        put_buttons(
            buttons=[
                {"label": "模拟运行图标(10s)", "value": 1, "color": "success"},
                {"label": "模拟错误图标(10s)", "value": 3, "color": "danger"},
                {"label": "模拟更新图标(10s)", "value": 4, "color": "warning"},
            ],
            onclick=lambda state: _mock_icon_state(state, 10),
            scope="develop_detail",
        )
        put_button(
            label="清除图标模拟状态",
            onclick=_clear_mock_icon_state,
            color="secondary",
            scope="develop_detail",
        )

        def _force_restart():
            if State.restart_event is None:
                toast(t("Gui.Toast.ReloadEnabled"), color="error")
                return
            if request_webui_restart():
                toast(t("Gui.Toast.AlasRestart"), duration=0, color="error")
            else:
                toast("自动更新正在进行或无法保存运行实例，已取消重启", color="error")

        put_button(label=t("重启Alas"), onclick=_force_restart, scope="develop_detail")

        def _test_notify_update():
            from module.notify.notify import notify_webui

            instance = getattr(self, "alas_name", DEFAULT_CONFIG_NAME)
            notify_webui(
                instance=instance,
                title="发现更新喵！",
                content="测试更新推送逻辑，启动器应显示专用标题。",
                update=True,
            )
            toast("已发送更新测试通知", color="success")

        def _test_notify_announcement():
            from module.notify.notify import notify_webui

            instance = getattr(self, "alas_name", DEFAULT_CONFIG_NAME)
            notify_webui(
                instance=instance,
                title="新公告喵！",
                content="测试公告推送逻辑，启动器应显示专用标题。",
                updata=False,
            )
            toast("已发送公告测试通知", color="info")

        def _test_notify_error():
            from module.notify import handle_notify

            instance = _get_debug_target_instance()
            if not instance:
                toast("未找到可用实例，无法发送错误推送测试", color="warning")
                return
            config = load_config(instance)
            success = handle_notify(
                config.Error_OnePushConfig,
                title=f"AzurPilot <{instance}> 崩溃",
                content=f"<{instance}> 开发者错误推送测试",
            )
            if success:
                toast("已发送错误推送测试", color="success")
            else:
                toast("错误推送测试发送失败，请检查错误推送设置", color="error")

        put_buttons(
            buttons=[
                {
                    "label": "测试更新推送 (updata=True)",
                    "value": "update",
                    "color": "danger",
                },
                {
                    "label": "测试公告推送 (updata=False)",
                    "value": "announcement",
                    "color": "info",
                },
                {
                    "label": "测试错误推送",
                    "value": "error",
                    "color": "danger",
                },
            ],
            onclick=[
                _test_notify_update,
                _test_notify_announcement,
                _test_notify_error,
            ],
            scope="develop_detail",
        )

    @use_scope("content", clear=True)
    def dev_remote(self) -> None:
        self.init_menu(name="Remote")
        self.set_title(t("Gui.MenuDevelop.Remote"))
        put_scope("develop_detail")
        with use_scope("develop_detail"):
            put_row(
                content=[put_scope("remote_loading"), None, put_scope("remote_state")],
                size="auto .25rem 1fr",
            )
            put_scope("remote_info")

        def u(state):
            if state == -1:
                return
            status_map = {
                "direct_p2p": t("Gui.Remote.StatusDirect"),
                "turn_relay": t("Gui.Remote.StatusTurn"),
                "ssh_forward": t("Gui.Remote.StatusSsh"),
                "waiting_peer": t("Gui.Remote.StatusSignaling"),
                "signaling": t("Gui.Remote.StatusSignaling"),
                "starting": t("Gui.Remote.StatusStarting"),
                "dependency_missing": t("Gui.Remote.StatusSsh"),
                "failed": t("Gui.Remote.StatusFailed"),
            }
            clear("remote_loading")
            clear("remote_state")
            clear("remote_info")
            if state in (1, 2):
                put_loading("grow", "success", "remote_loading").style(
                    "--loading-grow--"
                )
                remote_status = RemoteAccess.get_connection_state()
                put_text(
                    f"{t('Gui.Remote.Running')} · {status_map.get(remote_status, remote_status)}",
                    scope="remote_state",
                )
                put_text(t("Gui.Remote.EntryPoint"), scope="remote_info")
                entrypoint = RemoteAccess.get_entry_point()
                if entrypoint:
                    if State.electron:  # Prevent click into url in electron client
                        put_text(entrypoint, scope="remote_info").style(
                            "text-decoration-line: underline"
                        )
                    else:
                        put_link(name=entrypoint, url=entrypoint, scope="remote_info")
                else:
                    put_text("Loading...", scope="remote_info")
                remote_error = RemoteAccess.get_error()
                if remote_error and remote_status in ("dependency_missing", "failed"):
                    put_warning(remote_error, closable=False, scope="remote_info")
            elif state in (0, 3, 4):
                put_loading("border", "secondary", "remote_loading").style(
                    "--loading-border-fill--"
                )
                if State.deploy_config.EnableRemoteAccess and (
                    State.deploy_config.Password or os.environ.get("DEMO") == "1"
                ):
                    put_text(t("Gui.Remote.NotRunning"), scope="remote_state")
                else:
                    put_text(t("Gui.Remote.NotEnable"), scope="remote_state")
                put_text(t("Gui.Remote.ConfigureHint"), scope="remote_info")
                url = "http://app.azurlane.cloud" + (
                    "" if State.deploy_config.Language.startswith("zh") else "/en.html"
                )
                put_html(
                    f'<a href="{url}" target="_blank">{url}</a>', scope="remote_info"
                )
                if state == 3:
                    put_warning(
                        t("Gui.Remote.SSHNotInstall"),
                        closable=False,
                        scope="remote_info",
                    )

        remote_switch = Switch(
            status=u, get_state=RemoteAccess.get_state, name="remote"
        )

        self.task_handler.add(remote_switch.g(), delay=1, pending_delete=True)
