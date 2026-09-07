"""S5: auto-save re-pointing regression.

Old defect: AutoSaveService.start() caches the config object and project
path (core/auto_save_service.py), and start() was only ever called by
temp-project init and project-open — after 新建项目/另存为 the service kept
auto-saving the OLD config object to the OLD backup location, and crash
recovery restored the wrong project.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import types
import unittest
from pathlib import Path

from tests.qt_harness import ensure_app

from config.epconfig import EPConfig, CONFIG_FILENAME


def setUpModule():
    ensure_app()


class _ServiceSpy:
    def __init__(self):
        self.started_with = None

    def start(self, config_obj, project_path, base_dir):
        self.started_with = (config_obj, project_path, base_dir)


def _null(*_a, **_k):
    return None


class AutoSaveRepointTests(unittest.TestCase):
    def test_new_project_repoints_autosave(self):
        from gui import main_window as mw_mod
        from gui.main_window import MainWindow

        with tempfile.TemporaryDirectory() as temp_dir:
            w = MainWindow.__new__(MainWindow)
            old_config = EPConfig()
            w._config = old_config
            w._base_dir = ""
            w._project_path = ""
            w._temp_dir = ""
            w._is_modified = False
            w._loop_image_path = None
            w._auto_save_service = _ServiceSpy()
            w._check_save = lambda: True
            w._cleanup_temp_dir = _null
            w._update_title = _null
            w._reset_undo_history = _null  # undo subsystem out of scope here
            for name in ("video_preview", "intro_preview", "frame_capture_preview"):
                setattr(w, name, types.SimpleNamespace(
                    clear=_null, set_epconfig=_null))
            w.transition_preview = types.SimpleNamespace(clear_image=_null)
            w.timeline = types.SimpleNamespace(set_total_frames=_null)
            w.advanced_config_panel = types.SimpleNamespace(set_config=_null)
            w.json_preview = types.SimpleNamespace(set_config=_null)
            w.status_bar = types.SimpleNamespace(showMessage=_null)

            fake_dialog = types.SimpleNamespace(
                getExistingDirectory=staticmethod(lambda *a, **k: temp_dir)
            )
            orig_dialog = mw_mod.QFileDialog
            mw_mod.QFileDialog = fake_dialog
            try:
                MainWindow._on_new_project(w)
            finally:
                mw_mod.QFileDialog = orig_dialog

            self.assertIsNotNone(
                w._auto_save_service.started_with,
                "new project must restart the auto-save service",
            )
            cfg, path, base = w._auto_save_service.started_with
            self.assertIs(cfg, w._config)
            self.assertIsNot(cfg, old_config, "service must track the NEW config")
            self.assertEqual(path, os.path.join(temp_dir, CONFIG_FILENAME))
            self.assertEqual(base, temp_dir)

    def test_save_as_repoints_autosave(self):
        from gui import main_window as mw_mod
        from gui.main_window import MainWindow

        with tempfile.TemporaryDirectory() as temp_dir:
            target = os.path.join(temp_dir, CONFIG_FILENAME)
            w = MainWindow.__new__(MainWindow)
            w._config = EPConfig()
            w._config.save_to_file = _null  # no real disk write needed
            w._base_dir = os.path.join(temp_dir, "old")
            w._project_path = os.path.join(w._base_dir, CONFIG_FILENAME)
            w._temp_dir = ""
            w._is_modified = True
            w._auto_save_service = _ServiceSpy()
            w._update_title = _null
            # 此替身只验证另存为后的 AutoSaveService 重指向；脚本上下文
            # 由 M6 专属测试覆盖，不能依赖未初始化的真实 MainWindow。
            w._configure_preview_render_contexts = _null
            w.advanced_config_panel = types.SimpleNamespace(set_config=_null)
            w.json_preview = types.SimpleNamespace(set_config=_null)
            w.status_bar = types.SimpleNamespace(showMessage=_null)
            w.video_preview = types.SimpleNamespace(set_render_context=_null)
            w.intro_preview = types.SimpleNamespace(set_render_context=_null)

            fake_dialog = types.SimpleNamespace(
                getSaveFileName=staticmethod(lambda *a, **k: (target, ""))
            )
            orig_dialog = mw_mod.QFileDialog
            mw_mod.QFileDialog = fake_dialog
            try:
                MainWindow._on_save_as(w)
            finally:
                mw_mod.QFileDialog = orig_dialog

            self.assertIsNotNone(
                w._auto_save_service.started_with,
                "save-as must restart the auto-save service at the new path",
            )
            cfg, path, base = w._auto_save_service.started_with
            self.assertIs(cfg, w._config)
            self.assertEqual(path, target)
            self.assertEqual(base, temp_dir)
            self.assertFalse(w._is_modified)


if __name__ == "__main__":
    unittest.main()
