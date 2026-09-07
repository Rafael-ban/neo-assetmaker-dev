"""M4: main-window preview synchronization regression tests."""
import os
import types
import unittest
from unittest import mock

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from config.epconfig import EPConfig, EditorTrackState
import gui.main_window as main_window
from gui.main_window import MainWindow


class _Action:
    def setEnabled(self, enabled):
        self.enabled = enabled


class _InactiveTimer:
    def isActive(self):
        return False


class _Timeline:
    def __init__(self):
        self.in_points = []
        self.out_points = []

    def set_in_point(self, value):
        self.in_points.append(value)

    def set_out_point(self, value):
        self.out_points.append(value)


class _Preview:
    def __init__(self, *, total_frames=100, frame=None, rotation=0):
        self.total_frames = total_frames
        self.current_frame = frame
        self.current_frame_index = 0
        self.rotation = rotation
        self.cropbox = (0, 0, 100, 100)
        self.timeline_ranges = []

    def set_epconfig(self, config):
        self.config = config

    def get_rotation(self):
        return self.rotation

    def set_rotation(self, rotation):
        self.rotation = rotation

    def get_cropbox(self):
        return self.cropbox

    def set_cropbox(self, *cropbox):
        self.cropbox = cropbox

    def set_timeline_range(self, start_frame, end_exclusive):
        self.timeline_ranges.append((start_frame, end_exclusive))


class _CapturePreview:
    def __init__(self):
        self.frames = []

    def update_static_frame(self, frame):
        self.frames.append(frame.copy())


class MainWindowPreviewSyncTests(unittest.TestCase):
    def _state(self, in_frame, out_frame):
        config = EPConfig()
        config.loop.file = "loop.mp4"
        config.editor.loop = EditorTrackState(
            in_frame=in_frame,
            out_frame=out_frame,
        )
        return config.to_dict()

    def _window_for_same_media_undo(self, current_state):
        window = MainWindow.__new__(MainWindow)
        window._config = EPConfig.from_dict(current_state)
        window._base_dir = ""
        window._applying_undo = False
        window._restoring_editor_state = False
        window._undo_stack = []
        window._redo_stack = []
        window._undo_baseline = current_state
        window._undo_timer = _InactiveTimer()
        window._max_history = 50
        window._loop_in_out = (0, 0)
        window._intro_in_out = (0, 0)
        window._is_modified = False
        window._update_title = lambda: None
        window.status_bar = types.SimpleNamespace(
            showMessage=lambda *args, **kwargs: None
        )
        window.timeline = _Timeline()
        window.video_preview = _Preview()
        window.intro_preview = _Preview()
        window._is_timeline_bound_to = lambda preview: (
            preview is window.video_preview
        )
        window._preview_has_loaded_media = lambda preview: (
            preview is window.video_preview
        )
        window._resolve_media_path = lambda path: path

        inert_panel = types.SimpleNamespace(
            set_config=lambda *args, **kwargs: None,
            get_target_resolution=lambda: (360, 640),
        )
        window.advanced_config_panel = inert_panel
        window.json_preview = types.SimpleNamespace(
            set_config=lambda *args, **kwargs: None
        )
        for name in (
            "action_undo",
            "action_redo",
            "menu_action_undo",
            "menu_action_redo",
            "_shortcut_undo",
            "_shortcut_redo",
        ):
            setattr(window, name, _Action())
        return window

    def test_same_media_undo_redo_converts_inclusive_trim_to_worker_bounds(self):
        full_range = self._state(0, 99)
        selected_range = self._state(20, 80)
        window = self._window_for_same_media_undo(selected_range)
        window._undo_stack = [full_range]

        MainWindow._on_undo(window)
        MainWindow._on_redo(window)

        self.assertEqual(window.video_preview.timeline_ranges, [(0, 100), (20, 81)])
        self.assertEqual(window.timeline.in_points, [0, 20])
        self.assertEqual(window.timeline.out_points, [99, 80])
        self.assertEqual(window._loop_in_out, (20, 80))
        self.assertEqual(
            (
                window._config.editor.loop.in_frame,
                window._config.editor.loop.out_frame,
            ),
            (20, 80),
        )

    def test_same_media_undo_syncs_a_single_inclusive_frame(self):
        selected_range = self._state(42, 42)
        window = self._window_for_same_media_undo(self._state(0, 99))
        window._undo_stack = [selected_range]

        MainWindow._on_undo(window)

        self.assertEqual(window.video_preview.timeline_ranges, [(42, 43)])
        self.assertEqual(window.timeline.in_points, [42])
        self.assertEqual(window.timeline.out_points, [42])
        self.assertEqual(window._loop_in_out, (42, 42))
        self.assertEqual(
            (
                window._config.editor.loop.in_frame,
                window._config.editor.loop.out_frame,
            ),
            (42, 42),
        )

    def test_same_media_intro_restore_syncs_exclusive_worker_bounds(self):
        window = self._window_for_same_media_undo(self._state(0, 99))
        track = EditorTrackState(in_frame=20, out_frame=80)
        window._is_timeline_bound_to = lambda preview: (
            preview is window.intro_preview
        )
        window._preview_has_loaded_media = lambda preview: (
            preview is window.intro_preview
        )

        MainWindow._apply_undo_track(
            window,
            window.intro_preview,
            track,
            "intro.mp4",
            "intro.mp4",
            is_image=False,
            image_changed=False,
        )

        self.assertEqual(window.intro_preview.timeline_ranges, [(20, 81)])
        self.assertEqual(window.timeline.in_points, [20])
        self.assertEqual(window.timeline.out_points, [80])
        self.assertEqual(window._intro_in_out, (20, 80))

    def test_capture_tab_live_update_keeps_worker_rotated_non_square_frame(self):
        source_frame = np.arange(2 * 3 * 3, dtype=np.uint8).reshape(2, 3, 3)
        worker_rotated_frame = np.rot90(source_frame, -1).copy()
        window = MainWindow.__new__(MainWindow)
        window.preview_tabs = types.SimpleNamespace(currentIndex=lambda: 1)
        window._current_video_preview = _Preview(
            frame=worker_rotated_frame,
            rotation=90,
        )
        window.frame_capture_preview = _CapturePreview()

        def rotate_clockwise(frame, rotation):
            if rotation == 90:
                return np.rot90(frame, -1).copy()
            return frame

        with mock.patch.object(
            main_window.VideoPreviewWidget,
            "apply_rotation_to_frame",
            side_effect=rotate_clockwise,
        ):
            MainWindow._on_video_frame_changed(window, 0)

        self.assertEqual(len(window.frame_capture_preview.frames), 1)
        np.testing.assert_array_equal(
            window.frame_capture_preview.frames[0],
            worker_rotated_frame,
        )
        self.assertEqual(window.frame_capture_preview.frames[0].shape[:2], (3, 2))


if __name__ == "__main__":
    unittest.main()
