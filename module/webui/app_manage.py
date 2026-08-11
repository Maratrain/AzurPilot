"""WebUI实例管理页面"""

from typing import TYPE_CHECKING

from module.webui.app_dependencies import (
    Any,
    Dict,
    IS_ON_PHONE_CLOUD,
    List,
    Optional,
    ProcessManager,
    State,
    actions,
    alas_instance,
    alas_template,
    cast,
    clear,
    download,
    eval_js,
    file_upload,
    filepath_args,
    filepath_config,
    get_config_mod,
    input_group,
    json,
    load_config,
    os,
    parse_task_priority,
    partial,
    pin,
    put_button,
    put_buttons,
    put_column,
    put_error,
    put_input,
    put_row,
    put_scope,
    put_select,
    put_text,
    put_warning,
    read_file,
    t,
    task_priority_from_config,
    toast,
    use_scope,
)

if TYPE_CHECKING:
    from module.webui.app import AlasGUI


def app_manage(gui: "AlasGUI") -> None:
    """显示实例创建、导入、导出和删除管理页。

    Args:
        gui: 当前 WebUI 会话对象。
    """
    expanded_summaries: set[str] = set()

    def _read_config_mapping(path: str) -> Dict[str, Any]:
        """读取配置 JSON，并确保根节点是对象。"""
        data = read_file(path)
        if not isinstance(data, dict):
            raise ValueError(f"配置文件根节点不是对象：{path}")
        return cast(Dict[str, Any], data)

    def _show_legacy_import_result():
        raw = eval_js(
            "(function(){var r=sessionStorage.getItem('import_msg');"
            "if(r){sessionStorage.removeItem('import_msg');return r;}"
            "return null;})()"
        )
        if not isinstance(raw, str):
            return
        try:
            result = json.loads(raw)
        except TypeError, ValueError:
            return
        if not isinstance(result, dict):
            return
        legacy_result = cast(Dict[str, Any], result)
        if legacy_result.get("ok"):
            toast(
                t("Gui.AppManage.ImportLegacySuccess"),
                color="success",
                duration=10,
            )
        else:
            toast(
                t(
                    "Gui.AppManage.ImportLegacyFailed",
                    error=legacy_result.get(
                        "error", t("Gui.AppManage.ImportLegacyUnknownError")
                    ),
                ),
                color="error",
                duration=10,
            )

    def get_unused_name():
        all_name = alas_instance()
        for i in range(2, 100):
            if f"alas{i}" not in all_name:
                return f"alas{i}"
        return ""

    def validate_name(name: str):
        if name in alas_instance():
            return t("Gui.AppManage.NameExist")
        if set(name) & set(".\\/:*?\"'<>|"):
            return t("Gui.AppManage.InvalidChar")
        if name.lower().startswith("template"):
            return t("Gui.AppManage.InvalidPrefixTemplate")
        return None

    def _export(config_name: str):
        mod_name = get_config_mod(config_name)
        if mod_name == "alas":
            filename = f"{config_name}.json"
        else:
            filename = f"{config_name}.{mod_name}.json"
        with open(filepath_config(config_name, mod_name), "rb") as f:
            download(filename, f.read())

    def _get_enabled_tasks(config_name: str, mod_name: str) -> List[str]:
        config = _read_config_mapping(filepath_config(config_name, mod_name))
        args = _read_config_mapping(filepath_args("args", mod_name))
        priority = parse_task_priority(task_priority_from_config(config, args))

        enabled: List[str] = []
        for task_data in config.values():
            if not isinstance(task_data, dict):
                continue
            scheduler = task_data.get("Scheduler")
            if not isinstance(scheduler, dict) or scheduler.get("Enable") is not True:
                continue
            command = scheduler.get("Command")
            if isinstance(command, str) and command and command not in enabled:
                enabled.append(command)

        enabled_set = set(enabled)
        ordered = [task for task in priority if task in enabled_set]
        ordered.extend(task for task in enabled if task not in ordered)
        return ordered

    def _toggle_summary(config_name: str, index: int):
        summary_scope = f"manage_config_summary_{index}"
        if config_name in expanded_summaries:
            expanded_summaries.remove(config_name)
            clear(summary_scope)
            _render_config_actions(config_name, index)
            return

        mod_name = get_config_mod(config_name)
        try:
            tasks = _get_enabled_tasks(config_name, mod_name)
        except (OSError, ValueError) as e:
            toast(
                t("Gui.AppManage.SummaryLoadFailed", error=e),
                color="error",
            )
            return

        expanded_summaries.add(config_name)
        with use_scope(summary_scope, clear=True):
            summary_content = [
                put_text(f"{t('Gui.AppManage.EnabledTasks')}: {len(tasks)}").style(
                    "--manage-summary-title--"
                ),
                put_text(t("Gui.AppManage.SchedulerOrderHint")).style(
                    "--manage-summary-hint--"
                ),
            ]
            if not tasks:
                summary_content.append(
                    put_text(t("Gui.Overview.NoTask")).style("--manage-summary-empty--")
                )
            else:
                for task_index, task in enumerate(tasks, start=1):
                    summary_content.append(
                        put_row(
                            [
                                put_text(str(task_index)).style(
                                    "--manage-summary-rank--"
                                ),
                                put_column(
                                    [
                                        put_text(t(f"Task.{task}.name")).style(
                                            "--manage-summary-name--"
                                        ),
                                        put_text(task).style("--manage-summary-code--"),
                                    ],
                                    size="auto auto",
                                ),
                            ],
                            size="2.25rem minmax(0, 1fr)",
                        ).style("--manage-summary-task--")
                    )
            put_column(summary_content).style("--manage-summary-panel--")
        _render_config_actions(config_name, index)

    def _render_config_actions(config_name: str, index: int):
        action_scope = f"manage_config_actions_{index}"
        with use_scope(action_scope, clear=True):
            put_buttons(
                buttons=[
                    {
                        "label": t(
                            "Gui.AppManage.Collapse"
                            if config_name in expanded_summaries
                            else "Gui.AppManage.Summary"
                        ),
                        "value": "summary",
                        "color": "primary",
                    },
                    {
                        "label": t("Gui.AppManage.Export"),
                        "value": "export",
                        "color": "primary",
                    },
                    {
                        "label": t("Gui.AppManage.Delete"),
                        "value": "delete",
                        "color": "danger",
                        "disabled": IS_ON_PHONE_CLOUD,
                    },
                ],
                onclick=[
                    partial(_toggle_summary, config_name, index),
                    partial(_export, config_name),
                    partial(_delete, config_name),
                ],
            ).style("--manage-config-actions--")

    def _delete_block_reason(config_name: str) -> Optional[str]:
        if len(alas_instance()) <= 1:
            return t("Gui.AppManage.DeleteLast")
        if ProcessManager.is_running(config_name):
            return t("Gui.AppManage.DeleteRunning", name=config_name)
        return None

    def _delete(config_name: str):
        if IS_ON_PHONE_CLOUD:
            return

        reason = _delete_block_reason(config_name)
        if reason:
            toast(reason, color="warning")
            return

        resp = input_group(
            label=f"{t('Gui.AppManage.Delete')}: {config_name}",
            inputs=[
                actions(
                    name="action",
                    label=t("Gui.AppManage.DeleteConfirm", name=config_name),
                    buttons=[
                        {
                            "label": t("Gui.AppManage.Delete"),
                            "value": "confirm",
                            "type": "submit",
                            "color": "danger",
                        },
                        {
                            "label": t("Gui.AppManage.Back"),
                            "type": "cancel",
                            "color": "light",
                        },
                    ],
                )
            ],
        )
        if resp is None:
            return

        reason = _delete_block_reason(config_name)
        if reason:
            toast(reason, color="warning")
            return

        mod_name = get_config_mod(config_name)
        try:
            os.remove(filepath_config(config_name, mod_name))
        except OSError as e:
            toast(
                t("Gui.AppManage.DeleteFailed", error=e),
                color="error",
            )
            return

        ProcessManager.remove_manager(config_name)
        gui.refresh_aside_instances(force=True)
        toast(
            t("Gui.AppManage.DeleteSuccess", name=config_name),
            color="success",
        )
        _show_list()

    @use_scope("content", clear=True)
    def _show_list():
        expanded_summaries.clear()
        gui.init_menu(name="ManageList")
        gui.set_title(t("Gui.AppManage.PageTitle"))
        put_scope("manage_config_list")
        with use_scope("manage_config_list"):
            for index, name in enumerate(alas_instance()):
                mod_name = get_config_mod(name)
                action_scope = f"manage_config_actions_{index}"
                summary_scope = f"manage_config_summary_{index}"
                put_scope(
                    f"manage_config_card_{index}",
                    [
                        put_row(
                            [
                                put_column(
                                    [
                                        put_text(name).style("--manage-config-name--"),
                                        put_text(
                                            f"{t('Gui.AppManage.Mod')}: {mod_name}"
                                        ).style("--manage-config-meta--"),
                                    ],
                                    size="auto auto",
                                ).style("--manage-config-identity--"),
                                put_scope(action_scope),
                            ],
                            size="minmax(0, 1fr) auto",
                        ).style("--manage-config-row--"),
                        put_scope(summary_scope),
                    ],
                ).style("--manage-config-card--")
                _render_config_actions(name, index)

    def _create():
        name = cast(str, pin["ManageNew_name"])
        origin = cast(str, pin["ManageNew_copyfrom"])
        clear("manage_add_feedback")
        gui.pin_remove_invalid_mark("ManageNew_name")

        error = validate_name(name)
        if error:
            gui.pin_set_invalid_mark("ManageNew_name")
            put_error(error, scope="manage_add_feedback")
            return

        config = load_config(origin).read_file(origin)
        State.config_updater.write_file(name, config, get_config_mod(origin))
        toast(t("Gui.AppManage.NewSuccess"), color="success")
        gui.refresh_aside_instances(force=True)
        _show_list()

    @use_scope("content", clear=True)
    def _show_new():
        gui.init_menu(name="ManageNew", skip_clear=True)
        gui.set_title(t("Gui.AppManage.TitleNew"))
        put_scope("manage_add_form")
        with use_scope("manage_add_form"):
            put_input(
                name="ManageNew_name",
                label=t("Gui.AppManage.NewName"),
                value=get_unused_name(),
            )
            put_select(
                name="ManageNew_copyfrom",
                label=t("Gui.AppManage.CopyFrom"),
                options=alas_template() + alas_instance(),
                value="template-alas",
            )
            put_scope("manage_add_feedback")
            put_buttons(
                buttons=[
                    {
                        "label": t("Gui.AddAlas.Confirm"),
                        "value": "confirm",
                        "color": "on",
                    },
                    {
                        "label": t("Gui.AppManage.Back"),
                        "value": "back",
                        "color": "off",
                    },
                ],
                onclick=[_create, _show_list],
            )

    def _import():
        resp = cast(
            Optional[Dict[str, Any]],
            input_group(
                label=t("Gui.AppManage.Import"),
                inputs=[
                    file_upload(
                        label=t("Gui.AppManage.Import"),
                        name="file",
                        placeholder=t("Gui.Text.ChooseFile"),
                        help_text=t("Gui.AppManage.OverrideWarning"),
                        accept=".json",
                        required=True,
                        max_size="1M",
                    ),
                    actions(
                        name="action",
                        buttons=[
                            {
                                "label": t("Gui.AppManage.Import"),
                                "value": "confirm",
                                "type": "submit",
                                "color": "primary",
                            },
                            {
                                "label": t("Gui.AppManage.Back"),
                                "type": "cancel",
                                "color": "light",
                            },
                        ],
                    ),
                ],
            ),
        )

        if resp is None:
            return

        upload = cast(Dict[str, Any], resp["file"])
        file = cast(bytes, upload["content"])
        file_name = cast(str, upload["filename"])

        if IS_ON_PHONE_CLOUD:
            config_name = mod_name = "alas"
        elif len(file_name.split(".")) == 2:
            config_name, _ = file_name.split(".")
            mod_name = "alas"
        else:
            config_name, mod_name, _ = file_name.rsplit(".", maxsplit=2)

        config = cast(Dict[str, Any], json.loads(file.decode(encoding="utf-8")))
        State.config_updater.write_file(config_name, config, mod_name)
        toast(t("Gui.AppManage.ImportSuccess"), color="success")

        gui.refresh_aside_instances(force=True)
        _show_list()

    @use_scope("content", clear=True)
    def _show_import():
        gui.init_menu(name="ManageImport")
        gui.set_title(t("Gui.AppManage.Import"))
        put_scope("manage_import_panel")
        with use_scope("manage_import_panel"):
            put_warning(t("Gui.AppManage.OverrideWarning"), closable=False)
            put_button(
                t("Gui.Text.ChooseFile"),
                onclick=_import,
                color="on",
            )

    with use_scope("menu", clear=True):
        put_button(
            t("Gui.AppManage.Name"),
            onclick=_show_list,
            color="menu",
        ).style("--menu-ManageList--")
        put_button(
            t("Gui.AppManage.New"),
            onclick=_show_new,
            color="menu",
            disabled=IS_ON_PHONE_CLOUD,
        ).style("--menu-ManageNew--")
        put_button(
            t("Gui.AppManage.Import"),
            onclick=_show_import,
            color="menu",
        ).style("--menu-ManageImport--")
        put_button(
            t("Gui.AppManage.ImportLegacy"),
            onclick=gui.ui_import_legacy,
            color="menu",
        ).style("--menu-ManageImportLegacy--")

    _show_legacy_import_result()
    _show_list()
