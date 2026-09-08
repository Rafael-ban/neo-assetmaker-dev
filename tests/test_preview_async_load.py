"""M4: worker 预览的异步受理、结果和 epoch 生命周期。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import time
import tempfile
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
from PyQt6.QtCore import QCoreApplication

from config.vs_runtime import VSRuntimeConfig
from core.media_tools import MediaToolchain
from core.vs_runtime.snapshot import RuntimeSnapshot
from core.vs_runtime.script_header import parse_script_header
from core.vs_runtime.session import (
    ScriptSelection,
    SessionMetadata,
    compute_script_bundle_hash,
)
from tests.test_preview_worker_integration import FakeWorkerClient, _node

REPO = Path(__file__).resolve().parent.parent
TC = MediaToolchain.discover(str(REPO))
ENCODE_OK = HAS_CV2 and not TC.missing_for_export()


def _resolved_path(path: str | Path) -> str:
    """Match the widget's deliberate canonical media-path representation."""
    return str(Path(path).resolve())


def setUpModule():
    ensure_app()


class AsyncLoadTests(unittest.TestCase):
    def setUp(self):
        import gui.widgets.video_preview as vp

        self.vp = vp
        self.client = FakeWorkerClient()
        self.tmp = tempfile.mkdtemp()
        self.file_a = os.path.join(self.tmp, "a.mp4")
        self.file_b = os.path.join(self.tmp, "b.mp4")
        Path(self.file_a).write_bytes(b"x")
        Path(self.file_b).write_bytes(b"x")
        script = REPO / "resources" / "vapoursynth" / "default_pipeline.vpy"
        header = parse_script_header(script)
        selection = ScriptSelection.from_header(
            script, header, compute_script_bundle_hash(script)
        )
        self.snapshot = RuntimeSnapshot(str(REPO), VSRuntimeConfig(), "b" * 64)
        self.w = vp.VideoPreviewWidget(
            worker_client_factory=lambda _parent: self.client
        )
        self.addCleanup(lambda: self.w.clear(sync_shutdown=True))
        self.w.set_render_context(
            vp.PreviewRenderContext(
                project_root=self.tmp,
                track="loop",
                selection=selection,
                cache_dir=os.path.join(self.tmp, "cache"),
                runtime_snapshot=self.snapshot,
            )
        )

    @staticmethod
    def _metadata(epoch, width=240, height=360, frames=90):
        return SessionMetadata(
            epoch=epoch,
            mode="compatible",
            capabilities=frozenset({"source"}),
            output0=_node(
                frames=frames,
                fps=(30, 1),
                size=(384, 640),
                final=True,
            ),
            editor=_node(
                frames=frames,
                fps=(30, 1),
                size=(width, height),
                final=False,
            ),
        )

    def test_load_is_accepted_without_waiting_for_metadata(self):
        loaded = {}
        self.w.video_loaded.connect(lambda n, fps: loaded.update(n=n, fps=fps))
        self.assertTrue(self.w.load_video(self.file_a))
        self.assertEqual(
            self.client.loads[-1].runtime_fingerprint, self.snapshot.fingerprint
        )
        # Async acceptance is a causal contract, not a benchmark tied to one
        # runner's filesystem/antivirus latency.  FakeWorkerClient.load() only
        # records the request and deliberately emits no metadata here: if
        # load_video() waited for probing, this call could not have returned.
        self.assertEqual(len(self.client.loads), 1)
        self.assertEqual(loaded, {})
        self.assertEqual(self.w.video_label.text(), "正在加载视频元数据…")
        self.assertFalse(self.w._has_video)

        epoch = self.client.loads[-1].epoch
        self.client.emit_metadata(self.w._load_request_id, self._metadata(epoch))
        QCoreApplication.processEvents()
        self.assertEqual(loaded.get("n"), 90)
        self.assertTrue(self.w._has_video)
        self.assertEqual(self.w.video_path, _resolved_path(self.file_a))
        self.assertEqual((self.w.video_width, self.w.video_height), (240, 360))

    def test_probe_failure_emits_load_failed(self):
        failures = []
        self.w.load_failed.connect(failures.append)
        self.assertTrue(self.w.load_video(self.file_a))
        self.client.request_failed.emit(self.w._load_request_id, "script.error", "boom")
        QCoreApplication.processEvents()
        self.assertEqual(failures, ["boom"])
        self.assertFalse(self.w._has_video)
        self.assertEqual(self.w.video_label.text(), "无法加载视频元数据")

    def test_stale_probe_result_is_discarded_after_newer_load(self):
        self.assertTrue(self.w.load_video(self.file_a))
        epoch_a = self.client.loads[-1].epoch
        request_a = self.w._load_request_id
        self.assertTrue(self.w.load_video(self.file_b))
        epoch_b = self.client.loads[-1].epoch
        request_b = self.w._load_request_id

        loaded = []
        self.w.video_loaded.connect(lambda n, fps: loaded.append(self.w.video_path))
        self.client.emit_metadata(
            request_a, self._metadata(epoch_a, width=111, height=222)
        )
        QCoreApplication.processEvents()
        self.assertEqual(loaded, [], "stale probe result must be discarded")
        self.assertFalse(self.w._has_video)

        self.client.emit_metadata(request_b, self._metadata(epoch_b))
        QCoreApplication.processEvents()
        self.assertEqual(loaded, [_resolved_path(self.file_b)])
        self.assertEqual(self.w.video_path, _resolved_path(self.file_b))

    def test_stale_probe_failure_is_discarded_after_clear(self):
        failures = []
        self.w.load_failed.connect(failures.append)
        self.assertTrue(self.w.load_video(self.file_a))
        request_id = self.w._load_request_id
        self.w.clear()
        self.client.request_failed.emit(request_id, "script.error", "late failure")
        QCoreApplication.processEvents()
        self.assertEqual(failures, [], "failure from a cleared load must be dropped")


@unittest.skipUnless(ENCODE_OK, "encode toolchain (tools/media) unavailable")
class AsyncLoadRealMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.helpers.m5_render_fixture import (
            build_default_render_session,
            encode_render_session,
        )

        cls.d = Path(tempfile.mkdtemp())
        png = cls.d / "m.png"
        cv2.imwrite(str(png), np.full((360, 240, 3), 128, np.uint8))
        cls.mp4 = cls.d / "src.mp4"
        session = build_default_render_session(
            cls.d / "render-session",
            source_path=png,
            source_kind="image",
            end_frame=30,
        )
        encode_render_session(TC, session, cls.mp4)

    def _pump_until(self, condition, timeout_s):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            if condition():
                return True
            time.sleep(0.01)
        return False

    def test_widget_loads_real_video_asynchronously(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        w = VideoPreviewWidget()
        self.addCleanup(lambda: w.clear(sync_shutdown=True))
        loaded = {}
        w.video_loaded.connect(lambda n, fps: loaded.update(n=n, fps=fps))
        self.assertTrue(w.load_video(str(self.mp4)))
        self.assertEqual(loaded, {}, "metadata must arrive asynchronously")
        self.assertFalse(w._has_video)
        self.assertTrue(self._pump_until(lambda: "n" in loaded, 20.0), "no video_loaded")
        self.assertGreater(loaded["n"], 0)
        self.assertTrue(
            self._pump_until(
                lambda: w._vs_active and w.current_frame is not None, 20.0),
            "preview never produced a frame",
        )

    def test_reload_mid_probe_settles_on_second_video(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        w = VideoPreviewWidget()
        self.addCleanup(lambda: w.clear(sync_shutdown=True))
        events = []
        w.video_loaded.connect(lambda n, fps: events.append(w.video_path))
        self.assertTrue(w.load_video(str(self.mp4)))
        self.assertTrue(w.load_video(str(self.mp4)))  # immediate reload mid-probe
        self.assertTrue(self._pump_until(lambda: events, 20.0), "no video_loaded")
        time.sleep(0.3)
        QCoreApplication.processEvents()
        self.assertEqual(len(events), 1, "stale probe must not double-fire video_loaded")
        self.assertEqual(w.video_path, _resolved_path(self.mp4))
        self.assertTrue(w._has_video)


if __name__ == "__main__":
    unittest.main()
