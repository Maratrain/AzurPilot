"""WebUI 的安全判断、模板读取和轻量 HTML 构造函数。"""

from module.webui.app_dependencies import (
    Path,
    State,
    logger,
    os,
    secrets,
    string,
    t,
)

WEBUI_AUTO_PASSWORD_FILE = "password.txt"
DEMO_DEVICE_ID_TEXT = "此程序是为了演示用途构建的版本/This application is a version built for demonstration purposes."


def is_demo_mode():
    """
    判断是否处于演示环境。

    Returns:
        bool: True 表示 DEMO=1。
    """
    return os.environ.get("DEMO") == "1"


def is_public_webui_host(host):
    """
    判断 WebUI 是否监听所有网络接口。

    Args:
        host (str): WebUI 监听地址。

    Returns:
        bool: True 表示 WebUI 允许所有设备访问。
    """
    host = str(host or "").strip().lower()
    return host in ("0.0.0.0", "::", "[::]")


def is_webui_password_set(password):
    """
    判断 WebUI 密码是否有效设置。

    Args:
        password: WebUI 密码配置。

    Returns:
        bool: True 表示密码包含非空白字符。
    """
    return bool(str(password or "").strip())


def generate_webui_password(length=32):
    """
    生成包含大小写字母和数字的 WebUI 密码。

    Args:
        length (int): 密码长度。

    Returns:
        str: 随机密码。
    """
    letters_upper = string.ascii_uppercase
    letters_lower = string.ascii_lowercase
    digits = string.digits
    alphabet = letters_upper + letters_lower + digits
    password = [
        secrets.choice(letters_upper),
        secrets.choice(letters_lower),
        secrets.choice(digits),
    ]
    password.extend(secrets.choice(alphabet) for _ in range(length - len(password)))
    secrets.SystemRandom().shuffle(password)
    return "".join(password)


def ensure_public_webui_password(key):
    """
    公网监听且未设置密码时自动生成密码。

    Args:
        key: 命令行或部署配置中的 WebUI 密码。

    Returns:
        tuple[str | None, str | None]: 有效密码和失败原因。
    """
    if is_demo_mode():
        return key, None

    host = State.webui_host or State.deploy_config.WebuiHost
    if not is_public_webui_host(host) or is_webui_password_set(key):
        return key, None

    try:
        password = generate_webui_password()
        from deploy.atomic import atomic_write

        atomic_write(WEBUI_AUTO_PASSWORD_FILE, f"{password}\n")
        State.deploy_config.Password = password
        logger.warning(
            f"[WebUI] WebUI 已自动生成密码，请在根目录 {WEBUI_AUTO_PASSWORD_FILE} 查看。"
        )
        return password, None
    except Exception as e:
        logger.exception(f"WebUI 自动生成密码失败: {e}")
        return None, str(e)


def timedelta_to_text(delta=None):
    """将时间差数据转换为仪表盘本地化文本。

    Args:
        delta: 时间差字典或空值。

    Returns:
        str: 本地化相对时间。
    """
    time_delta_name_suffix_dict = {
        "Y": "YearsAgo",
        "M": "MonthsAgo",
        "D": "DaysAgo",
        "h": "HoursAgo",
        "m": "MinutesAgo",
        "s": "SecondsAgo",
    }
    time_delta_name_prefix = "Gui.Dashboard."
    time_delta_name_suffix = "NoData"
    time_delta_display = ""
    if isinstance(delta, dict):
        for _key in delta:
            if delta[_key]:
                time_delta_name_suffix = time_delta_name_suffix_dict[_key]
                time_delta_display = delta[_key]
                break
    time_delta_display = str(time_delta_display)
    time_delta_name = time_delta_name_prefix + time_delta_name_suffix
    return time_delta_display + t(time_delta_name)


def read_webapp_template(filename: str) -> str:
    """读取 WebUI 复用的 HTML 模板。

    Args:
        filename: 模板文件名。

    Returns:
        str: 模板内容。
    """
    template_path = Path(os.getcwd()) / "webapp" / filename
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()


def build_title_block(
    title: str, margin_top: int = 12, margin_bottom: int = 8, font_weight: int = 600
) -> str:
    """构造统一标题块。

    Args:
        title: 标题文本。
        margin_top: 顶部间距。
        margin_bottom: 底部间距。
        font_weight: 标题字重。

    Returns:
        str: 标题块 HTML。
    """
    tpl = read_webapp_template("title_block.html")
    return tpl.format(
        title=title,
        margin_top=margin_top,
        margin_bottom=margin_bottom,
        font_weight=font_weight,
    )


def build_muted_notice(text: str) -> str:
    """构造弱强调提示块。

    Args:
        text: 提示文本。

    Returns:
        str: 提示块 HTML。
    """
    tpl = read_webapp_template("muted_notice.html")
    return tpl.format(text=text)


def build_simple_table(headers, rows, extra_style: str = "") -> str:
    """构造统计用的简洁表格。

    Args:
        headers: 表头列表。
        rows: 表格行数据。
        extra_style: 附加 CSS 样式。

    Returns:
        str: 表格 HTML。
    """
    tpl = read_webapp_template("simple_table.html")
    thead_cells = "".join(
        [f'<th style="text-align:left;padding:6px">{h}</th>' for h in headers]
    )
    tbody_rows = "".join(
        [
            "<tr>"
            + "".join(
                [f'<td style="text-align:center;padding:6px">{v}</td>' for v in row]
            )
            + "</tr>"
            for row in rows
        ]
    )
    return tpl.format(
        thead_cells=thead_cells,
        tbody_rows=tbody_rows,
        extra_style=extra_style,
    )


def build_copyable_device_id(device_id: str) -> str:
    """构造可复制设备标识的 HTML。

    Args:
        device_id: 设备标识。

    Returns:
        str: 设备标识 HTML。
    """
    tpl = read_webapp_template("copyable_device_id.html")
    return tpl.format(device_id=device_id)


def build_recommendation_box(text: str) -> str:
    """构造推荐提示框。

    Args:
        text: 提示文本。

    Returns:
        str: 提示框 HTML。
    """
    tpl = read_webapp_template("recommendation_box.html")
    return tpl.format(text=text)
