"""M4: Qt 预览只能通过 VSWorkerClient 消费用户 ``.vpy``。"""

from __future__ import annotations

import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PyQt6.QtCore import QObject, QCoreApplication, pyqtSignal

from config.vs_runtime import VSRuntimeConfig
from core.vs_runtime.job import RationalFPS, load_render_job
from core.vs_runtime.snapshot import RuntimeSnapshot
from core.vs_runtime.script_header import parse_script_header
from core.vs_runtime.session import (
    NodeMetadata,
    ScriptSelection,
    SessionMetadata,
    compute_script_bundle_hash,
)
from tests.qt_harness import ensure_app


def setUpModule():
    ensure_app()


def _node(
    *,
    frames: int,
    fps: tuple[int, int],
    size: tuple[int, int],
    final: bool,
) -> NodeMetadata:
    return NodeMetadata(
        width=size[0],
        height=size[1],
        num_frames=frames,
        fps_num=fps[0],
        fps_den=fps[1],
        pixel_format="YUV420P8" if final else "RGB24",
        matrix="170m" if final else None,
        transfer="170m" if final else None,
        primaries="170m" if final else None,
        range="limited" if final else None,
    )


class FakeWorkerClient(QObject):
    ready = pyqtSignal()
    metadata_ready = pyqtSignal(object, object, object)
    frame_ready = pyqtSignal(object, object, str, object, object)
    frame_discarded = pyqtSignal(object, object, str, object)
    request_failed = pyqtSignal(object, str, str)
    request_timed_out = pyqtSignal(object, object)
    worker_crashed = pyqtSignal(str)
    log_received = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.started = 0
        self.closed = 0
        self.loads = []
        self.requests = []
        self.cancelled = []
        self.unloads = 0
        self.restarts = 0
        self.continued = []
        self._request_id = 10

    def _id(self):
        self._request_id += 1
        return self._request_id

    def start(self):
        self.started += 1
        return self._id()

    def load(self, session):
        self.loads.append(session)
        return self._id()

    def emit_metadata(self, request_id, metadata):
        self.metadata_ready.emit(request_id, metadata.epoch, metadata)

    def request_frame(self, **kwargs):
        request_id = self._id()
        self.requests.append(types.SimpleNamespace(request_id=request_id, **kwargs))
        return request_id

    def cancel_epoch(self, epoch):
        self.cancelled.append(epoch)
        return self._id()

    def unload(self):
        self.unloads += 1
        return self._id()

    def close(self):
        self.closed += 1

    def continue_wait(self, request_id):
        self.continued.append(request_id)
        return True

    def terminate_and_restart(self):
        self.restarts += 1


class PreviewWorkerContractTests(unittest.TestCase):
    def setUp(self):
        import gui.widgets.video_preview as vp

        self.vp = vp
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / "cache"
        self.media = self.root / "中文素材.mp4"
        self.media.write_bytes(b"not-decoded-by-fake")
        script = Path(__file__).resolve().parents[1] / "resources" / "vapoursynth" / "default_pipeline.vpy"
        header = parse_script_header(script)
        self.selection = ScriptSelection.from_header(
            script, header, compute_script_bundle_hash(script)
        )
        self.client = FakeWorkerClient()
        self.snapshot = RuntimeSnapshot(
            str(Path(__file__).resolve().parents[1]), VSRuntimeConfig(), "a" * 64
        )
        self.widget = vp.VideoPreviewWidget(
            worker_client_factory=lambda _parent: self.client
        )
        self.addCleanup(lambda: self.widget.clear(sync_shutdown=True))
        self.widget.set_render_context(
            vp.PreviewRenderContext(
                project_root=str(self.root),
                track="loop",
                selection=self.selection,
                cache_dir=str(self.cache),
                runtime_snapshot=self.snapshot,
            )
        )

    def _metadata(self, epoch: int, *, compatible=True) -> SessionMetadata:
        return SessionMetadata(
            epoch=epoch,
            mode="compatible" if compatible else "raw",
            capabilities=frozenset({"source", "trim", "crop", "rotation"}),
            output0=_node(
                frames=61 if compatible else 120,
                fps=(30_000, 1_001),
                size=(384, 640),
                final=True,
            ),
            editor=(
                _node(
                    frames=300,
                    fps=(30_000, 1_001),
                    size=(1920, 1080),
                    final=False,
                )
                if compatible
                else None
            ),
        )

    def _load_compatible(self):
        self.assertTrue(self.widget.load_video(str(self.media)))
        session = self.client.loads[-1]
        job = load_render_job(session.job_path)
        self.assertIsNone(job.timeline.end_frame)
        self.assertIsNone(job.timeline.fps)
        self.client.emit_metadata(
            self.widget._load_request_id, self._metadata(session.epoch)
        )
        QCoreApplication.processEvents()
        return session

    def _resolve_current(self, *, compatible=True):
        epoch = self.client.loads[-1].epoch
        self.client.emit_metadata(
            self.widget._load_request_id,
            self._metadata(epoch, compatible=compatible),
        )
        QCoreApplication.processEvents()

    def test_bootstrap_uses_output1_metadata_and_preserves_rational_fps(self):
        loaded = []
        self.widget.video_loaded.connect(lambda n, fps: loaded.append((n, fps)))

        session = self._load_compatible()

        self.assertEqual(self.client.started, 1)
        self.assertEqual(self.widget.total_frames, 300)
        self.assertEqual((self.widget.video_width, self.widget.video_height), (1920, 1080))
        self.assertEqual(self.widget._fps_rational, RationalFPS(30_000, 1_001))
        self.assertAlmostEqual(loaded[-1][1], 30_000 / 1_001, places=12)
        self.assertEqual(self.widget.current_render_session(), session)
        request = self.client.requests[-1]
        self.assertEqual((request.surface, request.index), ("editor", 0))

    def test_resolved_range_and_surface_indices_use_exclusive_end(self):
        self._load_compatible()
        self.widget.set_timeline_range(20, 81)
        session = self.widget.flush_render_job()
        self._resolve_current()
        job = load_render_job(session.job_path)
        self.assertEqual((job.timeline.start_frame, job.timeline.end_frame), (20, 81))
        self.assertEqual(job.timeline.fps, RationalFPS(30_000, 1_001))

        self.widget.current_frame_index = 27
        self.widget.set_preview_mode(False)
        self.widget._request_current_frame()
        self.assertEqual(
            (self.client.requests[-1].surface, self.client.requests[-1].index),
            ("editor", 27),
        )

        self.widget.set_preview_mode(True)
        self.widget._request_current_frame()
        self.assertEqual(
            (self.client.requests[-1].surface, self.client.requests[-1].index),
            ("final", 7),
        )

    def test_final_mode_clamps_source_index_and_raw_always_uses_final(self):
        self._load_compatible()
        self.widget.set_timeline_range(20, 81)
        self.widget.flush_render_job()
        self._resolve_current()
        changed = []
        self.widget.frame_changed.connect(changed.append)
        self.widget.current_frame_index = 4
        self.widget.set_preview_mode(True)
        self.assertEqual(self.widget.current_frame_index, 20)
        self.assertEqual(changed[-1], 20)
        self.assertEqual(
            (self.client.requests[-1].surface, self.client.requests[-1].index),
            ("final", 0),
        )

        self.widget.clear()
        raw_script = self.root / "raw.vpy"
        raw_script.write_text(
            "# assetmaker-api: 1\n"
            "# assetmaker-mode: raw\n"
            "# assetmaker-capabilities: source\n"
            "# assetmaker-requires:\n"
            "# assetmaker-editor-output: 0\n",
            encoding="utf-8",
        )
        raw_header = parse_script_header(raw_script)
        raw_selection = ScriptSelection.from_header(
            raw_script, raw_header, compute_script_bundle_hash(raw_script)
        )
        self.widget.set_render_context(
            self.vp.PreviewRenderContext(
                project_root=str(self.root),
                track="loop",
                selection=raw_selection,
                cache_dir=str(self.cache),
            )
        )
        self.assertTrue(self.widget.load_video(str(self.media)))
        epoch = self.client.loads[-1].epoch
        self.client.emit_metadata(
            self.widget._load_request_id,
            self._metadata(epoch, compatible=False),
        )
        QCoreApplication.processEvents()
        self.widget.current_frame_index = 200
        self.widget.set_preview_mode(False)
        if not self.widget._worker_ready_for_frames:
            self._resolve_current(compatible=False)
        self.widget._request_current_frame()
        self.assertEqual(
            (self.client.requests[-1].surface, self.client.requests[-1].index),
            ("final", 119),
        )

    def test_missing_compatible_editor_metadata_fails_loudly(self):
        failures = []
        self.widget.load_failed.connect(failures.append)
        script = self.root / "editor-required.vpy"
        script.write_text(
            "# assetmaker-api: 1\n"
            "# assetmaker-mode: compatible\n"
            "# assetmaker-capabilities: source,trim,crop,rotation\n"
            "# assetmaker-requires:\n"
            "# assetmaker-editor-output: 1\n",
            encoding="utf-8",
        )
        header = parse_script_header(script)
        self.widget.set_render_context(
            self.vp.PreviewRenderContext(
                project_root=str(self.root),
                track="loop",
                selection=ScriptSelection.from_header(
                    script, header, compute_script_bundle_hash(script)
                ),
                cache_dir=str(self.cache),
                header=header,
            )
        )
        self.assertTrue(self.widget.load_video(str(self.media)))
        epoch = self.client.loads[-1].epoch
        metadata = self._metadata(epoch)
        metadata = SessionMetadata(
            epoch=epoch,
            mode="compatible",
            capabilities=metadata.capabilities,
            output0=metadata.output0,
            editor=None,
        )
        self.client.emit_metadata(self.widget._load_request_id, metadata)
        QCoreApplication.processEvents()
        self.assertFalse(self.widget._has_video)
        self.assertEqual(len(failures), 1)
        self.assertIn("output 1", failures[0])

    def test_stale_metadata_frame_and_failure_cannot_repopulate_clear(self):
        failures = []
        self.widget.load_failed.connect(failures.append)
        self.assertTrue(self.widget.load_video(str(self.media)))
        stale_session = self.client.loads[-1]
        stale_request = self.client.loads and self.widget._load_request_id
        self.widget.clear()
        self.client.emit_metadata(
            stale_request, self._metadata(stale_session.epoch)
        )
        self.client.frame_ready.emit(
            999,
            stale_session.epoch,
            "editor",
            0,
            np.full((4, 4, 3), 255, np.uint8),
        )
        self.client.request_failed.emit(stale_request, "script.error", "late")
        QCoreApplication.processEvents()
        self.assertIsNone(self.widget.current_frame)
        self.assertEqual(self.widget.total_frames, 0)
        self.assertEqual(failures, [])

    def test_capture_is_non_coalesced_and_returns_owned_bgr_copy(self):
        self._load_compatible()
        self.client.requests.clear()
        received = []
        self.widget.capture_frame_async(received.append)
        request = self.client.requests[-1]
        self.assertFalse(request.coalesce)
        frame = np.full((8, 6, 3), (2, 3, 4), np.uint8)
        self.client.frame_ready.emit(
            request.request_id,
            request.epoch,
            request.surface,
            request.index,
            frame,
        )
        QCoreApplication.processEvents()
        self.assertEqual(len(received), 1)
        self.assertFalse(np.shares_memory(received[0], frame))
        self.assertEqual(tuple(received[0][0, 0]), (2, 3, 4))

    def test_crop_rotation_debounce_and_explicit_flush(self):
        self._load_compatible()
        initial = len(self.client.loads)
        self.widget.set_cropbox(10, 20, 300, 500)
        self.widget.set_rotation(90)
        deadline = time.monotonic() + 0.4
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.01)
        self.assertEqual(len(self.client.loads), initial + 1)

        self.widget.set_cropbox(12, 22, 280, 498)
        session = self.widget.flush_render_job()
        after_flush = len(self.client.loads)
        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.01)
        self.assertEqual(len(self.client.loads), after_flush)
        job = load_render_job(session.job_path)
        self.assertEqual(job.transform.rotation, 90)
        self.assertEqual(
            (job.transform.crop.x, job.transform.crop.y),
            tuple(self.widget.cropbox[:2]),
        )


if __name__ == "__main__":
    unittest.main()
