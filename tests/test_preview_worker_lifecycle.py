"""M4：预览 worker 的传输终态必须留下可恢复的 UI。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt6.QtCore import QCoreApplication, pyqtSignal

from config.vs_runtime import VSRuntimeConfig
from core.vs_runtime.snapshot import RuntimeSnapshot
from core.vs_runtime.script_header import parse_script_header
from core.vs_runtime.session import (
    NodeMetadata,
    ScriptSelection,
    SessionMetadata,
    compute_script_bundle_hash,
)
from tests.qt_harness import ensure_app
from tests.test_preview_worker_integration import FakeWorkerClient


def setUpModule():
    ensure_app()


class PreviewWorkerLifecycleTests(unittest.TestCase):
    def setUp(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        self.client = FakeWorkerClient()
        self.widget = VideoPreviewWidget(
            worker_client_factory=lambda _parent: self.client
        )
        self.addCleanup(lambda: self.widget.clear(sync_shutdown=True))
        self.widget._ensure_worker()
        # A retry preserves the session; it must not turn into load_failed.
        self.widget._render_session = object()
        self.widget._restart_pending = True
        self.widget._worker_ready_for_frames = True
        self.widget._vs_active = True

    def _assert_terminal_is_recoverable(self, code: str):
        failures = []
        self.widget.load_failed.connect(failures.append)
        self.client.request_failed.emit(0, code, "replacement worker failed")
        QCoreApplication.processEvents()

        self.assertFalse(self.widget._restart_pending)
        self.assertFalse(self.widget._worker_ready_for_frames)
        self.assertFalse(self.widget._vs_active)
        self.assertEqual(self.widget.video_label.text(), "渲染进程已退出")
        # The offscreen parent is never shown, so isVisible() stays false even
        # after setVisible(True). isHidden() checks the widget's own state.
        self.assertFalse(self.widget.restart_button.isHidden())
        self.assertEqual(failures, [])

        self.widget.restart_rendering()
        self.assertEqual(self.client.restarts, 1)
        self.assertTrue(self.widget._restart_pending)

    def test_restart_failure_leaves_a_recoverable_ui(self):
        self._assert_terminal_is_recoverable("worker.restart_failed")

    def test_staging_cleanup_terminal_leaves_a_recoverable_ui(self):
        self._assert_terminal_is_recoverable("worker.staging_cleanup_failed")

    def test_worker_crash_keeps_the_same_recovery_contract(self):
        failures = []
        self.widget.load_failed.connect(failures.append)
        self.client.worker_crashed.emit("worker exited")
        QCoreApplication.processEvents()

        self.assertFalse(self.widget._restart_pending)
        self.assertFalse(self.widget._worker_ready_for_frames)
        self.assertFalse(self.widget._vs_active)
        self.assertEqual(self.widget.video_label.text(), "渲染进程已退出")
        self.assertFalse(self.widget.restart_button.isHidden())
        self.assertEqual(failures, [])

class _TerminalAwareFakeWorkerClient(FakeWorkerClient):
    """以既有 fake 为基础，只补充真实 client 将公开的终态信号。"""

    operation_completed = pyqtSignal(object, str)
    worker_stopped = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.unload_request_ids = []

    def unload(self):
        request_id = super().unload()
        self.unload_request_ids.append(request_id)
        return request_id


class _RelayTransport:
    """只实现 VSWorkerClient 建立和消费单个 wire event 所需的最小接口。"""

    generation = 1
    pid = None
    alive = False

    def __init__(self):
        self.listener = None
        self.closed = False

    def add_listener(self, listener):
        self.listener = listener

    def remove_listener(self, listener):
        if self.listener is listener:
            self.listener = None

    def close(self):
        self.closed = True


class VSWorkerClientLifecycleSignalTests(unittest.TestCase):
    def test_existing_control_terminal_and_worker_exit_are_relayed(self):
        from gui.workers.vs_worker_client import VSWorkerClient

        transport = _RelayTransport()
        client = VSWorkerClient(transport=transport, worker_config=mock.Mock())
        self.addCleanup(client.close)
        completed = []
        stopped = []
        client.operation_completed.connect(
            lambda request_id, operation: completed.append((request_id, operation))
        )
        client.worker_stopped.connect(lambda: stopped.append(True))

        client._handle_transport_event(
            {
                "type": "ready",
                "generation": transport.generation,
                "request_id": 17,
                "operation": "unload",
            }
        )
        client._handle_transport_event(
            {
                "type": "worker_exited",
                "generation": transport.generation,
                "exit_code": 0,
            }
        )

        self.assertEqual(completed, [(17, "unload")])
        self.assertEqual(stopped, [True])


class PreviewWorkerRetirementTests(unittest.TestCase):
    def setUp(self):
        import gui.widgets.video_preview as vp

        self.vp = vp
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.media = self.root / "素材.mp4"
        self.media.write_bytes(b"fake media")
        script = (
            Path(__file__).resolve().parents[1]
            / "resources"
            / "vapoursynth"
            / "default_pipeline.vpy"
        )
        header = parse_script_header(script)
        selection = ScriptSelection.from_header(
            script,
            header,
            compute_script_bundle_hash(script),
        )
        self.client = _TerminalAwareFakeWorkerClient()
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
                selection=selection,
                cache_dir=str(self.root / "cache"),
                runtime_snapshot=self.snapshot,
            )
        )

    @staticmethod
    def _metadata(epoch: int) -> SessionMetadata:
        return SessionMetadata(
            epoch=epoch,
            mode="compatible",
            capabilities=frozenset({"source", "trim", "crop", "rotation"}),
            output0=NodeMetadata(
                width=384,
                height=640,
                num_frames=90,
                fps_num=30,
                fps_den=1,
                pixel_format="YUV420P8",
                matrix="170m",
                transfer="170m",
                primaries="170m",
                range="limited",
            ),
            editor=NodeMetadata(
                width=240,
                height=360,
                num_frames=90,
                fps_num=30,
                fps_den=1,
                pixel_format="RGB24",
                matrix=None,
                transfer=None,
                primaries=None,
                range=None,
            ),
        )

    def _load_resolved(self):
        self.assertTrue(self.widget.load_video(str(self.media)))
        session = self.client.loads[-1]
        self.client.emit_metadata(
            self.widget._load_request_id,
            self._metadata(session.epoch),
        )
        QCoreApplication.processEvents()
        return session

    def test_clear_drops_late_frame_and_releases_job_on_unload_terminal(self):
        session = self._load_resolved()
        request = self.client.requests[-1]
        job_path = Path(session.job_path)
        self.assertTrue(job_path.is_file())

        self.widget.clear()
        self.client.frame_ready.emit(
            request.request_id,
            request.epoch,
            request.surface,
            request.index,
            np.full((4, 4, 3), 255, dtype=np.uint8),
        )
        QCoreApplication.processEvents()
        self.assertIsNone(self.widget.current_frame)
        self.assertEqual(self.widget.video_label.text(), "No media loaded")
        self.assertTrue(job_path.is_file())

        self.client.operation_completed.emit(
            self.client.unload_request_ids[-1],
            "unload",
        )
        QCoreApplication.processEvents()
        self.assertFalse(job_path.exists())

    def test_repeated_flushes_collect_retired_jobs_but_keep_current_job(self):
        current = self._load_resolved()
        retired_paths = []
        unload_request_ids = []
        for _ in range(4):
            retired_paths.append(Path(current.job_path))
            current = self.widget.flush_render_job()
            unload_request_ids.append(self.client.unload_request_ids[-1])

        current_path = Path(current.job_path)
        self.assertTrue(current_path.is_file())
        self.assertTrue(all(path.is_file() for path in retired_paths))

        for request_id in unload_request_ids:
            self.client.operation_completed.emit(request_id, "unload")
        QCoreApplication.processEvents()

        self.assertTrue(current_path.is_file())
        self.assertTrue(all(not path.exists() for path in retired_paths))

    def test_worker_death_releases_a_retiring_job(self):
        session = self._load_resolved()
        job_path = Path(session.job_path)
        self.widget.clear()
        self.assertTrue(job_path.is_file())

        self.client.worker_stopped.emit()
        QCoreApplication.processEvents()

        self.assertFalse(job_path.exists())


if __name__ == "__main__":
    unittest.main()
