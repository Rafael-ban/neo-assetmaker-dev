"""M1: 单一设置面板下的真实主窗口 GUI 回归测试。"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import numpy as np
from tests.qt_harness import ensure_app
from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtTest import QTest


def setUpModule():
    ensure_app()


class SingleSettingsGuiTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self._old_cwd = os.getcwd()
        os.chdir(self._temp_dir.name)
        self.addCleanup(self._temp_dir.cleanup)
        self.addCleanup(self._restore_cwd)

    def _restore_cwd(self):
        os.chdir(self._old_cwd)

    def _create_window(self):
        from PyQt6.QtCore import QEvent
        from gui.main_window import MainWindow
        from utils import file_utils

        patches = (
            mock.patch.object(
                file_utils, "get_app_dir", return_value=self._temp_dir.name
            ),
            mock.patch.object(MainWindow, "_load_settings", lambda _self: None),
            mock.patch.object(
                MainWindow, "_load_user_settings", lambda _self: None
            ),
            mock.patch.object(MainWindow, "_check_first_run", lambda _self: None),
            mock.patch.object(
                MainWindow, "_check_update_on_startup", lambda _self: None
            ),
            mock.patch.object(
                MainWindow, "_check_crash_recovery", lambda _self: None
            ),
            mock.patch.object(MainWindow, "_save_settings", lambda _self: None),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        window = MainWindow()
        def dispose_window():
            # 真实编辑入口会标脏项目；夹具清理不应进入模态保存确认。
            window._is_modified = False
            window._shutdown_runtime_resources()
            window.close()
            window.deleteLater()
            app = ensure_app()
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()

        self.addCleanup(dispose_window)
        return window

    def _click_preview_tab(self, window, index):
        """经 Fluent 的真实 TabItem 鼠标事件切换预览页。"""
        window.show()
        ensure_app().processEvents()
        QTest.mouseClick(
            window.preview_tabs.tabBar.items[index],
            Qt.MouseButton.LeftButton,
        )
        ensure_app().processEvents()

    def _assert_preview_tab_state(self, window, index, timeline_preview):
        """断言用户点击后的 Tab/stack/选择态与业务时间轴一致。"""
        self.assertEqual(window.preview_tabs.currentIndex(), index)
        self.assertEqual(window.preview_tabs.stackedWidget.currentIndex(), index)
        self.assertIs(
            window.preview_tabs.currentWidget(),
            window.preview_tabs.widget(index),
        )
        self.assertEqual(window.preview_tabs.tabBar._currentIndex, index)
        self.assertEqual(
            sum(item.isSelected for item in window.preview_tabs.tabBar.items),
            1,
        )
        self.assertTrue(window.preview_tabs.tabBar.items[index].isSelected)
        self.assertIs(window._timeline_preview, timeline_preview)

    def test_real_tab_item_clicks_sync_all_preview_pages_and_drop_context(self):
        """真实页签点击更新四页业务状态，过渡拖放后可恢复循环上下文。"""
        window = self._create_window()
        window.intro_preview.supports_editor_capability = lambda _capability: True
        window.video_preview.supports_editor_capability = lambda _capability: True
        window.intro_preview.total_frames = 91
        window.intro_preview.video_fps = 24.0
        window.video_preview.total_frames = 120
        window.video_preview.video_fps = 30.0
        transition_path = os.path.join(self._temp_dir.name, "transition.png")
        Path(transition_path).touch()

        self.assertFalse(hasattr(window, "basic_config_panel"))
        self.assertFalse(hasattr(window, "settings_mode_combo"))
        self.assertEqual(window.config_layout.indexOf(window.advanced_config_panel), 1)
        self.assertEqual(window.preview_tabs.count(), 4)
        self._assert_preview_tab_state(window, 3, window.video_preview)

        self._click_preview_tab(window, 0)
        self._assert_preview_tab_state(window, 0, window.intro_preview)
        self.assertFalse(window.timeline.isHidden())

        self._click_preview_tab(window, 1)
        self._assert_preview_tab_state(window, 1, window.video_preview)
        self.assertFalse(window.timeline.isHidden())

        self._click_preview_tab(window, 2)
        self._assert_preview_tab_state(window, 2, window.video_preview)
        self.assertTrue(window.timeline.isHidden())

        with mock.patch.object(window.transition_preview, "load_image"):
            with mock.patch.object(
                window.advanced_config_panel,
                "_process_transition_image",
                side_effect=lambda path, kind: (
                    window.advanced_config_panel.transition_image_changed.emit(
                        kind, path
                    )
                ),
            ):
                window._drop_overlay.file_dropped.emit(
                    transition_path, QPoint(0, 0)
                )
        self.assertEqual(window.preview_tabs.currentIndex(), 2)
        self.assertTrue(window.timeline.isHidden())

        self._click_preview_tab(window, 3)
        self._assert_preview_tab_state(window, 3, window.video_preview)
        self.assertFalse(window.timeline.isHidden())
        self.assertEqual(window._drop_overlay._hint_text, "释放以导入循环素材")
        self.assertIn(".mp4", window._drop_overlay._accepted_extensions)
        self.assertIn(".png", window._drop_overlay._accepted_extensions)

    def test_real_programmatic_import_drop_capture_and_navigation_keep_tracks_synced(self):
        """真实业务入口切页前快照旧轨道，返回素材页仍保留单一配置面板。"""
        window = self._create_window()
        window.intro_preview.supports_editor_capability = lambda _capability: True
        window.video_preview.supports_editor_capability = lambda _capability: True
        intro_path = os.path.join(self._temp_dir.name, "intro.mp4")
        loop_path = os.path.join(self._temp_dir.name, "loop.mp4")
        transition_path = os.path.join(self._temp_dir.name, "transition.png")
        for path in (intro_path, loop_path, transition_path):
            Path(path).touch()

        with mock.patch.object(window.intro_preview, "load_video", return_value=True):
            window._on_intro_video_selected(intro_path)
        self.assertEqual(window.preview_tabs.currentIndex(), 0)
        self.assertIs(window._timeline_preview, window.intro_preview)

        window.timeline.set_in_point(11)
        window.timeline.set_out_point(22)
        with mock.patch.object(window.video_preview, "load_video", return_value=True):
            window._on_video_file_selected(loop_path)
        self.assertEqual(window.preview_tabs.currentIndex(), 3)
        self.assertEqual(window._intro_in_out, (11, 22))
        self.assertIs(window._timeline_preview, window.video_preview)

        window.timeline.set_in_point(31)
        window.timeline.set_out_point(44)
        with mock.patch.object(window.transition_preview, "load_image"):
            with mock.patch.object(
                window.advanced_config_panel,
                "_process_transition_image",
                side_effect=lambda path, kind: (
                    window.advanced_config_panel.transition_image_changed.emit(
                        kind, path
                    )
                ),
            ):
                window._handle_drop_transition(transition_path, QPoint(0, 0))
        self.assertEqual(window.preview_tabs.currentIndex(), 2)
        self.assertEqual(window._loop_in_out, (31, 44))

        window.video_preview._has_video = True
        with mock.patch.object(
            window.video_preview,
            "capture_frame_async",
            side_effect=lambda deliver: deliver(np.zeros((4, 4, 3), dtype=np.uint8)),
        ):
            window._on_capture_frame()
        self.assertEqual(window.preview_tabs.currentIndex(), 1)
        self.assertIs(window._current_video_preview, window.video_preview)
        self.assertIs(window._timeline_preview, window.video_preview)

        original_panel = window.advanced_config_panel
        original_config = window._config
        window._read_user_settings = lambda: {
            "usb_controler_vid": "",
            "usb_controler_pid": "",
        }
        window._on_sidebar_settings()
        self.assertTrue(window.splitter.isHidden())
        self.assertFalse(window._settings_page.isHidden())
        window._on_sidebar_material()
        self.assertFalse(window.splitter.isHidden())
        self.assertIs(window.advanced_config_panel, original_panel)
        self.assertIs(window._config, original_config)
        self.assertEqual(window.preview_tabs.currentIndex(), 1)

    def test_late_intro_video_loaded_keeps_loop_timeline_and_cache_unchanged(self):
        """入场元数据在切至循环后才到达时，不能篡改循环轨的编辑状态。"""
        window = self._create_window()
        window.intro_preview.supports_editor_capability = lambda _capability: True
        window.video_preview.supports_editor_capability = lambda _capability: True
        intro_path = os.path.join(self._temp_dir.name, "intro.mp4")
        Path(intro_path).touch()

        def start_intro_load(_path):
            window.intro_preview.total_frames = 91
            window.intro_preview.video_fps = 24.0
            return True

        with mock.patch.object(
            window.intro_preview, "load_video", side_effect=start_intro_load
        ):
            window._on_intro_video_selected(intro_path)
        self.assertIs(window._timeline_preview, window.intro_preview)
        window.timeline.set_in_point(11)
        window.timeline.set_out_point(22)

        window.video_preview.total_frames = 120
        window.video_preview.video_fps = 30.0
        self._click_preview_tab(window, 3)
        window.timeline.set_in_point(31)
        window.timeline.set_out_point(44)
        self._click_preview_tab(window, 2)
        self.assertEqual(window._loop_in_out, (31, 44))
        self._click_preview_tab(window, 3)
        expected_loop_timeline = (
            window.timeline._total_frames,
            window.timeline.get_in_point(),
            window.timeline.get_out_point(),
        )

        QTimer.singleShot(
            0,
            lambda: window.intro_preview.video_loaded.emit(91, 24.0),
        )
        QTest.qWait(20)

        self.assertEqual(window.preview_tabs.currentIndex(), 3)
        self.assertIs(window._timeline_preview, window.video_preview)
        self.assertEqual(window._loop_in_out, (31, 44))
        self.assertEqual(
            (
                window.timeline._total_frames,
                window.timeline.get_in_point(),
                window.timeline.get_out_point(),
            ),
            expected_loop_timeline,
        )
        self.assertEqual(window._intro_in_out, (0, 90))

    def test_delayed_intro_capture_keeps_loop_snapshot_and_binds_capture_to_intro(self):
        """截图回调到达前切换循环时，交付后仍以原入场轨为来源。"""
        window = self._create_window()
        window._base_dir = self._temp_dir.name
        window.intro_preview.supports_editor_capability = lambda _capability: True
        window.video_preview.supports_editor_capability = lambda _capability: True
        window.intro_preview.total_frames = 91
        window.intro_preview.video_fps = 24.0
        window.intro_preview._has_video = True
        window._select_preview_tab(0)
        window.timeline.set_in_point(7)
        window.timeline.set_out_point(18)
        pending_callbacks = []

        with mock.patch.object(
            window.intro_preview,
            "capture_frame_async",
            side_effect=pending_callbacks.append,
        ):
            window._on_capture_frame()
        self.assertEqual(len(pending_callbacks), 1)

        window.video_preview.total_frames = 120
        window.video_preview.video_fps = 30.0
        self._click_preview_tab(window, 3)
        window.timeline.set_in_point(31)
        window.timeline.set_out_point(44)

        QTimer.singleShot(
            0,
            lambda: pending_callbacks.pop()(np.zeros((4, 4, 3), dtype=np.uint8)),
        )
        QTest.qWait(20)

        self.assertEqual(window._loop_in_out, (31, 44))
        self.assertEqual(window.preview_tabs.currentIndex(), 1)
        self.assertIs(window._current_video_preview, window.intro_preview)
        self.assertIs(window._timeline_preview, window.intro_preview)
        self.assertEqual(
            (window.timeline.get_in_point(), window.timeline.get_out_point()),
            (7, 18),
        )

    def test_consecutive_loop_drops_keep_real_panel_mode_path_and_signals(self):
        """连续拖入图片、视频时，唯一 ConfigPanel 保留各自模式、路径和信号。"""
        window = self._create_window()
        panel = window.advanced_config_panel
        image_path = os.path.join(self._temp_dir.name, "loop-image.png")
        video_path = os.path.join(self._temp_dir.name, "loop-video.mp4")
        Path(image_path).touch()
        Path(video_path).touch()
        selected_images = []
        selected_videos = []
        panel.loop_image_selected.connect(selected_images.append)
        panel.video_file_selected.connect(selected_videos.append)

        with mock.patch.object(
            window.video_preview, "load_image_as_loop", return_value=True
        ):
            with mock.patch.object(window.video_preview, "load_video", return_value=True):
                window._handle_drop_loop(image_path)
                self.assertTrue(panel.radio_loop_image.isChecked())
                self.assertFalse(panel.radio_loop_video.isChecked())
                self.assertTrue(panel.edit_loop_file.text().endswith(".png"))
                self.assertEqual(selected_images, [image_path])
                self.assertEqual(selected_videos, [])
                self.assertTrue(window._config.loop.is_image)
                self.assertTrue(window._config.loop.file.endswith(".png"))

                window._handle_drop_loop(video_path)
        self.assertFalse(panel.radio_loop_image.isChecked())
        self.assertTrue(panel.radio_loop_video.isChecked())
        self.assertTrue(panel.edit_loop_file.text().endswith(".mp4"))
        self.assertEqual(selected_images, [image_path])
        self.assertEqual(selected_videos, [video_path])
        self.assertFalse(window._config.loop.is_image)
        self.assertTrue(window._config.loop.file.endswith(".mp4"))


if __name__ == "__main__":
    unittest.main()
