"""Offscreen playback acceptance when rendering takes longer than a tick."""

import os
import unittest
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
            preview._on_timer_tick()

        self.assertEqual(request_frame.call_count, 2)
        preview.is_playing = False


if __name__ == "__main__":
    unittest.main()
