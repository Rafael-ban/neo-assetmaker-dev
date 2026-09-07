"""真实 ExportService 包发布的端到端回归测试。"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest import mock

import cv2
import numpy as np
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QThread

from core.media_tools import MediaToolchain
from tests.helpers.m5_render_fixture import build_default_render_session
from tests.qt_harness import ensure_app


ROOT = Path(__file__).resolve().parents[1]
TOOLCHAIN = MediaToolchain.discover(str(ROOT))
REAL_EXPORT_READY = not TOOLCHAIN.missing_for_export()


def setUpModule():
    ensure_app()


@contextmanager
def _running_export(service, **kwargs):
    """每条真实导出测试共用的退出保护，必须嵌套在临时目录内。"""
    worker = None
    try:
        service.export_all(**kwargs)
        worker = service._worker
        yield worker
    finally:
        if worker is None:
            worker = service._worker
        if worker is not None and not sip.isdeleted(worker):
            try:
                if worker.isRunning():
                    worker.cancel()
            finally:
                # 不把等待超时变成目录可删除的依据。测试在主线程调用；
                # 必须确认该线程退出，才能离开外层 TemporaryDirectory。
                worker.wait()
        QCoreApplication.processEvents()


@unittest.skipUnless(REAL_EXPORT_READY, "bundled VSPipe/x264/muxer unavailable")
class ExportPackageIntegrationTests(unittest.TestCase):
    @staticmethod
    def _wait_for_terminal(completed, failed, timeout=90.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            if completed or failed:
                return
            time.sleep(0.01)
        raise AssertionError("真实 ExportService 导出超时")

    def test_real_service_publishes_and_replaces_sealed_video_package(self):
        """图片 loop 首发，再用真实视频替换为 loop+intro，覆盖中文路径。"""
        from config.epconfig import EPConfig
        from core.export_service import ExportService
        from core.vs_runtime.job import load_render_job, write_render_job
        from core.vs_runtime.session import compute_job_sha256

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "中文 完整导出"
            root.mkdir()
            source = root / "source.png"
            image = np.zeros((640, 360, 3), np.uint8)
            image[..., 1] = 180
            encoded, png = cv2.imencode(".png", image)
            self.assertTrue(encoded)
            source.write_bytes(png.tobytes())
            final_dir = root / "package"
            service = ExportService()
            service._media_toolchain = TOOLCHAIN
            completed, failed, terminal_states = [], [], []
            worker = None

            def on_completed(message):
                completed.append(message)
                terminal_states.append((
                    service._worker,
                    worker.isRunning(),
                    QThread.currentThread() == service.thread(),
                ))

            service.export_completed.connect(on_completed)
            service.export_failed.connect(failed.append)

            for generation in (1, 2):
                completed.clear()
                failed.clear()
                terminal_states.clear()
                session = build_default_render_session(
                    root / f"session-{generation}",
                    source_path=source if generation == 1 else final_dir / "loop.mp4",
                    source_kind="image" if generation == 1 else "video",
                    end_frame=12,
                    epoch=generation,
                )
                intro_session = None
                if generation == 2:
                    intro_job = replace(
                        load_render_job(session.job_path), track="intro", epoch=3
                    )
                    intro_job_path = write_render_job(intro_job)
                    intro_session = replace(
                        session,
                        track="intro",
                        epoch=3,
                        job_path=str(intro_job_path),
                        job_sha256=compute_job_sha256(intro_job_path),
                    )
                config = EPConfig(uuid=f"real-package-{generation}")
                config.icon = ""
                config.loop.file = "loop.mp4"
                config.intro.enabled = intro_session is not None
                config.intro.file = "intro.mp4" if intro_session else ""

                with _running_export(
                    service,
                    output_dir=str(final_dir),
                    epconfig=config,
                    loop_render_session=session,
                    intro_render_session=intro_session,
                ) as worker:
                    self.assertIsNotNone(worker)
                    frozen_jobs = [Path(task.session.job_path) for task in worker._tasks]
                    self._wait_for_terminal(completed, failed)

                self.assertEqual(len(completed), 1)
                self.assertEqual(failed, [])
                self.assertEqual(terminal_states, [(None, False, True)])
                self.assertIsNone(service._worker)
                video_names = ["loop.mp4"]
                if intro_session:
                    video_names.append("intro.mp4")
                self.assertEqual(
                    sorted(path.name for path in final_dir.iterdir()),
                    sorted(["epconfig.json", *video_names]),
                )
                self.assertFalse((final_dir / ".assetmaker-work").exists())
                self.assertTrue(all(not path.exists() for path in frozen_jobs))
                self.assertTrue(Path(session.job_path).is_file())
                if intro_session:
                    self.assertTrue(Path(intro_session.job_path).is_file())
                self.assertFalse(list(root.glob(".package.staging-*")))
                self.assertFalse(list(root.glob(".package.backup-*")))
                self.assertFalse((root / ".package.lock").exists())
                payload = json.loads((final_dir / "epconfig.json").read_text("utf-8"))
                self.assertEqual(payload["uuid"], f"real-package-{generation}")
                self.assertEqual(payload["loop"]["file"], "loop.mp4")
                if intro_session:
                    self.assertEqual(payload["intro"]["file"], "intro.mp4")

                for name in video_names:
                    with self.subTest(generation=generation, video=name):
                        capture = cv2.VideoCapture(str(final_dir / name))
                        try:
                            decoded_frames = 0
                            while True:
                                decoded, frame = capture.read()
                                if not decoded:
                                    break
                                self.assertEqual(frame.shape, (640, 384, 3))
                                decoded_frames += 1
                        finally:
                            capture.release()
                        self.assertEqual(decoded_frames, 12)

    def test_real_cancellation_and_script_failure_preserve_old_package(self):
        """真实 worker 预检取消或脚本失败均不发布半包、不覆盖旧包。"""
        from config.epconfig import EPConfig
        from core.export_service import ExportService
        from core.vs_runtime.session import compute_script_bundle_hash
        from tests.test_export_robustness import _package_bytes, _write_old_package

        for mode in ("cancel", "script_error"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                source = root / "source.png"
                encoded, png = cv2.imencode(
                    ".png", np.full((16, 16, 3), 127, np.uint8)
                )
                self.assertTrue(encoded)
                source.write_bytes(png.tobytes())
                session = build_default_render_session(
                    root / "session", source_path=source,
                    source_kind="image", end_frame=12,
                )
                if mode == "script_error":
                    script = Path(session.selection.script_path)
                    script.write_text(
                        script.read_text("utf-8")
                        + "\nraise RuntimeError('injected user script failure')\n",
                        encoding="utf-8",
                    )
                    session = replace(session, selection=replace(
                        session.selection, bundle_hash=compute_script_bundle_hash(script)
                    ))
                final_dir = root / "package"
                old_bytes = _write_old_package(final_dir)
                service = ExportService()
                completed, failed, terminal_states, cancellations = [], [], [], []
                worker = None

                def cancel_on_preflight(_value, message):
                    if "预检 VapourSynth" in message and not cancellations:
                        cancellations.append(message)
                        service.cancel()

                def on_failed(message):
                    failed.append(message)
                    terminal_states.append((service._worker, worker.isRunning()))

                service.export_completed.connect(completed.append)
                service.export_failed.connect(on_failed)
                if mode == "cancel":
                    service.progress_updated.connect(cancel_on_preflight)
                with _running_export(
                    service, output_dir=str(final_dir), epconfig=EPConfig(),
                    loop_render_session=session,
                ) as worker:
                    self.assertIsNotNone(worker)
                    self._wait_for_terminal(completed, failed)

                self.assertEqual(completed, [])
                self.assertEqual(len(failed), 1)
                self.assertEqual(terminal_states, [(None, False)])
                if mode == "cancel":
                    self.assertEqual(len(cancellations), 1)
                    self.assertIn("cancel", failed[0].lower())
                else:
                    self.assertIn("injected user script failure", failed[0])
                self.assertEqual(_package_bytes(final_dir), old_bytes)
                self.assertFalse(list(root.glob(".package.staging-*")))
                self.assertFalse(list(root.glob(".package.backup-*")))
                self.assertFalse((root / ".package.lock").exists())


class ExportIntegrationCleanupTests(unittest.TestCase):
    def test_wait_failure_joins_worker_before_temporary_directory_cleanup(self):
        """等待抛异常时，测试自身也必须先取消并回收确切的 worker。"""
        from config.epconfig import EPConfig
        from core.export_service import ExportService, ExportWorker

        for error_type in (AssertionError, RuntimeError):
            with self.subTest(error_type=error_type.__name__):
                workers, services, directories, cleanup_states = [], [], [], []
                real_temporary_directory = tempfile.TemporaryDirectory

                class CancelledOnlyWorker(ExportWorker):
                    def __init__(self, parent):
                        super().__init__(parent)
                        self.entered = threading.Event()
                        self.released = threading.Event()
                        self.cancel_requested = False
                        self.directory_alive_at_exit = False
                        workers.append(self)
                        services.append(parent)

                    def run(self):
                        self.entered.set()
                        self.released.wait(5)
                        self.directory_alive_at_exit = (
                            self._package.staging_dir.is_dir()
                        )
                        super().run()

                    def cancel(self):
                        self.cancel_requested = True
                        super().cancel()
                        self.released.set()

                class ObservedTemporaryDirectory(real_temporary_directory):
                    def __init__(self, *args, **kwargs):
                        super().__init__(*args, **kwargs)
                        directories.append(self)

                    def cleanup(self):
                        running = any(
                            not sip.isdeleted(worker) and worker.isRunning()
                            for worker in workers
                        )
                        cleanup_states.append(running)
                        # 安全观测旧实现；真正删除留到所有线程退出之后。
                        if not running:
                            super().cleanup()

                try:
                    with mock.patch(
                        "core.export_service.ExportWorker", CancelledOnlyWorker
                    ), mock.patch.object(
                        tempfile, "TemporaryDirectory", ObservedTemporaryDirectory
                    ), mock.patch.object(
                        MediaToolchain, "discover", return_value=MediaToolchain()
                    ), self.assertRaisesRegex(error_type, "injected export wait"):
                        # 直接测试共用退出保护，不绕过真实集成类的 skip。
                        # 使用普通图片任务且强制无工具；闩锁 worker 在执行
                        # 任何生产任务之前被取消，不需要 VS、脚本或编码器。
                        with tempfile.TemporaryDirectory() as temp_dir:
                            service = ExportService()
                            with _running_export(
                                service,
                                output_dir=str(Path(temp_dir) / "package"),
                                epconfig=EPConfig(),
                                logo_mat=np.zeros((2, 2, 3), dtype=np.uint8),
                            ) as worker:
                                self.assertIsNotNone(worker)
                                self.assertTrue(worker.entered.wait(2))
                                raise error_type("injected export wait failure")

                    self.assertEqual(cleanup_states, [False])
                    self.assertTrue(workers[0].cancel_requested)
                    self.assertTrue(workers[0].directory_alive_at_exit)
                    self.assertTrue(
                        sip.isdeleted(workers[0]) or not workers[0].isRunning()
                    )
                finally:
                    for worker in workers:
                        if not sip.isdeleted(worker):
                            worker.cancel()
                            self.assertTrue(worker.wait(5000))
                    QCoreApplication.processEvents()
                    for directory in directories:
                        directory.cleanup()


if __name__ == "__main__":
    unittest.main()
