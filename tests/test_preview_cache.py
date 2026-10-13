"""Preview caches isolate restarted processes and retire only after worker unload."""

from __future__ import annotations

import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from PyQt6.QtCore import QCoreApplication

from core.vs_runtime.job import load_render_job
from core.vs_runtime.preview_cache import clear_preview_cache
from resources.vapoursynth.python.assetmaker_vs import job_api
from tests import test_preview_worker_lifecycle as lifecycle
from tests.qt_harness import ensure_app


def setUpModule():
    ensure_app()


class PreviewCacheTests(unittest.TestCase):
    def setUp(self):
        lifecycle.PreviewWorkerRetirementTests.setUp(self)
        if os.name != "nt":
            # Fake-worker lifecycle tests use host temp paths; the production
            # RenderJob wire format intentionally only accepts Windows paths.
            path_check = mock.patch.object(
                job_api, "is_canonical_windows_absolute_path",
                side_effect=lambda value: isinstance(value, str) and Path(value).is_absolute(),
            )
            path_check.start()
            self.addCleanup(path_check.stop)

    _load_resolved = lifecycle.PreviewWorkerRetirementTests._load_resolved
    _metadata = staticmethod(lifecycle.PreviewWorkerRetirementTests._metadata)

    def test_new_widgets_ignore_abandoned_jobs_and_isolate_identical_epochs(self):
        root = self.root / "cache"
        root.mkdir()
        legacy = root / "job-1.json"
        legacy.write_text("abandoned job", encoding="utf-8")
        sibling = root / "session-another-process"
        sibling.mkdir()
        sentinel = sibling / "source.cache"
        sentinel.write_bytes(b"keep")

        first = self._load_resolved()
        other_client = lifecycle._TerminalAwareFakeWorkerClient()
        other = self.vp.VideoPreviewWidget(
            worker_client_factory=lambda _parent: other_client
        )
        self.addCleanup(lambda: other.clear(sync_shutdown=True))
        other.set_render_context(self.widget._render_context)
        self.assertTrue(other.load_video(str(self.media)))
        second = other.current_render_session()

        first_dir = Path(first.job_path).parent
        second_dir = Path(second.job_path).parent
        self.assertEqual(first.epoch, second.epoch)
        self.assertNotEqual(first_dir, second_dir)
        self.assertEqual(first_dir.parent, root)
        self.assertEqual(second_dir.parent, root)
        self.assertEqual(load_render_job(first.job_path).paths.cache_dir, str(first_dir))

        self.widget.clear(sync_shutdown=True)
        self.assertFalse(first_dir.exists())
        self.assertTrue(Path(second.job_path).exists())
        self.assertEqual(legacy.read_text(encoding="utf-8"), "abandoned job")
        self.assertEqual(sentinel.read_bytes(), b"keep")

    def test_cancel_keeps_script_cache_until_unload_then_removes_session_directory(self):
        session = self._load_resolved()
        cache = Path(session.job_path).parent
        script_cache = cache / "decoder" / "index.cache"
        script_cache.parent.mkdir()
        script_cache.write_bytes(b"worker still needs this")

        self.widget.clear()
        self.assertTrue(script_cache.exists())
        self.assertTrue(Path(session.job_path).exists())

        self.client.operation_completed.emit(self.client.unload_request_ids[-1], "unload")
        QCoreApplication.processEvents()
        self.assertFalse(cache.exists())

    def test_retired_job_does_not_remove_current_sessions_script_cache(self):
        first = self._load_resolved()
        cache = Path(first.job_path).parent
        script_cache = cache / "index.cache"
        script_cache.write_bytes(b"keep while loaded")
        current = self.widget.flush_render_job()

        self.client.operation_completed.emit(self.client.unload_request_ids[-1], "unload")
        QCoreApplication.processEvents()
        self.assertFalse(Path(first.job_path).exists())
        self.assertTrue(Path(current.job_path).exists())
        self.assertTrue(script_cache.exists())

        self.widget.clear()
        self.client.operation_completed.emit(self.client.unload_request_ids[-1], "unload")
        QCoreApplication.processEvents()
        self.assertFalse(cache.exists())

    def test_worker_stop_cleans_cancelled_session_directory(self):
        session = self._load_resolved()
        cache = Path(session.job_path).parent
        self.widget.clear()
        self.client.worker_stopped.emit()
        QCoreApplication.processEvents()
        self.assertFalse(cache.exists())

    def test_runtime_reload_cleans_current_and_pending_retirement_jobs(self):
        first = self._load_resolved()
        current = self.widget.flush_render_job()
        cache = Path(current.job_path).parent
        self.widget.set_render_context(replace(
            self.widget._render_context,
            runtime_snapshot=replace(self.snapshot, fingerprint="b" * 64),
        ))
        self.assertEqual(self.client.closed, 1)
        self.assertFalse(Path(first.job_path).exists())
        self.assertFalse(cache.exists())
        self.assertEqual(self.widget._retire_after_unload, {})

    def test_close_cleans_allocated_cache_even_without_a_worker(self):
        self.widget.video_path = str(self.media)
        job = self.widget._make_render_job(bootstrap=True)
        cache = Path(job.paths.cache_dir)
        self.assertTrue(cache.exists())
        self.assertIsNone(self.widget._worker_client)
        self.widget.clear(sync_shutdown=True)
        self.assertFalse(cache.exists())

    def test_failed_job_publish_cleans_unsubmitted_cache(self):
        with mock.patch.object(self.vp, "write_render_job", side_effect=OSError("disk full")):
            self.assertFalse(self.widget.load_video(str(self.media)))
        self.assertEqual(self.widget._preview_caches, {})
        self.assertEqual(list((self.root / "cache").iterdir()), [])

    def test_offline_cleanup_removes_legacy_and_abandoned_preview_files_only(self):
        root = self.root / "cache"
        abandoned = root / "session-crashed"
        abandoned.mkdir(parents=True)
        (abandoned / "job-1.json").write_bytes(b"abandoned")
        (root / "job-1.json").write_bytes(b"legacy")
        project_file = self.root / "project.json"
        project_file.write_bytes(b"keep")

        clear_preview_cache(root)
        clear_preview_cache(root)
        self.assertFalse(root.exists())
        self.assertEqual(project_file.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
