"""Opt-in acceptance of the relocated Windows application and media pipeline.

This runs production models, worker IPC and package export without opening the
GUI or reading the user's runtime overrides. Linux unit tests mock the media
boundary; the real acceptance is run against the frozen Windows executable.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback

from core.file_utils import atomic_write_json
from utils.file_utils import get_app_dir


class ExportShutdownTimeout(TimeoutError):
    """Keep the live service owned until the smoke process is terminated."""

    def __init__(self, service):
        self.service = service
        super().__init__("Export did not stop within 35 seconds after cancellation")


@contextmanager
def _isolated_environment(values):
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _wait_until(predicate, timeout_ms):
    from PyQt6.QtCore import QEventLoop, QTimer

    if predicate():
        return True
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(10)
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    poll.start()
    deadline.start(timeout_ms)
    loop.exec()
    poll.stop()
    deadline.stop()
    return bool(predicate())


def _export(session, root, progress):
    from config.epconfig import EPConfig, LoopConfig
    from core.export_service import ExportService

    service = ExportService()
    terminal = []
    service.export_completed.connect(lambda message: terminal.append((True, message)))
    service.export_failed.connect(lambda message: terminal.append((False, message)))
    service.progress_updated.connect(
        lambda percent, message: progress("export", export_progress=percent, export_message=message)
    )
    output = root / "导出 包"
    service.export_all(
        str(output), EPConfig(name="Frozen application smoke", icon="", loop=LoopConfig(file="loop.mp4")),
        loop_render_session=session,
    )
    if not _wait_until(lambda: bool(terminal), 120_000):
        service.cancel()
        if not _wait_until(lambda: bool(terminal), 35_000):
            # Never delete the job/source while a QThread or its child still
            # uses them. The entry point writes the report and exits the process.
            raise ExportShutdownTimeout(service)
        raise TimeoutError(f"Export exceeded 120 seconds; cancellation result: {terminal[-1][1]}")
    succeeded, message = terminal[0]
    if not succeeded:
        raise RuntimeError(message)
    if service.is_exporting:
        raise RuntimeError("Export signalled completion before its worker stopped")
    return output


def _decode_export(output):
    import cv2

    video = output / "loop.mp4"
    capture = cv2.VideoCapture(str(video))
    count = 0
    try:
        if not capture.isOpened():
            raise RuntimeError(f"OpenCV cannot open exported video: {video}")
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame.shape != (640, 360, 3):
                raise RuntimeError(f"Unexpected decoded frame shape: {frame.shape}")
            if float(frame.mean()) < 20:
                raise RuntimeError("Exported frame is unexpectedly black")
            count += 1
            if count > 12:
                raise RuntimeError("Export contains more than 12 frames")
    finally:
        capture.release()
    if count != 12:
        raise RuntimeError(f"Expected 12 decoded frames, got {count}")
    leftovers = [str(path.relative_to(output.parent)) for path in output.parent.rglob("*")
                 if path.name == ".assetmaker-work"
                 or path.name.startswith(f".{output.name}.staging-")
                 or path.name.startswith(f".{output.name}.backup-")
                 or path.name == f".{output.name}.lock"]
    if leftovers:
        raise RuntimeError(f"Export transaction left private paths: {leftovers}")
    if not (output / "epconfig.json").is_file():
        raise RuntimeError("Export package is missing epconfig.json")
    return {"frames": count, "width": 360, "height": 640, "private_work_removed": True}


def _exercise(app_dir, root, progress):
    import cv2
    import numpy as np
    from PyQt6.QtCore import QCoreApplication
    from core.vs_runtime.job import (
        CropSpec, OutputSpec, PathSpec, RationalFPS, RenderJob, SourceSpec,
        TimelineSpec, TransformSpec, write_render_job,
    )
    from core.vs_runtime.script_header import parse_script_header
    from core.vs_runtime.session import (
        RenderSession, ScriptSelection, compute_job_sha256, compute_script_bundle_hash,
    )
    from core.vs_runtime.snapshot import RuntimeSnapshot
    from core.vs_runtime.worker_process import SyncVSWorkerProcess

    # Hold the Qt application for the entire export signal/event lifecycle.
    app = QCoreApplication.instance() or QCoreApplication([sys.argv[0]])
    progress("runtime")
    snapshot = RuntimeSnapshot.resolve(app_dir, user_path=root / "不存在 user.json")
    environment = snapshot.worker_environment()
    verified = RuntimeSnapshot.from_environment(app_dir, environment)
    if verified != snapshot:
        raise RuntimeError("RuntimeSnapshot environment round trip differs")
    progress("runtime", runtime_fingerprint=snapshot.fingerprint)
    image = np.full((640, 360, 3), (32, 96, 208), dtype=np.uint8)
    source = root / "中文 素材.png"
    ok, png = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("PNG fixture encoding failed")
    source.write_bytes(png.tobytes())
    script = app_dir / "resources" / "vapoursynth" / "default_pipeline.vpy"
    job = RenderJob(
        api_version=1, epoch=1, track="loop", project_root=str(root),
        source=SourceSpec(str(source), "image", 12),
        timeline=TimelineSpec(0, 12, RationalFPS(30, 1)),
        transform=TransformSpec(0, CropSpec("post_rotation_source_pixels", 0, 0, 0, 0)),
        output=OutputSpec.from_profile("360x640"), paths=PathSpec(str(root / "cache")),
    )
    job_path = write_render_job(job)
    selection = ScriptSelection.from_header(
        script, parse_script_header(script), compute_script_bundle_hash(script),
    )
    session = RenderSession(1, "loop", selection, str(job_path),
                            compute_job_sha256(job_path), snapshot.fingerprint)
    # ExportService's existing default config readers and its preflight worker
    # use the same isolated APPDATA and full snapshot environment as preview.
    environment["APPDATA"] = str(root / "isolated appdata")
    with _isolated_environment(environment):
        worker = SyncVSWorkerProcess(app_dir=app_dir, env=environment)
        try:
            progress("worker_start")
            worker.start(timeout_ms=15_000)
            progress("worker_load")
            metadata = worker.load(session, timeout_ms=30_000)
            node = metadata.output0
            if (node.width, node.height, node.num_frames) != (360, 640, 12):
                raise RuntimeError(f"Unexpected worker output metadata: {node}")
            progress("worker_frame")
            for index in (0, 11):
                frame = worker.request_frame(
                    epoch=1, index=index, surface="final", viewport=(360, 640),
                    zoom_factor=1.0, pan=(0.0, 0.0), timeout_ms=10_000,
                )
                if frame.shape != (640, 360, 3) or float(frame.mean()) < 20:
                    raise RuntimeError(f"Invalid preview frame {index}: {frame.shape}")
            progress("worker_shutdown")
            worker.shutdown(timeout_ms=5_000)
            if worker.wait(timeout_ms=5_000) != 0:
                raise RuntimeError("Preview worker did not exit successfully")
        finally:
            worker.close()
            progress(None, worker_logs=worker.logs[-20:])
        progress("export")
        output = _export(session, root, progress)
        progress("decode_export")
        progress("verified", export=_decode_export(output))
    # Keep a strong Qt reference until all worker activity has finished.
    assert app is not None


def run_smoke(argv=None):
    """Return a process exit code; every execution stage is persisted as JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke-test", action="store_true", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    report_path = args.report.resolve()
    app_dir = Path(get_app_dir()).resolve()
    started = time.monotonic()
    report = {"schema_version": 1, "success": False, "stage": "starting",
              "app_dir": str(app_dir), "executable": str(Path(sys.executable).resolve()),
              "frozen": bool(getattr(sys, "frozen", False)), "platform": sys.platform,
              "cwd": str(Path.cwd())}

    def progress(stage, **details):
        report.update(stage=stage or report["stage"], elapsed_seconds=round(time.monotonic() - started, 3), **details)
        atomic_write_json(report_path, report, indent=2)

    def failure(exc):
        report.update(error_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc())
        progress(report["stage"])

    try:
        progress("starting")
        progress("platform")
        if sys.platform != "win32":
            raise RuntimeError("Application smoke acceptance requires Windows media tools")
        with tempfile.TemporaryDirectory(prefix="AssetMaker 中文 验收 ") as temp:
            root = Path(temp).resolve()
            progress("temporary_directory", temporary_directory=str(root))
            try:
                _exercise(app_dir, root, progress)
            except ExportShutdownTimeout as exc:
                report["forced_exit"] = True
                failure(exc)
                # Avoid QThread destruction and fixture removal under live work.
                # CI kills the entire process tree if even this exit is blocked.
                os._exit(1)
        progress("complete", success=True, temporary_directory_removed=not root.exists())
        return 0
    except Exception as exc:
        failure(exc)
        return 1


__all__ = ["run_smoke"]
