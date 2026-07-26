"""WebUI活动计算器和大世界模拟器"""

from module.webui.app_dependencies import (
    Any,
    BinarySwitchButton,
    Dict,
    List,
    Optional,
    ProcessManager,
    RichLog,
    base64,
    build_error_html,
    build_event_calculator_html,
    build_event_calculator_js,
    cast,
    current_time,
    datetime,
    deep_get,
    eval_js,
    json,
    load_event_calculator,
    logger,
    pin,
    put_button,
    put_html,
    put_row,
    put_scope,
    put_text,
    re,
    run_js,
    t,
    to_pin_value,
    toast,
    use_scope,
)


from module.webui.app_types import WebUIMixinBase


class EventToolsMixin(WebUIMixinBase):
    """WebUI活动计算器和大世界模拟器"""

    def _event_calculator_scope_id(self) -> str:
        name = re.sub(r"[^0-9A-Za-z_]", "_", self.alas_name)
        return f"event_calculator_{name}"

    def _event_calculator_state(self) -> Optional[Dict[str, Any]]:
        scope_id = self._event_calculator_scope_id()
        return eval_js(
            """
            (window.alasEventCalculator
             && window.alasEventCalculator[scopeId]
             && window.alasEventCalculator[scopeId].getState())
             || null
            """,
            scopeId=scope_id,
        )

    @staticmethod
    def _format_event_end_time(date_text: str) -> Optional[str]:
        if not date_text:
            return None
        try:
            date = datetime.fromisoformat(date_text.replace("/", "-")).date()
        except ValueError:
            return None
        return f"{date.isoformat()} 00:00:00"

    def _save_event_calculator_result(
        self,
        *,
        save_target: bool,
        save_time: bool,
        save_shop_filter: bool = False,
    ) -> None:
        state = self._event_calculator_state()
        if not state:
            toast("活动计算器还没有加载完成", color="warning")
            return

        modified: Dict[str, Any] = {}
        if save_target:
            target = int(state.get("target") or 0)
            modified["EventGeneral.EventGeneral.PtLimit"] = target
        if save_time:
            end_time = self._format_event_end_time(state.get("endDate") or "")
            if end_time is None:
                toast("活动结束日期无效", color="warning")
                return
            modified["EventGeneral.EventGeneral.TimeLimit"] = end_time
        if save_shop_filter:
            filters = state.get("shopFilter") or []
            if not filters:
                toast("没有可写入的商店过滤器项目", color="warning")
                return
            missing = state.get("shopFilterMissing") or []
            modified["EventShop.EventShop.PresetFilter"] = "custom"
            modified["EventShop.EventShop.CustomFilter"] = " > ".join(filters)
            if missing:
                toast(
                    "以下兑换项暂未映射到过滤器：" + "、".join(missing),
                    color="warning",
                    duration=6,
                )

        if not modified:
            return
        self._save_config(modified, self.alas_name, self.alas_config)
        for key, value in modified.items():
            pin["_".join(key.split("."))] = to_pin_value(value)
        self.alas_config.load()

    @staticmethod
    def _is_task_enabled(config: Dict[str, Any], task: str) -> bool:
        return bool(deep_get(config, f"{task}.Scheduler.Enable", False))

    @staticmethod
    def _is_task_done_today(config: Dict[str, Any], task: str) -> bool:
        next_run = deep_get(config, f"{task}.Scheduler.NextRun")
        if not isinstance(next_run, datetime):
            return False
        return next_run.date() > current_time().date()

    @staticmethod
    def _split_stage_filter(value: Any) -> List[str]:
        return [
            item.strip().upper()
            for item in str(value or "").replace("\n", ">").split(">")
            if item.strip()
        ]

    def _event_calculator_defaults(
        self, config: Dict[str, Any], wiki_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        daily: Dict[str, Dict[str, bool]] = {}

        def set_daily(name: str, will_do: bool, already: bool = False) -> None:
            if name:
                daily[name] = {"never": not will_do, "already": already}

        gacha_value = deep_get(config, "Gacha.Gacha.Amount", 0)
        if isinstance(gacha_value, (bool, int, float, str)):
            try:
                gacha_amount = int(gacha_value or 0)
            except TypeError, ValueError, OverflowError:
                gacha_amount = 0
        else:
            gacha_amount = 0
        set_daily(
            "建造3次",
            self._is_task_enabled(config, "Gacha") and gacha_amount >= 3,
            self._is_task_done_today(config, "Gacha"),
        )
        set_daily(
            "出击胜利15次",
            self._is_task_enabled(config, "Daily"),
            self._is_task_done_today(config, "Daily"),
        )
        set_daily(
            "通关1次困难关卡",
            self._is_task_enabled(config, "Hard"),
            self._is_task_done_today(config, "Hard"),
        )

        extra: Dict[str, Dict[str, bool]] = {}
        wiki_extra = wiki_data.get("extra", [])
        if isinstance(wiki_extra, list):
            for item in wiki_extra:
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                if isinstance(name, str) and name:
                    extra[name] = {"never": True, "already": False}
        for task in ("EventA", "EventB", "EventC", "EventD"):
            enabled = self._is_task_enabled(config, task)
            already = self._is_task_done_today(config, task)
            for stage in self._split_stage_filter(
                deep_get(config, f"{task}.EventDaily.StageFilter", "")
            ):
                if stage in extra:
                    extra[stage] = {"never": not enabled, "already": already}
        if "SP" in extra:
            extra["SP"] = {
                "never": not self._is_task_enabled(config, "EventSp"),
                "already": self._is_task_done_today(config, "EventSp"),
            }

        return {"daily": daily, "extra": extra}

    def _render_event_calculator(
        self, config: Dict[str, Any], force_refresh: bool = False
    ) -> None:
        scope_id = self._event_calculator_scope_id()
        with use_scope("group_EventCalculator", clear=True):
            put_text("活动计算器")
            put_text(
                "从碧蓝航线 Wiki 自动读取活动商店、结束日期和各图 PT，计算后可写回活动通用设置。"
            )
            put_html('<hr class="hr-group">')

            data = load_event_calculator(force_refresh=force_refresh)
            if data.get("error") and not data.get("shop_items"):
                put_html(build_error_html(data["error"]))
                put_button(
                    label="重新从 Wiki 拉取",
                    onclick=lambda: self._render_event_calculator(
                        self.alas_config.read_file(self.alas_name), True
                    ),
                    color="warning",
                )
                return

            target = deep_get(config, "EventGeneral.EventGeneral.PtLimit", 0) or 0
            if not target:
                target = data.get("shop_total", 0)
            end_date = data.get("end_date", "")
            current_time = deep_get(config, "EventGeneral.EventGeneral.TimeLimit")
            if isinstance(current_time, datetime) and current_time.year > 2023:
                end_date = current_time.date().isoformat()
            elif isinstance(current_time, str) and current_time[:4] not in (
                "2020",
                "2023",
            ):
                end_date = current_time[:10]

            initial = {
                "target": target,
                "owned": deep_get(config, "Dashboard.Pt.Value", 0) or 0,
                "end_date": end_date,
            }
            initial.update(self._event_calculator_defaults(config, data))
            put_html(build_event_calculator_html(scope_id))
            run_js(build_event_calculator_js(scope_id, data, initial))
            put_row(
                [
                    put_button(
                        label="刷新 Wiki 数据",
                        onclick=lambda: self._render_event_calculator(
                            self.alas_config.read_file(self.alas_name), True
                        ),
                        color="off",
                    ),
                    put_button(
                        label="写入目标 PT",
                        onclick=lambda: self._save_event_calculator_result(
                            save_target=True, save_time=False
                        ),
                        color="off",
                    ),
                    put_button(
                        label="写入结束时间",
                        onclick=lambda: self._save_event_calculator_result(
                            save_target=False, save_time=True
                        ),
                        color="off",
                    ),
                    put_button(
                        label="写入目标 PT 和结束时间",
                        onclick=lambda: self._save_event_calculator_result(
                            save_target=True, save_time=True
                        ),
                        color="off",
                    ),
                    put_button(
                        label="写入商店过滤器",
                        onclick=lambda: self._save_event_calculator_result(
                            save_target=False,
                            save_time=False,
                            save_shop_filter=True,
                        ),
                        color="off",
                    ),
                ],
                size="auto auto auto auto auto",
                scope=f"{scope_id}_write_actions",
            )

    def _os_simulator(self):
        self.simulator.set_config(self.alas_config)
        self._last_os_simulator_figure = None

        if self._simulator_logger_pm is None:

            class SimulatorLogger:
                def __init__(self):
                    self.renderables = []
                    self.renderables_max_length = 2000
                    self.renderables_reduce_length = 1000
                    self.renderables_total = 0

            self._simulator_logger_pm = SimulatorLogger()

        pm = self._simulator_logger_pm
        import logging

        class ListHandler(logging.Handler):
            """将模拟器日志转存到 WebUI 可消费的缓冲区。"""

            is_webui_simulator_handler: bool = True

            def emit(self, record: logging.LogRecord) -> None:
                msg = self.format(record)
                pm.renderables.append(msg + "\n")
                pm.renderables_total += 1
                if len(pm.renderables) > pm.renderables_max_length:
                    del pm.renderables[: pm.renderables_reduce_length]

        # Remove existing handlers to avoid duplication on page refresh
        for h in self.simulator.logger.handlers[:]:
            if getattr(h, "is_webui_simulator_handler", False):
                self.simulator.logger.removeHandler(h)

        handler = ListHandler()
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s.%(msecs)03d | %(levelname)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        self.simulator.logger.addHandler(handler)

        put_scope(
            "scheduler-bar",
            [
                put_text(t("Task.OpsiSimulator.name")).style(
                    "font-size: 1.25rem; margin: auto .5rem auto;"
                ),
                put_scope("scheduler_btn"),
            ],
        )

        put_scope("figure_display")

        put_scope(
            "logs",
            [
                put_scope(
                    "log-bar",
                    [
                        put_text(t("Gui.Overview.Log")).style(
                            "font-size: 1.25rem; margin: auto .5rem auto;"
                        ),
                        put_scope(
                            "log-bar-btns",
                            [
                                put_scope("log_scroll_btn"),
                                put_button(
                                    label="截图预览",
                                    onclick=lambda: run_js(
                                        f"window.alasToggleLivePreview({json.dumps(self.alas_name)});"
                                    ),
                                    color="off",
                                ),
                            ],
                        ),
                    ],
                ),
                put_scope("log-container", [put_scope("log", [put_html("")])]),
            ],
        )

        switch_scheduler = BinarySwitchButton(
            label_on=t("Gui.Button.Stop"),
            label_off=t("Gui.Button.Start"),
            onclick_on=self.simulator.interrupt,
            onclick_off=self._simulator_start,
            get_state=lambda: self.simulator.is_running,
            color_on="off",
            color_off="on",
            scope="scheduler_btn",
        )
        self.task_handler.add(switch_scheduler.g(), 1, True)

        log = RichLog("log")
        log.console.width = log.get_width()
        switch_log_scroll = BinarySwitchButton(
            label_on=t("Gui.Button.ScrollON"),
            label_off=t("Gui.Button.ScrollOFF"),
            onclick_on=lambda: log.set_scroll(False),
            onclick_off=lambda: log.set_scroll(True),
            get_state=lambda: log.keep_bottom,
            color_on="on",
            color_off="off",
            scope="log_scroll_btn",
        )
        self.task_handler.add(switch_log_scroll.g(), 1, True)

        def _update_simulator_figure():
            # Prevent flicker by checking if figure has changed
            last_figure = getattr(self, "_last_os_simulator_figure", None)
            if self.simulator.figure == last_figure:
                return

            figure_path = self.simulator.figure
            self._last_os_simulator_figure = figure_path

            if figure_path:
                try:
                    with open(figure_path, "rb") as f:
                        img_b64 = base64.b64encode(f.read()).decode("utf-8")
                    with use_scope("figure_display", clear=True):
                        put_html(
                            f'<img src="data:image/png;base64,{img_b64}" style="max-width: 100%; height: auto; display: block; margin: 0 auto;">'
                        )
                except FileNotFoundError:
                    # This can happen if the figure is deleted before it's read
                    with use_scope("figure_display", clear=True):
                        pass  # Clear the image
                except Exception as e:
                    logger.warning(f"[WebUI-活动工具] 更新模拟器图表失败: {e}")
            else:
                with use_scope("figure_display", clear=True):
                    pass  # Clear the image

        self.task_handler.add(_update_simulator_figure, 0.5, True)

        # RichLog 只读取日志缓冲字段；模拟器使用同构的轻量对象而非进程管理器。
        self.task_handler.add(log.put_log(cast(ProcessManager, pm)), 0.25, True)
