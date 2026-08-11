"""WebUI部署设置"""

from module.webui.app_dependencies import (
    json,
    put_html,
    put_scope,
    run_js,
    t,
    use_scope,
)


from module.webui.app_types import WebUIMixinBase


class DeveloperSettingsMixin(WebUIMixinBase):
    """WebUI部署设置"""

    @use_scope("content", clear=True)
    def dev_setting(self) -> None:
        self.init_menu(name="Setting", skip_clear=True)
        self.set_title(t("Gui.MenuDevelop.Setting"))
        put_scope("develop_detail")
        put_html(
            f"""
            <div class="launcher-setting-panel">
              <h2 class="alas-develop-section-title">{t("Gui.Launcher.StartupTitle")}</h2>
              <div class="launcher-setting-row">
                <div>
                  <div class="launcher-setting-title">{t("Gui.Launcher.AutoStart")}</div>
                  <div class="launcher-setting-desc">{t("Gui.Launcher.AutoStartHelp")}</div>
                </div>
                <label class="launcher-switch" title="{t("Gui.Launcher.AutoStart")}">
                  <input id="launcher-autostart-switch" type="checkbox" disabled>
                </label>
              </div>
              <div id="launcher-status" class="launcher-setting-status">{t("Gui.Launcher.Loading")}</div>
            </div>
            """,
            scope="develop_detail",
        )
        run_js(
            f"""
            (function(){{
              const statusEl = document.getElementById('launcher-status');
              const switchEl = document.getElementById('launcher-autostart-switch');
              const text = {{
                loading: {json.dumps(t("Gui.Launcher.Loading"))},
                connected: {json.dumps(t("Gui.Launcher.Connected"))},
                disconnected: {json.dumps(t("Gui.Launcher.Disconnected"))},
                remote: {json.dumps(t("Gui.Launcher.RemoteUnavailable"))},
                unsupported: {json.dumps(t("Gui.Launcher.Unsupported"))},
                enabled: {json.dumps(t("Gui.Launcher.Enabled"))},
                disabled: {json.dumps(t("Gui.Launcher.Disabled"))},
                setting: {json.dumps(t("Gui.Launcher.Setting"))},
                failed: {json.dumps(t("Gui.Launcher.Failed"))}
              }};

              async function refresh() {{
                switchEl.disabled = true;
                statusEl.textContent = text.loading;
                try {{
                  const resp = await fetch('/api/launcher/status', {{cache: 'no-store'}});
                  const data = await resp.json();
                  const enabled = data.autostart_enabled === true;
                  switchEl.checked = enabled;
                  if (!data.request_local) {{
                    statusEl.textContent = text.remote;
                    return;
                  }}
                  if (!data.autostart_supported) {{
                    statusEl.textContent = text.unsupported;
                    return;
                  }}
                  if (!data.launcher_connected) {{
                    statusEl.textContent = text.disconnected;
                    return;
                  }}
                  switchEl.disabled = false;
                  if (data.autostart_enabled === null) {{
                    statusEl.textContent = text.connected + ' · ' + text.loading;
                  }} else {{
                    statusEl.textContent = text.connected + ' · ' + (enabled ? text.enabled : text.disabled);
                  }}
                }} catch (err) {{
                  statusEl.textContent = text.failed + ': ' + err;
                }}
              }}

              switchEl.addEventListener('change', async function() {{
                const target = switchEl.checked;
                switchEl.disabled = true;
                statusEl.textContent = text.setting;
                try {{
                  const resp = await fetch('/api/launcher/startup', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{enabled: target}})
                  }});
                  const result = await resp.json();
                  if (!result.success) {{
                    throw new Error(result.error || 'unknown error');
                  }}
                }} catch (err) {{
                  switchEl.checked = !target;
                  statusEl.textContent = text.failed + ': ' + err.message;
                  setTimeout(refresh, 1600);
                  return;
                }}
                await refresh();
              }});

              refresh();
            }})();
            """
        )
        put_html(
            f"""
            <div id="deploy-setting-root" class="deploy-setting-panel">
              <div class="deploy-setting-toolbar">
                <div>
                  <h2 class="alas-develop-section-title">{t("Gui.DeploySetting.Title")}</h2>
                  <div id="deploy-setting-notice" class="deploy-setting-notice">{t("Gui.DeploySetting.Loading")}</div>
                </div>
                <button id="deploy-setting-refresh" class="deploy-setting-button" type="button">{t("Gui.DeploySetting.Refresh")}</button>
              </div>
              <div id="deploy-setting-fields"></div>
              <div class="deploy-setting-actions">
                <button id="deploy-setting-save" class="deploy-setting-button primary" type="button" disabled>{t("Gui.DeploySetting.Save")}</button>
              </div>
              <div id="deploy-setting-status" class="deploy-setting-status"></div>
            </div>
            """,
            scope="develop_detail",
        )
        run_js(
            f"""
            (function(){{
              const fieldsEl = document.getElementById('deploy-setting-fields');
              const noticeEl = document.getElementById('deploy-setting-notice');
              const statusEl = document.getElementById('deploy-setting-status');
              const saveBtn = document.getElementById('deploy-setting-save');
              const refreshBtn = document.getElementById('deploy-setting-refresh');
              const text = {{
                loading: {json.dumps(t("Gui.DeploySetting.Loading"))},
                save: {json.dumps(t("Gui.DeploySetting.Save"))},
                saving: {json.dumps(t("Gui.DeploySetting.Saving"))},
                saved: {json.dumps(t("Gui.DeploySetting.Saved"))},
                failed: {json.dumps(t("Gui.DeploySetting.Failed"))},
                yes: {json.dumps(t("Gui.DeploySetting.Enabled"))},
                no: {json.dumps(t("Gui.DeploySetting.Disabled"))},
                demo: {json.dumps(t("Gui.DeploySetting.DemoDisabled"))}
              }};
              let schema = null;

              function escapeHtml(value) {{
                return String(value == null ? '' : value)
                  .replace(/&/g, '&amp;')
                  .replace(/</g, '&lt;')
                  .replace(/>/g, '&gt;')
                  .replace(/"/g, '&quot;');
              }}

              function fieldHtml(field) {{
                const value = field.value == null ? '' : field.value;
                let input = '';
                if (field.type === 'bool') {{
                  input = `<label class="launcher-switch"><input data-deploy-key="${{escapeHtml(field.key)}}" type="checkbox" ${{value === true ? 'checked' : ''}}></label>`;
                }} else if (field.type === 'select') {{
                  const options = (field.options || []).map(opt => `<option value="${{escapeHtml(opt)}}" ${{String(opt) === String(value) ? 'selected' : ''}}>${{escapeHtml(opt)}}</option>`).join('');
                  input = `<select class="deploy-setting-select" data-deploy-key="${{escapeHtml(field.key)}}">${{options}}</select>`;
                }} else if (field.type === 'int') {{
                  input = `<input class="deploy-setting-input" data-deploy-key="${{escapeHtml(field.key)}}" type="number" min="0" value="${{escapeHtml(value)}}">`;
                }} else {{
                  input = `<input class="deploy-setting-input" data-deploy-key="${{escapeHtml(field.key)}}" type="text" value="${{escapeHtml(value)}}">`;
                }}
                return `
                  <div class="deploy-setting-field">
                    <div>
                      <label>${{escapeHtml(field.label)}}</label>
                      <div class="deploy-setting-help">${{escapeHtml(field.help)}}</div>
                    </div>
                    <div>${{input}}</div>
                  </div>
                `;
              }}

              function render(data) {{
                schema = data;
                noticeEl.textContent = data.notice || '';
                fieldsEl.innerHTML = (data.groups || []).map(group => `
                  <div class="deploy-setting-group">
                    <div class="deploy-setting-group-title">${{escapeHtml(group.label)}}</div>
                    ${{(group.fields || []).map(fieldHtml).join('')}}
                  </div>
                `).join('');
                saveBtn.disabled = !!data.demo;
                statusEl.textContent = data.demo ? text.demo : '';
              }}

              function collectValues() {{
                const values = {{}};
                fieldsEl.querySelectorAll('[data-deploy-key]').forEach(el => {{
                  const key = el.getAttribute('data-deploy-key');
                  if (el.type === 'checkbox') {{
                    values[key] = el.checked;
                  }} else if (el.type === 'number') {{
                    values[key] = el.value;
                  }} else {{
                    values[key] = el.value;
                  }}
                }});
                return values;
              }}

              async function refresh() {{
                saveBtn.disabled = true;
                statusEl.textContent = text.loading;
                try {{
                  const resp = await fetch('/api/deploy/settings', {{cache: 'no-store'}});
                  const result = await resp.json();
                  if (!result.success) {{
                    throw new Error(result.error || 'unknown error');
                  }}
                  render(result.data);
                  statusEl.textContent = '';
                }} catch (err) {{
                  statusEl.textContent = text.failed + ': ' + (err.message || err);
                }}
              }}

              async function save() {{
                if (!schema || saveBtn.disabled) return;
                saveBtn.disabled = true;
                saveBtn.textContent = text.saving;
                statusEl.textContent = text.saving;
                try {{
                  const resp = await fetch('/api/deploy/settings', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{values: collectValues()}})
                  }});
                  const result = await resp.json();
                  if (!result.success) {{
                    throw new Error(result.error || 'unknown error');
                  }}
                  await refresh();
                  statusEl.textContent = text.saved;
                }} catch (err) {{
                  statusEl.textContent = text.failed + ': ' + (err.message || err);
                }} finally {{
                  saveBtn.textContent = text.save;
                  saveBtn.disabled = schema && schema.demo;
                }}
              }}

              refreshBtn.addEventListener('click', refresh);
              saveBtn.addEventListener('click', save);
              refresh();
            }})();
            """
        )
