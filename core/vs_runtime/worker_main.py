"""独立 VapourSynth worker 的协议命令循环。"""

from __future__ import annotations

import hashlib
import io
import math
import os
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from core.vs_runtime.protocol import (
    MessageDecoder,
    ProtocolError,
    encode_message,
    write_all,
)


WORKER_API_VERSION = 1
MAX_TRACEBACK_CHARS = 256 * 1024
MAX_ERROR_VALUE_CHARS = 4 * 1024
MAX_ERROR_ITEMS = 16
MAX_ERROR_NODES = 64
FATAL_RETIREMENT_EXIT = 70
FATAL_DRAIN_EXIT = 71
FATAL_TRANSPORT_EXIT = 72
FATAL_RUNTIME_CHANGED_EXIT = 73


class ProtocolWriter:
    """序列化对原始 binary stdout 的所有写入。"""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._lock = threading.Lock()

    def send(self, message: dict[str, Any]) -> None:
        self.send_encoded(encode_message(message))

    def send_encoded(self, payload: bytes) -> None:
        with self._lock:
            write_all(self._stream, payload)
            self._stream.flush()


class _SafeLogSink:
    """按最终 wire 编码大小拆分日志，且协议失败绝不反抛给脚本。"""

    def __init__(self, writer: ProtocolWriter) -> None:
        self._writer = writer

    @staticmethod
    def _largest_encodable_prefix(text: str) -> tuple[str, bytes]:
        low, high = 1, len(text)
        best_text, best_payload = "", b""
        while low <= high:
            midpoint = (low + high) // 2
            candidate = text[:midpoint]
            try:
                payload = encode_message(
                    {"type": "log", "level": "info", "message": candidate}
                )
            except ProtocolError as exc:
                if exc.code != "protocol.invalid_length":
                    raise
                high = midpoint - 1
            else:
                best_text, best_payload = candidate, payload
                low = midpoint + 1
        if not best_text:
            raise ProtocolError(
                "单个日志字符无法编码", code="protocol.invalid_length"
            )
        return best_text, best_payload

    def __call__(self, text: str) -> None:
        try:
            remaining = text
            while remaining:
                try:
                    payload = encode_message(
                        {"type": "log", "level": "info", "message": remaining}
                    )
                    consumed = remaining
                except ProtocolError as exc:
                    if exc.code != "protocol.invalid_length":
                        return
                    consumed, payload = self._largest_encodable_prefix(remaining)
                self._writer.send_encoded(payload)
                remaining = remaining[len(consumed) :]
        except BaseException:
            # 用户 print() 绝不能因日志协议或已关闭的 pipe 改变脚本结果。
            return


def _install_structured_stdout(writer: ProtocolWriter) -> io.TextIOBase:
    # executor 在结构化 stdout 安装路径内才导入；入口脚本更早已把 stdout
    # 临时指向 stderr，避免任何 import-time 输出污染协议。
    from resources.vapoursynth.python.assetmaker_vs.executor import PythonLogWriter

    class _WorkerPythonLogWriter(PythonLogWriter):
        def write(self, text: str) -> int:
            if not isinstance(text, str):
                try:
                    text = str(text)
                except BaseException:
                    text = "<unprintable>"
            original_length = len(text)
            safe_text = text.encode("utf-8", errors="replace").decode("utf-8")
            try:
                super().write(safe_text)
            except BaseException:
                # Logging is observational only: neither malformed Unicode nor
                # a closed protocol sink may change user-script execution.
                return original_length
            return original_length

        def flush(self) -> None:
            try:
                super().flush()
            except BaseException:
                return

    log_writer = _WorkerPythonLogWriter(_SafeLogSink(writer))
    sys.stdout = log_writer
    sys.__stdout__ = log_writer
    return log_writer


class _FatalWorkerExit(RuntimeError):
    def __init__(self, exit_code: int) -> None:
        self.exit_code = exit_code
        super().__init__(f"worker 必须退出: {exit_code}")


@dataclass
class _LoadedGraph:
    epoch: int
    job: dict[str, Any]
    header: dict[str, Any]
    graph: Any
    outputs: Any
    snapshot: Any


@dataclass
class _FrameRequest:
    request_id: int
    epoch: int
    index: int
    surface: str
    slot: Any
    display_clip: Any


def _strict_positive_int(value: Any, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise ProtocolError(
            f"{field} 必须是严格正整数", code="protocol.invalid_request"
        )
    return value


def _strict_non_negative_int(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ProtocolError(
            f"{field} 必须是非负整数", code="protocol.invalid_request"
        )
    return value


def _strict_finite_number(value: Any, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ProtocolError(
            f"{field} 必须是有限数值", code="protocol.invalid_request"
        )
    return float(value)


def _validate_request_fields(message: dict[str, Any], expected: set[str]) -> int:
    if set(message) != expected:
        raise ProtocolError(
            "请求字段不完整或包含未知字段", code="protocol.request_shape"
        )
    return _strict_positive_int(message["request_id"], "request_id")


def _safe_text(
    value: Any,
    fallback: str,
    *,
    limit: int = MAX_TRACEBACK_CHARS,
) -> str:
    try:
        text = str(value)
    except BaseException:
        text = fallback
    # Lone UTF-16 surrogates can exist in Python strings but cannot be emitted
    # on the strict UTF-8 protocol. Preserve all valid Unicode and replace only
    # those invalid code points before bounding the payload.
    return text[:limit].encode("utf-8", errors="replace").decode("utf-8")


def _traceback_text(error: BaseException) -> str:
    try:
        text = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
    except BaseException:
        text = f"{type(error).__name__}: traceback unavailable"
    return _safe_text(
        text[-MAX_TRACEBACK_CHARS:],
        f"{type(error).__name__}: traceback unavailable",
    )


def _wire_safe_value(
    value: Any,
    *,
    remaining: list[int] | None = None,
    seen: set[int] | None = None,
) -> Any:
    """把异常携带的任意机器字段收敛为有界、严格 JSON/UTF-8 值。"""
    budget = [MAX_ERROR_NODES] if remaining is None else remaining
    visited = set() if seen is None else seen
    budget[0] -= 1
    if budget[0] < 0:
        return {
            "type": _safe_text(type(value).__name__, "object", limit=256),
            "truncated": True,
        }
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if value.bit_length() <= 256:
            return value
        return {
            "type": "int",
            "bits": value.bit_length(),
            "truncated": True,
        }
    if type(value) is float:
        return value if math.isfinite(value) else _safe_text(repr(value), "float")
    if type(value) is str:
        return _safe_text(value, "", limit=MAX_ERROR_VALUE_CHARS)
    if type(value) in (bytes, bytearray):
        prefix = bytes(value[:256])
        return {
            "type": _safe_text(type(value).__name__, "bytes", limit=256),
            "length": len(value),
            "hex": prefix.hex(),
            "truncated": len(value) > len(prefix),
        }

    object_id = id(value)
    if object_id in visited:
        return {
            "type": _safe_text(type(value).__name__, "object", limit=256),
            "cycle": True,
        }
    if type(value) in (list, tuple):
        visited.add(object_id)
        try:
            result = [
                _wire_safe_value(item, remaining=budget, seen=visited)
                for item in value[:MAX_ERROR_ITEMS]
            ]
            if len(value) > MAX_ERROR_ITEMS:
                result.append({"truncated": True})
            return result
        finally:
            visited.remove(object_id)
    if type(value) is dict:
        visited.add(object_id)
        try:
            result: dict[str, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                if index == MAX_ERROR_ITEMS:
                    result["__assetmaker_truncated__"] = True
                    break
                safe_key = _safe_text(key, "key", limit=MAX_ERROR_VALUE_CHARS)
                result[safe_key] = _wire_safe_value(
                    item, remaining=budget, seen=visited
                )
            return result
        except BaseException as error:
            return {
                "type": _safe_text(
                    type(value).__name__, "mapping", limit=256
                ),
                "error": _safe_text(
                    type(error).__name__, "mapping", limit=256
                ),
            }
        finally:
            visited.remove(object_id)
    try:
        representation = repr(value)
    except BaseException:
        representation = f"<{type(value).__name__}>"
    return {
        "type": _safe_text(type(value).__name__, "object", limit=256),
        "repr": _safe_text(
            representation, "<unavailable>", limit=MAX_ERROR_VALUE_CHARS
        ),
    }


def _machine_fields(error: BaseException) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in ("code", "field", "path", "expected", "actual", "hint"):
        try:
            value = getattr(error, field, None)
        except BaseException:
            value = None
        if field in {"code", "field", "path", "hint"}:
            result[field] = (
                None
                if value is None
                else _safe_text(value, field, limit=MAX_ERROR_VALUE_CHARS)
            )
        else:
            result[field] = _wire_safe_value(value)
    return result


def _has_retirement_failure(error: BaseException) -> bool:
    seen: set[int] = set()
    pending: list[BaseException] = [error]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        try:
            code = getattr(current, "code", None)
        except BaseException:
            code = None
        if type(code) is str and code == "executor.retirement_failed":
            return True
        try:
            notes = getattr(current, "__notes__", ())
        except BaseException:
            notes = ()
        if type(notes) in (list, tuple):
            for note in notes:
                if (
                    type(note) is str
                    and (
                        "[executor.retirement_failed]" in note
                        or note.startswith("脚本清理阶段另有异常：")
                    )
                ):
                    return True
        for attribute in ("__cause__", "__context__"):
            try:
                linked = getattr(current, attribute, None)
            except BaseException:
                continue
            if isinstance(linked, BaseException):
                pending.append(linked)
    return False


def _error_message(
    error_type: str,
    request_id: int,
    error: BaseException,
    *,
    epoch: int | None,
    slot: Any | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": error_type,
        "request_id": request_id,
        "epoch": epoch,
        "error_type": _safe_text(type(error).__name__, "Exception", limit=256),
        "message": _safe_text(error, type(error).__name__),
        "traceback": _traceback_text(error),
        **_machine_fields(error),
    }
    if slot is not None:
        payload["slot_name"] = slot.name
        payload["slot_generation"] = slot.generation
    return payload


def _node_metadata(
    clip: Any, *, colour: dict[str, str] | None
) -> dict[str, Any]:
    format_ = getattr(clip, "format", None)
    name = getattr(format_, "name", None)
    if type(name) is not str or not name:
        raise ProtocolError(
            "输出节点格式不可识别", code="protocol.invalid_metadata"
        )
    return {
        "width": int(clip.width),
        "height": int(clip.height),
        "num_frames": int(clip.num_frames),
        "fps_num": int(clip.fps_num),
        "fps_den": int(clip.fps_den),
        "pixel_format": name,
        "matrix": None if colour is None else colour["matrix"],
        "transfer": None if colour is None else colour["transfer"],
        "primaries": None if colour is None else colour["primaries"],
        "range": None if colour is None else colour["range"],
    }


class WorkerServer:
    """只在 child 内存在的 VS 状态机。"""

    MAX_INFLIGHT_FRAMES = 3

    def __init__(
        self,
        *,
        writer: ProtocolWriter,
        app_dir: Path,
        self_test: bool,
        generation_staging: Any | None,
    ) -> None:
        # 入口已先把 Python stdout 挪到 stderr；这里在导入受指纹保护的
        # helper 前建立基线，随后安装结构化 logger 并立即复核。
        from core.vs_runtime.snapshot import (
            RuntimeSnapshot,
            SNAPSHOT_ENV_KEYS,
        )

        self.writer = writer
        self.app_dir = app_dir
        self.self_test = self_test
        try:
            if SNAPSHOT_ENV_KEYS.intersection(os.environ):
                snapshot = RuntimeSnapshot.from_environment(app_dir, os.environ)
            else:
                # 兼容直接 WorkerProcess/self-test：只有完全没有快照环境才
                # 允许在 child 启动边界读取磁盘。GUI 总是传入完整冻结环境。
                snapshot = RuntimeSnapshot.resolve(app_dir)
        except (ValueError, RuntimeError, OSError) as error:
            raise ProtocolError(
                f"worker runtime 环境无效: {error}",
                code="worker.runtime_environment",
            ) from error
        self.runtime = snapshot.runtime
        self.runtime_fingerprint = snapshot.fingerprint
        if generation_staging is None:
            from core.vs_runtime.session import GenerationStagingRoot

            generation_staging = GenerationStagingRoot.from_environment(
                os.environ
            )
        self.generation_staging = generation_staging
        self._vs: Any | None = None
        self._loaded: _LoadedGraph | None = None
        self._cancelled_epochs: set[int] = set()
        self._frames: dict[int, _FrameRequest] = {}
        self._condition = threading.Condition(threading.RLock())

    def _assert_runtime_unchanged(self, expected_fingerprint: str) -> None:
        from core.vs_runtime.vs_loader import compute_runtime_fingerprint

        try:
            actual = compute_runtime_fingerprint(self.app_dir, self.runtime)
        except BaseException as error:
            raise ProtocolError(
                "worker runtime 无法重新验证，必须启动新进程",
                code="worker.runtime_changed",
            ) from error
        if (
            actual != self.runtime_fingerprint
            or expected_fingerprint != self.runtime_fingerprint
        ):
            raise ProtocolError(
                "worker runtime 已变化，必须启动新进程",
                code="worker.runtime_changed",
            )

    def _ensure_vs(self) -> Any:
        if self._vs is None:
            from core.vs_runtime.vs_loader import load_vapoursynth

            self._vs = load_vapoursynth(self.app_dir, self.runtime)
        return self._vs

    def _send_ready(self, request_id: int, operation: str) -> None:
        self.writer.send(
            {
                "type": "ready",
                "request_id": request_id,
                "api_version": WORKER_API_VERSION,
                "operation": operation,
            }
        )

    def _send_error(
        self,
        kind: str,
        request_id: int,
        error: BaseException,
        *,
        epoch: int | None,
        slot: Any | None = None,
    ) -> None:
        try:
            self.writer.send(
                _error_message(
                    kind, request_id, error, epoch=epoch, slot=slot
                )
            )
            return
        except ProtocolError:
            fallback: dict[str, Any] = {
                "type": "request_error",
                "request_id": request_id,
                "epoch": epoch,
                "error_type": "ProtocolError",
                "message": "worker error response encoding failed",
                "traceback": "",
                "code": "worker.error_encoding",
                "field": None,
                "path": None,
                "expected": None,
                "actual": None,
                "hint": None,
            }
            if slot is not None:
                fallback["slot_name"] = _safe_text(
                    slot.name, "invalid-slot", limit=256
                )
                fallback["slot_generation"] = slot.generation
            try:
                self.writer.send(fallback)
                return
            except BaseException:
                pass
        except BaseException:
            pass
        # A broken protocol pipe cannot leave the host waiting forever for a
        # terminal response. Callback threads cannot unwind the main read loop,
        # so the only reliable transport-fatal action is immediate child exit.
        os._exit(FATAL_TRANSPORT_EXIT)

    def _prepare_load(
        self, message: dict[str, Any]
    ) -> tuple[int, dict[str, Any], dict[str, Any], Path, str, Any]:
        from core.vs_runtime.session import (
            ScriptBundleSnapshot,
            compute_script_bundle_hash,
        )

        request_id = _validate_request_fields(
            message,
            {
                "type",
                "request_id",
                "api_version",
                "track",
                "epoch",
                "script_path",
                "job_path",
                "job_sha256",
                "bundle_hash",
                "runtime_fingerprint",
                "mode",
            },
        )
        api_version = message["api_version"]
        if type(api_version) is not int or api_version != WORKER_API_VERSION:
            raise ProtocolError(
                "load.api_version 不受支持", code="invocation.api"
            )
        epoch = _strict_positive_int(message["epoch"], "epoch")
        track = message["track"]
        if track not in ("loop", "intro"):
            raise ProtocolError("track 必须是 loop/intro", code="job.track")
        mode = message["mode"]
        if mode not in ("compatible", "raw"):
            raise ProtocolError(
                "mode 必须是 compatible/raw", code="invocation.mode"
            )
        for field in ("script_path", "job_path"):
            value = message[field]
            if type(value) is not str or not Path(value).is_absolute():
                raise ProtocolError(
                    f"{field} 必须是绝对路径", code="protocol.invalid_request"
                )
        for field in ("job_sha256", "bundle_hash", "runtime_fingerprint"):
            value = message[field]
            if (
                type(value) is not str
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ProtocolError(
                    f"{field} 必须是小写 SHA-256",
                    code="protocol.invalid_request",
                )
        snapshot = ScriptBundleSnapshot.create(
            message["script_path"],
            message["job_path"],
            self.generation_staging,
        )
        try:
            self._assert_snapshot_job_identity(snapshot, message["job_sha256"])
            actual_bundle = compute_script_bundle_hash(snapshot.script_path)
            if actual_bundle != message["bundle_hash"]:
                raise ProtocolError(
                    "脚本 bundle 在执行前发生变化",
                    code="worker.bundle_mismatch",
                )
            self._assert_runtime_unchanged(message["runtime_fingerprint"])
            from resources.vapoursynth.python.assetmaker_vs.job_api import (
                load_job,
            )
            from resources.vapoursynth.python.assetmaker_vs.script_header import (
                parse_script_header,
                validate_invocation,
            )

            # helper import 可能跨越文件更新；在读取 job/header 前再次核验，
            # 后续还会在 VS 与全部执行 helper 就绪后做最终核验。
            self._assert_runtime_unchanged(message["runtime_fingerprint"])

            self._assert_snapshot_job_identity(snapshot, message["job_sha256"])
            job = load_job(snapshot.job_path)
            self._assert_snapshot_job_identity(snapshot, message["job_sha256"])
            header = parse_script_header(snapshot.script_path)
            comparisons = (
                ("job.api_version", job["api_version"], api_version),
                ("job.track", job["track"], track),
                ("job.epoch", job["epoch"], epoch),
            )
            for field, actual, expected in comparisons:
                if type(actual) is not type(expected) or actual != expected:
                    raise ProtocolError(
                        f"{field} 与 load message 不一致",
                        code="worker.identity_mismatch",
                    )
            validate_invocation(header, api_version=api_version, mode=mode)
            return (
                request_id,
                job,
                header,
                snapshot.script_path,
                str(snapshot.job_path),
                snapshot,
            )
        except BaseException:
            snapshot.close()
            raise

    @staticmethod
    def _assert_snapshot_job_identity(snapshot: Any, expected_sha256: str) -> None:
        """拒绝同路径替换；worker 只执行 load message 冻结的 job 字节。"""
        actual_sha256 = hashlib.sha256(snapshot.job_path.read_bytes()).hexdigest()
        if actual_sha256 != expected_sha256:
            raise ProtocolError(
                "冻结 job 内容在执行前发生变化",
                code="worker.job_mismatch",
            )

    @staticmethod
    def _close_snapshot(snapshot: Any, error: BaseException) -> bool:
        try:
            snapshot.close()
        except BaseException as cleanup_error:
            try:
                error.add_note(
                    "执行快照清理阶段另有异常："
                    f"[{getattr(cleanup_error, 'code', type(cleanup_error).__name__)}]"
                )
            except BaseException:
                pass
            return False
        return True

    def _retire_current(
        self, request_id: int, *, response_epoch: int | None = None
    ) -> None:
        with self._condition:
            loaded = self._loaded
            if loaded is None:
                return
            deadline = (
                time.monotonic()
                + self.runtime.worker.shutdown_timeout_ms / 1000
            )
            while any(
                frame.epoch == loaded.epoch for frame in self._frames.values()
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    error = ProtocolError(
                        "旧图的异步帧请求未能在退休期限内排空",
                        code="worker.drain_timeout",
                    )
                    self._send_error(
                        "request_error",
                        request_id,
                        error,
                        epoch=response_epoch or loaded.epoch,
                    )
                    raise _FatalWorkerExit(FATAL_DRAIN_EXIT)
                self._condition.wait(remaining)
            self._loaded = None
            self._cancelled_epochs.discard(loaded.epoch)
        failure: BaseException | None = None
        try:
            loaded.graph.close()
        except BaseException as error:
            failure = error
        try:
            loaded.snapshot.close()
        except BaseException as error:
            if failure is None:
                failure = error
            else:
                try:
                    failure.add_note(
                        "执行快照清理阶段另有异常："
                        f"[{getattr(error, 'code', type(error).__name__)}]"
                    )
                except BaseException:
                    pass
        if failure is not None:
            self._send_error(
                "request_error",
                request_id,
                failure,
                epoch=response_epoch or loaded.epoch,
            )
            raise _FatalWorkerExit(FATAL_RETIREMENT_EXIT) from failure

    def _handle_load(self, message: dict[str, Any]) -> None:
        epoch = message.get("epoch") if type(message.get("epoch")) is int else None
        request_id = _strict_positive_int(message.get("request_id"), "request_id")
        try:
            (
                request_id,
                job,
                header,
                script_path,
                job_path,
                snapshot,
            ) = self._prepare_load(message)
        except BaseException as error:
            self._send_error("request_error", request_id, error, epoch=epoch)
            if (
                isinstance(error, ProtocolError)
                and error.code == "worker.runtime_changed"
            ):
                raise _FatalWorkerExit(FATAL_RUNTIME_CHANGED_EXIT) from error
            return
        try:
            self._retire_current(request_id, response_epoch=job["epoch"])
        except BaseException as error:
            self._close_snapshot(snapshot, error)
            raise
        # C2: worker 与 VSPipe 都执行 canonical user script；因此不能再靠
        # 私有代码副本抵御 retirement wait 中的改写。紧挨执行点重算整个
        # script bundle，发现变化即拒绝这次 load，避免 __file__/资源根分叉。
        try:
            from core.vs_runtime.session import compute_script_bundle_hash

            if (
                compute_script_bundle_hash(script_path)
                != message["bundle_hash"]
            ):
                raise ProtocolError(
                    "脚本 bundle 在执行前发生变化",
                    code="worker.bundle_mismatch",
                )
        except BaseException as error:
            self._close_snapshot(snapshot, error)
            self._send_error(
                "request_error", request_id, error, epoch=job["epoch"]
            )
            return
        try:
            vs = self._ensure_vs()
        except BaseException as error:
            self._close_snapshot(snapshot, error)
            raise
        try:
            from resources.vapoursynth.python.assetmaker_vs.contract import (
                OutputContractError,
                RequirementError,
                decode_output_contract_error,
                validate_outputs,
                verify_required_callables,
            )
            from resources.vapoursynth.python.assetmaker_vs.executor import (
                execute_user_script,
            )
        except BaseException as error:
            self._close_snapshot(snapshot, error)
            raise

        try:
            # _retire_current() 可能等待旧图；_ensure_vs() 会加载受指纹保护
            # 的 pyd/DLL/plugin。用户代码执行前必须重新确认三方身份一致。
            self._assert_runtime_unchanged(message["runtime_fingerprint"])
            self._assert_snapshot_job_identity(snapshot, message["job_sha256"])
            from core.vs_runtime.vs_loader import restore_vapoursynth_resources

            restore_vapoursynth_resources(vs, self.runtime)
        except ProtocolError as error:
            self._close_snapshot(snapshot, error)
            self._send_error(
                "request_error", request_id, error, epoch=job["epoch"]
            )
            raise _FatalWorkerExit(FATAL_RUNTIME_CHANGED_EXIT) from error
        except BaseException as error:
            clean = self._close_snapshot(snapshot, error)
            self._send_error(
                "request_error", request_id, error, epoch=job["epoch"]
            )
            if not clean:
                raise _FatalWorkerExit(FATAL_RETIREMENT_EXIT) from error
            return

        try:
            verify_required_callables(vs.core, header["requires"])
        except RequirementError as error:
            clean = self._close_snapshot(snapshot, error)
            self._send_error(
                "requirement_error", request_id, error, epoch=job["epoch"]
            )
            if not clean:
                raise _FatalWorkerExit(FATAL_RETIREMENT_EXIT) from error
            return
        try:
            graph = execute_user_script(
                script_path=script_path,
                job_path=job_path,
                api_version=message["api_version"],
                mode=message["mode"],
                python_module_dirs=self.runtime.plugins.python_module_dirs,
            )
        except BaseException as error:
            clean = self._close_snapshot(snapshot, error)
            self._send_error(
                "script_error", request_id, error, epoch=job["epoch"]
            )
            if not clean or _has_retirement_failure(error):
                raise _FatalWorkerExit(FATAL_RETIREMENT_EXIT) from error
            return
        try:
            self._assert_snapshot_job_identity(snapshot, message["job_sha256"])
        except BaseException as error:
            try:
                graph.close()
            except BaseException as cleanup_error:
                try:
                    error.add_note(
                        "脚本清理阶段另有异常："
                        f"[{getattr(cleanup_error, 'code', type(cleanup_error).__name__)}]"
                    )
                except BaseException:
                    pass
            clean = self._close_snapshot(snapshot, error)
            self._send_error(
                "request_error", request_id, error, epoch=job["epoch"]
            )
            if not clean:
                raise _FatalWorkerExit(FATAL_RETIREMENT_EXIT) from error
            return
        try:
            outputs = validate_outputs(vs, job, header)
        except BaseException as error:
            decoded = (
                error
                if isinstance(error, OutputContractError)
                else decode_output_contract_error(error)
            )
            response_error = decoded or error
            response_type = (
                "contract_error" if decoded is not None else "script_error"
            )
            fatal = False
            try:
                graph.close()
            except BaseException as cleanup_error:
                try:
                    response_error.add_note(
                        "脚本清理阶段另有异常："
                        f"[{getattr(cleanup_error, 'code', type(cleanup_error).__name__)}]"
                    )
                except BaseException:
                    pass
                fatal = _has_retirement_failure(cleanup_error)
            if not self._close_snapshot(snapshot, response_error):
                fatal = True
            self._send_error(
                response_type, request_id, response_error, epoch=job["epoch"]
            )
            if fatal:
                raise _FatalWorkerExit(FATAL_RETIREMENT_EXIT) from response_error
            return
        loaded = _LoadedGraph(
            epoch=job["epoch"],
            job=job,
            header=header,
            graph=graph,
            outputs=outputs,
            snapshot=snapshot,
        )
        with self._condition:
            self._loaded = loaded
            self._cancelled_epochs.discard(loaded.epoch)
        output_colour = {
            field: job["output"][field]
            for field in ("matrix", "transfer", "primaries", "range")
        }
        metadata = {
            "epoch": loaded.epoch,
            "mode": header["mode"],
            "capabilities": list(header["capabilities"]),
            "output0": _node_metadata(outputs.guarded_clip, colour=output_colour),
            "editor": (
                None
                if outputs.editor_clip is None
                else _node_metadata(outputs.editor_clip, colour=None)
            ),
        }
        self.writer.send(
            {
                "type": "metadata",
                "request_id": request_id,
                "epoch": loaded.epoch,
                "metadata": metadata,
            }
        )

    def _frame_request_identity(
        self, message: dict[str, Any]
    ) -> tuple[int, int, Any]:
        from core.vs_runtime.shared_frame import FrameSlotDescriptor

        request_id = _validate_request_fields(
            message,
            {
                "type",
                "request_id",
                "epoch",
                "index",
                "surface",
                "slot",
                "display",
            },
        )
        epoch = _strict_positive_int(message["epoch"], "epoch")
        slot = FrameSlotDescriptor.from_wire(message["slot"])
        return request_id, epoch, slot

    def _frame_request(
        self,
        message: dict[str, Any],
        *,
        request_id: int,
        epoch: int,
        slot: Any,
    ) -> tuple[int, Any, Any]:
        index = _strict_non_negative_int(message["index"], "index")
        surface = message["surface"]
        if surface not in ("final", "editor"):
            raise ProtocolError(
                "surface 必须是 final/editor", code="protocol.invalid_request"
            )
        display = message["display"]
        if not isinstance(display, dict) or set(display) != {
            "viewport",
            "zoom_factor",
            "pan",
        }:
            raise ProtocolError("display 字段非法", code="protocol.request_shape")
        viewport = display["viewport"]
        if not isinstance(viewport, list) or len(viewport) != 2:
            raise ProtocolError(
                "display.viewport 字段非法", code="protocol.invalid_request"
            )
        viewport_tuple = (
            _strict_positive_int(viewport[0], "display.viewport.width"),
            _strict_positive_int(viewport[1], "display.viewport.height"),
        )
        zoom_factor = _strict_finite_number(
            display["zoom_factor"], "display.zoom_factor"
        )
        pan = display["pan"]
        if not isinstance(pan, list) or len(pan) != 2:
            raise ProtocolError(
                "display.pan 字段非法", code="protocol.invalid_request"
            )
        pan_tuple = (
            _strict_finite_number(pan[0], "display.pan.x"),
            _strict_finite_number(pan[1], "display.pan.y"),
        )
        return request_id, slot, {
            "epoch": epoch,
            "index": index,
            "surface": surface,
            "viewport": viewport_tuple,
            "zoom_factor": zoom_factor,
            "pan": pan_tuple,
        }

    def _send_frame_terminal(
        self,
        context: _FrameRequest,
        response_type: str,
        **fields: Any,
    ) -> None:
        self._send_frame_terminal_identity(
            request_id=context.request_id,
            epoch=context.epoch,
            index=context.index,
            surface=context.surface,
            slot=context.slot.descriptor,
            response_type=response_type,
            **fields,
        )

    def _send_frame_terminal_identity(
        self,
        *,
        request_id: int,
        epoch: int,
        index: int,
        surface: str,
        slot: Any,
        response_type: str,
        **fields: Any,
    ) -> None:
        try:
            self.writer.send(
                {
                    "type": response_type,
                    "request_id": request_id,
                    "epoch": epoch,
                    "index": index,
                    "surface": surface,
                    "slot_name": slot.name,
                    "slot_generation": slot.generation,
                    **fields,
                }
            )
        except BaseException:
            # A frame terminal may have been partially written. Never attempt a
            # second terminal on the same stream; make process death the host's
            # unambiguous reclamation signal.
            os._exit(FATAL_TRANSPORT_EXIT)

    def _finish_frame(self, context: _FrameRequest, future: Any) -> None:
        frame = None
        frame_error: BaseException | None = None
        try:
            try:
                frame = future.result()
            except BaseException as error:
                frame_error = error
            with self._condition:
                cancelled = (
                    context.epoch in self._cancelled_epochs
                    or self._loaded is None
                    or self._loaded.epoch != context.epoch
                )
                if cancelled:
                    self._send_frame_terminal(
                        context, "frame_discarded", reason="epoch_cancelled"
                    )
                elif frame_error is None:
                    assert frame is not None
                    try:
                        byte_count = context.slot.write_vs_rgb(frame)
                    except BaseException as error:
                        frame_error = error
                    else:
                        self._send_frame_terminal(
                            context,
                            "frame_ready",
                            width=int(frame.width),
                            height=int(frame.height),
                            byte_count=byte_count,
                        )
                if not cancelled and frame_error is not None:
                    from resources.vapoursynth.python.assetmaker_vs.contract import (
                        decode_output_contract_error,
                    )

                    try:
                        decoded = decode_output_contract_error(frame_error)
                    except BaseException:
                        decoded = None
                    terminal_error = decoded if decoded is not None else frame_error
                    self._send_error(
                        "request_error",
                        context.request_id,
                        terminal_error,
                        epoch=context.epoch,
                        slot=context.slot.descriptor,
                    )
        finally:
            if frame is not None:
                try:
                    frame.close()
                except BaseException:
                    pass
            try:
                context.slot.close()
            finally:
                with self._condition:
                    self._frames.pop(context.request_id, None)
                    self._condition.notify_all()

    def _handle_frame(self, message: dict[str, Any]) -> None:
        from core.vs_runtime.shared_frame import FrameSlot
        from resources.vapoursynth.python.assetmaker_vs.display import to_display_clip

        request_id = _strict_positive_int(message.get("request_id"), "request_id")
        slot_descriptor = None
        epoch = message.get("epoch") if type(message.get("epoch")) is int else None
        try:
            request_id, epoch, slot_descriptor = self._frame_request_identity(
                message
            )
            request_id, slot_descriptor, fields = self._frame_request(
                message,
                request_id=request_id,
                epoch=epoch,
                slot=slot_descriptor,
            )
            with self._condition:
                loaded = self._loaded
                if fields["epoch"] in self._cancelled_epochs:
                    self._send_frame_terminal_identity(
                        request_id=request_id,
                        epoch=fields["epoch"],
                        index=fields["index"],
                        surface=fields["surface"],
                        slot=slot_descriptor,
                        response_type="frame_discarded",
                        reason="epoch_cancelled",
                    )
                    return
                if loaded is None or fields["epoch"] != loaded.epoch:
                    raise ProtocolError(
                        "frame epoch 不再活动", code="worker.stale_epoch"
                    )
                if len(self._frames) >= self.MAX_INFLIGHT_FRAMES:
                    raise ProtocolError(
                        "worker 已达到 3 个 in-flight frame 上限",
                        code="worker.inflight_limit",
                    )
                source = (
                    loaded.outputs.guarded_clip
                    if fields["surface"] == "final"
                    else loaded.outputs.editor_clip
                )
                if source is None:
                    raise ProtocolError(
                        "当前脚本没有 editor output",
                        code="worker.editor_unavailable",
                    )
            slot = FrameSlot.open(slot_descriptor)
            try:
                display_clip = to_display_clip(
                    source,
                    viewport=fields["viewport"],
                    zoom_factor=fields["zoom_factor"],
                    pan=fields["pan"],
                )
                if fields["index"] >= int(display_clip.num_frames):
                    raise ProtocolError(
                        "frame index 超出范围", code="worker.frame_index"
                    )
                context = _FrameRequest(
                    request_id=request_id,
                    epoch=fields["epoch"],
                    index=fields["index"],
                    surface=fields["surface"],
                    slot=slot,
                    display_clip=display_clip,
                )
                with self._condition:
                    self._frames[request_id] = context
                future = display_clip.get_frame_async(fields["index"])
                future.add_done_callback(
                    lambda completed, request=context: self._finish_frame(
                        request, completed
                    )
                )
            except BaseException:
                with self._condition:
                    self._frames.pop(request_id, None)
                    self._condition.notify_all()
                slot.close()
                raise
        except BaseException as error:
            if slot_descriptor is None:
                self._send_error("request_error", request_id, error, epoch=epoch)
            else:
                self._send_error(
                    "request_error",
                    request_id,
                    error,
                    epoch=epoch,
                    slot=slot_descriptor,
                )

    @staticmethod
    def _plane_digests(frame: Any) -> dict[str, str]:
        import numpy as np

        labels = ("Y", "U", "V")
        format_ = frame.format
        if format_.num_planes != 3 or format_.bits_per_sample != 8:
            raise ProtocolError(
                "诊断 digest 只支持三平面 8-bit output0",
                code="worker.digest_format",
            )
        result: dict[str, str] = {}
        for plane, label in enumerate(labels):
            shift_w = 0 if plane == 0 else int(format_.subsampling_w)
            shift_h = 0 if plane == 0 else int(format_.subsampling_h)
            width = (int(frame.width) + (1 << shift_w) - 1) >> shift_w
            height = (int(frame.height) + (1 << shift_h) - 1) >> shift_h
            array = np.asarray(frame[plane])
            if array.ndim != 2 or array.shape[0] < height or array.shape[1] < width:
                raise ProtocolError(
                    "VS 平面视图短于有效区域", code="worker.digest_shape"
                )
            digest = hashlib.sha256()
            for row in range(height):
                # np.asarray 保留 R73 的真实 stride；逐有效行切片并转为
                # packed bytes，明确排除行尾 padding。
                digest.update(array[row, :width].tobytes(order="C"))
            result[label] = digest.hexdigest()
        return result

    def _handle_plane_digest(self, message: dict[str, Any]) -> None:
        request_id = _strict_positive_int(message.get("request_id"), "request_id")
        epoch = message.get("epoch") if type(message.get("epoch")) is int else None
        try:
            request_id = _validate_request_fields(
                message,
                {"type", "request_id", "epoch", "index", "surface"},
            )
            epoch = _strict_positive_int(message["epoch"], "epoch")
            index = _strict_non_negative_int(message["index"], "index")
            if message["surface"] != "final":
                raise ProtocolError(
                    "plane digest 仅支持 final output0",
                    code="worker.digest_surface",
                )
            with self._condition:
                loaded = self._loaded
                if loaded is None or loaded.epoch != epoch:
                    raise ProtocolError(
                        "digest epoch 不再活动", code="worker.stale_epoch"
                    )
                clip = loaded.outputs.guarded_clip
            if index >= int(clip.num_frames):
                raise ProtocolError(
                    "frame index 超出范围", code="worker.frame_index"
                )
            frame = clip.get_frame(index)
            try:
                digests = self._plane_digests(frame)
            finally:
                frame.close()
            self.writer.send(
                {
                    "type": "plane_digest",
                    "request_id": request_id,
                    "epoch": epoch,
                    "index": index,
                    "surface": "final",
                    "digests": digests,
                }
            )
        except BaseException as error:
            self._send_error("request_error", request_id, error, epoch=epoch)

    def handle(self, message: dict[str, Any]) -> int | None:
        message_type = message["type"]
        if message_type == "hello":
            request_id = _validate_request_fields(
                message, {"type", "request_id", "api_version"}
            )
            if (
                type(message["api_version"]) is not int
                or message["api_version"] != WORKER_API_VERSION
            ):
                error = ProtocolError(
                    "不支持的 worker API", code="worker.api_version"
                )
                self._send_error("request_error", request_id, error, epoch=None)
                return None
            if self.self_test:
                print("中文自测 " + "x" * 70_000)
            self._send_ready(request_id, "hello")
            return None
        if message_type == "load":
            self._handle_load(message)
            return None
        if message_type == "request_frame":
            self._handle_frame(message)
            return None
        if message_type == "request_plane_digest":
            self._handle_plane_digest(message)
            return None
        if message_type == "cancel_epoch":
            request_id = _validate_request_fields(
                message, {"type", "request_id", "epoch"}
            )
            epoch = _strict_positive_int(message["epoch"], "epoch")
            with self._condition:
                self._cancelled_epochs.add(epoch)
                # ACK 是 cancel 的线性化点。frame callback 的 mmap commit 与
                # terminal send 使用同一把锁，因此 ACK 之后未终态请求绝不
                # 可能再发送 frame_ready。
                self._send_ready(request_id, "cancel_epoch")
            return None
        if message_type == "unload":
            request_id = _validate_request_fields(
                message, {"type", "request_id"}
            )
            self._retire_current(request_id)
            self._send_ready(request_id, "unload")
            return None
        if message_type == "shutdown":
            request_id = _validate_request_fields(
                message, {"type", "request_id"}
            )
            self._retire_current(request_id)
            self._send_ready(request_id, "shutdown")
            return 0
        request_id = _strict_positive_int(message.get("request_id"), "request_id")
        error = ProtocolError(
            f"不支持请求 {message_type!r}", code="protocol.request_type"
        )
        self._send_error("request_error", request_id, error, epoch=None)
        return None

    def close_for_eof(self) -> int:
        try:
            self._retire_current(1)
        except _FatalWorkerExit as fatal:
            return fatal.exit_code
        return 0


def run_worker(
    *,
    protocol_stream: BinaryIO,
    input_stream: BinaryIO,
    app_dir: Path,
    self_test: bool,
    generation_staging: Any | None,
) -> int:
    writer = ProtocolWriter(protocol_stream)
    server = WorkerServer(
        writer=writer,
        app_dir=app_dir,
        self_test=self_test,
        generation_staging=generation_staging,
    )
    log_writer = _install_structured_stdout(writer)
    try:
        server._assert_runtime_unchanged(server.runtime_fingerprint)
    except ProtocolError as error:
        _SafeLogSink(writer)(_safe_text(error, "worker runtime changed"))
        log_writer.flush()
        return FATAL_RUNTIME_CHANGED_EXIT
    decoder = MessageDecoder()
    read1 = getattr(input_stream, "read1", None)
    while True:
        chunk = read1(64 * 1024) if callable(read1) else input_stream.read(64 * 1024)
        if not chunk:
            decoder.finish()
            log_writer.flush()
            return server.close_for_eof()
        for message in decoder.feed(chunk):
            try:
                exit_code = server.handle(message)
            except _FatalWorkerExit as fatal:
                log_writer.flush()
                return fatal.exit_code
            except ProtocolError as error:
                request_id = message.get("request_id")
                if type(request_id) is not int or request_id <= 0:
                    raise
                server._send_error(
                    "request_error",
                    request_id,
                    error,
                    epoch=(
                        message.get("epoch")
                        if type(message.get("epoch")) is int
                        else None
                    ),
                )
                exit_code = None
            finally:
                # 用户脚本可能重绑 sys.stdout；每个命令边界都恢复永久的
                # 结构化 writer，延迟 VS callback 因而仍走协议 log。
                sys.stdout = log_writer
                sys.__stdout__ = log_writer
            if exit_code is not None:
                log_writer.flush()
                return exit_code


def main(
    *,
    protocol_stream: BinaryIO | None = None,
    input_stream: BinaryIO | None = None,
    app_dir: str | os.PathLike[str] | None = None,
    self_test: bool | None = None,
    generation_staging: Any | None = None,
) -> int:
    if protocol_stream is None:
        protocol_stream = sys.stdout.buffer
    if input_stream is None:
        input_stream = sys.stdin.buffer
    if app_dir is None:
        app_dir = (
            Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parents[2]
        )
    if self_test is None:
        self_test = "--self-test" in sys.argv[1:]
    return run_worker(
        protocol_stream=protocol_stream,
        input_stream=input_stream,
        app_dir=Path(app_dir).resolve(),
        self_test=self_test,
        generation_staging=generation_staging,
    )


__all__ = ["ProtocolWriter", "WorkerServer", "main", "run_worker"]
