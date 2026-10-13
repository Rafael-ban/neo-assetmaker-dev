"""Mock coverage only: frozen Windows rendering is accepted by build-app CI."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

from core import application_smoke as smoke
from tests.qt_harness import ensure_app


ROOT = Path(__file__).resolve().parents[1]


def setUpModule():
    ensure_app()


class ApplicationSmokeTests(unittest.TestCase):
    def test_report_success_records_resolved_root_and_cleans_fixture(self):
        with tempfile.TemporaryDirectory() as temp:
            report_path = Path(temp) / "中文 报告.json"
            seen = []

            def exercise(app_dir, root, progress):
                seen.append(root)
                self.assertEqual(app_dir, ROOT)
                self.assertIn("中文", str(root))
                self.assertIn(" ", root.name)
                progress("verified", export={"frames": 12})

            with mock.patch.object(smoke.sys, "platform", "win32"), \
                    mock.patch.object(smoke.sys, "frozen", True, create=True), \
                    mock.patch.object(smoke, "get_app_dir", return_value=str(ROOT)), \
                    mock.patch.object(smoke, "_exercise", side_effect=exercise):
                code = smoke.run_smoke(["--smoke-test", "--report", str(report_path)])
            result = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(code, 0)
            self.assertTrue(result["success"])
            self.assertTrue(result["frozen"])
            self.assertTrue(result["temporary_directory_removed"])
            self.assertEqual(result["app_dir"], str(ROOT))
            self.assertEqual(result["executable"], str(Path(sys.executable).resolve()))
            self.assertFalse(seen[0].exists())

    def test_failure_report_has_stage_traceback_and_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            report_path = Path(temp) / "error.json"

            def fail(_app_dir, _root, progress):
                progress("worker_load")
                raise TimeoutError("request 2 timed out")

            with mock.patch.object(smoke.sys, "platform", "win32"), \
                    mock.patch.object(smoke, "_exercise", side_effect=fail):
                code = smoke.run_smoke(["--smoke-test", "--report", str(report_path)])
            result = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(code, 1)
            self.assertFalse(result["success"])
            self.assertEqual(result["stage"], "worker_load")
            self.assertEqual(result["error_type"], "TimeoutError")
            self.assertIn("request 2 timed out", result["traceback"])

    def test_linux_reports_unsupported_instead_of_claiming_media_acceptance(self):
        with tempfile.TemporaryDirectory() as temp:
            report_path = Path(temp) / "unsupported.json"
            with mock.patch.object(smoke.sys, "platform", "linux"), \
                    mock.patch.object(smoke, "_exercise") as exercise:
                code = smoke.run_smoke(["--smoke-test", "--report", str(report_path)])
            exercise.assert_not_called()
            self.assertEqual(code, 1)
            result = json.loads(report_path.read_text())
            self.assertEqual(result["stage"], "platform")
            self.assertEqual(result["platform"], "linux")
            self.assertIn("Windows", result["error"])

    def test_report_argument_is_required_and_unknown_arguments_are_rejected(self):
        for arguments in (["--smoke-test"], ["--smoke-test", "--report", "x", "--wrong"]):
            with self.subTest(arguments=arguments), mock.patch("sys.stderr"), \
                    self.assertRaises(SystemExit) as raised:
                smoke.run_smoke(arguments)
            self.assertEqual(raised.exception.code, 2)

    def test_production_job_and_selection_are_used_with_isolated_runtime(self):
        from core.vs_runtime.job import load_render_job
        from core.vs_runtime.session import NodeMetadata, SessionMetadata

        snapshot = mock.Mock(fingerprint="b" * 64)
        snapshot.worker_environment.return_value = {"SMOKE_RUNTIME": "isolated"}
        worker = mock.Mock(logs=[])
        worker.load.return_value = SessionMetadata(
            epoch=1, mode="compatible", capabilities=frozenset(), editor=None,
            output0=NodeMetadata(width=360, height=640, num_frames=12, fps_num=30, fps_den=1,
                                 pixel_format="YUV420P8", matrix="170m", transfer="170m",
                                 primaries="170m", range="limited"),
        )
        worker.request_frame.return_value = np.full((640, 360, 3), 100, np.uint8)
        worker.wait.return_value = 0
        observations = []

        def export(session, root, _progress):
            job = load_render_job(session.job_path, for_export=True)
            observations.append((session, job))
            self.assertEqual((job.source.kind, job.source.virtual_frame_count), ("image", 12))
            self.assertEqual(job.timeline.end_frame, 12)
            self.assertTrue(Path(job.source.path).is_file())
            self.assertEqual(os.environ["APPDATA"], str(root / "isolated appdata"))
            self.assertEqual(os.environ["SMOKE_RUNTIME"], "isolated")
            return root / "output"

        before = dict(os.environ)
        # Only adapt the Windows-only path ABI in this mock test. All models,
        # job serialization, hashing and session selection stay production code.
        with tempfile.TemporaryDirectory(prefix="中文 路径 ") as temp, \
                mock.patch("resources.vapoursynth.python.assetmaker_vs.job_api.is_canonical_windows_absolute_path",
                           side_effect=lambda path: Path(path).is_absolute()), \
                mock.patch("core.vs_runtime.snapshot.RuntimeSnapshot.resolve", return_value=snapshot) as resolve, \
                mock.patch("core.vs_runtime.snapshot.RuntimeSnapshot.from_environment", return_value=snapshot), \
                mock.patch("core.vs_runtime.worker_process.SyncVSWorkerProcess", return_value=worker) as worker_type, \
                mock.patch.object(smoke, "_export", side_effect=export), \
                mock.patch.object(smoke, "_decode_export", return_value={"frames": 12}):
            root = Path(temp).resolve()
            smoke._exercise(ROOT, root, mock.Mock())
            resolve.assert_called_once_with(ROOT, user_path=root / "不存在 user.json")
            self.assertFalse(resolve.call_args.kwargs["user_path"].exists())
            self.assertEqual(worker_type.call_args.kwargs["env"]["APPDATA"], str(root / "isolated appdata"))
        self.assertEqual(dict(os.environ), before)
        self.assertEqual(len(observations), 1)
        session, _job = observations[0]
        self.assertEqual(session.selection.script_path, str(ROOT / "resources/vapoursynth/default_pipeline.vpy"))
        self.assertEqual(session.runtime_fingerprint, "b" * 64)
        self.assertEqual([call.kwargs["index"] for call in worker.request_frame.call_args_list], [0, 11])
        worker.shutdown.assert_called_once()
        worker.close.assert_called_once()

    def test_worker_failure_closes_before_fixture_cleanup_and_restores_environment(self):
        snapshot = mock.Mock(fingerprint="b" * 64)
        snapshot.worker_environment.return_value = {}
        worker = mock.Mock(logs=[])
        worker.load.side_effect = RuntimeError("plugin missing")
        before = dict(os.environ)
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch("resources.vapoursynth.python.assetmaker_vs.job_api.is_canonical_windows_absolute_path",
                           side_effect=lambda path: Path(path).is_absolute()), \
                mock.patch("core.vs_runtime.snapshot.RuntimeSnapshot.resolve", return_value=snapshot), \
                mock.patch("core.vs_runtime.snapshot.RuntimeSnapshot.from_environment", return_value=snapshot), \
                mock.patch("core.vs_runtime.worker_process.SyncVSWorkerProcess", return_value=worker), \
                mock.patch.object(smoke, "_export") as export:
            with self.assertRaisesRegex(RuntimeError, "plugin missing"):
                smoke._exercise(ROOT, Path(temp).resolve(), mock.Mock())
            worker.close.assert_called_once()
            self.assertTrue(Path(temp).exists())
            export.assert_not_called()
        self.assertEqual(dict(os.environ), before)

    def test_export_timeout_cancels_and_waits_for_terminal_before_raising(self):
        service = mock.Mock(is_exporting=False)
        with mock.patch("core.export_service.ExportService", return_value=service), \
                mock.patch.object(smoke, "_wait_until", side_effect=[False, True]):
            service.cancel.side_effect = lambda: service.export_failed.connect.call_args.args[0]("Cancelled")
            with self.assertRaisesRegex(TimeoutError, "120 seconds"):
                smoke._export(mock.Mock(), ROOT, mock.Mock())
        service.cancel.assert_called_once()

    def test_unstoppable_export_retains_service_for_process_exit(self):
        service = mock.Mock()
        with mock.patch("core.export_service.ExportService", return_value=service), \
                mock.patch.object(smoke, "_wait_until", return_value=False):
            with self.assertRaises(smoke.ExportShutdownTimeout) as raised:
                smoke._export(mock.Mock(), ROOT, mock.Mock())
        self.assertIs(raised.exception.service, service)

    def test_export_success_waits_for_real_qt_signal_delivery(self):
        from PyQt6.QtCore import QObject, QTimer, pyqtSignal

        class Service(QObject):
            export_completed = pyqtSignal(str)
            export_failed = pyqtSignal(str)
            progress_updated = pyqtSignal(int, str)
            is_exporting = False

            def export_all(self, output, config, **kwargs):
                case.assertEqual(config.icon, "")
                case.assertEqual(config.loop.file, "loop.mp4")
                case.assertFalse(config.intro.enabled)
                case.assertEqual(config.to_dict(normalize_paths=True)["loop"]["file"], "loop.mp4")
                QTimer.singleShot(0, lambda: self.export_completed.emit("done"))

        case = self
        with mock.patch("core.export_service.ExportService", Service):
            output = smoke._export(mock.Mock(), ROOT, mock.Mock())
        self.assertEqual(output, ROOT / "导出 包")

    def test_decoded_frame_count_and_transaction_cleanup_are_required(self):
        for frame_count, private_work, succeeds in ((12, False, True), (11, False, False),
                                                    (13, False, False), (12, True, False)):
            with self.subTest(frame_count=frame_count, private_work=private_work), \
                    tempfile.TemporaryDirectory() as temp:
                output = Path(temp) / "导出 包"
                output.mkdir()
                (output / "epconfig.json").write_text("{}")
                if private_work:
                    (output / ".assetmaker-work").mkdir()
                capture = mock.Mock()
                capture.isOpened.return_value = True
                frame = np.full((640, 360, 3), 100, np.uint8)
                capture.read.side_effect = [(True, frame)] * frame_count + [(False, None)]
                with mock.patch("cv2.VideoCapture", return_value=capture):
                    if succeeds:
                        result = smoke._decode_export(output)
                        self.assertEqual(result, {"frames": 12, "width": 360, "height": 640,
                                                  "private_work_removed": True})
                    else:
                        with self.assertRaises(RuntimeError):
                            smoke._decode_export(output)
                capture.release.assert_called_once()


if __name__ == "__main__":
    unittest.main()
