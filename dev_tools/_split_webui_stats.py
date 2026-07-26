"""从原 app.py 提取统计视图的临时工具。"""

import ast
import re
import sys
from pathlib import Path


SOURCE = Path("module/webui/app.py")


def raw(lines, node):
    return "\n".join(lines[node.lineno - 1 : node.end_lineno]).rstrip()


def strip(lines, width):
    prefix = " " * width
    return "\n".join(
        line[width:] if line.startswith(prefix) else line for line in lines
    ).rstrip()


def class_code(lines, node):
    return strip(raw(lines, node).splitlines(), max(node.col_offset - 4, 0))


def without_ranges(lines, node, ranges):
    selected = [
        lines[number - 1]
        for number in range(node.lineno, node.end_lineno + 1)
        if not any(start <= number <= end for start, end in ranges)
    ]
    return strip(selected, max(node.col_offset - 4, 0))


def self_reference(code, old, new=None):
    new = new or old
    code = re.sub(rf"(?<![\w.]){re.escape(old)}\b", f"self.{new}", code)
    pattern = re.compile(
        rf"^(?P<indent>\s*)def self\.{re.escape(new)}\((.*)\):", re.MULTILINE
    )

    def header(match):
        args = match.group(2).strip()
        return f"{match.group(1)}def {new}({'self, ' + args if args else 'self'}):"

    return pattern.sub(header, code, count=1)


def main(target):
    sys.stdout.reconfigure(encoding="utf-8")
    source = SOURCE.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source)
    gui = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AlasGUI"
    )
    stat_page = next(
        node for node in gui.body if isinstance(node, ast.FunctionDef) and node.name == "alas_set_stat"
    )
    imports = "\n".join(lines[2:133])
    import_tree = ast.parse(imports)
    dependency_names = set()
    for node in import_tree.body:
        if isinstance(node, ast.Import):
            dependency_names.update(
                alias.asname or alias.name.split(".")[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            dependency_names.update(alias.asname or alias.name for alias in node.names)
    helper_names = {
        "build_muted_notice",
        "build_simple_table",
        "build_title_block",
        "read_webapp_template",
    }

    def import_block(code):
        parsed = ast.parse(strip(code.splitlines(), 4))
        names = {
            node.id
            for node in ast.walk(parsed)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        blocks = []
        deps = sorted(names & dependency_names)
        helpers = sorted(names & helper_names)
        if deps:
            blocks.append(
                "from module.webui.app_dependencies import (\n"
                + "".join(f"    {name},\n" for name in deps)
                + ")"
            )
        if helpers:
            blocks.append(
                "from module.webui.app_helpers import (\n"
                + "".join(f"    {name},\n" for name in helpers)
                + ")"
            )
        return "\n\n".join(blocks)

    def mixin(doc, name, code):
        return (
            f'"""{doc}"""\n\n'
            + import_block(code)
            + "\n\n"
            + f"class {name}:\n"
            + f'    """{doc}"""\n\n'
            + code
            + "\n"
        )

    children = {
        node.name: node
        for node in stat_page.body
        if isinstance(node, ast.FunctionDef)
    }
    ap = children["_render_ap_chart"]
    opsi = children["_render_opsi_stats"]

    if target == "action_point":
        code = self_reference(
            without_ranges(lines, ap, [(1090, 1342)]), "_render_ap_chart"
        ).replace(
            "            run_js(js_code)\n",
            "            run_js(js_code)\n            self._render_ap_chart_toolbar(current_view, chart_id)\n",
            1,
        )
        filename = "module/webui/app_stat_action_point.py"
        content = mixin(
            "WebUI 体力趋势图的数据装配和图表渲染。",
            "ActionPointStatisticsMixin",
            code,
        )
    elif target == "action_point_toolbar":
        toolbar = "    def _render_ap_chart_toolbar(self, current_view, chart_id):\n"
        toolbar += strip(lines[1089:1342], 8)
        toolbar = re.sub(
            r"(?<![\w.])_render_ap_chart\b", "self._render_ap_chart", toolbar
        )
        filename = "module/webui/app_stat_action_point_toolbar.py"
        content = mixin(
            "WebUI 体力趋势图的视图切换工具栏。",
            "ActionPointToolbarMixin",
            toolbar,
        )
    elif target == "resource":
        code = self_reference(class_code(lines, children["_render_resource_chart"]), "_render_resource_chart")
        filename = "module/webui/app_stat_resource.py"
        content = mixin("WebUI 全资源趋势图视图。", "ResourceStatisticsMixin", code)
    elif target == "opsi":
        code = self_reference(
            without_ranges(lines, opsi, [(1843, 1883), (1887, 2029)]),
            "_render_opsi_stats",
        )
        code = re.sub(
            r"(?<![\w.])_render_meowofficer_farming\b",
            "self._render_meowofficer_farming",
            code,
        )
        code = re.sub(r"(?<![\w.])export_opsi_csv\b", "self._export_opsi_csv", code)
        filename = "module/webui/app_stat_opsi.py"
        content = mixin("WebUI 大世界统计视图。", "OpsiStatisticsMixin", code)
    elif target == "opsi_export":
        nested = {
            node.name: node
            for node in ast.walk(opsi)
            if isinstance(node, ast.FunctionDef) and node is not opsi
        }
        code = "\n\n".join(
            [
                self_reference(
                    class_code(lines, nested["_refresh_meowofficer_farming"]),
                    "_render_meowofficer_farming",
                ),
                self_reference(
                    class_code(lines, nested["_render_meowofficer_farming"]),
                    "_refresh_meowofficer_farming",
                ),
                self_reference(
                    class_code(lines, nested["export_opsi_csv"]),
                    "export_opsi_csv",
                    "_export_opsi_csv",
                ),
            ]
        )
        filename = "module/webui/app_stat_opsi_export.py"
        content = mixin("WebUI 短猫收益刷新和大世界统计导出。", "OpsiExportMixin", code)
    elif target == "ship":
        code = self_reference(class_code(lines, children["_render_ship_exp"]), "_render_ship_exp")
        filename = "module/webui/app_stat_ship.py"
        content = mixin("WebUI 舰船经验统计视图。", "ShipExperienceStatisticsMixin", code)
    elif target == "commission":
        code = self_reference(
            class_code(lines, children["_render_commission_income"]),
            "_render_commission_income",
        )
        filename = "module/webui/app_stat_commission.py"
        content = mixin("WebUI 委托收益统计视图。", "CommissionIncomeStatisticsMixin", code)
    else:
        raise SystemExit(f"未知目标: {target}")

    ast.parse(content, filename=filename)
    patch = ["*** Begin Patch", f"*** Add File: {filename}"]
    patch.extend("+" + line for line in content.splitlines())
    patch.append("*** End Patch")
    print("\n".join(patch))


if __name__ == "__main__":
    main(sys.argv[1])
