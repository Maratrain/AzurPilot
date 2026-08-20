import tempfile
import unittest
from pathlib import Path
from urllib.parse import urljoin

from pywebio import config
from starlette.testclient import TestClient

from module.webui.app import (
    INITIAL_LOADING_JS,
    _initial_loading_css,
    _initial_style_names,
)
from module.webui.app_shell import normalize_webui_theme, pywebio_theme_for
from module.webui.fastapi import (
    INITIAL_LOADING_STYLE_MARKER,
    VERSIONED_STATIC_ASSET_CACHE_CONTROL,
    asgi_app,
)
from module.webui.utils import Icon


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestWebUIStaticAssets(unittest.TestCase):
    def setUp(self):
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary_directory.name)
        self.assets = self.root / "assets"
        self.doc = self.root / "doc"
        css_directory = self.assets / "gui" / "css"
        css_directory.mkdir(parents=True)
        self.doc.mkdir()
        (css_directory / "test.css").write_text("body {}", encoding="utf-8")
        (self.doc / "logo.webp").write_bytes(b"RIFF")
        (self.root / "config").mkdir()
        (self.root / "config" / "deploy.yaml").write_text("secret: value", encoding="utf-8")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "HEAD").write_text("ref: main", encoding="utf-8")

        self.app = asgi_app(
            {"index": lambda: None},
            cdn=False,
            static_mounts={
                "/static/assets": str(self.assets),
                "/static/doc": str(self.doc),
            },
        )
        self.client = TestClient(self.app)

    def tearDown(self):
        self._temporary_directory.cleanup()

    def test_mounts_only_public_static_directories(self):
        css_response = self.client.get("/static/assets/gui/css/test.css")
        logo_response = self.client.get("/static/doc/logo.webp")

        self.assertEqual(css_response.status_code, 200)
        self.assertEqual(css_response.headers["content-type"], "text/css; charset=utf-8")
        self.assertEqual(css_response.headers["cache-control"], "no-cache")
        self.assertEqual(logo_response.status_code, 200)
        self.assertEqual(self.client.get("/static/config/deploy.yaml").status_code, 404)
        self.assertEqual(self.client.get("/static/.git/HEAD").status_code, 404)

    def test_versioned_static_assets_are_cached_immutably(self):
        response = self.client.get("/static/assets/gui/css/test.css?v=content-hash")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["cache-control"],
            VERSIONED_STATIC_ASSET_CACHE_CONTROL,
        )

    def test_relative_css_url_preserves_reverse_proxy_prefix(self):
        css_url = urljoin(
            "https://example.test/azur/", "static/assets/gui/css/alas.css"
        )
        icon_url = urljoin(
            "https://example.test/azur/",
            "static/assets/spa/spa-icon-192x192.png",
        )
        logo_url = urljoin(css_url, "../../../doc/logo.webp")

        self.assertEqual(
            css_url, "https://example.test/azur/static/assets/gui/css/alas.css"
        )
        self.assertEqual(
            icon_url,
            "https://example.test/azur/static/assets/spa/spa-icon-192x192.png",
        )
        self.assertEqual(logo_url, "https://example.test/azur/static/doc/logo.webp")

    def test_default_pywebio_assets_are_self_hosted(self):
        response = TestClient(asgi_app({"index": lambda: None})).get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn('href="pywebio_static/css/app.css?v=', response.text)
        self.assertNotIn("cdn.jsdelivr.net", response.text)

    def test_theme_keeps_random_background_without_external_font_services(self):
        theme_css = (PROJECT_ROOT / "assets/gui/css/advanced-material-alas.css").read_text(
            encoding="utf-8"
        )
        obs_overlay = (PROJECT_ROOT / "module/webui/obs_overlay.html").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("fonts.googleapis.com", theme_css)
        self.assertNotIn("fonts.gstatic.com", theme_css)
        self.assertIn('url("https://api.yppp.net/api.php")', theme_css)
        self.assertNotIn("fonts.googleapis.com", obs_overlay)
        self.assertNotIn("fonts.gstatic.com", obs_overlay)

    def test_initial_shell_uses_inline_loading_fallback(self):
        loading_css = _initial_loading_css("default")

        self.assertIn(INITIAL_LOADING_STYLE_MARKER, loading_css)
        self.assertIn("#pywebio-scope-ROOT:empty::before", loading_css)
        self.assertNotIn("#pywebio-scope-ROOT > *", loading_css)
        self.assertNotIn("visibility: hidden", loading_css)
        self.assertIn("alas-initial-ready", loading_css)
        self.assertIn("MutationObserver", INITIAL_LOADING_JS)
        self.assertIn("input-cards", INITIAL_LOADING_JS)
        self.assertNotIn("stylesSettled", INITIAL_LOADING_JS)
        self.assertNotIn("alas-initial-style-settled", INITIAL_LOADING_JS)

    def test_initial_shell_paints_before_external_stylesheets(self):
        @config(
            css_file="static/assets/gui/css/test.css?v=content-hash",
            css_style=_initial_loading_css("default"),
            js_code=INITIAL_LOADING_JS,
        )
        def index():
            return None

        response = TestClient(asgi_app([index], cdn=False)).get("/")
        html = response.text
        stylesheet_tags = [
            part.split(">", 1)[0]
            for part in html.split('<link rel="stylesheet"')[1:]
        ]

        self.assertLess(
            html.index(INITIAL_LOADING_STYLE_MARKER),
            html.index('<link rel="stylesheet"'),
        )
        self.assertGreater(len(stylesheet_tags), 1)
        self.assertTrue(
            all('media="print"' in tag for tag in stylesheet_tags)
        )
        self.assertTrue(
            all('onload=\'this.media="all"' in tag for tag in stylesheet_tags)
        )
        self.assertNotIn("data-alas-initial-style", html)
        self.assertNotIn("alas-initial-style-settled", html)
        self.assertIn("pywebio_static/css/markdown.min.css?v=", html)
        self.assertIn("pywebio_static/js/jquery.min.js?v=", html)

    def test_initial_styles_are_loaded_before_websocket_output(self):
        self.assertEqual(
            ("alas", "entry-alas", "light-alas"),
            _initial_style_names("default"),
        )
        self.assertEqual(
            (
                "alas",
                "entry-alas",
                "advanced-material-alas",
                "dark-advanced-material-overrides-alas",
            ),
            _initial_style_names("dark_advanced_material"),
        )

    def test_theme_is_normalized_before_initial_html_rendering(self):
        self.assertEqual("advanced_material", normalize_webui_theme("apple"))
        self.assertEqual("default", normalize_webui_theme("unknown"))
        self.assertEqual("dark", pywebio_theme_for("dark"))
        self.assertEqual("default", pywebio_theme_for("dark_advanced_material"))

    def test_header_icon_is_a_static_resource(self):
        self.assertIn("static/assets/spa/spa-icon-192x192.png", Icon.ALAS)
        self.assertNotIn("base64", Icon.ALAS)

    def test_initial_css_does_not_download_full_misans_font(self):
        css = (PROJECT_ROOT / "assets/gui/css/alas.css").read_text(encoding="utf-8")

        self.assertNotIn("MiSans-Demibold.ttf", css)
