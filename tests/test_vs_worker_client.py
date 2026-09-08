from __future__ import annotations

import os
import sys
import threading
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QEvent, QObject, QThread, QTimer, pyqtSlot

from config.vs_runtime import VSRuntimeConfig, WorkerConfig
from core.vs_runtime.session import NodeMetadata, SessionMetadata
from core.vs_runtime.worker_process import STAGING_CLEANUP_ERROR_CODE
from gui.workers.vs_worker_client import VSWorkerClient
from tests.qt_harness import ensure_app


class _Signal:
    def __init__(self):
        self.callback = None

    def connect(self, callback):
        self.callback = callback

    def emit(self):
        if self.callback is not None:
            self.callback()


class _FakeTimer:
    instances = []

    def __init__(self, _parent=None):
        self.timeout = _Signal()
        self.active = False
        self.interval = None
        _FakeTimer.instances.append(self)

    def setSingleShot(self, _single):
        pass

    def start(self, interval):
        self.active = True
        self.interval = interval

    def stop(self):
        self.active = False

    def deleteLater(self):
        self.deleted = True

    def fire(self, *, even_if_stopped=False):
        if self.active or even_if_stopped:
            self.active = False
            self.timeout.emit()


class _FakeTransport:
    def __init__(self, *, first_request_id=1):
        self.generation = 0
        self.alive = False
        self.settling = False
        self.listener = None
        self.next_request_id = first_request_id
        self.requests = []
        self.frame_requests = []
        self.terminate_count = 0
        self.kill_count = 0

    def add_listener(self, listener):
        self.listener = listener

    def remove_listener(self, listener):
        if self.listener == listener:
            self.listener = None

    def start(self):
        self.generation += 1
        self.alive = True
        self.settling = False
        return self.generation

    def send_request(self, message):
        request_id = self.next_request_id
        self.next_request_id += 1
        self.requests.append((request_id, message))
        return request_id

    def request_frame(self, **fields):
        request_id = self.next_request_id
        self.next_request_id += 1
        self.frame_requests.append((request_id, fields))
        return request_id

    def cancel_epoch(self, epoch):
        return self.send_request({"type": "cancel_epoch", "epoch": epoch})

    def terminate(self):
        self.terminate_count += 1
        self.alive = False

    def kill(self):
        self.kill_count += 1
        self.alive = False

    def close(self):
        self.alive = False

    def emit(self, event):
        assert self.listener is not None
        self.listener(event)


class _FakeSession:
    def __init__(self, epoch=7):
        self.epoch = epoch

    def to_load_message(self, request_id):
        return {"type": "load", "request_id": request_id, "epoch": self.epoch}


def _metadata(epoch=7):
    output0 = NodeMetadata(
        width=384,
        height=640,
        num_frames=3,
        fps_num=30000,
        fps_den=1001,
        pixel_format="YUV420P8",
        matrix="170m",
        transfer="170m",
        primaries="170m",
        range="limited",
    )
    return SessionMetadata(
        epoch=epoch,
        mode="raw",
        capabilities=frozenset({"source"}),
        output0=output0,
        editor=None,
    )


class _ThreadProbe(QObject):
    def __init__(self):
        super().__init__()
        self.calls = []

    @pyqtSlot(object, object, str, object, object)
    def receive(self, request_id, epoch, surface, index, frame):
        self.calls.append(
            (request_id, epoch, surface, index, frame, QThread.currentThread())
        )


class VSWorkerClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = ensure_app()

    def setUp(self):
        _FakeTimer.instances.clear()
        self.transport = _FakeTransport()
        self.client = VSWorkerClient(
            transport=self.transport,
            worker_config=WorkerConfig(
                startup_timeout_ms=20,
                frame_timeout_ms=30,
                shutdown_timeout_ms=40,
            ),
            timer_factory=_FakeTimer,
        )
        self.addCleanup(self.client.close)

    def _drain_events(self):
        for _ in range(20):
            self.app.processEvents()

    def test_wraps_the_supplied_transport_and_emits_typed_metadata(self):
        received = []
        self.client.metadata_ready.connect(
            lambda request_id, epoch, metadata: received.append(
                (request_id, epoch, metadata)
            )
        )

        hello = self.client.start()
        self.transport.emit(
            {
                "type": "ready",
                "request_id": hello,
                "operation": "hello",
                "generation": self.transport.generation,
            }
        )
        request_id = self.client.load(_FakeSession())
        metadata = _metadata()
        self.transport.emit(
            {
                "type": "metadata",
                "request_id": request_id,
                "epoch": 7,
                "metadata": metadata,
                "generation": self.transport.generation,
            }
        )
        self._drain_events()

        self.assertIs(self.client.transport, self.transport)
        self.assertEqual(received, [(request_id, 7, metadata)])

    def test_explicit_runtime_snapshot_controls_client_timeouts_and_transport(self):
        """同一快照同时决定 worker 环境与 GUI 请求超时。"""
        from core.vs_runtime.snapshot import RuntimeSnapshot

        snapshot = RuntimeSnapshot(
            app_dir=str(Path(__file__).resolve().parents[1]),
            runtime=VSRuntimeConfig(
                worker=WorkerConfig(
                    startup_timeout_ms=101,
                    frame_timeout_ms=202,
                    shutdown_timeout_ms=303,
                )
            ),
            fingerprint="a" * 64,
        )
        client = VSWorkerClient(runtime_snapshot=snapshot)
        self.addCleanup(client.close)

        self.assertIs(client.runtime_snapshot, snapshot)
        self.assertEqual(client.worker_config, snapshot.runtime.worker)
        self.assertEqual(
            client.transport.env["ASSETMAKER_VS_RUNTIME_FINGERPRINT"],
            snapshot.fingerprint,
        )

    def test_reader_thread_event_reaches_receiver_on_gui_thread(self):
        probe = _ThreadProbe()
        self.client.frame_ready.connect(probe.receive)
        frame = object()
        event = {
            "type": "frame_ready",
            "request_id": 9,
            "epoch": 7,
            "index": 2,
            "surface": "editor",
            "frame": frame,
            "generation": 0,
        }

        thread = threading.Thread(target=self.transport.emit, args=(event,))
        thread.start()
        thread.join()
        self._drain_events()

        self.assertEqual(len(probe.calls), 1)
        self.assertEqual(probe.calls[0][:5], (9, 7, "editor", 2, frame))
        self.assertIs(probe.calls[0][5], self.app.thread())

    def test_frame_timeout_continue_wait_reuses_request_and_does_not_kill(self):
        timeouts = []
        self.client.request_timed_out.connect(
            lambda request_id, epoch: timeouts.append((request_id, epoch))
        )
        self.transport.start()
        request_id = self.client.request_frame(
            epoch=7,
            index=1,
            surface="final",
            viewport=(384, 640),
            zoom_factor=1.0,
            pan=(0.5, 0.5),
        )
        first_timer = _FakeTimer.instances[-1]

        first_timer.fire()
        self.assertEqual(timeouts, [(request_id, 7)])
        self.assertEqual(self.transport.terminate_count, 0)
        self.assertTrue(self.client.continue_wait(request_id))
        second_timer = _FakeTimer.instances[-1]
        second_timer.fire()

        self.assertEqual(timeouts, [(request_id, 7), (request_id, 7)])
        self.assertEqual(len(self.transport.frame_requests), 1)
        self.assertEqual(self.transport.terminate_count, 0)

    def test_auto_submitted_coalesced_frame_gets_timeout(self):
        timeouts = []
        self.client.request_timed_out.connect(
            lambda request_id, epoch: timeouts.append((request_id, epoch))
        )
        self.transport.start()

        self.transport.emit(
            {
                "type": "frame_submitted",
                "request_id": 91,
                "epoch": 7,
                "index": 4,
                "surface": "final",
                "generation": self.transport.generation,
            }
        )
        self._drain_events()

        self.assertEqual(len(_FakeTimer.instances), 1)
        _FakeTimer.instances[0].fire()
        self.assertEqual(timeouts, [(91, 7)])
        self.assertTrue(self.client.continue_wait(91))

    def test_stale_timeout_cannot_terminate_new_generation(self):
        self.client.start()
        old_timer = _FakeTimer.instances[-1]
        self.client.terminate_and_restart()
        self.transport.emit(
            {
                "type": "worker_crashed",
                "generation": 1,
                "message": "old child exited",
            }
        )
        self._drain_events()
        self.assertEqual(self.transport.generation, 2)
        terminated = self.transport.terminate_count

        old_timer.fire(even_if_stopped=True)

        self.assertEqual(self.transport.terminate_count, terminated)

    def test_shutdown_timeout_escalates_terminate_then_kill(self):
        self.transport.start()
        self.client.shutdown()
        shutdown_timer = _FakeTimer.instances[-1]

        shutdown_timer.fire()
        self.assertEqual(self.transport.terminate_count, 1)
        kill_timer = _FakeTimer.instances[-1]
        self.transport.alive = True
        kill_timer.fire()

        self.assertEqual(self.transport.kill_count, 1)

    def test_shutdown_ready_keeps_watchdog_until_process_exit(self):
        self.transport.start()
        request_id = self.client.shutdown()
        shutdown_timer = _FakeTimer.instances[-1]
        self.transport.emit(
            {
                "type": "ready",
                "request_id": request_id,
                "operation": "shutdown",
                "generation": self.transport.generation,
            }
        )
        self._drain_events()

        shutdown_timer.fire()

        self.assertEqual(self.transport.terminate_count, 1)

    def test_queued_exit_event_cannot_restart_after_close(self):
        self.transport.start()
        self.client.terminate_and_restart()
        self.transport.emit(
            {
                "type": "worker_crashed",
                "generation": self.transport.generation,
                "message": "old child exited",
            }
        )

        self.client.close()
        self._drain_events()

        self.assertEqual(self.transport.generation, 1)

    def test_protocol_error_waits_for_final_exit_before_restart(self):
        self.transport.start()
        self.client.terminate_and_restart()

        self.transport.emit(
            {
                "type": "protocol_error",
                "generation": self.transport.generation,
                "message": "bad wire response",
            }
        )
        self._drain_events()
        self.assertEqual(self.transport.generation, 1)

        self.transport.emit(
            {
                "type": "worker_crashed",
                "generation": self.transport.generation,
                "message": "old child exited",
            }
        )
        self._drain_events()

        self.assertEqual(self.transport.generation, 2)

    def test_manual_restart_waits_for_exited_child_to_finish_settling(self):
        self.transport.start()
        self.transport.alive = False
        self.transport.settling = True

        self.client.terminate_and_restart()

        self.assertEqual(self.transport.generation, 1)
        self.transport.settling = False
        self.transport.emit(
            {
                "type": "worker_crashed",
                "generation": 1,
                "message": "old child cleanup finished",
            }
        )
        self._drain_events()
        self.assertEqual(self.transport.generation, 2)

    def test_protocol_error_and_final_exit_emit_one_crash(self):
        crashes = []
        self.client.worker_crashed.connect(crashes.append)
        self.transport.start()

        self.transport.emit(
            {
                "type": "protocol_error",
                "generation": self.transport.generation,
                "message": "bad wire response",
            }
        )
        self.transport.emit(
            {
                "type": "worker_crashed",
                "generation": self.transport.generation,
                "message": "child terminated",
            }
        )
        self._drain_events()

        self.assertEqual(crashes, ["bad wire response"])

    def test_cleanup_failure_is_one_stable_signal_and_one_restart(self):
        failures = []
        crashes = []
        self.client.request_failed.connect(
            lambda request_id, code, message: failures.append(
                (request_id, code, message)
            )
        )
        self.client.worker_crashed.connect(crashes.append)
        uncaught = []
        original_excepthook = sys.excepthook
        sys.excepthook = lambda *args: uncaught.append(args)
        try:
            self.client.start()
            self.client.terminate_and_restart()
            old_generation = self.transport.generation
            event = {
                "type": "worker_crashed",
                "generation": old_generation,
                "exit_code": 0,
                "code": "worker.staging_cleanup_failed",
                "message": (
                    "[worker.staging_cleanup_failed] "
                    "generation staging cleanup failed"
                ),
            }
            self.transport.emit(event)
            self._drain_events()
            self.transport.emit(event)
            self._drain_events()
        finally:
            sys.excepthook = original_excepthook

        self.assertEqual(uncaught, [])
        self.assertEqual(self.transport.generation, 2)
        self.assertEqual(
            failures,
            [
                (
                    0,
                    "worker.staging_cleanup_failed",
                    "[worker.staging_cleanup_failed] "
                    "generation staging cleanup failed",
                )
            ],
        )
        self.assertEqual(crashes, [])

    def test_cleanup_terminal_suppresses_same_cleanup_restart_failure(self):
        class RetainedCleanupError(RuntimeError):
            code = STAGING_CLEANUP_ERROR_CODE

        class RetainedCleanupTransport(_FakeTransport):
            def __init__(self):
                super().__init__()
                self.start_count = 0

            def start(self):
                self.start_count += 1
                if self.start_count == 2:
                    raise RetainedCleanupError("retained cleanup still denied")
                return super().start()

        transport = RetainedCleanupTransport()
        client = VSWorkerClient(
            transport=transport,
            worker_config=self.client.worker_config,
            timer_factory=_FakeTimer,
        )
        self.addCleanup(client.close)
        failures = []
        crashes = []
        client.request_failed.connect(
            lambda request_id, code, message: failures.append(
                (request_id, code, message)
            )
        )
        client.worker_crashed.connect(crashes.append)

        client.start()
        client.terminate_and_restart()
        cleanup_event = {
            "type": "worker_crashed",
            "generation": 1,
            "exit_code": 0,
            "code": STAGING_CLEANUP_ERROR_CODE,
            "message": "generation staging cleanup failed",
        }
        transport.emit(cleanup_event)
        self._drain_events()
        transport.emit(cleanup_event)
        self._drain_events()

        self.assertEqual(transport.start_count, 2)
        self.assertEqual(transport.generation, 1)
        self.assertEqual(
            failures,
            [
                (
                    0,
                    STAGING_CLEANUP_ERROR_CODE,
                    "generation staging cleanup failed",
                )
            ],
        )
        self.assertEqual(crashes, [])

    def test_new_generation_cleanup_coded_start_failure_is_reported_once(self):
        class NewGenerationCleanupError(RuntimeError):
            code = STAGING_CLEANUP_ERROR_CODE

        class AdvancedGenerationTransport(_FakeTransport):
            def __init__(self):
                super().__init__()
                self.start_count = 0

            def start(self):
                self.start_count += 1
                generation = super().start()
                if self.start_count == 2:
                    raise NewGenerationCleanupError(
                        "worker 协议线程启动失败；新 generation staging 清理失败"
                    )
                return generation

        transport = AdvancedGenerationTransport()
        client = VSWorkerClient(
            transport=transport,
            worker_config=self.client.worker_config,
            timer_factory=_FakeTimer,
        )
        self.addCleanup(client.close)
        failures = []
        crashes = []
        client.request_failed.connect(
            lambda request_id, code, message: failures.append(
                (request_id, code, message)
            )
        )
        client.worker_crashed.connect(crashes.append)

        client.start()
        client.terminate_and_restart()
        transport.emit(
            {
                "type": "worker_crashed",
                "generation": 1,
                "exit_code": 0,
                "code": STAGING_CLEANUP_ERROR_CODE,
                "message": "旧 generation staging 清理失败",
            }
        )
        self._drain_events()

        self.assertEqual(transport.generation, 2)
        self.assertEqual(transport.start_count, 2)
        self.assertEqual(
            [item[:2] for item in failures],
            [
                (0, STAGING_CLEANUP_ERROR_CODE),
                (0, "worker.restart_failed"),
            ],
        )
        self.assertIn("worker 协议线程启动失败", failures[1][2])

        transport.emit(
            {
                "type": "worker_crashed",
                "generation": 2,
                "exit_code": 0,
                "code": STAGING_CLEANUP_ERROR_CODE,
                "message": "新 generation staging 清理失败",
            }
        )
        self._drain_events()

        self.assertEqual(len(failures), 2)
        self.assertEqual(crashes, [])

    def test_queued_restart_startup_failures_are_single_safe_terminals(self):
        class FailingRestartTransport(_FakeTransport):
            def __init__(self, stage):
                super().__init__()
                self.stage = stage
                self.start_count = 0

            def start(self):
                self.start_count += 1
                if self.start_count == 2 and self.stage == "spawn":
                    raise OSError("replacement spawn failed")
                return super().start()

            def send_request(self, message):
                if self.generation == 2 and self.stage == "hello":
                    raise OSError("replacement hello failed")
                return super().send_request(message)

        for stage in ("spawn", "hello", "timer"):
            with self.subTest(stage=stage):
                transport = FailingRestartTransport(stage)
                timer_calls = 0

                def timer_factory(parent):
                    nonlocal timer_calls
                    timer_calls += 1
                    if stage == "timer" and timer_calls == 3:
                        raise RuntimeError("replacement timer failed")
                    return _FakeTimer(parent)

                client = VSWorkerClient(
                    transport=transport,
                    worker_config=self.client.worker_config,
                    timer_factory=timer_factory,
                )
                failures = []
                client.request_failed.connect(
                    lambda request_id, code, message: failures.append(
                        (request_id, code, message)
                    )
                )
                uncaught = []
                original_excepthook = sys.excepthook
                sys.excepthook = lambda *args: uncaught.append(args)
                try:
                    client.start()
                    client.terminate_and_restart()
                    transport.emit(
                        {
                            "type": "worker_crashed",
                            "generation": 1,
                            "message": "old child exited",
                        }
                    )
                    self._drain_events()

                    failed_generation = transport.generation
                    transport.emit(
                        {
                            "type": "worker_crashed",
                            "generation": failed_generation,
                            "message": "partial replacement exited",
                        }
                    )
                    self._drain_events()
                finally:
                    sys.excepthook = original_excepthook
                    client.close()

                self.assertEqual(uncaught, [])
                self.assertEqual(transport.start_count, 2)
                self.assertEqual(len(failures), 1)
                self.assertEqual(failures[0][:2], (0, "worker.restart_failed"))
                self.assertIn(f"replacement {stage} failed", failures[0][2])
                self.assertEqual(client._timeouts, {})
                if stage != "spawn":
                    self.assertGreaterEqual(transport.terminate_count, 2)

    def test_large_request_id_is_not_truncated_by_qt_signal(self):
        large = 2**40
        self.transport.next_request_id = large
        failures = []
        self.client.request_failed.connect(
            lambda request_id, code, message: failures.append(
                (request_id, code, message)
            )
        )
        self.transport.start()
        request_id = self.client.load(_FakeSession())
        self.transport.emit(
            {
                "type": "request_error",
                "request_id": request_id,
                "epoch": 7,
                "code": "worker.failure",
                "message": "failed",
                "generation": self.transport.generation,
            }
        )
        self._drain_events()

        self.assertEqual(failures, [(large, "worker.failure", "failed")])

    def test_completed_requests_do_not_accumulate_real_qtimer_children(self):
        transport = _FakeTransport()
        client = VSWorkerClient(
            transport=transport,
            worker_config=WorkerConfig(
                startup_timeout_ms=60_000,
                frame_timeout_ms=60_000,
                shutdown_timeout_ms=60_000,
            ),
            timer_factory=QTimer,
        )
        self.addCleanup(client.close)
        transport.start()

        for index in range(100):
            request_id = client.request_frame(
                epoch=7,
                index=index,
                surface="final",
                viewport=(384, 640),
                zoom_factor=1.0,
                pan=(0.5, 0.5),
            )
            transport.emit(
                {
                    "type": "frame_discarded",
                    "request_id": request_id,
                    "epoch": 7,
                    "generation": transport.generation,
                }
            )
            self._drain_events()

        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self._drain_events()

        self.assertEqual(client.findChildren(QTimer), [])


if __name__ == "__main__":
    unittest.main()
