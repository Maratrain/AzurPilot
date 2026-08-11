"""WebUI更新和启动项设置"""

from module.webui.app_dependencies import (
    DEFAULT_CONFIG_NAME,
    State,
    Switch,
    clear,
    json,
    put_button,
    put_html,
    put_loading,
    put_row,
    put_scope,
    put_table,
    put_text,
    put_warning,
    re,
    run_js,
    t,
    updater,
    use_scope,
)


from module.webui.app_types import WebUIMixinBase


class DeveloperUpdateMixin(WebUIMixinBase):
    """WebUI更新和启动项设置"""

    @use_scope("content", clear=True)
    def dev_update(self) -> None:
        self.init_menu(name="Update", skip_clear=True)
        self.set_title(t("Gui.MenuDevelop.Update"))

        put_scope("updater_info")
        with use_scope("updater_info"):
            if State.restart_event is None:
                put_warning(t("Gui.Update.DisabledWarn"))

            put_row(
                content=[
                    put_scope("updater_loading"),
                    None,
                    put_scope("updater_state"),
                ],
                size="auto .25rem 1fr",
            )

            put_scope("updater_btn")
            put_scope("updater_table")
        put_scope("updater_detail")

        def update_table():
            with use_scope("updater_table", clear=True):
                local_commit = updater.get_commit(short_sha1=True)
                upstream_commit = updater.get_commit(
                    f"origin/{updater.Branch}", short_sha1=True
                )
                put_table(
                    [
                        [t("Gui.Update.Local"), *local_commit],
                        [t("Gui.Update.Upstream"), *upstream_commit],
                    ],
                    header=[
                        "",
                        "SHA1",
                        t("Gui.Update.Author"),
                        t("Gui.Update.Time"),
                        t("Gui.Update.Message"),
                    ],
                )
            with use_scope("updater_detail", clear=True):
                put_text(t("Gui.Update.DetailedHistory"))
                history = updater.get_commit(
                    f"origin/{updater.Branch}", n=20, short_sha1=True
                )
                put_table(
                    [commit for commit in history],
                    header=[
                        "SHA1",
                        t("Gui.Update.Author"),
                        t("Gui.Update.Time"),
                        t("Gui.Update.Message"),
                    ],
                )

        def u(state):
            if state == -1:
                return
            clear("updater_loading")
            clear("updater_state")
            clear("updater_btn")
            if state == 0:
                put_loading("border", "secondary", "updater_loading").style(
                    "--loading-border-fill--"
                )
                put_text(t("Gui.Update.UpToDate"), scope="updater_state")
                put_button(
                    t("Gui.Button.CheckUpdate"),
                    onclick=updater.check_update,
                    color="info",
                    scope="updater_btn",
                )
                update_table()
            elif state == 1:
                put_loading("grow", "success", "updater_loading").style(
                    "--loading-grow--"
                )
                put_text(t("Gui.Update.HaveUpdate"), scope="updater_state")
                put_button(
                    t("Gui.Button.ClickToUpdate"),
                    onclick=updater.run_update,
                    color="success",
                    scope="updater_btn",
                )
                update_table()
            elif state == "checking":
                put_loading("border", "primary", "updater_loading").style(
                    "--loading-border--"
                )
                put_text(t("Gui.Update.UpdateChecking"), scope="updater_state")
            elif state == "failed":
                put_loading("grow", "danger", "updater_loading").style(
                    "--loading-grow--"
                )
                put_text(t("Gui.Update.UpdateFailed"), scope="updater_state")
                put_button(
                    t("Gui.Button.RetryUpdate"),
                    onclick=updater.run_update,
                    color="primary",
                    scope="updater_btn",
                )
            elif state == "start":
                put_loading("border", "primary", "updater_loading").style(
                    "--loading-border--"
                )
                put_text(t("Gui.Update.UpdateStart"), scope="updater_state")
                put_button(
                    t("Gui.Button.CancelUpdate"),
                    onclick=updater.cancel,
                    color="danger",
                    scope="updater_btn",
                )
            elif state == "wait":
                put_loading("border", "primary", "updater_loading").style(
                    "--loading-border--"
                )
                put_text(t("Gui.Update.UpdateWait"), scope="updater_state")
                put_button(
                    t("Gui.Button.CancelUpdate"),
                    onclick=updater.cancel,
                    color="danger",
                    scope="updater_btn",
                )
            elif state == "run update":
                put_loading("border", "primary", "updater_loading").style(
                    "--loading-border--"
                )
                put_text(t("Gui.Update.UpdateRun"), scope="updater_state")
                put_button(
                    t("Gui.Button.CancelUpdate"),
                    onclick=updater.cancel,
                    color="danger",
                    scope="updater_btn",
                    disabled=True,
                )
            elif state == "reload":
                put_loading("grow", "success", "updater_loading").style(
                    "--loading-grow--"
                )
                put_text(t("Gui.Update.UpdateSuccess"), scope="updater_state")
                update_table()
            elif state == "finish":
                put_loading("grow", "success", "updater_loading").style(
                    "--loading-grow--"
                )
                put_text(t("Gui.Update.UpdateFinish"), scope="updater_state")
                update_table()
            elif state == "cancel":
                put_loading("border", "danger", "updater_loading").style(
                    "--loading-border--"
                )
                put_text(t("Gui.Update.UpdateCancel"), scope="updater_state")
                put_button(
                    t("Gui.Button.CancelUpdate"),
                    onclick=updater.cancel,
                    color="danger",
                    scope="updater_btn",
                    disabled=True,
                )
            else:
                put_text(
                    "Something went wrong, please contact develops",
                    scope="updater_state",
                )
                put_text(f"state: {state}", scope="updater_state")

        updater_switch = Switch(
            status=u, get_state=lambda: updater.state, name="updater"
        )

        update_table()
        self.task_handler.add(updater_switch.g(), delay=0.5, pending_delete=True)

        updater.check_update()

    def _render_startup_run_setting(self) -> None:
        instance = self.alas_name or DEFAULT_CONFIG_NAME
        scope_id = re.sub(r"[^0-9A-Za-z_]", "_", instance)
        switch_id = f"startup-run-switch-{scope_id}"
        status_id = f"startup-run-status-{scope_id}"
        put_html(
            f"""
            <div class="startup-run-panel">
              <div class="startup-run-row">
                <div>
                  <div class="startup-run-title">{t("Gui.StartupRun.Title")}</div>
                  <div class="startup-run-desc">{t("Gui.StartupRun.Description")}</div>
                </div>
                <label class="launcher-switch" title="{t("Gui.StartupRun.Title")}">
                  <input id="{switch_id}" type="checkbox" disabled>
                </label>
              </div>
              <div id="{status_id}" class="startup-run-status">{t("Gui.StartupRun.Loading")}</div>
            </div>
            """
        )
        run_js(
            f"""
            (function(){{
              const instance = {json.dumps(instance)};
              const switchEl = document.getElementById({json.dumps(switch_id)});
              const statusEl = document.getElementById({json.dumps(status_id)});
              const text = {{
                loading: {json.dumps(t("Gui.StartupRun.Loading"))},
                enabled: {json.dumps(t("Gui.StartupRun.Enabled"))},
                disabled: {json.dumps(t("Gui.StartupRun.Disabled"))},
                setting: {json.dumps(t("Gui.StartupRun.Setting"))},
                failed: {json.dumps(t("Gui.StartupRun.Failed"))},
                unavailable: {json.dumps(t("Gui.StartupRun.Unavailable"))}
              }};

              async function refresh() {{
                switchEl.disabled = true;
                statusEl.textContent = text.loading;
                try {{
                  const resp = await fetch('/api/deploy/startup-run?instance=' + encodeURIComponent(instance), {{cache: 'no-store'}});
                  const result = await resp.json();
                  if (!result.success) {{
                    throw new Error(result.error || 'unknown error');
                  }}
                  switchEl.checked = result.data.enabled === true;
                  switchEl.disabled = false;
                  statusEl.textContent = result.data.enabled ? text.enabled : text.disabled;
                }} catch (err) {{
                  statusEl.textContent = text.unavailable + ': ' + (err.message || err);
                }}
              }}

              switchEl.addEventListener('change', async function() {{
                const target = switchEl.checked;
                switchEl.disabled = true;
                statusEl.textContent = text.setting;
                try {{
                  const resp = await fetch('/api/deploy/startup-run', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{instance, enabled: target}})
                  }});
                  const result = await resp.json();
                  if (!result.success) {{
                    throw new Error(result.error || 'unknown error');
                  }}
                  switchEl.checked = result.data.enabled === true;
                  statusEl.textContent = result.data.enabled ? text.enabled : text.disabled;
                }} catch (err) {{
                  switchEl.checked = !target;
                  statusEl.textContent = text.failed + ': ' + (err.message || err);
                  setTimeout(refresh, 1600);
                  return;
                }}
                switchEl.disabled = false;
              }});

              refresh();
            }})();
            """
        )
