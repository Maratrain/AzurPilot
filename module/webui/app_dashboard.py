"""WebUI仪表盘刷新逻辑"""

import json
import os

from module.webui.app_dependencies import (
    Function,
    LogRes,
    clear,
    current_time,
    datetime,
    deep_get,
    eval_js,
    get_dashboard_scope_id,
    get_group_scope_id,
    logger,
    put_button,
    put_buttons,
    put_column,
    put_html,
    put_row,
    put_scope,
    put_text,
    re,
    run_js,
    t,
    time,
    time_delta,
    use_scope,
)

from module.webui.app_helpers import (
    timedelta_to_text,
)


from module.webui.app_types import WebUIMixinBase


class DashboardMixin(WebUIMixinBase):
    """WebUI仪表盘刷新逻辑"""

    _OVERVIEW_DOM_CHECK_JS = (
        "!!(document.getElementById('pywebio-scope-running_tasks')"
        " && document.getElementById('pywebio-scope-pending_tasks')"
        " && document.getElementById('pywebio-scope-waiting_tasks')"
        " && document.getElementById('pywebio-scope-dashboard'))"
    )

    def _overview_dom_ready(self) -> bool:
        """确认总览页的目标 scope 仍存在于客户端 DOM。

        页面切换会先移除周期任务再清空 content，但已在前台线程执行中的
        刷新无法被移除；若它此时重建缺失的 scope，PyWebIO 会把新 scope
        挂到根节点，形成脱离卡片布局的无样式残留。写入前统一校验，
        缺失则放弃本次刷新。
        """
        try:
            return bool(eval_js(self._OVERVIEW_DOM_CHECK_JS))
        except Exception:
            return False

    def alas_update_overview_task(self) -> None:
        if not self.visible or self.page != "Overview":
            return
        config_name = self.alas_name
        self.alas_config.load()
        self.alas_config.get_next_task()

        # 检查任务失败保护通知（即使任务列表未变化也要检查，
        # 否则快照短路会导致通知永不弹出）
        self._check_task_failure_notifications()

        if len(self.alas_config.pending_task) >= 1:
            if self.alas.alive:
                running = self.alas_config.pending_task[:1]
                pending = self.alas_config.pending_task[1:]
            else:
                running = []
                pending = self.alas_config.pending_task[:]
        else:
            running = []
            pending = []
        waiting = self.alas_config.waiting_task

        snapshot = {
            "running": tuple((task.command, task.next_run) for task in running),
            "pending": tuple((task.command, task.next_run) for task in pending),
            "waiting": tuple((task.command, task.next_run) for task in waiting),
            "alive": self.alas.alive,
        }
        if self._overview_snapshot == snapshot:
            return
        self._overview_snapshot = snapshot

        with self._page_lock:
            # 与页面切换（init_menu / ui_alas 的 page+clear）互斥：导航先完成
            # 则放弃写入，刷新先完成则导航的 clear 会连同输出一起清掉，
            # 两种时序都不会在根节点留下重建的 scope。实例已切换时同样放弃，
            # 避免把旧实例的任务数据写进新实例的列表。
            if (
                self.alas_name != config_name
                or self.page != "Overview"
                or not self._overview_dom_ready()
            ):
                return

            def put_task(func: Function):
                with use_scope(f"overview-task_{func.command}"):
                    put_column(
                        [
                            put_text(t(f"Task.{func.command}.name")).style("--arg-title--"),
                            put_text(str(func.next_run)).style("--arg-help--"),
                        ],
                        size="auto auto",
                    )
                    put_button(
                        label=t("Gui.Button.Setting"),
                        onclick=lambda: self.alas_set_group(func.command),
                        color="off",
                    )

            clear("running_tasks")
            clear("pending_tasks")
            clear("waiting_tasks")
            with use_scope("running_tasks"):
                if running:
                    for task in running:
                        put_task(task)
                else:
                    put_text(t("Gui.Overview.NoTask")).style("--overview-notask-text--")
            with use_scope("pending_tasks"):
                if pending:
                    for task in pending:
                        put_task(task)
                else:
                    put_text(t("Gui.Overview.NoTask")).style("--overview-notask-text--")
            with use_scope("waiting_tasks"):
                if waiting:
                    for task in waiting:
                        put_task(task)
                else:
                    put_text(t("Gui.Overview.NoTask")).style("--overview-notask-text--")

    def _check_task_failure_notifications(self) -> None:
        """检查任务失败保护通知并弹出浮动提示卡片。

        读取当前实例对应的失败记录文件（存放在 ``log/`` 目录），
        如果存在未读通知则显示类似更新提示的浮动通知卡片。
        用户点击"去处理"跳转到该任务的设置页，点击"全部已知晓"关闭提示。
        已展示过的通知不会重复弹出。
        """
        if not self.alas_name:
            return

        # 弹窗已显示时不重复检查
        if getattr(self, "_failure_popup_shown", False):
            return

        # 失败记录文件存放在 log 目录下
        filepath = os.path.join('./log', f'{self.alas_name}.task_failure.json')
        if not os.path.exists(filepath):
            return
        try:
            with open(filepath, encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return

        notifications = data.get('notifications', [])
        unread = [n for n in notifications if not n.get('read', False)]
        if not unread:
            return

        # 避免重复弹窗：所有未读通知组合成一个标识
        notice_key = '_'.join(
            f'{n.get("task", "")}_{n.get("timestamp", "")}' for n in unread
        )
        shown_set = getattr(self, '_shown_failure_keys', set())
        if notice_key in shown_set:
            return
        shown_set.add(notice_key)
        self._shown_failure_keys = shown_set
        self._failure_popup_shown = True

        # 生成唯一 scope id
        notice_id = f"task_failure_batch_{int(time.time() * 1000)}"

        _remove_js = (
            "(function () {\n"
            "    var el = document.getElementById('" + notice_id + "');\n"
            "    if (el && el.parentNode) {\n"
            "        el.parentNode.removeChild(el);\n"
            "    }\n"
            "})();\n"
        )

        def _remove_failure_notice():
            run_js(_remove_js)

        # 为每个未读通知准备任务信息
        task_rows = []
        for n in unread:
            task = n.get('task', '')
            reason = n.get('reason', '')
            count = n.get('count', 0)
            task_display = task
            try:
                task_display = t(f'Task.{task}.name')
            except Exception:
                pass
            task_rows.append((task, task_display, reason, count))

        unread_count = len(task_rows)
        title_text = (
            f'{unread_count} 个任务已自动关闭' if unread_count > 1
            else '1 个任务已自动关闭'
        )

        # 构建每个任务的列表行 HTML（信息展示，不绑按钮）
        rows_html = []
        for task, task_display, reason, count in task_rows:
            row_html = (
                '<div style="display: flex; align-items: center; justify-content: space-between;'
                ' padding: 6px 8px; margin-bottom: 4px;'
                ' background: rgba(240, 62, 62, 0.06); border-radius: 6px; gap: 8px;">'
                '<div style="min-width: 0; flex: 1;">'
                '<div style="font-weight: 700; font-size: 0.9rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">'
                '{task_display}</div>'
                '<div style="font-size: 0.78rem; opacity: 0.7; line-height: 1.3;">'
                '{reason} &middot; {count} 次</div>'
                '</div>'
                '</div>'
            ).format(task_display=task_display, reason=reason, count=count)
            rows_html.append(row_html)
        rows_html_str = '\n'.join(rows_html)

        html = (
            '<div id="{}" class="alas-update-notice" role="status" aria-live="polite" '
            'style="max-height: 70vh; overflow: hidden;">'
            '<div class="alas-update-notice__halo"></div>'
            '<div class="alas-update-notice__icon" aria-hidden="true">'
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
            ' stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M12 9v4"></path>'
            '<path d="M12 17h.01"></path>'
            '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>'
            '</svg>'
            '</div>'
            '<div class="alas-update-notice__body">'
            '<div class="alas-update-notice__eyebrow">任务异常</div>'
            '<div class="alas-update-notice__title">{title_text}</div>'
            '<div class="alas-update-notice__text">'
            '以下任务因连续失败已被自动关闭，请逐一处理。</div>'
            '<div style="max-height: 180px; overflow-y: auto; margin: 4px 0 8px 0;">'
            '{rows_html}'
            '</div>'
            '<div id="pywebio-scope-{scope_id}_actions" class="alas-update-notice__actions"></div>'
            '</div>'
            '</div>'
        ).format(notice_id, title_text=title_text, rows_html=rows_html_str,
                 scope_id=notice_id)

        # 先移除已存在的同 id 提示
        run_js(_remove_js)

        with use_scope("ROOT"):
            put_html(html)

            # 汇总所有未读通知，"去处理"跳转到第一个出错任务的设置页
            first_task = task_rows[0][0] if task_rows else ''

            def _go_handle_first():
                _remove_failure_notice()
                self._failure_popup_shown = False
                for n in unread:
                    self._mark_failure_notification_read(n.get('task', ''))
                if first_task:
                    self.alas_set_group(first_task)

            def acknowledge_all():
                """关闭提示，标记所有通知已读。"""
                _remove_failure_notice()
                self._failure_popup_shown = False
                for n in unread:
                    self._mark_failure_notification_read(n.get('task', ''))

            put_buttons(
                [
                    {"label": "去处理", "value": "handle", "color": "danger"},
                    {"label": "全部已知晓", "value": "ack", "color": "secondary"},
                ],
                onclick=[_go_handle_first, acknowledge_all],
                small=True,
                scope=f"{notice_id}_actions",
            )

    def _mark_failure_notification_read(self, task: str) -> None:
        """将指定任务的失败保护通知标记为已读。

        Args:
            task: 任务名称。
        """
        if not self.alas_name:
            return
        filepath = os.path.join('./log', f'{self.alas_name}.task_failure.json')
        if not os.path.exists(filepath):
            return
        try:
            with open(filepath, encoding='utf-8') as f:
                data = json.load(f)
            changed = False
            for n in data.get('notifications', []):
                if n.get('task') == task and not n.get('read', False):
                    n['read'] = True
                    changed = True
            if changed:
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f'[WebUI] 标记失败保护通知已读失败: {e}')

    def _update_dashboard(self, num=None, groups_to_display=None):
        x = 0
        _num = 10000 if num is None else num
        _arg_group = (
            self._log.dashboard_arg_group
            if groups_to_display is None
            else groups_to_display
        )
        time_now = current_time().replace(microsecond=0)
        for group_name in _arg_group:
            group = LogRes(self.alas_config).group(group_name)
            if group is None:
                continue

            value = str(group["Value"])
            value_total = ""
            if "Limit" in group.keys():
                value_limit = f" / {group['Limit']}"
            elif "Total" in group.keys():
                value_total = f" ({group['Total']})"
                value_limit = ""
            elif group_name == "Pt":
                value_limit = " / " + re.sub(
                    r'[,.\'"，。]',
                    "",
                    str(
                        deep_get(
                            self.alas_config.data, "EventGeneral.EventGeneral.PtLimit"
                        )
                    ),
                )
                if value_limit == " / 0":
                    value_limit = ""
            else:
                value_limit = ""
                value_total = ""

            value_time = group["Record"]
            if value_time is None or value_time == datetime(2020, 1, 1, 0, 0, 0):
                value_time = datetime(2023, 1, 1, 0, 0, 0)

            # Handle time delta
            if value_time == datetime(2023, 1, 1, 0, 0, 0):
                value = "None"
                delta = timedelta_to_text()
            else:
                delta = timedelta_to_text(time_delta(value_time - time_now))

            if group_name not in self._log.last_display_time.keys():
                self._log.last_display_time[group_name] = ""
            if (
                self._log.last_display_time[group_name] == delta
                and not self._log.first_display
            ):
                continue
            self._log.last_display_time[group_name] = delta

            # if self._log.first_display:
            # Handle width
            # value_width = len(value) * 0.7 + 0.6 if value != 'None' else 4.5
            # value_width = str(value_width/1.12) + 'rem' if self.is_mobile else str(value_width) + 'rem'
            value_limit = "" if value == "None" else value_limit
            # limit_width = len(value_limit) * 0.7
            # limit_width = str(limit_width) + 'rem'
            value_total = "" if value == "None" else value_total
            limit_style = (
                "--dashboard-limit--" if value_limit else "--dashboard-total--"
            )
            value_limit = value_limit if value_limit else value_total
            # Handle dot color
            # 旧配置可能缺少颜色字段，仍渲染条目而不是中断整个仪表盘刷新。
            color_value = deep_get(group, "Color") or ""
            _color = f"background-color:{color_value.replace('^', '#')}"
            color = f'<div class="status-point" style={_color}>'
            # 使用集中管理的辅助函数生成 scope_id，确保命名一致性和安全性
            scope_id = get_dashboard_scope_id(group_name)
            with use_scope(scope_id, clear=True):
                put_row(
                    [
                        put_html(color),
                        put_scope(
                            get_group_scope_id(group_name),
                            [
                                put_column(
                                    [
                                        put_row(
                                            [
                                                put_text(value).style(
                                                    f"--dashboard-value--"
                                                ),
                                                put_text(value_limit).style(
                                                    limit_style
                                                ),
                                            ],
                                        ).style(
                                            "grid-template-columns:min-content auto;align-items: baseline;"
                                        ),
                                        put_text(
                                            t(f"Gui.Dashboard.{group_name}")
                                            + " - "
                                            + delta
                                        ).style("---dashboard-help--"),
                                    ],
                                    size="auto auto",
                                ),
                            ],
                        ),
                    ],
                    size="20px 1fr",
                ).style("height: 1fr")
            x += 1
            if x >= _num:
                break
        if self._log.first_display:
            self._log.first_display = False

    def alas_update_dashboard(self, _clear=False):
        if not self.visible or self.page != "Overview":
            return
        config_name = self.alas_name
        with self._page_lock:
            if (
                self.alas_name != config_name
                or self.page != "Overview"
                or not self._overview_dom_ready()
            ):
                return
            with use_scope("dashboard", clear=_clear):
                if not self._log.display_dashboard:
                    self._update_dashboard(
                        num=4, groups_to_display=["Oil", "Coin", "Gem", "Pt"]
                    )
                elif self._log.display_dashboard:
                    self._update_dashboard()
