"""M4 review I1--I6: preview worker request identity and terminal lifecycle."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PyQt6.QtCore import QCoreApplication, Qt

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


def _node(*, final: bool) -> NodeMetadata:
    return NodeMetadata(
        width=384 if final else 1920,
        height=640 if final else 1080,
        num_frames=60,
        fps_num=30,
        fps_den=1,
        pixel_format="YUV420P8" if final else "RGB24",
        matrix="170m" if final else None,
        transfer="170m" if final else None,
        primaries="170m" if final else None,
        range="limited" if final else None,
    )


class PreviewWorkerReviewRegressionTests(unittest.TestCase):
    def setUp(self):
        import gui.widgets.video_preview as vp

        self.vp = vp
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.media = self.root / "review.mp4"
        self.media.write_bytes(b"fake")
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
            output0=_node(final=True),
            editor=_node(final=False),
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

    def _capture_request(self):
        self._load_resolved()
        received = []
        self.widget.capture_frame_async(received.append)
        return self.client.requests[-1], received

    def _emit_frame(self, request, value: int):
        self.client.frame_ready.emit(
            request.request_id,
            request.epoch,
            request.surface,
            request.index,
            np.full((4, 4, 3), value, dtype=np.uint8),
        )
        QCoreApplication.processEvents()

    def test_late_editor_response_cannot_overwrite_current_final_request(self):
        self._load_resolved()
        # 首次 metadata 后若控件排队了一个 resolved job，先让该 job 完成
        # metadata 回合。后面的 editor/final 请求必须属于同一个 active epoch，
        # 否则测试测到的只是预期的旧 epoch 丢弃，而不是 surface 身份问题。
        if self.widget._job_dirty:
            session = self.widget.flush_render_job()
            self.client.emit_metadata(
                self.widget._load_request_id,
                self._metadata(session.epoch),
            )
            QCoreApplication.processEvents()
        editor_request = self.client.requests[-1]
        self.widget.set_preview_mode(True)
        final_request = self.client.requests[-1]
        self.assertEqual((editor_request.surface, final_request.surface), ("editor", "final"))

        self._emit_frame(final_request, 22)
        self._emit_frame(editor_request, 11)

        self.assertEqual(int(self.widget.current_frame[0, 0, 0]), 22)

    def test_capture_uses_only_its_exact_request_identity(self):
        self._load_resolved()
        display_request = self.client.requests[-1]
        received = []
        self.widget.capture_frame_async(received.append)
        capture_request = self.client.requests[-1]
        self.assertEqual(
            (display_request.epoch, display_request.surface, display_request.index),
            (capture_request.epoch, capture_request.surface, capture_request.index),
        )

        self._emit_frame(display_request, 7)
        self.assertEqual(received, [])
        self._emit_frame(capture_request, 9)
        self.assertEqual(len(received), 1)
        self.assertEqual(int(received[0][0, 0, 0]), 9)

    def test_capture_failure_resolves_none_once(self):
        request, received = self._capture_request()

        self.client.request_failed.emit(request.request_id, "worker.failure", "failed")
        self.client.request_failed.emit(request.request_id, "worker.failure", "late")
        QCoreApplication.processEvents()

        self.assertEqual(received, [None])

    def test_capture_clear_crash_unavailable_and_close_each_resolve_none(self):
        request, received = self._capture_request()
        self.widget.clear()
        self.client.request_failed.emit(request.request_id, "worker.failure", "late")
        self.assertEqual(received, [None])

        self.setUp()
        _request, received = self._capture_request()
        self.client.worker_crashed.emit("worker exited")
        QCoreApplication.processEvents()
        self.assertEqual(received, [None])

        self.setUp()
        _request, received = self._capture_request()
        self.client.request_failed.emit(0, "worker.restart_failed", "unavailable")
        QCoreApplication.processEvents()
        self.assertEqual(received, [None])

        self.setUp()
        _request, received = self._capture_request()
        self.widget.close()
        QCoreApplication.processEvents()
        self.assertEqual(received, [None])

    def test_timeout_dialog_is_non_modal_and_stale_actions_are_discarded(self):
        self._load_resolved()
        request = self.client.requests[-1]
        self.client.request_timed_out.emit(request.request_id, request.epoch)
        QCoreApplication.processEvents()
        dialog = self.widget._timeout_dialogs[request.request_id]
        self.assertEqual(dialog.windowModality(), Qt.WindowModality.NonModal)

        self.widget.clear()
        self.assertNotIn(request.request_id, self.widget._timeout_dialogs)
        dialog.done(0)
        QCoreApplication.processEvents()
        self.assertEqual(self.client.continued, [])
        self.assertEqual(self.client.restarts, 0)

    def test_replaced_session_invalidates_old_timeout_dialog_actions(self):
        session_a = self._load_resolved()
        request_a = self.client.requests[-1]
        self.client.request_timed_out.emit(request_a.request_id, session_a.epoch)
        QCoreApplication.processEvents()
        old_dialog = self.widget._timeout_dialogs[request_a.request_id]

        session_b = self.widget.flush_render_job()
        self.assertIs(self.widget.current_render_session(), session_b)
        self.assertNotIn(request_a.request_id, self.widget._timeout_dialogs)
        old_dialog.done(0)
        QCoreApplication.processEvents()

        self.assertEqual(self.client.continued, [])
        self.assertEqual(self.client.restarts, 0)
        self.assertIs(self.widget.current_render_session(), session_b)

    def test_stale_metadata_releases_only_its_own_load_owner(self):
        # bootstrap 已解析，随后连续两个 resolved job 才能复现真实竞态：
        # A 仍在等待 metadata 时，B 已被 flush 成 current session。
        self._load_resolved()
        session_a = self.widget.flush_render_job()
        request_a = self.widget._load_request_id
        session_b = self.widget.flush_render_job()
        request_b = self.widget._load_request_id
        self.assertIn(request_a, self.widget._request_epochs)

        self.client.emit_metadata(
            request_a, self._metadata(session_a.epoch)
        )
        QCoreApplication.processEvents()

        self.assertNotIn(request_a, self.widget._request_epochs)
        self.assertIn(request_b, self.widget._request_epochs)
        self.assertIs(self.widget.current_render_session(), session_b)

        self.client.emit_metadata(
            request_b, self._metadata(session_b.epoch)
        )
        QCoreApplication.processEvents()
        self.assertTrue(self.widget._has_video)
        self.assertIs(self.widget.current_render_session(), session_b)

    def test_crash_pauses_playback_and_old_tick_cannot_resume_it(self):
        self._load_resolved()
        self.widget.play()
        self.assertTrue(self.widget.is_playing)
        self.assertTrue(self.widget.timer.isActive())
        index = self.widget.current_frame_index

        self.client.worker_crashed.emit("worker exited")
        QCoreApplication.processEvents()
        self.widget._on_timer_tick()

        self.assertFalse(self.widget.is_playing)
        self.assertFalse(self.widget.timer.isActive())
        self.assertEqual(self.widget.current_frame_index, index)

    def test_terminal_paths_release_request_owners_without_touching_id_zero(self):
        self._load_resolved()
        self.assertNotIn(self.widget._load_request_id, self.widget._request_epochs)

        ready = self.client.requests[-1]
        self._emit_frame(ready, 1)
        self.assertNotIn(ready.request_id, self.widget._request_epochs)

        self.widget._request_current_frame()
        discarded = self.client.requests[-1]
        self.client.frame_discarded.emit(
            discarded.request_id,
            discarded.epoch,
            discarded.surface,
            discarded.index,
        )
        QCoreApplication.processEvents()
        self.assertNotIn(discarded.request_id, self.widget._request_epochs)

        self.widget._request_current_frame()
        failed = self.client.requests[-1]
        self.client.request_failed.emit(failed.request_id, "worker.failure", "failed")
        QCoreApplication.processEvents()
        self.assertNotIn(failed.request_id, self.widget._request_epochs)

        self.widget._request_current_frame()
        normal = self.client.requests[-1]
        self.client.request_failed.emit(0, "worker.notice", "transport terminal")
        QCoreApplication.processEvents()
        self.assertIn(normal.request_id, self.widget._request_epochs)

        for _ in range(20):
            self.widget._request_current_frame(coalesce=True)
            request = self.client.requests[-1]
            self.client.frame_discarded.emit(
                request.request_id,
                request.epoch,
                request.surface,
                request.index,
            )
        QCoreApplication.processEvents()
        self.assertEqual(set(self.widget._request_epochs), {normal.request_id})


class PreviewWorkerMediaGateRegressionTests(unittest.TestCase):
    def test_preview_gate_requires_runtime_script_header_and_package_entry(self):
        from core.media_tools import MediaToolchain

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            from tests.test_vs_runtime_layout import _build_r79_fixture

            _build_r79_fixture(root)
            worker = root / "core" / "vs_runtime" / "worker_main.py"
            worker.parent.mkdir(parents=True)
            worker.write_text("# worker\n", encoding="utf-8")
            for relative in (
                "resources/vapoursynth/assetmaker_runner.vpy",
                "resources/vapoursynth/default_pipeline.vpy",
                "resources/vapoursynth/python/assetmaker_vs/executor.py",
                "resources/vapoursynth/python/assetmaker_vs/contract.py",
                "resources/vapoursynth/python/assetmaker_vs/display.py",
                "resources/vapoursynth/python/assetmaker_vs/job_api.py",
                "resources/vapoursynth/python/assetmaker_vs/runtime_fingerprint.py",
                "resources/vapoursynth/python/assetmaker_vs/runtime_layout.py",
                "resources/vapoursynth/python/assetmaker_vs/core_resources.py",
                "resources/vapoursynth/python/assetmaker_vs/native_plugins.py",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# present\n", encoding="utf-8")
            with (
                mock.patch("core.media_tools.get_app_dir", return_value=str(root)),
                mock.patch("core.media_tools.load_vs_runtime"),
                mock.patch(
                    "core.media_tools.resolve_worker_command",
                    return_value=("python", str(worker)),
                ),
            ):
                missing = MediaToolchain().missing_for_preview()

        self.assertIn("script_header.py", missing)
        self.assertIn("__init__.py", missing)


if __name__ == "__main__":
    unittest.main()
