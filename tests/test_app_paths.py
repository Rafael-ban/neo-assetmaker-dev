"""源码与 cx_Freeze 根目录选择，以及启动路径证据的回归测试。"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from utils import crash_diagnostics, file_utils


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT.parent / "clean-clone"
FROZEN_ROOT = ROOT.parent / "portable"
DEVELOPER_MODULE = str(ROOT.parent / "old-developer" / "utils" / "file_utils.py")


def _load_entrypoint():
    """只加载入口定义，替换早期诊断，避免创建 GUI、文件或安装全局 hooks。"""
    spec = importlib.util.spec_from_file_location("app_paths_entrypoint", ROOT / "main.py")
    module = importlib.util.module_from_spec(spec)
    diagnostic = mock.Mock()
    with (
        mock.patch.object(crash_diagnostics, "initialize", return_value=diagnostic),
        mock.patch.object(sys, "path", list(sys.path)),
        mock.patch.dict(os.environ),
    ):
        spec.loader.exec_module(module)
    return module, diagnostic


class AppPathsTests(unittest.TestCase):
    def test_source_root_ignores_python_executable_and_working_directory(self):
        with (
            mock.patch.object(sys, "frozen", False, create=True),
            mock.patch.object(sys, "executable", "/another/venv/python"),
            mock.patch.object(file_utils, "__file__", str(SOURCE_ROOT / "utils" / "file_utils.py")),
            mock.patch("os.getcwd", return_value="/unrelated/working/directory"),
        ):
            self.assertEqual(file_utils.get_app_dir(), str(SOURCE_ROOT))

    def test_source_root_when_frozen_attribute_is_absent(self):
        with mock.patch.dict(sys.__dict__), mock.patch.object(
            file_utils, "__file__", str(SOURCE_ROOT / "utils" / "file_utils.py"),
        ):
            sys.__dict__.pop("frozen", None)
            self.assertEqual(file_utils.get_app_dir(), str(SOURCE_ROOT))

    def test_frozen_root_ignores_compiled_module_developer_path_and_cwd(self):
        with (
            mock.patch.object(sys, "frozen", True, create=True),
            mock.patch.object(sys, "executable", str(FROZEN_ROOT / "ArknightsPassMaker.exe")),
            mock.patch.object(file_utils, "__file__", DEVELOPER_MODULE),
            mock.patch("os.getcwd", return_value="/another/directory"),
        ):
            self.assertEqual(file_utils.get_app_dir(), str(FROZEN_ROOT))

    def test_source_main_and_utility_resolve_same_root_and_record_it(self):
        with mock.patch.object(sys, "frozen", False, create=True):
            module, diagnostic = _load_entrypoint()
            self.assertEqual(module.APP_DIR, file_utils.get_app_dir())
            diagnostic.record_startup_paths.assert_called_once_with(
                str(ROOT), module.APP_VERSION, file_utils.__file__,
            )

    def test_frozen_main_and_utility_use_executable_directory(self):
        with (
            mock.patch.object(sys, "frozen", True, create=True),
            mock.patch.object(sys, "executable", str(FROZEN_ROOT / "ArknightsPassMaker.exe")),
            mock.patch.object(file_utils, "__file__", DEVELOPER_MODULE),
        ):
            module, diagnostic = _load_entrypoint()
            self.assertEqual(module.APP_DIR, str(FROZEN_ROOT))
            self.assertEqual(module.APP_DIR, file_utils.get_app_dir())
            diagnostic.record_startup_paths.assert_called_once_with(
                str(FROZEN_ROOT), module.APP_VERSION, DEVELOPER_MODULE,
            )

    def test_diagnostic_records_actual_runtime_paths_and_unknown_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            diagnostic = crash_diagnostics.CrashDiagnostics([directory])
            try:
                self.assertTrue(diagnostic.initialize())
                with (
                    mock.patch.object(sys, "frozen", True, create=True),
                    mock.patch.object(sys, "executable", "/应用根/工具箱.exe"),
                    mock.patch("os.getcwd", return_value="/工作目录"),
                ):
                    diagnostic.record_startup_paths(
                        "/应用根", "2.2.0", "/旧开发目录/utils/file_utils.py",
                    )
                diagnostic.flush()
                text = Path(diagnostic.path).read_text("utf-8")
                for evidence in (
                    "app_version=2.2.0 app_commit=unknown commit_source=unavailable",
                    "sys.frozen=True sys.executable='/应用根/工具箱.exe'",
                    "utils.file_utils.__file__='/旧开发目录/utils/file_utils.py'",
                    "resolved_app_dir='/应用根'",
                    "cwd='/工作目录'",
                ):
                    self.assertIn(evidence, text)
            finally:
                diagnostic.close()

    def test_unavailable_cwd_does_not_prevent_diagnostics(self):
        diagnostic = crash_diagnostics.CrashDiagnostics([])
        with (
            mock.patch.object(diagnostic, "record_text") as record,
            mock.patch("os.getcwd", side_effect=FileNotFoundError),
        ):
            diagnostic.record_startup_paths("/app", "test", "/app/utils/file_utils.py")
        record.assert_any_call("cwd=unavailable")

    def test_missing_windows_runtime_warns_with_deployment_instructions(self):
        module, _ = _load_entrypoint()
        logger = mock.Mock()
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(module, "APP_DIR", directory),
            mock.patch.object(sys, "platform", "win32"),
        ):
            module._warn_missing_media_runtime(logger)
        logger.warning.assert_called_once()
        message, runtime_path, app_dir = logger.warning.call_args.args
        self.assertEqual(runtime_path, os.path.join(directory, "tools", "media", "runtime"))
        self.assertEqual(app_dir, directory)
        self.assertIn("Git 克隆不包含媒体二进制", message)
        self.assertIn("verify-tree --app-dir .", message)
        self.assertIn("GUI 可以继续启动", message)

    def test_existing_windows_runtime_has_no_missing_directory_warning(self):
        module, _ = _load_entrypoint()
        logger = mock.Mock()
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "tools" / "media" / "runtime").mkdir(parents=True)
            with (
                mock.patch.object(module, "APP_DIR", directory),
                mock.patch.object(sys, "platform", "win32"),
            ):
                module._warn_missing_media_runtime(logger)
        logger.warning.assert_not_called()

    def test_other_platform_does_not_report_windows_runtime_deployment(self):
        module, _ = _load_entrypoint()
        logger = mock.Mock()
        with mock.patch.object(sys, "platform", "linux"):
            module._warn_missing_media_runtime(logger)
        logger.warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
