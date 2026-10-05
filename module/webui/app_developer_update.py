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

    @use_scope("content")
    def dev_update(self) -> None:
        self.init_menu(name="Update")
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

        def page_active() -> bool:
            """本任务渲染前检查页面归属。

            get_commit() 底层是 git 子进程调用，可能阻塞数百毫秒以上；
            期间用户可能已切换菜单并清空 content。后台任务线程的 scope
            栈顶是 ROOT，此时 use_scope 会在 ROOT 下创建孤儿容器并渲染
            更新器表格，全屏盖住当前页面。此函数可能被回调线程直接调用，
            不在这里自移除任务（remove_current_task 依赖任务循环线程上下文）。
            """
            return self.page == "Update"

        def update_table():
            local_commit = updater.get_commit(short_sha1=True)
            upstream_commit = updater.get_commit(
                f"origin/{updater.Branch}", short_sha1=True
            )
            if not page_active():
                return
            with use_scope("updater_table", clear=True):
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
            history = updater.get_commit(
                f"origin/{updater.Branch}", n=20, short_sha1=True
            )
            if not page_active():
                return
            with use_scope("updater_detail", clear=True):
                put_text(t("Gui.Update.DetailedHistory"))
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
            # 页面守卫：快速切换菜单时，本任务可能在 init_menu 移除 pending 任务
            # 之后才被注册而残留。此时更新器的 scope 已随 content 清空，继续渲染
            # 会把提交历史表格写到 ROOT 下的孤儿容器，全屏盖住当前页面。
            # 发现页面已切走时移除自身，停止渲染。
            if self.page != "Update":
                self.task_handler.remove_current_task()
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
                    onclick=lambda: updater.check_update(force=True),
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

        self._render_hide_update_notice_setting()

        updater.check_update()


    def _render_hide_update_notice_setting(self) -> None:
        scope_id = "hide-update-notice"
        switch_id = f"switch-{scope_id}"
        status_id = f"status-{scope_id}"
        put_html(
            f'''
            <div class="startup-run-panel">
              <div class="startup-run-row">
                <div>
                  <div class="startup-run-title">{t("Gui.DeploySetting.HideUpdateNotice")}</div>
                  <div class="startup-run-desc">{t("Gui.DeploySetting.HideUpdateNoticeHelp")}</div>
                </div>
                <label class="launcher-switch" title="{t("Gui.DeploySetting.HideUpdateNotice")}">
                  <input id="{switch_id}" type="checkbox" disabled>
                </label>
              </div>
              <div id="{status_id}" class="startup-run-status">{t("Gui.StartupRun.Loading")}</div>
            </div>
            '''
        )
        run_js(
            f'''
            (function(){{
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
                  const resp = await fetch("/api/deploy/settings", {{cache: "no-store"}});
                  const result = await resp.json();
                  if (!result.success) {{
                    throw new Error(result.error || "unknown error");
                  }}
                  let value = false;
                  for (const group of (result.data.groups || [])) {{
                    const field = (group.fields || []).find(item => item.key === "HideUpdateNotice");
                    if (field) {{
                      value = field.value === true;
                      break;
                    }}
                  }}
                  switchEl.checked = value;
                  switchEl.disabled = false;
                  statusEl.textContent = value ? text.enabled : text.disabled;
                }} catch (err) {{
                  statusEl.textContent = text.unavailable + ": " + (err.message || err);
                }}
              }}

              switchEl.addEventListener("change", async function() {{
                const target = switchEl.checked;
                switchEl.disabled = true;
                statusEl.textContent = text.setting;
                try {{
                  const resp = await fetch("/api/deploy/settings", {{
                    method: "POST",
                    headers: {{
                      "Content-Type": "application/json"
                    }},
                    body: JSON.stringify({{values: {{HideUpdateNotice: target}}}})
                  }});
                  const result = await resp.json();
                  if (!result.success) {{
                    throw new Error(result.error || "unknown error");
                  }}
                  switchEl.checked = target;
                  statusEl.textContent = target ? text.enabled : text.disabled;
              }} catch (err) {{
                switchEl.checked = !target;
                statusEl.textContent = text.failed + ": " + (err.message || err);
                setTimeout(refresh, 1600);
                return;
              }}
              switchEl.disabled = false;
              }});

              refresh();
            }})();
            '''
        )

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
