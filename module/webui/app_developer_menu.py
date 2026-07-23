"""WebUI开发菜单和预览入口"""

from module.webui.app_dependencies import (
    go_app,
    lang,
    put_button,
    t,
    toast,
    use_scope,
)
from module.webui.app_types import WebUIMixinBase


class DeveloperMenuMixin(WebUIMixinBase):
    """WebUI开发菜单和预览入口"""

    @use_scope("menu", clear=True)
    def dev_set_menu(self) -> None:
        self.init_menu(collapse_menu=False, name="Develop")

        put_button(
            label=t("Gui.MenuDevelop.HomePage"),
            onclick=self.show_home,
            color="menu",
        ).style(f"--menu-HomePage--")

        # put_button(
        #     label=t("Gui.MenuDevelop.Translate"),
        #     onclick=self.dev_translate,
        #     color="menu",
        # ).style(f"--menu-Translate--")

        put_button(
            label=t("Gui.MenuDevelop.Update"),
            onclick=self.dev_update,
            color="menu",
        ).style(f"--menu-Update--")

        put_button(
            label=t("Gui.MenuDevelop.Remote"),
            onclick=self.dev_remote,
            color="menu",
        ).style(f"--menu-Remote--")

        put_button(
            label=t("Gui.MenuDevelop.Setting"),
            onclick=self.dev_setting,
            color="menu",
        ).style(f"--menu-Setting--")

        put_button(
            label=t("Gui.MenuDevelop.Announcement"),
            onclick=lambda: self.ui_check_announcement(force=True),
            color="menu",
        ).style(f"--menu-Announcement--")

        put_button(
            label=t("Gui.MenuDevelop.Utils"),
            onclick=self.dev_utils,
            color="menu",
        ).style(f"--menu-Utils--")

    def dev_translate(self) -> None:
        go_app("translate", new_window=True)
        lang.TRANSLATE_MODE = True
        self.show_home()

    def _preview_update_notice(self) -> None:
        def handle_preview_click():
            self._close_update_notice()
            toast("success", color="success")

        self._show_update_notice(handle_preview_click)

    def ui_develop(self) -> None:
        self.show_home()
