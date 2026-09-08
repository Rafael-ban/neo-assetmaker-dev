"""把唯一 :class:`WorkerProcess` transport 安全地包装为 Qt signals。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal, pyqtSlot

from config.vs_runtime import WorkerConfig, load_vs_runtime
from core.vs_runtime.session import RenderSession, SessionMetadata
from core.vs_runtime.snapshot import RuntimeSnapshot
from core.vs_runtime.worker_process import (
    STAGING_CLEANUP_ERROR_CODE,
    WorkerProcess,
)


@dataclass(frozen=True)
class _TimeoutToken:
    generation: int
    request_id: int
    epoch: int | None
    kind: str
    serial: int


class VSWorkerClient(QObject):
    """不复制 Popen/PIPE 逻辑的 GUI 线程适配器。"""

    # request_id/epoch 是协议严格正整数、没有 32-bit 上限；使用 object
    # 避免 pyqtSignal(int) 在长会话中静默截断 2**31 以上的标识。
    ready = pyqtSignal()
    # metadata 也是 request terminal。保留 request_id 才能让 widget 在
    # session 换代后精确收束旧 load owner。
    metadata_ready = pyqtSignal(object, object, object)
    # ``frame_ready`` 必须保留 worker wire 的完整身份；epoch/index 本身
    # 不能区分同一 session 中先后到达的 editor/final 请求。
    frame_ready = pyqtSignal(object, object, str, object, object)
    frame_discarded = pyqtSignal(object, object, str, object)
    frame_submitted = pyqtSignal(object, object, str, object)
    request_failed = pyqtSignal(object, str, str)
    request_timed_out = pyqtSignal(object, object)
    operation_completed = pyqtSignal(object, str)
    worker_crashed = pyqtSignal(str)
    worker_stopped = pyqtSignal()
    log_received = pyqtSignal(str, str)

    _transport_event = pyqtSignal(object)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        app_dir: str | Path | None = None,
        transport: WorkerProcess | None = None,
        worker_config: WorkerConfig | None = None,
        runtime_snapshot: RuntimeSnapshot | None = None,
        timer_factory: Callable[[QObject], Any] = QTimer,
    ) -> None:
        super().__init__(parent)
        self.transport = transport or WorkerProcess(app_dir=app_dir)
        self.runtime_snapshot = None
        self._owns_transport = transport is None
        self.worker_config = worker_config
        if runtime_snapshot is not None:
            if worker_config is not None and worker_config != runtime_snapshot.runtime.worker:
                raise ValueError("worker_config 与 runtime_snapshot 冲突")
            self.configure_runtime(runtime_snapshot)
        elif transport is not None and worker_config is None:
            self.worker_config = load_vs_runtime().worker
        self._timer_factory = timer_factory
        self._timeouts: dict[int, tuple[Any, _TimeoutToken]] = {}
        self._timed_out: dict[int, tuple[int, int]] = {}
        self._serial = 0
        self._closed = False
        self._restart_requested = False
        self._failure_reported_generation: int | None = None
        self._restart_kill_key = -1
        self._transport_event.connect(
            self._handle_transport_event,
            Qt.ConnectionType.QueuedConnection,
        )
        self.transport.add_listener(self._relay_transport_event)

    def configure_runtime(self, snapshot: RuntimeSnapshot) -> None:
        """工厂创建后、启动前绑定一次快照；运行中只能退休并新建 client。"""
        if not isinstance(snapshot, RuntimeSnapshot):
            raise TypeError("snapshot 必须是 RuntimeSnapshot")
        if self.transport.generation != 0:
            raise RuntimeError("已启动的 client 不能替换 runtime")
        if Path(self.transport.app_dir).resolve() != Path(snapshot.app_dir):
            raise ValueError("client app_dir 与 runtime_snapshot 冲突")
        self.runtime_snapshot = snapshot
        self.worker_config = snapshot.runtime.worker
        self.transport.env = snapshot.worker_environment(self.transport.env)

    @property
    def generation(self) -> int:
        return self.transport.generation

    @property
    def pid(self) -> int | None:
        return self.transport.pid

    def _relay_transport_event(self, event: dict[str, Any]) -> None:
        if self._closed:
            return
        try:
            self._transport_event.emit(event)
        except RuntimeError:
            # QObject 可在 reader 线程投递事件的同时被 Qt 销毁；迟到事件
            # 不应把 transport 线程变成未捕获异常来源。
            return

    def _schedule_timeout(
        self,
        request_id: int,
        epoch: int | None,
        kind: str,
        timeout_ms: int,
        *,
        generation: int | None = None,
    ) -> None:
        self._clear_timeout(request_id)
        self._serial += 1
        token = _TimeoutToken(
            generation=(
                self.transport.generation
                if generation is None
                else generation
            ),
            request_id=request_id,
            epoch=epoch,
            kind=kind,
            serial=self._serial,
        )
        timer = self._timer_factory(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda current=token: self._timeout_fired(current))
        self._timeouts[request_id] = (timer, token)
        timer.start(timeout_ms)

    @staticmethod
    def _dispose_timer(timer: Any) -> None:
        timer.stop()
        timer.deleteLater()

    def _clear_timeout(self, request_id: int) -> None:
        current = self._timeouts.pop(request_id, None)
        if current is not None:
            self._dispose_timer(current[0])
        self._timed_out.pop(request_id, None)

    def _clear_all_timeouts(self) -> None:
        active = tuple(self._timeouts.values())
        self._timeouts.clear()
        self._timed_out.clear()
        for timer, _token in active:
            self._dispose_timer(timer)

    def _timeout_fired(self, token: _TimeoutToken) -> None:
        current = self._timeouts.get(token.request_id)
        if current is None or current[1] != token:
            return
        self._timeouts.pop(token.request_id, None)
        self._dispose_timer(current[0])
        if self._closed or token.generation != self.transport.generation:
            return
        if token.kind == "frame":
            assert token.epoch is not None
            self._timed_out[token.request_id] = (
                token.generation,
                token.epoch,
            )
            self.request_timed_out.emit(token.request_id, token.epoch)
            return
        if token.kind in {"startup", "load"}:
            self.request_failed.emit(
                token.request_id,
                "worker.timeout",
                "VapourSynth worker 请求超时",
            )
            return
        if token.kind in {"shutdown", "restart"}:
            self.transport.terminate()
            self._schedule_timeout(
                self._restart_kill_key,
                None,
                f"{token.kind}_kill",
                self.worker_config.shutdown_timeout_ms,
            )
            return
        if token.kind in {"shutdown_kill", "restart_kill"}:
            if self.transport.alive:
                self.transport.kill()

    def _start_transport(self) -> int:
        if self._owns_transport and self.runtime_snapshot is None:
            snapshot = RuntimeSnapshot.resolve(self.transport.app_dir)
            if self.worker_config is not None and self.worker_config != snapshot.runtime.worker:
                raise ValueError("worker_config 与运行配置冲突")
            self.configure_runtime(snapshot)
        self.transport.start()
        self._failure_reported_generation = None
        request_id = self.transport.send_request(
            {"type": "hello", "api_version": 1}
        )
        self._schedule_timeout(
            request_id,
            None,
            "startup",
            self.worker_config.startup_timeout_ms,
        )
        return request_id

    def _report_restart_failure(
        self,
        error: BaseException,
        *,
        cleanup_terminal_generation: int | None = None,
    ) -> None:
        """把 queued 自动重启的本地异常收敛为单次稳定 terminal。"""
        self._restart_requested = False
        generation = self.transport.generation
        self._failure_reported_generation = generation
        try:
            self._clear_all_timeouts()
        except BaseException:
            # 自动重启已失败；timer dispose 也不能再逃逸 Qt slot。
            pass
        try:
            if self.transport.alive:
                self.transport.terminate()
        except BaseException:
            try:
                self.transport.kill()
            except BaseException:
                pass
        try:
            error_code = getattr(error, "code", None)
        except BaseException:
            error_code = None
        if (
            cleanup_terminal_generation is not None
            and generation == cleanup_terminal_generation
            and error_code == STAGING_CLEANUP_ERROR_CODE
        ):
            return
        try:
            detail = str(error)
        except BaseException:
            detail = type(error).__name__
        self.request_failed.emit(
            0,
            "worker.restart_failed",
            f"VapourSynth worker 自动重启失败：{detail}",
        )

    def start(self) -> int:
        if self._closed:
            raise RuntimeError("VSWorkerClient 已关闭")
        return self._start_transport()

    def load(self, session: RenderSession) -> int:
        if not hasattr(session, "to_load_message"):
            raise TypeError("session 必须提供 to_load_message()")
        payload = session.to_load_message(1)
        request_id = self.transport.send_request(payload)
        self._schedule_timeout(
            request_id,
            payload["epoch"],
            "load",
            self.worker_config.startup_timeout_ms,
        )
        return request_id

    def request_frame(
        self,
        *,
        epoch: int,
        index: int,
        surface: str,
        viewport: tuple[int, int],
        zoom_factor: float,
        pan: tuple[float, float],
        coalesce: bool = False,
    ) -> int | None:
        request_id = self.transport.request_frame(
            epoch=epoch,
            index=index,
            surface=surface,
            viewport=viewport,
            zoom_factor=zoom_factor,
            pan=pan,
            coalesce=coalesce,
        )
        if request_id is not None:
            self._schedule_timeout(
                request_id,
                epoch,
                "frame",
                self.worker_config.frame_timeout_ms,
            )
        return request_id

    def continue_wait(self, request_id: int) -> bool:
        timed_out = self._timed_out.pop(request_id, None)
        if timed_out is None or timed_out[0] != self.transport.generation:
            return False
        self._schedule_timeout(
            request_id,
            timed_out[1],
            "frame",
            self.worker_config.frame_timeout_ms,
        )
        return True

    def cancel_epoch(self, epoch: int) -> int:
        return self.transport.cancel_epoch(epoch)

    def unload(self) -> int:
        return self.transport.send_request({"type": "unload"})

    def shutdown(self) -> int:
        request_id = self.transport.send_request({"type": "shutdown"})
        self._schedule_timeout(
            request_id,
            None,
            "shutdown",
            self.worker_config.shutdown_timeout_ms,
        )
        return request_id

    def terminate_and_restart(self) -> None:
        if self._closed:
            return
        self._restart_requested = True
        self._clear_all_timeouts()
        if self.transport.alive:
            self.transport.terminate()
            self._schedule_timeout(
                self._restart_kill_key,
                None,
                "restart_kill",
                self.worker_config.shutdown_timeout_ms,
            )
        elif getattr(self.transport, "settling", False):
            # poll() 已观察到 child 退出不代表 reader/waiter 已关闭 PIPE、
            # 回收 slot 并发布最终代际事件；由该事件触发真正 restart。
            return
        else:
            self._restart_requested = False
            self._start_transport()

    @pyqtSlot(object)
    def _handle_transport_event(self, event: dict[str, Any]) -> None:
        if self._closed:
            return
        generation = event.get("generation")
        if type(generation) is not int or generation != self.transport.generation:
            return
        event_type = event.get("type")
        request_id = event.get("request_id")
        if event_type == "frame_submitted":
            epoch = event.get("epoch")
            if (
                type(request_id) is int
                and type(epoch) is int
                and request_id not in self._timeouts
            ):
                self._schedule_timeout(
                    request_id,
                    epoch,
                    "frame",
                    self.worker_config.frame_timeout_ms,
                    generation=generation,
                )
            self.frame_submitted.emit(
                request_id,
                event.get("epoch"),
                str(event.get("surface", "")),
                event.get("index"),
            )
            return
        shutdown_ack = (
            event_type == "ready" and event.get("operation") == "shutdown"
        )
        if type(request_id) is int and not shutdown_ack:
            self._clear_timeout(request_id)
        if event_type == "ready":
            operation = str(event.get("operation", ""))
            if operation == "hello":
                self.ready.emit()
            else:
                # cancel/unload/shutdown are request terminals too.  Preview
                # retirement needs their exact request_id before it may remove
                # the job file that the worker was still reading.
                self.operation_completed.emit(request_id, operation)
            return
        if event_type == "metadata":
            metadata = event.get("metadata")
            if not isinstance(metadata, SessionMetadata):
                self.request_failed.emit(
                    request_id,
                    "protocol.invalid_metadata",
                    "worker 返回了未解析的 metadata",
                )
                return
            self.metadata_ready.emit(request_id, metadata.epoch, metadata)
            return
        if event_type == "frame_ready":
            self.frame_ready.emit(
                request_id,
                event.get("epoch"),
                str(event.get("surface", "")),
                event.get("index"),
                event.get("frame"),
            )
            return
        if event_type == "frame_discarded":
            self.frame_discarded.emit(
                request_id,
                event.get("epoch"),
                str(event.get("surface", "")),
                event.get("index"),
            )
            return
        if event_type in {
            "requirement_error",
            "script_error",
            "contract_error",
            "request_error",
        }:
            code = event.get("code") or event.get("error_type") or event_type
            self.request_failed.emit(
                request_id, str(code), str(event.get("message", ""))
            )
            return
        if event_type == "log":
            self.log_received.emit(
                str(event.get("level", "info")),
                str(event.get("message", "")),
            )
            return
        if event_type == "protocol_error":
            if not self._restart_requested:
                self._clear_all_timeouts()
            if self._failure_reported_generation != generation:
                self._failure_reported_generation = generation
                self.worker_crashed.emit(
                    str(event.get("message", "worker protocol failed"))
                )
            # protocol_error 是 reader 发现损坏并请求 terminate 的预告；只有
            # waiter 的最终退出事件才能证明旧进程已经结束并允许启动新代。
            return
        if event_type in {"worker_crashed", "worker_exited"}:
            self._clear_all_timeouts()
            # 不论是 crash 还是已观察到的退出，旧进程均不再会读取退休 job。
            # 这与 worker_crashed 分开，避免有意重启时把 UI 误报为故障。
            self.worker_stopped.emit()
            cleanup_failure = (
                event.get("code") == STAGING_CLEANUP_ERROR_CODE
            )
            if (
                cleanup_failure
                and self._failure_reported_generation != generation
            ):
                self._failure_reported_generation = generation
                self.request_failed.emit(
                    0,
                    STAGING_CLEANUP_ERROR_CODE,
                    str(event.get("message", STAGING_CLEANUP_ERROR_CODE)),
                )
            if self._restart_requested:
                self._restart_requested = False
                try:
                    self._start_transport()
                except BaseException as error:
                    self._report_restart_failure(
                        error,
                        cleanup_terminal_generation=(
                            generation
                            if self._failure_reported_generation == generation
                            else None
                        ),
                    )
                return
            if (
                event_type == "worker_crashed"
                and self._failure_reported_generation != generation
            ):
                self._failure_reported_generation = generation
                self.worker_crashed.emit(str(event.get("message", "worker exited")))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._restart_requested = False
        self._clear_all_timeouts()
        self.transport.remove_listener(self._relay_transport_event)
        self.transport.close()


__all__ = ["VSWorkerClient"]
