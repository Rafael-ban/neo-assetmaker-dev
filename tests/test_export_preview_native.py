"""真实预览控件的导出暂停回归；默认异常钩子下的 native 退出隔离在子进程。"""

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock


def _run_probe():
    from PyQt6.QtCore import QTimer, Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QDialog

    from tests.qt_harness import ensure_app
    from tests.test_export_preview_pause import (
        _CompletedExportService,
        _QtExportHost,
        _ValidValidator,
    )
    from tests.test_preview_worker_integration import PreviewWorkerContractTests

    case = unittest.TestCase()
    case.assertIs(sys.excepthook, sys.__excepthook__)
    app = ensure_app()
    # 复用完整 FakeWorkerClient、真实 load/render context 和完整 SessionMetadata。
    # 不直接伪造 _worker_ready_for_frames、_selection 或 _session_metadata。
    fixture = PreviewWorkerContractTests()
    window = _QtExportHost()
    observations = {}

    def wait_for_requests(client, count):
        dialog = QDialog(window)
        timed_out = []
        poll = QTimer(dialog)
        poll.timeout.connect(
            lambda: dialog.accept() if len(client.requests) >= count else None
        )
        watchdog = QTimer(dialog)
        watchdog.setSingleShot(True)

        def timeout():
            timed_out.append(True)
            dialog.done(QDialog.DialogCode.Rejected)

        watchdog.timeout.connect(timeout)
        try:
            poll.start(10)
            watchdog.start(2000)
            dialog.exec()
            case.assertEqual(timed_out, [])
            case.assertGreaterEqual(len(client.requests), count)
        finally:
            poll.stop()
            watchdog.stop()
            dialog.deleteLater()

    try:
        fixture.setUp()
        fixture._load_compatible()
        fixture.widget.flush_render_job()
        fixture._resolve_current()
        preview, client = fixture.widget, fixture.client
        window.video_preview = preview
        preview.play()
        baseline = len(client.requests)
        wait_for_requests(client, baseline + 3)
        observations["playing_requests"] = len(client.requests) - baseline
        frozen_session = []

        def collect():
            case.assertFalse(preview.is_playing)
            session = preview.flush_render_job()
            fixture._resolve_current()
            frozen_session.append(session)
            return {"loop_render_session": session}

        window._collect_export_data = collect
        service = _CompletedExportService(window)
        finish_timer = QTimer(window)
        finish_timer.setSingleShot(True)
        watchdog = QTimer(window)
        watchdog.setSingleShot(True)
        timed_out = []
        request_count = 0

        def complete():
            observations["paused_requests"] = len(client.requests) - request_count
            service._worker = None
            service.export_completed.emit("已完成")
            QTest.mouseClick(
                window._export_dialog.btn_action, Qt.MouseButton.LeftButton
            )

        def export_all(**kwargs):
            nonlocal request_count
            case.assertIs(kwargs["loop_render_session"], frozen_session[-1])
            request_count = len(client.requests)
            service._worker = object()
            finish_timer.start(200)
            watchdog.start(2000)

        def timeout():
            timed_out.append(True)
            service._worker = None
            window._export_dialog.done(QDialog.DialogCode.Rejected)

        finish_timer.timeout.connect(complete)
        watchdog.timeout.connect(timeout)
        service.export_all = export_all
        try:
            with (
                mock.patch("core.validator.EPConfigValidator", _ValidValidator),
                mock.patch(
                    "gui.main_window.QFileDialog.getExistingDirectory",
                    return_value=str(fixture.root),
                ),
                mock.patch("core.export_service.ExportService", return_value=service),
            ):
                window._on_export()
        finally:
            finish_timer.stop()
            watchdog.stop()
        case.assertEqual(timed_out, [])
        case.assertEqual(observations["paused_requests"], 0)
        case.assertIs(preview.current_render_session(), frozen_session[-1])
        case.assertTrue(preview.is_playing)
        resumed = len(client.requests)
        wait_for_requests(client, resumed + 2)
        observations["resumed_requests"] = len(client.requests) - resumed
        preview.close()
        observations["worker_close_calls"] = client.closed
        case.assertEqual(client.closed, 1)
        case.assertFalse(preview.timer.isActive())
        case.assertIs(sys.excepthook, sys.__excepthook__)
    finally:
        case.assertTrue(fixture.doCleanups())
        window.deleteLater()
        app.processEvents()
    print("R3_REAL_WIDGET_OK=" + json.dumps(observations))


class ExportPreviewNativeTests(unittest.TestCase):
    def test_complete_worker_fixture_survives_modal_export_and_shutdown(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-m", "tests.test_export_preview_native", "--probe"],
            cwd=root,
            env={
                **os.environ,
                "QT_QPA_PLATFORM": "offscreen",
                "PYTHONIOENCODING": "utf-8",
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(
            result.returncode, 0,
            f"exit={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}",
        )
        marker = "R3_REAL_WIDGET_OK="
        payloads = [
            line[len(marker):]
            for line in result.stdout.splitlines()
            if line.startswith(marker)
        ]
        self.assertEqual(len(payloads), 1, result.stdout)
        observations = json.loads(payloads[0])
        self.assertGreaterEqual(observations["playing_requests"], 3)
        self.assertEqual(observations["paused_requests"], 0)
        self.assertGreaterEqual(observations["resumed_requests"], 2)
        self.assertEqual(observations["worker_close_calls"], 1)


if __name__ == "__main__":
    if "--probe" in sys.argv:
        _run_probe()
    else:
        unittest.main()
