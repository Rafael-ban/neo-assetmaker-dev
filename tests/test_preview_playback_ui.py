"""Offscreen playback acceptance when rendering takes longer than a tick."""

import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

from tests.qt_harness import ensure_app


class PlaybackUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_app()

    def test_slow_frame_remains_eligible_during_later_ticks(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        preview = VideoPreviewWidget()
        preview._has_video = True
        preview.total_frames = 100
        preview.is_playing = True
        preview._play_origin_ns = 0
        preview._play_origin_frame = 0
        preview._worker_ready_for_frames = True
        preview._render_session = SimpleNamespace(epoch=7)
        request_frame = Mock(side_effect=[11, 12])
        preview._worker_client = SimpleNamespace(request_frame=request_frame)
        preview._frame_request_target = lambda: ("final", preview.current_frame_index)
        preview._schedule_next_playback_tick = Mock()
        preview._display_frame = Mock()

        with patch("gui.widgets.video_preview.time.perf_counter_ns",
                   side_effect=[100_000_000, 150_000_000, 200_000_000]):
            preview._on_timer_tick()
            first_index = preview.current_frame_index
            preview._on_timer_tick()
            self.assertGreater(preview.current_frame_index, first_index)
            self.assertEqual(request_frame.call_count, 1)

            frame = np.zeros((2, 2, 3), dtype=np.uint8)
            preview._on_worker_frame(11, 7, "final", first_index, frame)
            self.assertEqual(preview._display_frame.call_count, 1)
            self.assertEqual(request_frame.call_count, 2)
            preview._on_timer_tick()

        self.assertEqual(request_frame.call_count, 2)
        preview.is_playing = False

    def test_playing_builtin_crop_waits_until_pause(self):
        from gui.widgets.video_preview import VideoPreviewWidget, get_app_dir

        preview = VideoPreviewWidget()
        preview.video_path = "source.mp4"
        preview._metadata_resolved = True
        preview.is_playing = True
        preview._session_metadata = SimpleNamespace(editor=object())
        preview._selection = SimpleNamespace(script_path=str(
            (Path(get_app_dir()) / "resources/vapoursynth/default_pipeline.vpy").resolve()
        ))
        preview._schedule_render_job(crop_only=True)
        self.assertTrue(preview._job_dirty)
        self.assertFalse(preview._job_debounce.isActive())
        preview.pause()
        self.assertTrue(preview._job_debounce.isActive())
        preview._job_debounce.stop()

    def test_custom_script_crop_still_reloads_during_playback(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        preview = VideoPreviewWidget()
        preview.video_path = "source.mp4"
        preview._metadata_resolved = True
        preview.is_playing = True
        preview._session_metadata = SimpleNamespace(editor=object())
        preview._selection = SimpleNamespace(script_path="custom.vpy")
        preview._schedule_render_job(crop_only=True)
        self.assertTrue(preview._job_debounce.isActive())
        preview._job_debounce.stop()
        preview.is_playing = False

    def test_pending_rotation_is_not_deferred_by_crop_commit(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        preview = VideoPreviewWidget()
        preview.video_path = "source.mp4"
        preview._metadata_resolved = True
        preview._can_defer_playing_crop = Mock(return_value=True)
        preview._schedule_render_job()
        preview._job_debounce.stop()  # A crop gesture suspends the debounce.
        preview._schedule_render_job(crop_only=True)
        self.assertTrue(preview._job_debounce.isActive())
        preview._job_debounce.stop()

    def test_reload_does_not_advance_playback_clock(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        preview = VideoPreviewWidget()
        preview._has_video = True
        preview.is_playing = True
        preview.current_frame_index = 27
        preview._render_session = SimpleNamespace(epoch=7)
        preview._worker_ready_for_frames = False
        preview._schedule_next_playback_tick = Mock()
        preview._on_timer_tick()
        self.assertEqual(preview.current_frame_index, 27)
        preview._schedule_next_playback_tick.assert_not_called()
        preview.is_playing = False

    def test_reload_metadata_resumes_clock_at_current_frame(self):
        from gui.widgets.video_preview import VideoPreviewWidget, _RequestOwner

        preview = VideoPreviewWidget()
        preview.is_playing = True
        preview.current_frame_index = 27
        preview._render_session = SimpleNamespace(epoch=7)
        preview._selection = SimpleNamespace(mode="compatible")
        preview._metadata_resolved = True
        preview._request_epochs[11] = _RequestOwner(7, "load")
        preview._request_current_frame = Mock()
        preview._schedule_next_playback_tick = Mock()
        metadata = SimpleNamespace(
            mode="compatible", editor=object(),
            output0=SimpleNamespace(num_frames=100),
        )
        with patch("gui.widgets.video_preview.time.perf_counter_ns", return_value=9_000):
            preview._on_worker_metadata(11, 7, metadata)
        self.assertTrue(preview._worker_ready_for_frames)
        self.assertEqual(preview._play_origin_frame, 27)
        self.assertEqual(preview._play_origin_ns, 9_000)
        preview._schedule_next_playback_tick.assert_called_once_with(1)
        preview.is_playing = False


if __name__ == "__main__":
    unittest.main()
