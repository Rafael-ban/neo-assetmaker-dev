"""S6.1-6.4: export pipeline robustness — progress, temp cleanup, atomic package.

- VSPipe -p progress ("Frame: n/total") is parsed and forwarded (old code
  drained it into a buffer and the dialog froze at a fixed percentage).
- A failed/cancelled encode cleans up its .tmp.264 / .tmp.mp4 (old code left
  them littering the export dir).
- ExportWorker stages every artifact and promotes atomically: a mid-export
  failure leaves NO half-populated package (old code wrote icon/overlay
  straight into output_dir before the video encode that could still fail).
"""
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
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


def setUpModule():
    ensure_app()


def _run_service_worker_synchronously(service) -> None:
    """让同步 start double 保留 QThread 的 finished 生命周期边界。"""
    worker = service._worker
    assert worker is not None
    worker.run()
    worker.finished.emit()


def _toolchain():
    from core.media_tools import MediaToolchain

    return MediaToolchain(
        vspipe_path="VSPipe.exe",
        x264_path="x264-7mod.exe", muxer_path="MP4Box.exe",
    )


def _vui():
    from core.vs_runtime.output_contract import X264Vui

    return X264Vui(
        colormatrix="smpte170m",
        colorprim="smpte170m",
        transfer="smpte170m",
        range_="tv",
    )


def _render_request(root: Path):
    from config.vs_runtime import VSRuntimeConfig
    from core.media_pipeline import VSPipeRenderRequest

    return VSPipeRenderRequest(
        runner_path=str(root / "runner.vpy"),
        script_path=str(root / "script.vpy"),
        job_path=str(root / "job.json"),
        expected_job_sha256="b" * 64,
        api_version=1,
        mode="compatible",
        app_dir=str(root),
        runtime=VSRuntimeConfig(),
        runtime_fingerprint="a" * 64,
    )


def _valid_render_session(root: Path, *, track: str = "loop"):
    """Create a real export-valid job while keeping encoding out of this test."""
    from core.vs_runtime.job import RenderJob, write_render_job
    from core.vs_runtime.session import (
        RenderSession,
        ScriptSelection,
        compute_job_sha256,
        compute_script_bundle_hash,
    )

    script = root / f"用户 脚本-{track}.vpy"
    script.write_text("# export fixture\n", encoding="utf-8")
    job = RenderJob.from_dict(
        {
            "api_version": 1,
            "epoch": 7,
            "track": track,
            "project_root": str(root.resolve()),
            "source": {
                "path": str((root / "source.mp4").resolve()),
                "kind": "video",
                "virtual_frame_count": None,
            },
            "timeline": {
                "start_frame": 0,
                "end_frame": 3,
                "fps": {"numerator": 30, "denominator": 1},
            },
            "transform": {
                "rotation": 0,
                "crop": {
                    "coordinate_space": "post_rotation_source_pixels",
                    "x": 0,
                    "y": 0,
                    "width": 0,
                    "height": 0,
                },
            },
            "output": {
                "profile": "360x640",
                "display_width": 360,
                "display_height": 640,
                "coded_width": 384,
                "coded_height": 640,
                "pixel_format": "YUV420P8",
                "matrix": "170m",
                "transfer": "170m",
                "primaries": "170m",
                "range": "limited",
                "final_rotate_180": False,
            },
            "paths": {
                "cache_dir": str((root / f"preview jobs-{track}").resolve())
            },
        }
    )
    job_path = write_render_job(job)
    return RenderSession(
        epoch=job.epoch,
        track=track,
        selection=ScriptSelection(
            script_path=str(script.resolve()),
            mode="compatible",
            bundle_hash=compute_script_bundle_hash(script),
        ),
        job_path=str(job_path),
        job_sha256=compute_job_sha256(job_path),
        runtime_fingerprint="a" * 64,
    )


def _package_bytes(path: Path) -> dict[str, bytes]:
    return {
        file.relative_to(path).as_posix(): file.read_bytes()
        for file in path.rglob("*")
        if file.is_file()
    }


def _write_old_package(path: Path) -> dict[str, bytes]:
    path.mkdir()
    path.joinpath("epconfig.json").write_text(
        json.dumps(
            {
                "version": 1,
                "uuid": "old-package",
                "screen": "360x640",
                "loop": {"file": ""},
                "icon": "icon.png",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    path.joinpath("icon.png").write_bytes(b"old-icon")
    return _package_bytes(path)


class _StderrStream:
    """Fake stderr exposing read1() (progress drain) and read() (plain drain)."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def read1(self, _n):
        return self._chunks.pop(0) if self._chunks else b""

    def read(self):
        data = b"".join(self._chunks)
        self._chunks = []
        return data

    def close(self):
        pass


class ProgressParsingTests(unittest.TestCase):
    def test_vspipe_frame_progress_is_forwarded(self):
        from core.media_pipeline import MediaEncoder
        from core.media_tools import MediaToolchain
        from core.vs_runtime.job import RationalFPS

        class FakePipe:
            def close(self):
                pass

        class FakePopen:
            def __init__(self, cmd, **kwargs):
                self.cmd = cmd
                self.returncode = 0
                if Path(cmd[0]).name == "VSPipe.exe":
                    self.stdout = FakePipe()
                    self.stderr = _StderrStream(
                        [b"Frame: 3/10\r", b"Frame: 7/10\r", b"Frame: 10/10\n"]
                    )
                else:  # x264
                    self.stdout = FakePipe()
                    self.stderr = _StderrStream([b""])
                    output_path = cmd[cmd.index("--output") + 1]
                    Path(output_path).write_bytes(b"raw")

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

            def terminate(self):
                pass

            def kill(self):
                pass

        progress = []

        def fake_run(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"mp4")
            return mock.Mock(returncode=0, stderr="")

        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "loop.mp4"
            root = Path(d)
            media_dir = root / "tools" / "media"
            media_dir.mkdir(parents=True)
            vspipe = media_dir / "VSPipe.exe"
            vspipe.touch()
            toolchain = MediaToolchain(
                vspipe_path=str(vspipe),
                x264_path="x264-7mod.exe",
                muxer_path="MP4Box.exe",
            )
            encoder = MediaEncoder(toolchain)
            with mock.patch("core.media_pipeline.subprocess.Popen", FakePopen):
                with mock.patch("core.media_pipeline.subprocess.run", fake_run), mock.patch(
                    "core.media_pipeline.build_vspipe_render_env", return_value={}
                ):
                    encoder.encode_vpy_to_mp4(
                        _render_request(root), str(out), RationalFPS(30, 1), vui=_vui(),
                        progress_cb=lambda done, total: progress.append((done, total)),
                    )

        self.assertIn((3, 10), progress)
        self.assertIn((10, 10), progress)


class TempCleanupTests(unittest.TestCase):
    def test_failed_encode_removes_temp_files(self):
        from core.media_pipeline import MediaEncoder
        from core.vs_runtime.job import RationalFPS

        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "loop.mp4"

            def boom(self, request, raw, vui, is_cancelled=None, progress_cb=None):
                # simulate x264 having written the raw temp before failing
                Path(raw).write_bytes(b"partial")
                return {"vspipe_returncode": 1, "x264_returncode": 0, "stderr": "err"}

            encoder = MediaEncoder(_toolchain())
            with mock.patch.object(MediaEncoder, "_run_encode_pipeline", boom):
                with self.assertRaises(RuntimeError):
                    encoder.encode_vpy_to_mp4(
                        _render_request(Path(d)),
                        str(out),
                        RationalFPS(30, 1),
                        vui=_vui(),
                    )

            leftovers = [p.name for p in Path(d).iterdir()]
            self.assertEqual(leftovers, [], f"temp files left behind: {leftovers}")


class ExportPathIdentityTests(unittest.TestCase):
    """Windows path spelling must not weaken sealed-package checks."""

    def test_non_reparse_directory_identity_accepts_short_long_aliases(self):
        from core.export_service import (
            _FilesystemIdentity,
            _is_same_non_reparse_directory,
        )

        short_parent = Path(r"C:\Users\RUNNER~1\AppData\Local\Temp")
        long_parent = Path(r"C:\Users\runneradmin\AppData\Local\Temp")
        identity = _FilesystemIdentity(1, 2, 0, 0)
        with mock.patch(
            "core.export_service._is_reparse_or_symlink", return_value=False
        ), mock.patch.object(Path, "is_dir", return_value=True), mock.patch(
            "core.export_service._filesystem_identity",
            side_effect=(identity, identity),
        ):
            self.assertTrue(
                _is_same_non_reparse_directory(short_parent, long_parent)
            )

    def test_package_path_accepts_transaction_parent_alias(self):
        from core.export_service import (
            ExportWorker,
            _PreparedExportPackage,
        )

        short_parent = Path(r"C:\Users\RUNNER~1\AppData\Local\Temp")
        long_parent = Path(r"C:\Users\runneradmin\AppData\Local\Temp")
        staging_dir = long_parent / ".package.staging-token"
        package = _PreparedExportPackage(
            final_dir=short_parent / "package",
            staging_dir=staging_dir,
            backup_dir=short_parent / ".package.backup-token",
            work_dir=staging_dir / ".assetmaker-work",
            tasks=(),
            manifest=(),
            epconfig_bytes=b"{}",
        )
        with mock.patch(
            "core.export_service._is_same_non_reparse_directory",
            return_value=True,
        ) as same_parent, mock.patch.object(
            ExportWorker, "_assert_directory_identity"
        ) as assert_identity:
            ExportWorker._assert_package_path(
                package,
                staging_dir,
                ".package.staging-",
                None,
            )

        same_parent.assert_called_once_with(staging_dir.parent, short_parent)
        assert_identity.assert_called_once_with(
            staging_dir, None, "清理或移动"
        )


@unittest.skipUnless(HAS_CV2, "opencv-python required for PNG artifact tasks")
class AtomicPackageTests(unittest.TestCase):
    def _worker(self, tasks, root: Path):
        from core.export_service import (
            ExportService,
            ExportWorker,
            _PreparedExportPackage,
            _filesystem_identity,
        )
        from core.file_utils import atomic_write_bytes

        final_dir = root / "package"
        staging_dir = Path(
            tempfile.mkdtemp(prefix=".package.staging-", dir=root)
        ).resolve()
        work_dir = staging_dir / ".assetmaker-work"
        work_dir.mkdir()
        epconfig_bytes = b'{"version": 1, "screen": "360x640", "loop": {"file": ""}}'
        package = _PreparedExportPackage(
            final_dir=final_dir,
            staging_dir=staging_dir,
            backup_dir=root / ".package.backup-test",
            work_dir=work_dir,
            tasks=tuple(tasks),
            manifest=ExportService._build_manifest(tuple(tasks), epconfig_bytes),
            epconfig_bytes=epconfig_bytes,
            staging_dir_identity=_filesystem_identity(staging_dir),
            work_dir_identity=_filesystem_identity(work_dir),
        )
        atomic_write_bytes(staging_dir / "epconfig.json", epconfig_bytes)
        worker = ExportWorker()
        worker.setup(prepared=package)
        return worker, final_dir

    def test_failed_video_task_leaves_no_partial_package(self):
        from core.export_service import ExportTask, ExportType

        icon = np.full((16, 16, 3), 200, np.uint8)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tasks = [
                ExportTask(ExportType.ICON, "icon.png", icon),
                ExportTask(
                    ExportType.LOOP_VIDEO,
                    "loop.mp4",
                    _valid_render_session(root),
                ),
            ]
            w, final_dir = self._worker(tasks, root)
            w._export_video = mock.Mock(side_effect=RuntimeError("encode boom"))
            failed = []
            w.export_failed.connect(failed.append)
            w.run()

            self.assertTrue(failed, "a failed task must emit export_failed")
            self.assertFalse(final_dir.exists())
            self.assertFalse(list(root.glob(".package.staging-*")))

    def test_aux_images_promoted_atomically_on_success(self):
        from core.export_service import ExportTask, ExportType

        icon = np.full((16, 16, 3), 200, np.uint8)
        class_icon = np.full((32, 32, 3), 100, np.uint8)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tasks = [
                ExportTask(ExportType.ICON, "icon.png", icon),
                ExportTask(ExportType.AUX_IMAGE, "class_icon.png", class_icon),
            ]
            w, final_dir = self._worker(tasks, root)
            completed = []
            w.export_completed.connect(completed.append)
            w.run()

            self.assertTrue(completed)
            names = sorted(p.name for p in final_dir.iterdir())
            self.assertEqual(names, ["class_icon.png", "epconfig.json", "icon.png"])
            self.assertFalse(list(root.glob(".package.staging-*")))


@unittest.skipUnless(HAS_CV2, "opencv-python required for PNG artifact tasks")
class C3PackageTransactionTests(unittest.TestCase):
    """C3: package publishing is one sealed directory transaction."""

    @staticmethod
    def _service():
        from core.export_service import ExportService

        service = ExportService()
        service._media_toolchain = mock.Mock(
            missing_for_export=mock.Mock(return_value=[])
        )
        return service

    @staticmethod
    def _config():
        from config.epconfig import EPConfig

        config = EPConfig(uuid="new-package")
        config.icon = "preview-icon.png"
        return config

    def test_export_all_owns_job_snapshot_before_qthread_start(self):
        """Deleting the preview job in start() cannot affect the worker session."""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "中文 空格 输出"
            root.mkdir()
            session = _valid_render_session(root)
            original_job = Path(session.job_path)
            original_bytes = original_job.read_bytes()
            captured = {}

            def capture_then_retire_preview():
                worker = service._worker
                export_session = worker._tasks[0].session
                captured["job_path"] = Path(export_session.job_path)
                captured["payload"] = captured["job_path"].read_bytes()
                original_job.unlink()

            service = self._service()
            snapshot_config = self._config()
            snapshot_config.icon = ""
            with mock.patch.object(
                ExportWorker,
                "start",
                side_effect=capture_then_retire_preview,
            ):
                service.export_all(
                    output_dir=str(root / "导出 包"),
                    epconfig=snapshot_config,
                    loop_render_session=session,
                )

            self.assertFalse(original_job.exists())
            self.assertNotEqual(captured["job_path"], original_job)
            self.assertEqual(captured["payload"], original_bytes)
            self.assertIn(".assetmaker-work", captured["job_path"].parts)
            self.assertTrue(
                captured["job_path"].parent.parent.parent.name.startswith(
                    ".导出 包.staging-"
                )
            )
            self.assertEqual(
                service._worker._tasks[0].session.selection.script_path,
                session.selection.script_path,
            )

    def test_missing_staged_artifact_preserves_existing_package(self):
        """A producer returning without its declared file must fail before publish."""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            final_dir = Path(temp_dir) / "package"
            old_bytes = _write_old_package(final_dir)
            failed, completed = [], []
            service = self._service()
            service.export_failed.connect(failed.append)
            service.export_completed.connect(completed.append)

            def run_without_icon():
                worker = service._worker
                worker._export_icon = mock.Mock(return_value=None)
                _run_service_worker_synchronously(service)

            with mock.patch.object(
                ExportWorker,
                "start",
                side_effect=run_without_icon,
            ):
                service.export_all(
                    output_dir=str(final_dir),
                    epconfig=self._config(),
                    logo_mat=np.full((16, 16, 3), 200, np.uint8),
                )

            self.assertTrue(failed)
            self.assertFalse(completed)
            self.assertEqual(_package_bytes(final_dir), old_bytes)

    def test_second_directory_rename_failure_restores_old_package(self):
        """A failed staging→final rename must roll back the complete old package."""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            final_dir = Path(temp_dir) / "package"
            old_bytes = _write_old_package(final_dir)
            failed, completed = [], []
            service = self._service()
            service.export_failed.connect(failed.append)
            service.export_completed.connect(completed.append)
            real_rename = os.rename

            def fail_only_staging_promotion(source, destination):
                source_path = Path(source)
                if (
                    source_path.name.startswith(".package.staging-")
                    and Path(destination) == final_dir
                ):
                    raise PermissionError("inject staging promotion failure")
                return real_rename(source, destination)

            with mock.patch.object(
                ExportWorker,
                "start",
                side_effect=lambda: _run_service_worker_synchronously(service),
            ), mock.patch(
                "core.export_service.os.rename",
                side_effect=fail_only_staging_promotion,
            ):
                service.export_all(
                    output_dir=str(final_dir),
                    epconfig=self._config(),
                    logo_mat=np.full((16, 16, 3), 200, np.uint8),
                )

            self.assertTrue(failed)
            self.assertFalse(completed)
            self.assertEqual(_package_bytes(final_dir), old_bytes)

    def test_first_publish_renames_only_the_sealed_manifest(self):
        """The final rename sees epconfig plus artifacts, never private work files."""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            final_dir = Path(temp_dir) / "中文 包"
            observed = {}
            service = self._service()
            config = self._config()
            real_rename = os.rename

            def inspect_sealed_staging(source, destination):
                source_path = Path(source)
                if Path(destination) == final_dir:
                    observed["files"] = sorted(
                        file.relative_to(source_path).as_posix()
                        for file in source_path.rglob("*")
                        if file.is_file()
                    )
                    observed["config"] = json.loads(
                        (source_path / "epconfig.json").read_text(encoding="utf-8")
                    )
                return real_rename(source, destination)

            def mutate_config_then_run():
                config.uuid = "mutated-after-start"
                _run_service_worker_synchronously(service)

            with mock.patch.object(
                ExportWorker,
                "start",
                side_effect=mutate_config_then_run,
            ), mock.patch(
                "core.export_service.os.rename",
                side_effect=inspect_sealed_staging,
            ):
                service.export_all(
                    output_dir=str(final_dir),
                    epconfig=config,
                    logo_mat=np.full((16, 16, 3), 200, np.uint8),
                )

            self.assertEqual(observed["files"], ["epconfig.json", "icon.png"])
            self.assertEqual(observed["config"]["uuid"], "new-package")
            self.assertEqual(sorted(file.name for file in final_dir.iterdir()), ["epconfig.json", "icon.png"])

    def test_cancel_before_commit_preserves_existing_package(self):
        """Cancellation after an artifact is staged must not enter the commit area."""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            final_dir = Path(temp_dir) / "package"
            old_bytes = _write_old_package(final_dir)
            failed, completed = [], []
            service = self._service()
            service.export_failed.connect(failed.append)
            service.export_completed.connect(completed.append)

            def stage_icon_then_cancel():
                worker = service._worker
                original_export_icon = worker._export_icon

                def export_icon_and_cancel(*args, **kwargs):
                    original_export_icon(*args, **kwargs)
                    worker._cancelled = True

                worker._export_icon = export_icon_and_cancel
                _run_service_worker_synchronously(service)

            with mock.patch.object(
                ExportWorker,
                "start",
                side_effect=stage_icon_then_cancel,
            ):
                service.export_all(
                    output_dir=str(final_dir),
                    epconfig=self._config(),
                    logo_mat=np.full((16, 16, 3), 200, np.uint8),
                )

            self.assertTrue(failed)
            self.assertFalse(completed)
            self.assertEqual(_package_bytes(final_dir), old_bytes)

    def test_rollback_failure_preserves_exact_recovery_directories(self):
        """Never delete backup/staging when both promotion and rollback fail."""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            final_dir = root / "package"
            old_bytes = _write_old_package(final_dir)
            failed = []
            service = self._service()
            service.export_failed.connect(failed.append)
            real_rename = os.rename

            def fail_promotion_and_rollback(source, destination):
                source_path = Path(source)
                if source_path.name.startswith(".package.staging-"):
                    raise PermissionError("inject promotion failure")
                if source_path.name.startswith(".package.backup-"):
                    raise PermissionError("inject rollback failure")
                return real_rename(source, destination)

            with mock.patch.object(
                ExportWorker,
                "start",
                side_effect=lambda: _run_service_worker_synchronously(service),
            ), mock.patch(
                "core.export_service.os.rename",
                side_effect=fail_promotion_and_rollback,
            ):
                service.export_all(
                    output_dir=str(final_dir),
                    epconfig=self._config(),
                    logo_mat=np.full((16, 16, 3), 200, np.uint8),
                )

            backups = list(root.glob(".package.backup-*"))
            stagings = list(root.glob(".package.staging-*"))
            self.assertFalse(final_dir.exists())
            self.assertEqual(len(backups), 1)
            self.assertEqual(len(stagings), 1)
            self.assertEqual(_package_bytes(backups[0]), old_bytes)
            self.assertEqual(
                sorted(file.name for file in stagings[0].iterdir()),
                ["epconfig.json", "icon.png"],
            )
            self.assertIn(str(final_dir), failed[0])
            self.assertIn(str(backups[0]), failed[0])
            self.assertIn(str(stagings[0]), failed[0])
            self.assertFalse(list(root.glob(".package.lock")))

    def test_non_package_directory_is_never_replaced(self):
        """A user directory with an unknown file fails closed before worker start."""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            final_dir = Path(temp_dir) / "user-files"
            final_dir.mkdir()
            final_dir.joinpath("keep.txt").write_text("do not touch", encoding="utf-8")
            failed = []
            service = self._service()
            service.export_failed.connect(failed.append)
            with mock.patch.object(ExportWorker, "start") as start:
                service.export_all(
                    output_dir=str(final_dir),
                    epconfig=self._config(),
                    logo_mat=np.full((16, 16, 3), 200, np.uint8),
                )

            start.assert_not_called()
            self.assertTrue(failed)
            self.assertEqual(final_dir.joinpath("keep.txt").read_text(encoding="utf-8"), "do not touch")

    def test_final_directory_symlink_is_rejected_before_worker_start(self):
        """The lexical output link must not be resolved into a replaceable package."""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "real-package"
            target_bytes = _write_old_package(target)
            requested = root / "requested-package"
            try:
                os.symlink(target, requested, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlink unavailable: {exc}")
            service = self._service()
            failed = []
            service.export_failed.connect(failed.append)

            with mock.patch.object(ExportWorker, "start") as start:
                service.export_all(
                    output_dir=str(requested),
                    epconfig=self._config(),
                    logo_mat=np.full((16, 16, 3), 200, np.uint8),
                )

            start.assert_not_called()
            self.assertTrue(failed)
            self.assertEqual(_package_bytes(target), target_bytes)
            self.assertFalse(list(root.glob(".requested-package.staging-*")))
            self.assertFalse(list(root.glob(".requested-package.backup-*")))
            self.assertFalse(list(root.glob(".requested-package.lock")))

    def test_parent_directory_symlink_is_rejected_before_worker_start(self):
        """The lexical parent link must not be resolved before sibling staging."""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target_parent = root / "real-parent"
            target_parent.mkdir()
            target = target_parent / "package"
            target_bytes = _write_old_package(target)
            linked_parent = root / "linked-parent"
            try:
                os.symlink(target_parent, linked_parent, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlink unavailable: {exc}")
            service = self._service()
            failed = []
            service.export_failed.connect(failed.append)

            with mock.patch.object(ExportWorker, "start") as start:
                service.export_all(
                    output_dir=str(linked_parent / "package"),
                    epconfig=self._config(),
                    logo_mat=np.full((16, 16, 3), 200, np.uint8),
                )

            start.assert_not_called()
            self.assertTrue(failed)
            self.assertEqual(_package_bytes(target), target_bytes)
            self.assertFalse(list(target_parent.glob(".package.staging-*")))
            self.assertFalse(list(target_parent.glob(".package.backup-*")))
            self.assertFalse(list(target_parent.glob(".package.lock")))

    def test_preexisting_transaction_lock_is_not_deleted(self):
        """An unprovable stale lock blocks preparation and remains untouched."""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            final_dir = root / "package"
            lock_path = root / ".package.lock"
            lock_path.write_bytes(b"foreign-or-stale-owner")
            service = self._service()
            failed = []
            service.export_failed.connect(failed.append)

            with mock.patch.object(ExportWorker, "start") as start:
                service.export_all(
                    output_dir=str(final_dir),
                    epconfig=self._config(),
                    logo_mat=np.full((16, 16, 3), 200, np.uint8),
                )

            start.assert_not_called()
            self.assertTrue(failed)
            self.assertEqual(lock_path.read_bytes(), b"foreign-or-stale-owner")
            self.assertFalse(final_dir.exists())
            self.assertFalse(list(root.glob(".package.staging-*")))

    def test_second_prepared_export_fails_closed_while_first_owns_target(self):
        """A second service cannot prepare or overwrite the first transaction."""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            final_dir = root / "package"
            first = self._service()
            second = self._service()
            second_failed = []
            second.export_failed.connect(second_failed.append)
            first_config = self._config()
            first_config.uuid = "A"
            second_config = self._config()
            second_config.uuid = "B"

            with mock.patch.object(ExportWorker, "start", return_value=None) as start:
                first.export_all(
                    output_dir=str(final_dir),
                    epconfig=first_config,
                    logo_mat=np.full((16, 16, 3), 200, np.uint8),
                )
                second.export_all(
                    output_dir=str(final_dir),
                    epconfig=second_config,
                    logo_mat=np.full((16, 16, 3), 100, np.uint8),
                )

            self.assertEqual(start.call_count, 1)
            self.assertEqual(len(second_failed), 1)
            self.assertIsNotNone(first._worker)
            _run_service_worker_synchronously(first)
            self.assertEqual(
                json.loads((final_dir / "epconfig.json").read_text(encoding="utf-8"))["uuid"],
                "A",
            )
            self.assertFalse(list(root.glob(".package.staging-*")))
            self.assertFalse(list(root.glob(".package.backup-*")))
            self.assertFalse(list(root.glob(".package.lock")))

    def test_changed_final_generation_after_prepare_fails_without_rename(self):
        """Commit may only replace the exact final generation observed at prepare."""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            final_dir = root / "package"
            _write_old_package(final_dir)
            service = self._service()
            failed = []
            service.export_failed.connect(failed.append)

            with mock.patch.object(ExportWorker, "start", return_value=None):
                service.export_all(
                    output_dir=str(final_dir),
                    epconfig=self._config(),
                    logo_mat=np.full((16, 16, 3), 200, np.uint8),
                )

            final_dir.joinpath("icon.png").write_bytes(b"external-generation")
            external_bytes = _package_bytes(final_dir)
            _run_service_worker_synchronously(service)

            self.assertTrue(failed)
            self.assertEqual(_package_bytes(final_dir), external_bytes)
            self.assertFalse(list(root.glob(".package.staging-*")))
            self.assertFalse(list(root.glob(".package.backup-*")))
            self.assertFalse(list(root.glob(".package.lock")))

    def test_replaced_staging_directory_is_never_removed_on_failure(self):
        """Failure cleanup must not recursively delete a substituted staging directory."""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            final_dir = root / "package"
            service = self._service()
            failed = []
            service.export_failed.connect(failed.append)

            with mock.patch.object(ExportWorker, "start", return_value=None):
                service.export_all(
                    output_dir=str(final_dir),
                    epconfig=self._config(),
                    logo_mat=np.full((16, 16, 3), 200, np.uint8),
                )

            worker = service._worker
            staging_dir = worker._package.staging_dir
            real_staging = root / "original-staging"
            os.rename(staging_dir, real_staging)
            staging_dir.mkdir()
            sentinel = staging_dir / "do-not-delete.txt"
            sentinel.write_text("external directory", encoding="utf-8")
            worker._export_icon = mock.Mock(side_effect=RuntimeError("injected task failure"))
            _run_service_worker_synchronously(service)

            self.assertTrue(failed)
            self.assertTrue(sentinel.exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "external directory")

    def test_final_replacement_between_identity_check_and_rename_requires_recovery(self):
        """A final directory swapped after its check must not become a deletable backup."""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            final_dir = root / "package"
            _write_old_package(final_dir)
            service = self._service()
            completed, failed = [], []
            service.export_completed.connect(completed.append)
            service.export_failed.connect(failed.append)
            real_rename = os.rename
            original_final = root / "original-final"
            replacement = {}

            def replace_final_before_backup_rename(source, destination):
                source_path = Path(source)
                destination_path = Path(destination)
                if source_path == final_dir and destination_path.name.startswith(
                    ".package.backup-"
                ):
                    real_rename(final_dir, original_final)
                    final_dir.mkdir()
                    sentinel = final_dir / "do-not-delete.txt"
                    sentinel.write_text("external directory", encoding="utf-8")
                    replacement["sentinel"] = destination_path / sentinel.name
                return real_rename(source, destination)

            with mock.patch.object(
                ExportWorker,
                "start",
                side_effect=lambda: _run_service_worker_synchronously(service),
            ), mock.patch(
                "core.export_service.os.rename",
                side_effect=replace_final_before_backup_rename,
            ):
                service.export_all(
                    output_dir=str(final_dir),
                    epconfig=self._config(),
                    logo_mat=np.full((16, 16, 3), 200, np.uint8),
                )

            self.assertFalse(completed)
            self.assertTrue(failed)
            self.assertIn("人工恢复", failed[0])
            sentinel = replacement["sentinel"]
            self.assertTrue(sentinel.exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "external directory")

    def test_replaced_backup_after_final_move_requires_manual_recovery(self):
        """A substituted backup after final→backup must be preserved for recovery."""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            final_dir = root / "package"
            _write_old_package(final_dir)
            service = self._service()
            completed, failed = [], []
            service.export_completed.connect(completed.append)
            service.export_failed.connect(failed.append)
            real_rename = os.rename
            original_backup = root / "original-backup"
            replacement = {}

            def replace_backup_after_final_move(source, destination):
                result = real_rename(source, destination)
                source_path = Path(source)
                destination_path = Path(destination)
                if source_path == final_dir and destination_path.name.startswith(
                    ".package.backup-"
                ):
                    real_rename(destination_path, original_backup)
                    destination_path.mkdir()
                    sentinel = destination_path / "do-not-delete.txt"
                    sentinel.write_text("external directory", encoding="utf-8")
                    replacement["sentinel"] = sentinel
                return result

            with mock.patch.object(
                ExportWorker,
                "start",
                side_effect=lambda: _run_service_worker_synchronously(service),
            ), mock.patch(
                "core.export_service.os.rename",
                side_effect=replace_backup_after_final_move,
            ):
                service.export_all(
                    output_dir=str(final_dir),
                    epconfig=self._config(),
                    logo_mat=np.full((16, 16, 3), 200, np.uint8),
                )

            self.assertFalse(completed)
            self.assertTrue(failed)
            self.assertIn("人工恢复", failed[0])
            sentinel = replacement["sentinel"]
            self.assertTrue(sentinel.exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "external directory")

    def test_final_replacement_after_staging_promotion_requires_recovery(self):
        """The promoted final must still be the original staging directory generation."""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            final_dir = root / "package"
            _write_old_package(final_dir)
            service = self._service()
            completed, failed = [], []
            service.export_completed.connect(completed.append)
            service.export_failed.connect(failed.append)
            real_rename = os.rename
            promoted_staging = root / "promoted-staging"
            replacement = {}

            def replace_final_after_staging_promotion(source, destination):
                result = real_rename(source, destination)
                source_path = Path(source)
                if (
                    source_path.name.startswith(".package.staging-")
                    and Path(destination) == final_dir
                ):
                    real_rename(final_dir, promoted_staging)
                    final_dir.mkdir()
                    sentinel = final_dir / "do-not-delete.txt"
                    sentinel.write_text("external directory", encoding="utf-8")
                    replacement["sentinel"] = sentinel
                return result

            with mock.patch.object(
                ExportWorker,
                "start",
                side_effect=lambda: _run_service_worker_synchronously(service),
            ), mock.patch(
                "core.export_service.os.rename",
                side_effect=replace_final_after_staging_promotion,
            ):
                service.export_all(
                    output_dir=str(final_dir),
                    epconfig=self._config(),
                    logo_mat=np.full((16, 16, 3), 200, np.uint8),
                )

            self.assertFalse(completed)
            self.assertTrue(failed)
            self.assertIn("人工恢复", failed[0])
            sentinel = replacement["sentinel"]
            self.assertTrue(sentinel.exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "external directory")

    def test_post_seal_identity_sweep_rejects_earlier_video_script_change(self):
        """A loop script changed while intro finishes must block the whole package."""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            final_dir = root / "package"
            old_bytes = _write_old_package(final_dir)
            loop_session = _valid_render_session(root, track="loop")
            intro_session = _valid_render_session(root, track="intro")
            service = self._service()
            config = self._config()
            config.icon = ""
            failed = []
            service.export_failed.connect(failed.append)

            def fake_export_video(output_path, session, base_progress):
                Path(output_path).write_bytes(b"\x00\x00\x00\x00ftypvideo")
                if session.track == "loop":
                    Path(loop_session.selection.script_path).write_text(
                        "# changed after loop completed\n", encoding="utf-8"
                    )

            with mock.patch.object(
                ExportWorker,
                "start",
                side_effect=lambda: _run_service_worker_synchronously(service),
            ), mock.patch.object(
                ExportWorker, "_export_video", side_effect=fake_export_video
            ), mock.patch.object(
                ExportWorker, "_runtime_for_export", return_value=mock.sentinel.runtime
            ), mock.patch.object(
                ExportWorker,
                "_runtime_fingerprint_for_export",
                return_value="a" * 64,
            ), mock.patch("core.export_service.os.rename", wraps=os.rename) as rename:
                service.export_all(
                    output_dir=str(final_dir),
                    epconfig=config,
                    loop_render_session=loop_session,
                    intro_render_session=intro_session,
                )

            self.assertTrue(failed)
            self.assertIn("bundle hash 已变化", failed[0])
            self.assertEqual(rename.call_count, 0)
            self.assertEqual(_package_bytes(final_dir), old_bytes)
            self.assertFalse(list(root.glob(".package.staging-*")))
            self.assertFalse(list(root.glob(".package.lock")))

    def test_frozen_video_job_is_checked_before_private_work_cleanup(self):
        """未变更冻结 job 必须允许首次发布及替换已存在的旧包。"""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            for existing in (False, True):
                with self.subTest(existing=existing):
                    root = Path(temp_dir) / str(existing)
                    root.mkdir()
                    final_dir = root / "package"
                    if existing:
                        _write_old_package(final_dir)
                    session = _valid_render_session(root)
                    source_job = Path(session.job_path)
                    service = self._service()
                    config = self._config()
                    config.icon = ""
                    completed, failed = [], []
                    service.export_completed.connect(completed.append)
                    service.export_failed.connect(failed.append)

                    def fake_export_video(output_path, _session, _base_progress):
                        Path(output_path).write_bytes(b"\x00\x00\x00\x00ftypvideo")

                    with mock.patch.object(
                        ExportWorker,
                        "start",
                        side_effect=lambda: _run_service_worker_synchronously(service),
                    ), mock.patch.object(
                        ExportWorker, "_export_video", side_effect=fake_export_video
                    ), mock.patch.object(
                        ExportWorker, "_runtime_for_export", return_value=mock.sentinel.runtime
                    ), mock.patch.object(
                        ExportWorker,
                        "_runtime_fingerprint_for_export",
                        return_value="a" * 64,
                    ):
                        service.export_all(
                            output_dir=str(final_dir),
                            epconfig=config,
                            loop_render_session=session,
                        )

                    self.assertEqual(len(completed), 1)
                    self.assertFalse(failed)
                    self.assertEqual(
                        sorted(path.name for path in final_dir.iterdir()),
                        ["epconfig.json", "loop.mp4"],
                    )
                    self.assertTrue(source_job.is_file())
                    self.assertFalse((final_dir / ".assetmaker-work").exists())
                    self.assertFalse(list(root.glob(".package.staging-*")))
                    self.assertFalse(list(root.glob(".package.lock")))

    def test_final_job_check_rejects_job_mutated_after_video_encode(self):
        """任务结束至清理私有目录前，job 变更必须阻止替换旧包。"""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            final_dir = root / "package"
            old_bytes = _write_old_package(final_dir)
            session = _valid_render_session(root)
            service = self._service()
            config = self._config()
            config.icon = ""
            failed = []
            service.export_failed.connect(failed.append)

            def fake_export_video(output_path, export_session, _base_progress):
                Path(output_path).write_bytes(b"\x00\x00\x00\x00ftypvideo")
                job_path = Path(export_session.job_path)
                os.chmod(job_path, 0o666)
                job_path.write_text("{\"mutated\": true}", encoding="utf-8")

            with mock.patch.object(
                ExportWorker,
                "start",
                side_effect=lambda: _run_service_worker_synchronously(service),
            ), mock.patch.object(
                ExportWorker, "_export_video", side_effect=fake_export_video
            ), mock.patch.object(
                ExportWorker, "_runtime_for_export", return_value=mock.sentinel.runtime
            ), mock.patch.object(
                ExportWorker,
                "_runtime_fingerprint_for_export",
                return_value="a" * 64,
            ):
                service.export_all(
                    output_dir=str(final_dir),
                    epconfig=config,
                    loop_render_session=session,
                )

            self.assertEqual(len(failed), 1)
            self.assertIn("冻结 job 内容 hash 已变化", failed[0])
            self.assertEqual(_package_bytes(final_dir), old_bytes)
            self.assertFalse(list(root.glob(".package.staging-*")))
            self.assertFalse(list(root.glob(".package.lock")))

    def test_post_seal_script_change_rejects_package_after_private_cleanup(self):
        """seal 后脚本变更仍须被检测，且不再读取已删除的 job。"""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            final_dir = root / "package"
            old_bytes = _write_old_package(final_dir)
            session = _valid_render_session(root)
            service = self._service()
            config = self._config()
            config.icon = ""
            failed = []
            service.export_failed.connect(failed.append)
            real_seal = ExportWorker._seal_package

            def fake_export_video(output_path, _session, _base_progress):
                Path(output_path).write_bytes(b"\x00\x00\x00\x00ftypvideo")

            def seal_then_mutate(worker, package):
                real_seal(worker, package)
                Path(session.selection.script_path).write_text("# changed after seal\n", encoding="utf-8")

            with mock.patch.object(
                ExportWorker,
                "start",
                side_effect=lambda: _run_service_worker_synchronously(service),
            ), mock.patch.object(
                ExportWorker, "_export_video", side_effect=fake_export_video
            ), mock.patch.object(
                ExportWorker, "_seal_package", autospec=True, side_effect=seal_then_mutate
            ), mock.patch.object(
                ExportWorker, "_runtime_for_export", return_value=mock.sentinel.runtime
            ), mock.patch.object(
                ExportWorker,
                "_runtime_fingerprint_for_export",
                return_value="a" * 64,
            ):
                service.export_all(
                    output_dir=str(final_dir),
                    epconfig=config,
                    loop_render_session=session,
                )

            self.assertEqual(len(failed), 1)
            self.assertIn("seal 后最终身份检查：用户脚本 bundle hash 已变化", failed[0])
            self.assertEqual(_package_bytes(final_dir), old_bytes)
            self.assertFalse(list(root.glob(".package.staging-*")))
            self.assertFalse(list(root.glob(".package.lock")))

    def test_post_seal_runtime_change_rejects_package_after_private_cleanup(self):
        """seal 后 runtime 变更必须阻止替换旧包。"""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            final_dir = root / "package"
            old_bytes = _write_old_package(final_dir)
            session = _valid_render_session(root)
            service = self._service()
            config = self._config()
            config.icon = ""
            failed = []
            service.export_failed.connect(failed.append)
            runtime_hash = {"value": "a" * 64}
            real_seal = ExportWorker._seal_package

            def fake_export_video(output_path, _session, _base_progress):
                Path(output_path).write_bytes(b"\x00\x00\x00\x00ftypvideo")

            def seal_then_mutate(worker, package):
                real_seal(worker, package)
                runtime_hash["value"] = "b" * 64

            with mock.patch.object(
                ExportWorker,
                "start",
                side_effect=lambda: _run_service_worker_synchronously(service),
            ), mock.patch.object(
                ExportWorker, "_export_video", side_effect=fake_export_video
            ), mock.patch.object(
                ExportWorker, "_seal_package", autospec=True, side_effect=seal_then_mutate
            ), mock.patch.object(
                ExportWorker, "_runtime_for_export", return_value=mock.sentinel.runtime
            ), mock.patch.object(
                ExportWorker,
                "_runtime_fingerprint_for_export",
                side_effect=lambda *_args: runtime_hash["value"],
            ):
                service.export_all(
                    output_dir=str(final_dir),
                    epconfig=config,
                    loop_render_session=session,
                )

            self.assertEqual(len(failed), 1)
            self.assertIn("seal 后最终身份检查：VapourSynth runtime fingerprint 已变化", failed[0])
            self.assertEqual(_package_bytes(final_dir), old_bytes)
            self.assertFalse(list(root.glob(".package.staging-*")))
            self.assertFalse(list(root.glob(".package.lock")))

    def test_start_exception_aborts_prepared_package_once(self):
        """A QThread.start exception keeps final and preview-owned job untouched."""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            final_dir = root / "package"
            old_bytes = _write_old_package(final_dir)
            session = _valid_render_session(root)
            preview_job = Path(session.job_path)
            preview_bytes = preview_job.read_bytes()
            config = self._config()
            config.icon = ""
            service = self._service()
            failed = []
            service.export_failed.connect(failed.append)

            with mock.patch.object(
                ExportWorker, "start", side_effect=RuntimeError("start boom")
            ):
                service.export_all(
                    output_dir=str(final_dir),
                    epconfig=config,
                    loop_render_session=session,
                )

            self.assertEqual(len(failed), 1)
            self.assertEqual(_package_bytes(final_dir), old_bytes)
            self.assertEqual(preview_job.read_bytes(), preview_bytes)
            self.assertFalse(list(root.glob(".package.staging-*")))
            self.assertFalse(list(root.glob(".package.backup-*")))
            self.assertFalse(list(root.glob(".package.lock")))

    def test_zero_byte_artifact_rejects_package_before_rename(self):
        """A producer that creates an empty declared artifact cannot publish."""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            final_dir = root / "package"
            old_bytes = _write_old_package(final_dir)
            service = self._service()
            failed = []
            service.export_failed.connect(failed.append)

            def write_empty_icon(output_path, mat):
                Path(output_path).write_bytes(b"")

            with mock.patch.object(
                ExportWorker,
                "start",
                side_effect=lambda: _run_service_worker_synchronously(service),
            ), mock.patch.object(ExportWorker, "_export_icon", side_effect=write_empty_icon), mock.patch(
                "core.export_service.os.rename", wraps=os.rename
            ) as rename:
                service.export_all(
                    output_dir=str(final_dir),
                    epconfig=self._config(),
                    logo_mat=np.full((16, 16, 3), 200, np.uint8),
                )

            self.assertTrue(failed)
            self.assertEqual(rename.call_count, 0)
            self.assertEqual(_package_bytes(final_dir), old_bytes)

    def test_changed_staged_epconfig_rejects_package_before_rename(self):
        """The sealed epconfig bytes must still equal the UI-thread snapshot."""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            final_dir = root / "package"
            old_bytes = _write_old_package(final_dir)
            service = self._service()
            failed = []
            service.export_failed.connect(failed.append)

            def export_icon_then_mutate_config(output_path, mat):
                original_export_icon(service._worker, output_path, mat)
                Path(service._worker._staging_dir, "epconfig.json").write_bytes(b"{}")

            with mock.patch.object(
                ExportWorker,
                "start",
                side_effect=lambda: _run_service_worker_synchronously(service),
            ), mock.patch("core.export_service.os.rename", wraps=os.rename) as rename:
                original_export_icon = ExportWorker._export_icon
                with mock.patch.object(
                    ExportWorker,
                    "_export_icon",
                    side_effect=export_icon_then_mutate_config,
                ):
                    service.export_all(
                        output_dir=str(final_dir),
                        epconfig=self._config(),
                        logo_mat=np.full((16, 16, 3), 200, np.uint8),
                    )

            self.assertTrue(failed)
            self.assertEqual(rename.call_count, 0)
            self.assertEqual(_package_bytes(final_dir), old_bytes)

    def test_successful_package_replaces_stale_old_artifacts(self):
        """Directory promotion removes artifacts no longer declared by the new package."""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            final_dir = root / "package"
            _write_old_package(final_dir)
            final_dir.joinpath("intro.mp4").write_bytes(b"old-intro")
            service = self._service()

            with mock.patch.object(
                ExportWorker,
                "start",
                side_effect=lambda: _run_service_worker_synchronously(service),
            ):
                service.export_all(
                    output_dir=str(final_dir),
                    epconfig=self._config(),
                    logo_mat=np.full((16, 16, 3), 200, np.uint8),
                )

            self.assertFalse((final_dir / "intro.mp4").exists())
            self.assertEqual(sorted(path.name for path in final_dir.iterdir()), ["epconfig.json", "icon.png"])

    def test_backup_cleanup_failure_reports_success_with_warning(self):
        """A post-commit backup cleanup failure preserves recovery material and succeeds."""
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            final_dir = root / "package"
            _write_old_package(final_dir)
            service = self._service()
            completed, failed = [], []
            service.export_completed.connect(completed.append)
            service.export_failed.connect(failed.append)

            with mock.patch.object(
                ExportWorker,
                "start",
                side_effect=lambda: _run_service_worker_synchronously(service),
            ), mock.patch.object(
                ExportWorker, "_remove_tree", side_effect=OSError("backup cleanup boom")
            ):
                service.export_all(
                    output_dir=str(final_dir),
                    epconfig=self._config(),
                    logo_mat=np.full((16, 16, 3), 200, np.uint8),
                )

            self.assertEqual(len(completed), 1)
            self.assertFalse(failed)
            self.assertIn("警告", completed[0])
            self.assertTrue(list(root.glob(".package.backup-*")))
            self.assertTrue((final_dir / "icon.png").read_bytes())


if __name__ == "__main__":
    unittest.main()
