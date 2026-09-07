"""S7: discover memoization, _export_argb byte-identity, and undo/redo wiring."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import struct
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from tests.qt_harness import ensure_app


def setUpModule():
    ensure_app()


class DiscoverCacheTests(unittest.TestCase):
    def test_discover_is_memoized_and_refreshable(self):
        from core import media_tools
        from core.media_tools import MediaToolchain

        with tempfile.TemporaryDirectory() as d:
            media = Path(d) / "tools" / "media"
            media.mkdir(parents=True)
            for name in ("VSPipe.exe", "x264-7mod.exe", "MP4Box.exe"):
                (media / name).write_text("", encoding="utf-8")

            MediaToolchain.refresh()
            calls = {"n": 0}
            real_find = media_tools._find_tool

            def counting_find(*a, **k):
                calls["n"] += 1
                return real_find(*a, **k)

            with mock.patch.object(media_tools, "_find_tool", counting_find):
                MediaToolchain.discover(d)
                first = calls["n"]
                self.assertGreater(first, 0)
                MediaToolchain.discover(d)  # cached -> no new scans
                self.assertEqual(calls["n"], first)
                MediaToolchain.refresh()
                MediaToolchain.discover(d)  # rescans
                self.assertGreater(calls["n"], first)
        MediaToolchain.refresh()


@unittest.skipUnless(HAS_CV2, "opencv-python required")
class ArgbByteIdentityTests(unittest.TestCase):
    def _old_argb_bytes(self, mat):
        """The original per-pixel writer, inlined as the golden reference."""
        mat = cv2.rotate(mat, cv2.ROTATE_180).astype(np.uint8)
        height, width = mat.shape[:2]
        channels = mat.shape[-1] if len(mat.shape) == 3 else 1
        out = bytearray()
        for y in range(height):
            for x in range(width):
                if channels == 4:
                    b, g, r, a = mat[y, x]
                elif channels == 3:
                    b, g, r = mat[y, x]
                    a = 255
                else:
                    b = g = r = mat[y, x]
                    a = 255
                out += struct.pack("BBBB", int(b), int(g), int(r), int(a))
        return bytes(out)

    def test_vectorized_matches_old_loop(self):
        from core.export_service import ExportWorker

        rng = np.arange(3 * 5, dtype=np.uint8)
        cases = [
            np.stack([rng.reshape(3, 5)] * 4, axis=-1),          # BGRA
            np.stack([rng.reshape(3, 5)] * 3, axis=-1),          # BGR
            rng.reshape(3, 5),                                    # grayscale
        ]
        worker = ExportWorker()
        worker._cancelled = False
        for mat in cases:
            with tempfile.TemporaryDirectory() as d:
                out = os.path.join(d, "overlay.argb")
                worker._export_argb(out, mat.copy())
                got = Path(out).read_bytes()
            self.assertEqual(
                got, self._old_argb_bytes(mat),
                f"argb bytes diverged for shape {mat.shape}",
            )


class UndoRedoTests(unittest.TestCase):
    def _window(self):
        from gui.main_window import MainWindow
        from config.epconfig import EPConfig
        from PyQt6.QtCore import QTimer

        w = MainWindow.__new__(MainWindow)
        w._config = EPConfig()
        w._config.name = "start"
        w._base_dir = ""
        w._undo_stack = []
        w._redo_stack = []
        w._max_history = 50
        w._applying_undo = False
        w._restoring_editor_state = False
        w._undo_baseline = {}
        w._undo_timer = QTimer()  # no parent: MainWindow.__new__ has no C++ side
        w._undo_timer.setSingleShot(True)
        w._loop_in_out = (0, 0)
        w._intro_in_out = (0, 0)
        w._is_modified = False
        w._update_title = lambda: None
        w.status_bar = types.SimpleNamespace(showMessage=lambda *a, **k: None)
        # panels/previews are inert for name-only undo
        inert = types.SimpleNamespace(
            set_config=lambda *a, **k: None, set_epconfig=lambda *a, **k: None,
            get_target_resolution=lambda: (360, 640),
        )
        w.advanced_config_panel = inert
        w.json_preview = inert
        w.video_preview = types.SimpleNamespace(
            set_epconfig=lambda *a, **k: None,
            video_path="", total_frames=0,
        )
        w.intro_preview = types.SimpleNamespace(
            set_epconfig=lambda *a, **k: None, video_path="", total_frames=0)
        w._preview_has_loaded_media = lambda p: False
        w._resolve_media_path = lambda f: ""
        # MainWindow is a QWidget; on a __new__ instance getattr of an unset
        # attribute raises, so the undo/redo action widgets must be present.
        act = lambda: types.SimpleNamespace(setEnabled=lambda *a: None)
        for name in ("action_undo", "action_redo", "menu_action_undo",
                     "menu_action_redo", "_shortcut_undo", "_shortcut_redo"):
            setattr(w, name, act())
        w._reset_undo_history()
        return w, MainWindow

    def test_commit_coalesces_and_undo_restores(self):
        w, MainWindow = self._window()
        self.assertEqual(w._undo_baseline.get("name"), "start")

        w._config.name = "edited"
        MainWindow._commit_undo_snapshot(w)
        self.assertEqual(len(w._undo_stack), 1)

        MainWindow._on_undo(w)
        self.assertEqual(w._config.name, "start")
        self.assertEqual(len(w._redo_stack), 1)

        MainWindow._on_redo(w)
        self.assertEqual(w._config.name, "edited")

    def test_no_op_change_is_not_recorded(self):
        w, MainWindow = self._window()
        MainWindow._commit_undo_snapshot(w)  # nothing changed since baseline
        self.assertEqual(w._undo_stack, [])

    def test_undo_flushes_pending_debounce(self):
        w, MainWindow = self._window()
        w._config.name = "typing"
        w._undo_timer.start(800)  # burst in flight
        MainWindow._on_undo(w)    # must commit the burst, then undo it
        self.assertEqual(w._config.name, "start")

    def test_editor_crop_is_undoable(self):
        w, MainWindow = self._window()
        from config.epconfig import EditorTrackState

        w._config.editor.loop = EditorTrackState(crop=[1, 2, 30, 40], rotation=90)
        MainWindow._commit_undo_snapshot(w)
        MainWindow._on_undo(w)
        self.assertTrue(w._config.editor.loop.is_default(),
                        "undo must revert the crop/rotation too")


if __name__ == "__main__":
    unittest.main()
