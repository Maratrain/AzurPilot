import unittest
from unittest.mock import Mock, patch

from module.webui.app_task_config import TaskConfigMixin
from module.webui.config_search import (
    ConfigSearchEntry,
    build_config_search_focus_script,
    build_config_search_result_click_script,
    config_search_config_signature,
    config_search_field_scope,
    normalize_search_text,
    search_config_entries,
    should_render_config_argument,
)


def make_entry(
    argument: str,
    argument_name: str,
    help_text: str = "",
    *,
    task: str = "Alas",
    group: str = "General",
    task_name: str = "任务",
    group_name: str = "通用",
) -> ConfigSearchEntry:
    """创建仅用于搜索逻辑测试的配置项。"""
    return ConfigSearchEntry(
        task=task,
        group=group,
        argument=argument,
        task_name=task_name,
        group_name=group_name,
        argument_name=argument_name,
        help_text=help_text,
    )


class TestWebUIConfigSearch(unittest.TestCase):
    def test_normalize_search_text_ignores_case_and_whitespace(self):
        self.assertEqual(normalize_search_text("  Package\n\t Name  "), "package name")
        self.assertEqual(normalize_search_text(None), "")

    def test_entry_exposes_full_technical_key(self):
        entry = make_entry(
            "PackageName",
            "客户端包名",
            task="Alas",
            group="Emulator",
        )

        self.assertEqual(entry.key, "Alas.Emulator.PackageName")

    def test_config_signature_changes_when_external_config_changes(self):
        visible_storage = {"Alas": {"Storage": {"Settings": {"enabled": True}}}}
        hidden_storage = {"Alas": {"Storage": {"Settings": {}}}}

        self.assertNotEqual(
            config_search_config_signature(visible_storage),
            config_search_config_signature(hidden_storage),
        )

    def test_search_matches_name_help_and_technical_key(self):
        by_name = make_entry("Path", "模拟器路径")
        by_help = make_entry(
            "Description",
            "说明",
            "用于  指定 Android\n应用标识。",
        )
        by_key = make_entry(
            "PackageName",
            "客户端包名",
            task="Alas",
            group="Emulator",
        )
        entries = [by_name, by_help, by_key]

        name_results, name_total = search_config_entries(entries, "模拟器")
        help_results, help_total = search_config_entries(entries, "ANDROID 应用标识")
        key_results, key_total = search_config_entries(entries, "packagename")

        self.assertEqual((name_results, name_total), ([by_name], 1))
        self.assertEqual((help_results, help_total), ([by_help], 1))
        self.assertEqual((key_results, key_total), ([by_key], 1))

    def test_search_sorts_by_match_field_relevance(self):
        primary_substring = make_entry("a", "before target after")
        help_match = make_entry("b", "帮助", "target")
        context_match = make_entry("c", "设置", task_name="target task")
        primary_exact = make_entry("d", "target")
        primary_prefix = make_entry("e", "target setting")

        results, total = search_config_entries(
            [
                primary_substring,
                help_match,
                context_match,
                primary_exact,
                primary_prefix,
            ],
            "target",
        )

        self.assertEqual(total, 5)
        self.assertEqual(
            results,
            [
                primary_exact,
                primary_prefix,
                primary_substring,
                context_match,
                help_match,
            ],
        )

    def test_search_limits_results_to_twenty_and_reports_full_total(self):
        entries = [
            make_entry(f"argument_{index}", f"setting {index}") for index in range(25)
        ]

        results, total = search_config_entries(entries, "setting")

        self.assertEqual(total, 25)
        self.assertEqual(len(results), 20)
        self.assertEqual(
            [entry.argument for entry in results],
            [f"argument_{index}" for index in range(20)],
        )

    def test_search_returns_no_results_for_blank_or_unmatched_query(self):
        entries = [make_entry("PackageName", "客户端包名")]

        self.assertEqual(search_config_entries(entries, "   "), ([], 0))
        self.assertEqual(search_config_entries(entries, "不存在的配置"), ([], 0))

    def test_should_render_config_argument_excludes_hidden_and_conditional_fields(self):
        self.assertFalse(
            should_render_config_argument(
                "Alas", "Emulator", "PackageName", "hide", "input", [], "com.example"
            )
        )
        self.assertFalse(
            should_render_config_argument(
                "Alas", "Storage", "Settings", None, "storage", [], {}
            )
        )
        self.assertFalse(
            should_render_config_argument(
                "GemsFarming", "Campaign", "Event", None, "select", ["active"], "active"
            )
        )
        self.assertTrue(
            should_render_config_argument(
                "GemsFarming",
                "Campaign",
                "Event",
                None,
                "select",
                ["active", "event"],
                "active",
            )
        )
        self.assertTrue(
            should_render_config_argument(
                "OtherTask", "Campaign", "Event", None, "select", ["active"], "active"
            )
        )
        self.assertTrue(
            should_render_config_argument(
                "Alas", "Storage", "Settings", None, "storage", [], {"enabled": True}
            )
        )

    def test_focus_script_targets_stable_field_scope_and_focuses_editable_control(self):
        scope = config_search_field_scope("Alas", "Emulator", "PackageName")
        script = build_config_search_focus_script(scope)

        self.assertEqual(scope, "config_search_field_Alas_Emulator_PackageName")
        self.assertIn(
            'document.getElementById("pywebio-scope-config_search_field_Alas_Emulator_PackageName")',
            script,
        )
        self.assertIn('document.getElementById("pywebio-scope-groups")', script)
        self.assertIn("container.scrollTo", script)
        self.assertIn('target.classList.add("config-search-target")', script)
        self.assertIn('target.classList.remove("config-search-target")', script)
        self.assertIn("}, 1800);", script)
        self.assertIn("input:not([disabled]):not([readonly])", script)
        self.assertIn(
            '.CodeMirror textarea:not([disabled]):not([readonly])', script
        )
        self.assertIn("candidate.getClientRects().length", script)
        self.assertIn('target.querySelector(".task-priority-list")', script)
        self.assertIn('control.closest(".bootstrap-select")', script)
        self.assertIn("selectButton.getClientRects().length", script)
        self.assertIn("focusControl.focus({preventScroll: true})", script)
        self.assertIn("focusControl.tabIndex = 0", script)
        self.assertIn("}, 360);", script)

    def test_result_click_script_uses_one_delegated_pin_callback(self):
        script = build_config_search_result_click_script("config_search_selection")

        self.assertIn("__alasConfigSearchResultClick", script)
        self.assertIn("window[listenerName]", script)
        self.assertIn("document.addEventListener(\"click\"", script)
        self.assertIn("data-config-search-key", script)
        self.assertIn("event.preventDefault()", script)
        self.assertIn('input[name="', script)
        self.assertIn("dispatchEvent(new Event(\"input\"", script)
        self.assertIn("dispatchEvent(new Event(\"change\"", script)

    def test_open_search_result_switches_task_before_running_focus_script(self):
        task_config = object.__new__(TaskConfigMixin)
        call_order = []
        task_config.alas_set_group = Mock(
            side_effect=lambda task: call_order.append(("alas_set_group", task))
        )
        entry = make_entry(
            "PackageName",
            "客户端包名",
            task="Alas",
            group="Emulator",
        )

        with patch(
            "module.webui.app_task_config.run_js",
            side_effect=lambda script: call_order.append(("run_js", script)),
        ) as run_js:
            task_config._open_config_search_result(entry)

        task_config.alas_set_group.assert_called_once_with("Alas")
        run_js.assert_called_once()
        self.assertEqual(call_order[0], ("alas_set_group", "Alas"))
        self.assertEqual(call_order[1][0], "run_js")
        script = call_order[1][1]
        self.assertIn("config_search_field_Alas_Emulator_PackageName", script)
        self.assertIn("config-search-target", script)
        self.assertIn("focus", script)

    def test_result_html_does_not_register_a_callback_per_entry(self):
        task_config = object.__new__(TaskConfigMixin)
        output = Mock()
        entry = make_entry(
            "PackageName",
            "客户端包名",
            task="Alas",
            group="Emulator",
        )

        with patch(
            "module.webui.app_task_config.put_html", return_value=output
        ) as put_html:
            task_config._put_config_search_result(entry)

        output.onclick.assert_not_called()
        self.assertIn(
            'data-config-search-key="Alas.Emulator.PackageName"',
            put_html.call_args.args[0],
        )

    def test_result_callback_only_opens_currently_visible_entry(self):
        task_config = object.__new__(TaskConfigMixin)
        entry = make_entry(
            "PackageName",
            "客户端包名",
            task="Alas",
            group="Emulator",
        )
        task_config._get_config_search_entries = Mock(return_value=[entry])
        task_config._open_config_search_result = Mock()

        task_config._on_config_search_result(entry.key)
        task_config._on_config_search_result("Alas.Emulator.NotVisible")

        task_config._open_config_search_result.assert_called_once_with(entry)

    def test_search_cache_rebuilds_after_external_config_change(self):
        task_config = object.__new__(TaskConfigMixin)
        task_config.alas_name = "alas"
        task_config.alas_mod = "alas"
        task_config.ALAS_ARGS = {}
        visible_entry = make_entry("Settings", "存储配置")
        visible_config = {"Alas": {"Storage": {"Settings": {"enabled": True}}}}
        hidden_config = {"Alas": {"Storage": {"Settings": {}}}}
        task_config.alas_config = Mock()
        task_config.alas_config.read_file.side_effect = [visible_config, hidden_config]
        task_config._build_config_search_entries = Mock(
            side_effect=[[visible_entry], []]
        )

        first = task_config._get_config_search_entries()
        second = task_config._get_config_search_entries()

        self.assertEqual(first, [visible_entry])
        self.assertEqual(second, [])
        self.assertEqual(task_config._build_config_search_entries.call_count, 2)

    def test_save_config_invalidates_search_cache_after_successful_write(self):
        task_config = object.__new__(TaskConfigMixin)
        task_config.ALAS_ARGS = {
            "Alas": {
                "Emulator": {
                    "PackageName": {"type": "input", "value": "com.example.old"}
                }
            }
        }
        task_config._config_search_entries = [
            make_entry("PackageName", "客户端包名", task="Alas", group="Emulator")
        ]
        task_config._config_search_signature = ("cached",)
        task_config.pin_remove_invalid_mark = Mock()
        task_config.pin_set_invalid_mark = Mock()
        config = {"Alas": {"Emulator": {"PackageName": "com.example.old"}}}
        config_updater = Mock()
        config_updater.read_file.return_value = config
        config_updater.save_callback.return_value = []

        with (
            patch(
                "module.webui.app_task_config.parse_pin_value",
                return_value="com.example.new",
            ),
            patch("module.webui.app_task_config.t", return_value="已保存"),
            patch("module.webui.app_task_config.toast"),
            patch("module.webui.app_task_config.logger"),
        ):
            task_config._save_config(
                {"Alas.Emulator.PackageName": "com.example.new"},
                "alas",
                config_updater,
            )

        config_updater.write_file.assert_called_once_with("alas", config)
        self.assertEqual(config["Alas"]["Emulator"]["PackageName"], "com.example.new")
        self.assertEqual(task_config._config_search_entries, [])
        self.assertIsNone(task_config._config_search_signature)


if __name__ == "__main__":
    unittest.main()
