"""P1：跨 client 的身份退休及普通交互快照稳定性。"""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import numpy as np
from PyQt6.QtCore import QCoreApplication, Qt

from config.vs_runtime import VSRuntimeConfig, WorkerConfig
from core.vs_runtime.snapshot import RuntimeSnapshot
from gui.widgets.video_preview import PreviewRenderContext, VideoPreviewWidget
from tests.qt_harness import ensure_app
from tests.test_preview_worker_integration import PreviewWorkerContractTests
from tests.test_preview_worker_lifecycle import _TerminalAwareFakeWorkerClient


ROOT = Path(__file__).resolve().parents[1]


def setUpModule():
    ensure_app()


class PreviewRuntimeSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.media = self.root / 'source.mp4'
        self.media.touch()
        self.a = RuntimeSnapshot(str(ROOT), VSRuntimeConfig(
            worker=WorkerConfig(15001, 10001, 3001)), 'a' * 64)
        self.b = RuntimeSnapshot(str(ROOT), VSRuntimeConfig(
            worker=WorkerConfig(15002, 10002, 3002)), 'b' * 64)
        self.clients = []

        def factory(parent):
            client = _TerminalAwareFakeWorkerClient()
            self.clients.append(client)
            return client

        self.widget = VideoPreviewWidget(worker_client_factory=factory)
        self.addCleanup(lambda: self.widget.clear(sync_shutdown=True))
        self.context = replace(PreviewRenderContext.builtin(
            project_root=str(self.root), track='loop',
            cache_dir=str(self.root / 'cache')), runtime_snapshot=self.a)
        self.widget.set_render_context(self.context)

    def load_resolved(self):
        self.assertTrue(self.widget.load_video(str(self.media)))
        client = self.clients[-1]
        session = client.loads[-1]
        client.emit_metadata(self.widget._load_request_id,
            PreviewWorkerContractTests._metadata(self, session.epoch))
        self.widget._job_debounce.stop()
        return client, session

    def test_reload_retires_client_and_queued_signals_cannot_consume_new_owner(self):
        old, session_a = self.load_resolved()
        old_path = Path(session_a.job_path)
        self.widget.set_render_context(replace(self.context, runtime_snapshot=self.b))
        self.assertEqual(old.closed, 1)
        self.assertFalse(old_path.exists())
        new, session_b = self.load_resolved()
        self.assertIsNot(old, new)
        self.assertEqual(session_a.runtime_fingerprint, 'a' * 64)
        self.assertEqual(session_b.runtime_fingerprint, 'b' * 64)
        request = new.requests[-1]
        owner = self.widget._request_epochs[request.request_id]
        # 两个 client 都从相同 request_id 开始，旧信号携带新身份也必须无效。
        old.frame_ready.emit(request.request_id, request.epoch,
                             request.surface, request.index, np.zeros((2, 2, 3)))
        old.worker_stopped.emit()
        old.request_failed.emit(0, 'worker.restart_failed', 'stale')
        old.operation_completed.emit(request.request_id, 'unload')
        QCoreApplication.processEvents()
        self.assertEqual(self.widget._request_epochs[request.request_id], owner)
        self.assertTrue(self.widget._worker_ready_for_frames)
        self.assertTrue(Path(session_b.job_path).exists())

    def test_trim_and_restart_keep_same_snapshot_without_reading_or_hashing(self):
        client, session = self.load_resolved()
        self.assertEqual(session.runtime_fingerprint, 'a' * 64)
        with mock.patch('core.vs_runtime.snapshot.RuntimeSnapshot.resolve',
                        side_effect=AssertionError('unexpected config read')):
            self.widget.set_timeline_range(1, 30)
            trimmed = self.widget.flush_render_job()
            self.assertEqual(trimmed.runtime_fingerprint, 'a' * 64)
            self.widget.restart_rendering()
            client.ready.emit()
        self.assertIs(client.loads[-1], trimmed)
        self.assertEqual(client.restarts, 1)

    def test_default_context_freezes_one_snapshot_for_session_and_client(self):
        """独立 widget 也不能分别读取 session/client 的 runtime。"""
        self.widget.set_render_context(None)
        with mock.patch(
            "gui.widgets.video_preview.RuntimeSnapshot.resolve", return_value=self.a
        ) as resolve:
            client, session = self.load_resolved()
            self.assertEqual(session.runtime_fingerprint, self.a.fingerprint)
            self.assertIs(self.widget._worker_runtime_snapshot, self.a)
            self.assertEqual(resolve.call_count, 1)
            with mock.patch(
                "gui.widgets.video_preview.RuntimeSnapshot.resolve",
                side_effect=AssertionError("unexpected default config read"),
            ):
                self.widget.set_timeline_range(1, 30)
                trimmed = self.widget.flush_render_job()
                self.assertEqual(trimmed.runtime_fingerprint, self.a.fingerprint)

    def test_blocked_context_retires_a_client_before_b_is_applied(self):
        """A→blocked→B 不能让 B session 复用 A client。"""
        old, session_a = self.load_resolved()
        self.widget.set_execution_blocked("blocked for reload")
        self.assertEqual(old.closed, 1)
        self.assertFalse(Path(session_a.job_path).exists())
        self.widget.set_render_context(replace(self.context, runtime_snapshot=self.b))
        new, session_b = self.load_resolved()
        self.assertIsNot(old, new)
        self.assertEqual(session_b.runtime_fingerprint, self.b.fingerprint)

    def test_default_client_retires_before_explicit_context_is_applied(self):
        """已经启动的默认 client 不能跨到首次显式 context。"""
        self.widget.set_render_context(None)
        with mock.patch(
            "gui.widgets.video_preview.RuntimeSnapshot.resolve", return_value=self.a
        ):
            old, _session_a = self.load_resolved()
        self.widget.set_render_context(replace(self.context, runtime_snapshot=self.b))
        self.assertEqual(old.closed, 1)
        new, session_b = self.load_resolved()
        self.assertIsNot(old, new)
        self.assertEqual(session_b.runtime_fingerprint, self.b.fingerprint)


if __name__ == '__main__':
    unittest.main()
