"""从原 app.py 提取普通 WebUI 模块的临时工具。"""

import argparse
import ast
import sys
from pathlib import Path


SOURCE = Path("module/webui/app.py")


def lines_for(source_lines, node):
    return source_lines[node.lineno - 1 : node.end_lineno]


def strip_indent(lines, width):
    prefix = " " * width
    return "\n".join(
        line[width:] if line.startswith(prefix) else line for line in lines
    ).rstrip()


def code_for_class(source_lines, node):
    return strip_indent(lines_for(source_lines, node), max(node.col_offset - 4, 0))


def code_for_module(source_lines, node):
    return strip_indent(lines_for(source_lines, node), node.col_offset)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("mixin", "top", "shared", "helpers"))
    parser.add_argument("filename")
    parser.add_argument("--class-name")
    parser.add_argument("--doc", required=True)
    parser.add_argument("--names", default="")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    source = SOURCE.read_text(encoding="utf-8")
    source_lines = source.splitlines()
    tree = ast.parse(source)
    gui = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AlasGUI"
    )
    methods = [node for node in gui.body if isinstance(node, ast.FunctionDef)]
    top_functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    wanted = set(filter(None, args.names.split(",")))

    original_imports = source_lines[2:133]
    import_tree = ast.parse("\n".join(original_imports))
    dependency_names = set()
    for node in import_tree.body:
        if isinstance(node, ast.Import):
            dependency_names.update(
                alias.asname or alias.name.split(".")[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            dependency_names.update(alias.asname or alias.name for alias in node.names)
    helper_names = {
        "DEMO_DEVICE_ID_TEXT",
        "build_copyable_device_id",
        "build_muted_notice",
        "build_recommendation_box",
        "build_simple_table",
        "build_title_block",
        "ensure_public_webui_password",
        "generate_webui_password",
        "is_demo_mode",
        "is_public_webui_host",
        "is_webui_password_set",
        "read_webapp_template",
        "timedelta_to_text",
    }

    def used_names(code):
        parsed = ast.parse(
            strip_indent(code.splitlines(), 4)
            if code.splitlines() and code.splitlines()[0].startswith("    ")
            else code
        )
        return {
            node.id
            for node in ast.walk(parsed)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }

    def imports_for(code):
        names = used_names(code)
        dependencies = sorted(names & dependency_names)
        helpers = sorted(names & helper_names)
        blocks = []
        if dependencies:
            blocks.append(
                "from module.webui.app_dependencies import (\n"
                + "".join(f"    {name},\n" for name in dependencies)
                + ")"
            )
        if helpers:
            blocks.append(
                "from module.webui.app_helpers import (\n"
                + "".join(f"    {name},\n" for name in helpers)
                + ")"
            )
        return "\n\n".join(blocks)

    if args.mode == "shared":
        content = (
            '"""WebUI 视图的共享依赖和一次性运行时初始化。"""\n\n'
            + "\n".join(original_imports)
            + "\n\npatch_executor()\npatch_mimetype()\nfix_py37_subprocess_communicate()\n\n"
            + "task_handler = TaskHandler()\n"
            + 'RESTRICTED_DEVICE_IDS = {"1", "2"}\n'
            + 'RESTRICTED_DEVICE_MESSAGE = "你的公网IP已泄露 请加群https://join.nanoda.work/#/join联系我们解除安全限制"\n'
            + 'PUBLIC_WEBUI_PASSWORD_GENERATE_FAILED_MESSAGE = "当前配置允许所有设备访问，但自动生成密码失败，请手动在 config/deploy.yaml 设置 Password 后重启。"\n'
        )
    elif args.mode == "helpers":
        nodes = [node for node in top_functions if node.lineno < gui.lineno]
        code = "\n\n".join(code_for_module(source_lines, node) for node in nodes)
        content = (
            '"""WebUI 的安全判断、模板读取和轻量 HTML 构造函数。"""\n\n'
            + imports_for(code)
            + '\n\nWEBUI_AUTO_PASSWORD_FILE = "password.txt"\n'
            + 'DEMO_DEVICE_ID_TEXT = "此程序是为了演示用途构建的版本/This application is a version built for demonstration purposes."\n\n'
            + code
            + "\n"
        )
    elif args.mode == "mixin":
        nodes = [node for node in methods if node.name in wanted]
        code = "\n\n".join(code_for_class(source_lines, node) for node in nodes)
        content = (
            f'"""{args.doc}"""\n\n'
            + imports_for(code)
            + "\n\n"
            + f"class {args.class_name}:\n"
            + f'    """{args.doc}"""\n\n'
            + code
            + "\n"
        )
    else:
        nodes = [node for node in top_functions if node.name in wanted]
        code = "\n\n".join(code_for_module(source_lines, node) for node in nodes)
        content = f'"""{args.doc}"""\n\n' + imports_for(code) + "\n\n" + code + "\n"

    ast.parse(content, filename=args.filename)
    patch = ["*** Begin Patch", f"*** Add File: {args.filename}"]
    patch.extend("+" + line for line in content.splitlines())
    patch.append("*** End Patch")
    print("\n".join(patch))


if __name__ == "__main__":
    main()

