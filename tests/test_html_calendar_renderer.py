import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_renderer_module():
    """Load the renderer without importing the AstrBot plugin entrypoint."""
    package_name = "_cake_renderer_test_package"
    package = types.ModuleType(package_name)
    package.__path__ = [str(PROJECT_ROOT)]
    sys.modules[package_name] = package

    resources = types.ModuleType(f"{package_name}.resources")
    resources.__path__ = [str(PROJECT_ROOT / "resources")]
    sys.modules[resources.__name__] = resources

    texts = types.ModuleType(f"{package_name}.resources.texts")
    texts.CALENDAR_SUMMARY = "本月投喂娅娅 {days} 天，共 {cakes} 块蛋糕"
    sys.modules[texts.__name__] = texts

    render_package = types.ModuleType(f"{package_name}.render_html")
    render_package.__path__ = [str(PROJECT_ROOT / "render_html")]
    sys.modules[render_package.__name__] = render_package

    module_name = f"{package_name}.render_html.calendar"
    spec = importlib.util.spec_from_file_location(
        module_name, PROJECT_ROOT / "render_html" / "calendar.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


renderer_module = _load_renderer_module()


class _FakeBrowser:
    def __init__(self):
        self.closed = False

    def is_connected(self):
        return not self.closed

    def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, browser=None, error=None):
        self.browser = browser or _FakeBrowser()
        self.error = error
        self.launch_calls = []

    def launch(self, **kwargs):
        self.launch_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.browser


class _FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium
        self.stopped = False

    def stop(self):
        self.stopped = True


class _ImmediateFuture:
    def __init__(self, value):
        self.value = value

    def result(self, timeout=None):
        return self.value


class HtmlCalendarRendererTests(unittest.TestCase):
    def setUp(self):
        renderer_module._browser = None
        renderer_module._playwright = None

    def tearDown(self):
        renderer_module._browser = None
        renderer_module._playwright = None

    def test_browser_launch_uses_playwright_headless_shell(self):
        chromium = _FakeChromium()
        renderer_module._playwright = _FakePlaywright(chromium)

        browser = renderer_module._get_browser(["--disable-dev-shm-usage"])

        self.assertIs(browser, chromium.browser)
        self.assertEqual(len(chromium.launch_calls), 1)
        launch = chromium.launch_calls[0]
        self.assertIs(launch["headless"], True)
        self.assertEqual(launch["timeout"], 30_000)
        self.assertEqual(launch["args"], ["--disable-dev-shm-usage"])
        self.assertNotIn("channel", launch)

    def test_browser_launch_failure_does_not_fallback_to_system_chrome(self):
        chromium = _FakeChromium(error=RuntimeError("headless shell missing"))
        renderer_module._playwright = _FakePlaywright(chromium)

        with self.assertRaisesRegex(RuntimeError, "chromium-headless-shell"):
            renderer_module._get_browser([])

        self.assertEqual(len(chromium.launch_calls), 1)

    def test_connected_browser_is_reused(self):
        chromium = _FakeChromium()
        renderer_module._playwright = _FakePlaywright(chromium)

        first = renderer_module._get_browser([])
        second = renderer_module._get_browser([])

        self.assertIs(first, second)
        self.assertEqual(len(chromium.launch_calls), 1)

    def test_render_uses_container_safe_args_and_unique_paths(self):
        submissions = []

        def submit(fn, *args):
            submissions.append((fn, args))
            return _ImmediateFuture(args[-1])

        with tempfile.TemporaryDirectory() as temp_dir:
            core = types.SimpleNamespace(temp_dir=temp_dir)
            renderer = renderer_module.HtmlCalendarRenderer(core, {})
            with (
                mock.patch.object(renderer_module, "_submit_render_task", side_effect=submit),
                mock.patch.object(renderer_module.time, "time", return_value=1234),
                mock.patch.object(renderer_module.os, "geteuid", return_value=0, create=True),
            ):
                first = renderer.render("42", "测试", 2026, 10, {1: 1}, 1)
                second = renderer.render("42", "测试", 2026, 10, {1: 1}, 1)

        self.assertNotEqual(first, second)
        self.assertEqual(
            submissions[0][1][0],
            ["--disable-dev-shm-usage", "--no-sandbox"],
        )


if __name__ == "__main__":
    unittest.main()
