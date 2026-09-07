"""M1/M2: 基础设置退役的静态与单面板契约。"""
from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from tests.qt_harness import ensure_app


def setUpModule():
    ensure_app()


class SingleSettingsContractTests(unittest.TestCase):
    def test_legacy_basic_panel_and_mode_switches_are_retired(self):
        """基础面板文件及主窗口的双模式入口均不得保留。"""
        from gui import main_window

        source_root = Path(__file__).resolve().parents[1]
        self.assertFalse(
            (source_root / "gui" / "widgets" / "basic_config_panel.py").exists(),
            "M1 后不应保留可导入的 BasicConfigPanel 模块",
        )

        source = inspect.getsource(main_window.MainWindow)
        for retired_name in (
            "BasicConfigPanel",
            "basic_config_panel",
            "settings_mode_combo",
            "_on_settings_mode_combo_changed",
            "_on_settings_mode_changed",
            "_show_loop_tab_only",
            "_show_all_tabs",
            "_fix_tab_selected_state",
            "_on_nav_basic",
            "_on_nav_advanced",
            "_get_active_config_panel",
        ):
            self.assertNotIn(retired_name, source)

    def test_basic_operator_resources_are_retired_but_shared_icons_remain(self):
        """仅退役基础面板专属数据，不影响通用职业图标打包。"""
        from config import constants

        source_root = Path(__file__).resolve().parents[1]
        self.assertFalse((source_root / "config" / "operator_db.py").exists())
        self.assertFalse(
            (source_root / "resources" / "data" / "character_table.json").exists()
        )
        for retired_name in (
            "OPERATOR_CLASS_PRESETS",
            "PROFESSION_CODE_MAP",
            "PROFESSION_NAME_MAP",
        ):
            self.assertFalse(hasattr(constants, retired_name))

        self.assertTrue(
            (source_root / "resources" / "class_icons" / "guard.png").is_file()
        )
        build_source = (source_root / "build.py").read_text(encoding="utf-8")
        self.assertIn('"resources/class_icons"', build_source)
        self.assertIn('"class_icons"', build_source)

    def test_script_export_gate_controls_only_the_retained_panel_button(self):
        """脚本门控只应访问保留 ConfigPanel 上的真实导出按钮。"""
        from PyQt6.QtWidgets import QMainWindow
        from gui.main_window import MainWindow
        from gui.widgets.config_panel import ConfigPanel

        window = MainWindow.__new__(MainWindow)
        QMainWindow.__init__(window)
        self.addCleanup(window.deleteLater)
        window.advanced_config_panel = ConfigPanel()

        MainWindow._set_script_export_enabled(window, False)
        self.assertFalse(window.advanced_config_panel.btn_export.isEnabled())

        MainWindow._set_script_export_enabled(window, True)
        self.assertTrue(window.advanced_config_panel.btn_export.isEnabled())


if __name__ == "__main__":
    unittest.main()
