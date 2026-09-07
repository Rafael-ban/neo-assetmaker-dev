"""Asset export service."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import shutil
import stat
import tempfile
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional, Tuple

import numpy as np

try:
    import cv2

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

from config.epconfig import EPConfig
from core.file_utils import atomic_write_bytes
from core.media_pipeline import MediaEncoder, VSPipeRenderRequest
from core.media_tools import MediaToolchain
from core.vs_runtime.job import RationalFPS, load_render_job
from core.vs_runtime.output_contract import X264Vui
from core.vs_runtime.session import (
    RenderSession,
    compute_job_sha256,
    compute_script_bundle_hash,
)
from core.vs_runtime.vs_loader import compute_runtime_fingerprint
from core.vs_runtime.worker_process import SyncVSWorkerProcess
from config.vs_runtime import load_vs_runtime
from utils.file_utils import get_app_dir

logger = logging.getLogger(__name__)

_KNOWN_PACKAGE_ARTIFACTS = frozenset(
    {
        "epconfig.json",
        "icon.png",
        "loop.mp4",
        "intro.mp4",
        "overlay.argb",
        "class_icon.png",
        "ark_logo.png",
        "overlay.png",
    }
)


def _to_bgra_bytes_source(mat: np.ndarray) -> np.ndarray:
    """Rotate 180° and return a contiguous BGRA uint8 array for .argb output.

    Matches the old per-pixel writer exactly: rotate first, then emit
    B,G,R,A per pixel (grayscale replicated across BGR, alpha defaults 255).
    """
    if HAS_CV2:
        mat = cv2.rotate(mat, cv2.ROTATE_180)
    else:
        mat = np.rot90(mat, 2)
    mat = mat.astype(np.uint8)
    if mat.ndim == 2:  # grayscale
        h, w = mat.shape
        bgra = np.empty((h, w, 4), np.uint8)
        bgra[..., 0] = bgra[..., 1] = bgra[..., 2] = mat
        bgra[..., 3] = 255
    elif mat.shape[2] == 4:
        bgra = mat
    else:  # 3-channel BGR
        h, w = mat.shape[:2]
        bgra = np.empty((h, w, 4), np.uint8)
        bgra[..., :3] = mat[..., :3]
        bgra[..., 3] = 255
    return np.ascontiguousarray(bgra)


class ExportType(Enum):
    """Export task type."""

    LOGO = "logo"
    OVERLAY = "overlay"
    LOOP_VIDEO = "loop"
    INTRO_VIDEO = "intro"
    ICON = "icon"
    AUX_IMAGE = "aux_image"  # class_icon.png / ark_logo.png / overlay.png (PNG mat)


@dataclass
class VideoExportParams:
    """Video export parameters."""

    video_path: str
    cropbox: Tuple[int, int, int, int]
    start_frame: int
    end_frame: int
    fps: float
    resolution: str = "360x640"
    is_image: bool = False
    rotation: int = 0


@dataclass(frozen=True)
class ExportTask:
    """One export task."""

    export_type: ExportType
    output_path: str
    data: Any

    def __post_init__(self) -> None:
        if self.export_type in (ExportType.LOOP_VIDEO, ExportType.INTRO_VIDEO):
            if not isinstance(self.data, RenderSession):
                raise TypeError("视频 ExportTask 必须持有不可变 RenderSession")

    @property
    def session(self) -> RenderSession:
        if self.export_type not in (ExportType.LOOP_VIDEO, ExportType.INTRO_VIDEO):
            raise TypeError("非视频 ExportTask 没有 RenderSession")
        if not isinstance(self.data, RenderSession):
            raise TypeError("该视频任务尚未冻结为 RenderSession")
        return self.data


@dataclass(frozen=True)
class _ArtifactExpectation:
    """One exact file which must exist before a package can be published."""

    relative_path: str
    kind: Literal["png", "argb", "mp4", "json"]
    exact_size: int | None = None
    expected_sha256: str | None = None


@dataclass(frozen=True)
class _FilesystemIdentity:
    """Enough lstat data to recognize one exact filesystem generation."""

    device: int
    inode: int
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class _FinalGeneration:
    """The final package generation observed while this transaction owns its lock."""

    exists: bool
    directory: _FilesystemIdentity | None
    files: tuple[tuple[str, _FilesystemIdentity, str], ...]


@dataclass(frozen=True)
class _PreparedExportPackage:
    """The immutable UI-to-worker ownership handoff for one export."""

    final_dir: Path
    staging_dir: Path
    backup_dir: Path
    work_dir: Path
    tasks: tuple[ExportTask, ...]
    manifest: tuple[_ArtifactExpectation, ...]
    epconfig_bytes: bytes
    final_generation: _FinalGeneration | None = None
    lock_path: Path | None = None
    lock_token: bytes = b""
    lock_identity: _FilesystemIdentity | None = None
    final_dir_identity: _FilesystemIdentity | None = None
    staging_dir_identity: _FilesystemIdentity | None = None
    backup_dir_identity: _FilesystemIdentity | None = None
    work_dir_identity: _FilesystemIdentity | None = None


class _RecoveryRequiredError(RuntimeError):
    """A rollback failed; preserve both directories for manual recovery."""


def _is_reparse_or_symlink(path: Path) -> bool:
    """Return whether *path* is a symlink or a Windows junction/reparse point."""
    try:
        metadata = path.lstat()
    except OSError:
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_point)


def _lexical_absolute_path(value: str | os.PathLike[str]) -> Path:
    """Make an absolute path without resolving any links in the user spelling."""
    return Path(os.path.abspath(os.fspath(value)))


def _filesystem_identity(path: Path) -> _FilesystemIdentity:
    metadata = path.lstat()
    return _FilesystemIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        size=metadata.st_size,
        mtime_ns=metadata.st_mtime_ns,
    )


def _is_same_filesystem_object(
    expected: _FilesystemIdentity, actual: _FilesystemIdentity
) -> bool:
    """Compare stable directory identity without mutable directory metadata."""
    return (expected.device, expected.inode) == (actual.device, actual.inode)


def _is_same_non_reparse_directory(left: Path, right: Path) -> bool:
    """Compare existing transaction parents without trusting their spelling.

    Windows can expose one directory through both a legacy 8.3 path and its
    long-name path.  ``tempfile.mkdtemp()`` is free to return either spelling,
    so lexical ``Path`` equality cannot prove that its result escaped the
    export parent.  Compare the non-reparse directory identities instead.

    Do not use ``Path.resolve()`` here: resolving links before this check would
    weaken the transaction's deliberate no-links/no-junctions boundary.
    """
    try:
        if _is_reparse_or_symlink(left) or _is_reparse_or_symlink(right):
            return False
        if not left.is_dir() or not right.is_dir():
            return False
        return _is_same_filesystem_object(
            _filesystem_identity(left), _filesystem_identity(right)
        )
    except OSError:
        return False


def _capture_final_generation(final_dir: Path) -> _FinalGeneration:
    """Capture the exact replaceable package generation, never following links."""
    if not os.path.lexists(final_dir):
        return _FinalGeneration(False, None, ())
    _assert_replaceable_final_dir(final_dir)
    directory = _filesystem_identity(final_dir)
    files: list[tuple[str, _FilesystemIdentity, str]] = []
    for entry in sorted(final_dir.iterdir(), key=lambda candidate: candidate.name):
        files.append(
            (
                entry.name,
                _filesystem_identity(entry),
                hashlib.sha256(entry.read_bytes()).hexdigest(),
            )
        )
    return _FinalGeneration(True, directory, tuple(files))


def _acquire_transaction_lock(
    final_dir: Path,
) -> tuple[Path, bytes, _FilesystemIdentity]:
    """Atomically claim one target-specific sibling lock without stale-lock cleanup."""
    lock_path = final_dir.parent / f".{final_dir.name}.lock"
    token = secrets.token_bytes(32)
    descriptor: int | None = None
    created_identity: _FilesystemIdentity | None = None
    try:
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0),
            stat.S_IREAD | stat.S_IWRITE,
        )
        created_identity = _filesystem_identity(lock_path)
        os.write(descriptor, token)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        return lock_path, token, _filesystem_identity(lock_path)
    except FileExistsError as exc:
        raise RuntimeError(
            f"导出目标已有运行中或遗留的事务锁，拒绝覆盖: {lock_path}"
        ) from exc
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        if (
            created_identity is not None
            and os.path.lexists(lock_path)
            and not _is_reparse_or_symlink(lock_path)
            and _filesystem_identity(lock_path) == created_identity
        ):
            try:
                lock_path.unlink()
            except OSError:
                logger.exception("Unable to remove newly-created transaction lock: %s", lock_path)
        raise


def _validated_relative_path(value: str) -> str:
    path = Path(value)
    if (
        not value
        or path.is_absolute()
        or len(path.parts) != 1
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise RuntimeError(f"导出文件名不安全: {value!r}")
    return path.as_posix()


def _epconfig_references(payload: object) -> set[str]:
    """Return package-local file references from normalized epconfig JSON."""
    references: set[str] = set()
    reference_keys = {"file", "icon", "image", "logo", "operator_class_icon"}

    def visit(value: object, key: str | None = None) -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                visit(child_value, child_key)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif key in reference_keys and isinstance(value, str) and value:
            if key == "operator_class_icon" and value.startswith("class_icons/"):
                return
            normalized = _validated_relative_path(value)
            references.add(normalized)

    visit(payload)
    return references


def _assert_replaceable_final_dir(final_dir: Path) -> None:
    """Fail closed unless an existing non-empty directory is our old package."""
    if final_dir.is_symlink():
        raise RuntimeError(f"导出目标不能是链接或非目录: {final_dir}")
    if not final_dir.exists():
        return
    if _is_reparse_or_symlink(final_dir) or not final_dir.is_dir():
        raise RuntimeError(f"导出目标不能是链接或非目录: {final_dir}")
    entries = list(final_dir.iterdir())
    if not entries:
        return
    files: set[str] = set()
    for entry in entries:
        if _is_reparse_or_symlink(entry) or not entry.is_file():
            raise RuntimeError(f"拒绝替换包含未知目录或链接的用户目录: {final_dir}")
        files.add(entry.name)
    config_path = final_dir / "epconfig.json"
    if "epconfig.json" not in files:
        raise RuntimeError(f"拒绝替换没有 epconfig.json 的非空目录: {final_dir}")
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"拒绝替换无效的旧导出包: {final_dir}") from exc
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("version"), int)
        or not isinstance(payload.get("screen"), str)
        or not isinstance(payload.get("loop"), dict)
    ):
        raise RuntimeError(f"拒绝替换无效的旧导出包: {final_dir}")
    references = _epconfig_references(payload)
    if not references.issubset(files) or not files.issubset(_KNOWN_PACKAGE_ARTIFACTS):
        raise RuntimeError(f"拒绝替换包含未知文件的目录: {final_dir}")


class ExportWorker(QThread):
    """Background export worker."""

    progress_updated = pyqtSignal(int, str)
    export_completed = pyqtSignal(str)
    export_failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._package: Optional[_PreparedExportPackage] = None
        # Kept as an immutable inspection view for existing callers/tests.  run()
        # deliberately reads _package.tasks, the single ownership source.
        self._tasks: tuple[ExportTask, ...] = ()
        self._staging_dir = ""
        self._cancelled = False
        self._resolution = "360x640"
        self._media_toolchain = MediaToolchain.discover()
        self._media_encoder: Optional[MediaEncoder] = None

    def setup(
        self,
        prepared: _PreparedExportPackage,
        media_toolchain: Optional[MediaToolchain] = None,
        resolution: str = "360x640",
    ) -> None:
        self._package = prepared
        self._tasks = prepared.tasks
        self._staging_dir = str(prepared.staging_dir)
        self._media_toolchain = media_toolchain or MediaToolchain.discover()
        self._resolution = resolution
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        if self._media_encoder is not None:
            self._media_encoder.terminate_active_processes()

    def run(self) -> None:
        package = self._package
        if package is None:
            self.export_failed.emit("Export worker has no prepared package")
            return
        try:
            total_tasks = len(package.tasks)
            for index, task in enumerate(package.tasks):
                if self._cancelled:
                    raise InterruptedError("Export cancelled")
                base_progress = int((index / (total_tasks + 1)) * 100)
                self._execute_task(task, base_progress, total_tasks)
                self._validate_artifact(package, task.output_path)

            if self._cancelled:
                raise InterruptedError("Export cancelled")
            self._assert_all_video_identities(
                package, "清理私有工作目录前最终身份检查"
            )
            self._remove_private_work_dir(package)
            self._seal_package(package)
            self._assert_all_video_nonprivate_identities(package)
            if self._cancelled:
                raise InterruptedError("Export cancelled")
            warning = self._commit_package(package)

            self.progress_updated.emit(100, "Export completed")
            message = f"Exported to {package.final_dir}"
            if warning:
                logger.warning("%s", warning)
                message = f"{message}（警告：{warning}）"
            self.export_completed.emit(message)
        except _RecoveryRequiredError as exc:
            logger.exception("Export rollback requires manual recovery")
            self.export_failed.emit(str(exc))
        except Exception as exc:
            logger.exception("Export failed")
            cleanup_warning = self._abort_package(package)
            message = f"Export failed: {exc}"
            if cleanup_warning:
                message = f"{message}（警告：{cleanup_warning}）"
            self.export_failed.emit(message)
        finally:
            self._staging_dir = ""
            self._release_package_lock(package)

    def _execute_task(self, task: ExportTask, base_progress: int, total_tasks: int) -> None:
        output_path = Path(self._staging_dir) / task.output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if task.export_type == ExportType.LOGO:
            self.progress_updated.emit(base_progress, f"Exporting {task.output_path}...")
            self._export_argb(str(output_path), task.data)
        elif task.export_type == ExportType.OVERLAY:
            self.progress_updated.emit(base_progress, f"Exporting {task.output_path}...")
            self._export_argb(str(output_path), task.data)
        elif task.export_type in (ExportType.ICON, ExportType.AUX_IMAGE):
            self.progress_updated.emit(base_progress, f"Exporting {task.output_path}...")
            self._export_icon(str(output_path), task.data)
        elif task.export_type in (ExportType.LOOP_VIDEO, ExportType.INTRO_VIDEO):
            self.progress_updated.emit(base_progress, f"Exporting {task.output_path}...")
            self._export_video(str(output_path), task.session, base_progress)

    def _export_icon(self, output_path: str, mat: np.ndarray) -> None:
        if not HAS_CV2:
            raise RuntimeError("opencv-python is required to export PNG icons")
        success, encoded = cv2.imencode(".png", mat)
        if not success:
            raise RuntimeError("Failed to encode icon PNG")
        with open(output_path, "wb") as fh:
            fh.write(encoded.tobytes())

    def _export_argb(self, output_path: str, mat: np.ndarray) -> None:
        # The 180° rotation and the B,G,R,A byte layout are the device
        # framebuffer contract (the overlay plane is the SCREEN size, 360-wide,
        # per simulator/src/config/firmware_config.rs). This is a vectorized
        # rewrite of the old per-pixel struct.pack loop — byte-identical output,
        # seconds -> milliseconds on a 720p overlay.
        bgra = _to_bgra_bytes_source(mat)
        with open(output_path, "wb") as fh:
            rows = bgra.shape[0]
            band = max(1, rows // 16)  # keep cancellation responsive on big screens
            for start in range(0, rows, band):
                if self._cancelled:
                    raise InterruptedError("Export cancelled")
                fh.write(bgra[start:start + band].tobytes())

    @staticmethod
    def _vui_from_preflight(metadata) -> X264Vui:
        """只把 worker 已验证 output 0 的属性翻译成 x264 VUI。"""
        output = metadata.output0
        matrix_map = {"170m": "smpte170m", "709": "bt709"}
        try:
            return X264Vui(
                colormatrix=matrix_map[output.matrix],
                colorprim=matrix_map[output.primaries],
                transfer=matrix_map[output.transfer],
                range_={"limited": "tv", "full": "pc"}[output.range],
            )
        except (KeyError, TypeError) as exc:
            raise RuntimeError("预检 output 0 未提供可编码的 VUI 属性") from exc

    @staticmethod
    def _runtime_fingerprint_for_export(app_dir: str, runtime=None) -> str:
        return compute_runtime_fingerprint(
            app_dir, load_vs_runtime() if runtime is None else runtime
        )

    @staticmethod
    def _runtime_for_export():
        return load_vs_runtime()

    def _assert_export_identity(self, session: RenderSession, stage: str):
        """验证 script/job/runtime 仍等于 UI 已冻结 session 的身份。"""
        self._assert_script_identity(session, stage)
        try:
            actual_job_sha256 = compute_job_sha256(session.job_path)
        except OSError as exc:
            raise RuntimeError(f"{stage}：冻结 job 无法读取") from exc
        if actual_job_sha256 != session.job_sha256:
            raise RuntimeError(f"{stage}：冻结 job 内容 hash 已变化")
        return self._assert_runtime_identity(session, stage)

    @staticmethod
    def _assert_script_identity(session: RenderSession, stage: str) -> None:
        if (
            compute_script_bundle_hash(session.selection.script_path)
            != session.selection.bundle_hash
        ):
            raise RuntimeError(f"{stage}：用户脚本 bundle hash 已变化")

    def _assert_runtime_identity(self, session: RenderSession, stage: str):
        app_dir = str(Path(get_app_dir()).resolve())
        runtime = self._runtime_for_export()
        if (
            self._runtime_fingerprint_for_export(app_dir, runtime)
            != session.runtime_fingerprint
        ):
            raise RuntimeError(f"{stage}：VapourSynth runtime fingerprint 已变化")
        return runtime

    def _preflight_render_session(self, session: RenderSession):
        """在短生命周期 worker 中验证与导出完全相同的 frozen session。"""
        app_dir = str(Path(get_app_dir()).resolve())
        self._assert_export_identity(session, "预检前")

        process = SyncVSWorkerProcess(app_dir=app_dir)
        try:
            process.start()
            metadata = process.load(session)
        finally:
            process.close()

        if metadata.epoch != session.epoch or metadata.mode != session.selection.mode:
            raise RuntimeError("预检失败：worker 返回的 session identity 不匹配")
        self._assert_export_identity(session, "预检后")
        return metadata

    def _export_video(
        self,
        output_path: str,
        session: RenderSession,
        base_progress: int,
    ) -> None:
        missing = self._media_toolchain.missing_for_export()
        if missing:
            raise RuntimeError("Missing media tools: " + ", ".join(missing))

        def _on_encode_progress(done: int, total: int) -> None:
            # Map VSPipe "Frame: done/total" onto this task's 50..90 band —
            # the dialog used to sit frozen at +50 for the entire encode.
            if total > 0:
                span = int(40 * min(done, total) / total)
                self.progress_updated.emit(
                    base_progress + 50 + span, f"Encoding video... {done}/{total}"
                )

        success = False
        try:
            self.progress_updated.emit(base_progress + 10, "预检 VapourSynth output 0...")
            metadata = self._preflight_render_session(session)
            runtime = self._assert_export_identity(session, "VSPipe 前")
            request = VSPipeRenderRequest(
                runner_path=str(
                    Path(get_app_dir())
                    / "resources"
                    / "vapoursynth"
                    / "assetmaker_runner.vpy"
                ),
                script_path=session.selection.script_path,
                job_path=session.job_path,
                expected_job_sha256=session.job_sha256,
                api_version=session.selection.api_version,
                mode=session.selection.mode,
                app_dir=str(Path(get_app_dir()).resolve()),
                runtime=runtime,
                runtime_fingerprint=session.runtime_fingerprint,
            )
            fps = RationalFPS(metadata.output0.fps_num, metadata.output0.fps_den)
            self.progress_updated.emit(base_progress + 50, "Encoding video...")
            self._media_encoder = MediaEncoder(self._media_toolchain)
            self._media_encoder.encode_vpy_to_mp4(
                request,
                output_path,
                fps,
                vui=self._vui_from_preflight(metadata),
                is_cancelled=lambda: self._cancelled_or_identity_changed(session),
                progress_cb=_on_encode_progress,
            )
            self._assert_export_identity(session, "编码后")
            success = True
        finally:
            self._media_encoder = None
            if not success:
                try:
                    Path(output_path).unlink(missing_ok=True)
                except OSError:
                    pass

    def _cancelled_or_identity_changed(self, session: RenderSession) -> bool:
        self._assert_export_identity(session, "编码期间")
        return self._cancelled

    @staticmethod
    def _assert_lock_owned(package: _PreparedExportPackage) -> None:
        """Refuse to touch final unless this package still owns its exact lock."""
        if package.lock_path is None:
            return
        lock_path = package.lock_path
        if (
            lock_path.parent != package.final_dir.parent
            or lock_path.name != f".{package.final_dir.name}.lock"
            or not os.path.lexists(lock_path)
            or _is_reparse_or_symlink(lock_path)
            or package.lock_identity is None
            or _filesystem_identity(lock_path) != package.lock_identity
            or lock_path.read_bytes() != package.lock_token
        ):
            raise RuntimeError(f"导出事务锁所有权无法证明: {lock_path}")

    @classmethod
    def _release_package_lock(cls, package: _PreparedExportPackage) -> None:
        """Release only the exact lock we created; stale or replaced locks stay put."""
        if package.lock_path is None:
            return
        try:
            cls._assert_lock_owned(package)
            package.lock_path.unlink()
        except FileNotFoundError:
            logger.error("Export transaction lock disappeared: %s", package.lock_path)
        except Exception:
            logger.exception(
                "Refusing to remove export transaction lock without ownership: %s",
                package.lock_path,
            )

    @staticmethod
    def _assert_directory_identity(
        path: Path,
        expected_identity: _FilesystemIdentity | None,
        operation: str,
    ) -> None:
        if expected_identity is None:
            raise RuntimeError(f"导出事务目录身份缺失，拒绝{operation}: {path}")
        if (
            not os.path.lexists(path)
            or _is_reparse_or_symlink(path)
            or not path.is_dir()
        ):
            raise RuntimeError(f"导出事务目录无效，拒绝{operation}: {path}")
        actual_identity = _filesystem_identity(path)
        if not _is_same_filesystem_object(expected_identity, actual_identity):
            raise RuntimeError(f"导出事务目录身份无法证明，拒绝{operation}: {path}")

    @classmethod
    def _assert_package_path(
        cls,
        package: _PreparedExportPackage,
        path: Path,
        prefix: str,
        expected_identity: _FilesystemIdentity | None,
    ) -> None:
        if (
            not _is_same_non_reparse_directory(
                path.parent, package.final_dir.parent
            )
            or not path.name.startswith(prefix)
        ):
            raise RuntimeError(f"拒绝清理或移动非本次导出路径: {path}")
        cls._assert_directory_identity(path, expected_identity, "清理或移动")

    @staticmethod
    def _package_directory_identity(
        package: _PreparedExportPackage, path: Path
    ) -> _FilesystemIdentity | None:
        if path == package.staging_dir:
            return package.staging_dir_identity
        if path == package.backup_dir:
            return package.backup_dir_identity
        raise RuntimeError(f"拒绝清理未知导出事务路径: {path}")

    @classmethod
    def _remove_tree(
        cls, package: _PreparedExportPackage, path: Path, prefix: str
    ) -> None:
        if not os.path.lexists(path):
            return
        cls._assert_package_path(
            package,
            path,
            prefix,
            cls._package_directory_identity(package, path),
        )

        def retry_readonly(function, failed_path, exception_info) -> None:
            del exception_info
            os.chmod(failed_path, stat.S_IWRITE)
            function(failed_path)

        shutil.rmtree(path, onexc=retry_readonly)

    def _abort_package(self, package: _PreparedExportPackage) -> str | None:
        try:
            self._remove_tree(package, package.staging_dir, f".{package.final_dir.name}.staging-")
        except Exception as exc:
            logger.exception("Unable to clean failed export staging: %s", package.staging_dir)
            return (
                f"失败导出 staging 未清理，保留在 {package.staging_dir}；"
                f"请人工检查: {exc}"
            )
        return None

    def _remove_private_work_dir(self, package: _PreparedExportPackage) -> None:
        if package.work_dir.parent != package.staging_dir:
            raise RuntimeError("导出私有工作目录越界")
        self._assert_directory_identity(
            package.work_dir,
            package.work_dir_identity,
            "清理私有工作目录",
        )
        def retry_readonly(function, failed_path, exception_info) -> None:
            del exception_info
            os.chmod(failed_path, stat.S_IWRITE)
            function(failed_path)

        shutil.rmtree(package.work_dir, onexc=retry_readonly)

    @staticmethod
    def _validate_file_kind(path: Path, expectation: _ArtifactExpectation) -> None:
        if _is_reparse_or_symlink(path) or not path.is_file():
            raise RuntimeError(f"导出工件不是普通文件: {expectation.relative_path}")
        size = path.stat().st_size
        if size <= 0:
            raise RuntimeError(f"导出工件为空: {expectation.relative_path}")
        if expectation.exact_size is not None and size != expectation.exact_size:
            raise RuntimeError(f"导出工件大小错误: {expectation.relative_path}")
        data = path.read_bytes()
        if expectation.expected_sha256 and hashlib.sha256(data).hexdigest() != expectation.expected_sha256:
            raise RuntimeError(f"导出配置在 worker 启动后发生变化: {expectation.relative_path}")
        if expectation.kind == "png":
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise RuntimeError(f"PNG 工件无效: {expectation.relative_path}")
            if HAS_CV2 and cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED) is None:
                raise RuntimeError(f"PNG 工件无法解码: {expectation.relative_path}")
        elif expectation.kind == "mp4" and (
            len(data) < 8 or data[4:8] != b"ftyp"
        ):
            raise RuntimeError(f"MP4 工件无效: {expectation.relative_path}")
        elif expectation.kind == "json":
            try:
                json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("epconfig.json 不是有效 UTF-8 JSON") from exc

    def _validate_artifact(self, package: _PreparedExportPackage, relative_path: str) -> None:
        expectation = next(
            item for item in package.manifest if item.relative_path == relative_path
        )
        self._validate_file_kind(package.staging_dir / expectation.relative_path, expectation)

    def _seal_package(self, package: _PreparedExportPackage) -> None:
        expected = {item.relative_path for item in package.manifest}
        actual: set[str] = set()
        for child in package.staging_dir.rglob("*"):
            if _is_reparse_or_symlink(child):
                raise RuntimeError(f"staging 包含链接: {child}")
            if child.is_dir():
                raise RuntimeError(f"staging 包含未声明目录: {child}")
            actual.add(child.relative_to(package.staging_dir).as_posix())
        if actual != expected:
            raise RuntimeError(
                f"导出 manifest 不完整或包含额外文件: 缺失={sorted(expected - actual)}, 额外={sorted(actual - expected)}"
            )
        for expectation in package.manifest:
            self._validate_file_kind(package.staging_dir / expectation.relative_path, expectation)
        payload = json.loads(package.epconfig_bytes.decode("utf-8"))
        references = _epconfig_references(payload)
        if not references.issubset(expected):
            raise RuntimeError("epconfig.json 包内引用未被 manifest 覆盖")

    def _assert_all_video_identities(
        self, package: _PreparedExportPackage, stage: str
    ) -> None:
        """在删除冻结 job 前，完整验证所有视频会话身份。"""
        for task in package.tasks:
            if task.export_type in (ExportType.LOOP_VIDEO, ExportType.INTRO_VIDEO):
                self._assert_export_identity(task.session, stage)

    def _assert_all_video_nonprivate_identities(
        self, package: _PreparedExportPackage
    ) -> None:
        """seal 后只验证仍应存在的 script/runtime 身份。"""
        for task in package.tasks:
            if task.export_type in (ExportType.LOOP_VIDEO, ExportType.INTRO_VIDEO):
                stage = "seal 后最终身份检查"
                self._assert_script_identity(task.session, stage)
                self._assert_runtime_identity(task.session, stage)

    @staticmethod
    def _rename_dir_no_replace(source: Path, destination: Path) -> None:
        os.rename(source, destination)

    @classmethod
    def _assert_final_directory(
        cls,
        package: _PreparedExportPackage,
        expected_identity: _FilesystemIdentity | None,
        operation: str,
    ) -> None:
        cls._assert_directory_identity(
            package.final_dir, expected_identity, operation
        )

    @staticmethod
    def _manual_recovery_error(
        package: _PreparedExportPackage, stage: str, cause: Exception
    ) -> _RecoveryRequiredError:
        return _RecoveryRequiredError(
            f"{stage}后导出事务目录身份无法证明；需要人工恢复。"
            f" final={package.final_dir} staging={package.staging_dir} "
            f"backup={package.backup_dir}; cause={cause}"
        )

    def _commit_package(self, package: _PreparedExportPackage) -> str | None:
        final_dir = package.final_dir
        staging_dir = package.staging_dir
        backup_dir = package.backup_dir
        self._assert_lock_owned(package)
        current_generation = _capture_final_generation(final_dir)
        if (
            package.final_generation is not None
            and current_generation != package.final_generation
        ):
            raise RuntimeError("导出目标在准备后已变化，拒绝覆盖")
        if os.path.lexists(backup_dir):
            raise RuntimeError(f"导出备份路径已存在: {backup_dir}")
        staging_prefix = f".{final_dir.name}.staging-"
        backup_prefix = f".{final_dir.name}.backup-"
        if package.final_dir_identity is not None:
            self._assert_final_directory(
                package, package.final_dir_identity, "移动旧包到备份"
            )
            try:
                self._rename_dir_no_replace(final_dir, backup_dir)
            except Exception as move_error:
                try:
                    self._assert_final_directory(
                        package, package.final_dir_identity, "确认旧包未移动"
                    )
                except Exception as identity_error:
                    raise self._manual_recovery_error(
                        package, "移动旧包到备份", identity_error
                    ) from move_error
                raise
            try:
                self._assert_package_path(
                    package,
                    backup_dir,
                    backup_prefix,
                    package.backup_dir_identity,
                )
            except Exception as identity_error:
                raise self._manual_recovery_error(
                    package, "旧包移入备份", identity_error
                ) from identity_error
            try:
                self._assert_package_path(
                    package,
                    staging_dir,
                    staging_prefix,
                    package.staging_dir_identity,
                )
                self._rename_dir_no_replace(staging_dir, final_dir)
                self._assert_final_directory(
                    package, package.staging_dir_identity, "发布新包"
                )
            except Exception as publish_error:
                try:
                    if os.path.lexists(final_dir):
                        raise RuntimeError("导出目标路径已被其他进程占用")
                    self._assert_package_path(
                        package,
                        backup_dir,
                        backup_prefix,
                        package.backup_dir_identity,
                    )
                    self._rename_dir_no_replace(backup_dir, final_dir)
                    self._assert_final_directory(
                        package, package.backup_dir_identity, "回滚旧包"
                    )
                except Exception as rollback_error:
                    raise self._manual_recovery_error(
                        package, "发布失败且回滚", rollback_error
                    ) from publish_error
                raise RuntimeError(f"发布新包失败，已恢复旧包: {publish_error}") from publish_error
            try:
                self._remove_tree(package, backup_dir, f".{final_dir.name}.backup-")
            except Exception as exc:
                return (
                    f"旧包备份身份无法证明，未执行清理，保留在 {backup_dir}；"
                    f"请人工检查或恢复: {exc}"
                )
            return None
        if os.path.lexists(final_dir):
            raise RuntimeError("导出目标在验证后出现，拒绝覆盖")
        self._assert_package_path(
            package,
            staging_dir,
            staging_prefix,
            package.staging_dir_identity,
        )
        self._rename_dir_no_replace(staging_dir, final_dir)
        try:
            self._assert_final_directory(
                package, package.staging_dir_identity, "发布新包"
            )
        except Exception as identity_error:
            raise self._manual_recovery_error(
                package, "发布新包", identity_error
            ) from identity_error
        return None


class ExportService(QObject):
    """High-level export service."""

    progress_updated = pyqtSignal(int, str)
    export_completed = pyqtSignal(str)
    export_failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: Optional[ExportWorker] = None
        self._worker_terminal: Optional[Tuple[bool, str]] = None
        self._media_toolchain = MediaToolchain.discover()

    @property
    def is_exporting(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    @property
    def media_pipeline_available(self) -> bool:
        if not self._media_toolchain:
            self._media_toolchain = MediaToolchain.discover()
        return not self._media_toolchain.missing_for_export()

    def _missing_media_tools_message(self) -> str:
        missing = self._media_toolchain.missing_for_export()
        return "Missing media tools: " + ", ".join(missing)

    def export_all(
        self,
        output_dir: str,
        epconfig: EPConfig,
        logo_mat: Optional[np.ndarray] = None,
        overlay_mat: Optional[np.ndarray] = None,
        loop_render_session: Optional[RenderSession] = None,
        intro_render_session: Optional[RenderSession] = None,
        aux_images: Optional[list] = None,
    ) -> None:
        if self._worker is not None:
            self.export_failed.emit("An export task is already running")
            return

        if (loop_render_session is not None or intro_render_session is not None) and not self.media_pipeline_available:
            self.export_failed.emit(self._missing_media_tools_message())
            return
        try:
            prepared = self._prepare_export_package(
                output_dir=output_dir,
                epconfig=epconfig,
                logo_mat=logo_mat,
                overlay_mat=overlay_mat,
                loop_render_session=loop_render_session,
                intro_render_session=intro_render_session,
                aux_images=aux_images,
            )
        except Exception as exc:
            logger.exception("Failed to prepare export package")
            self.export_failed.emit(f"Export preparation failed: {exc}")
            return

        worker = ExportWorker(self)
        self._worker = worker
        worker.setup(
            prepared=prepared,
            media_toolchain=self._media_toolchain,
            resolution=epconfig.screen.value,
        )
        worker.progress_updated.connect(self._on_worker_progress)
        worker.export_completed.connect(self._on_worker_completed)
        worker.export_failed.connect(self._on_worker_failed)
        worker.finished.connect(self._on_worker_finished)
        try:
            worker.start()
        except Exception as exc:
            logger.exception("Failed to start export worker")
            cleanup_warning = None
            try:
                cleanup_warning = worker._abort_package(prepared)
            finally:
                worker._release_package_lock(prepared)
            self._worker = None
            self._worker_terminal = None
            worker.deleteLater()
            message = f"Export failed to start: {exc}"
            if cleanup_warning:
                message = f"{message}（警告：{cleanup_warning}）"
            self.export_failed.emit(message)

    def _prepare_export_package(
        self,
        *,
        output_dir: str,
        epconfig: EPConfig,
        logo_mat: Optional[np.ndarray],
        overlay_mat: Optional[np.ndarray],
        loop_render_session: Optional[RenderSession],
        intro_render_session: Optional[RenderSession],
        aux_images: Optional[list],
    ) -> _PreparedExportPackage:
        if not output_dir:
            raise RuntimeError("导出目标不能为空")
        final_dir = _lexical_absolute_path(output_dir)
        final_parent = final_dir.parent
        if (
            not final_dir.name
            or not final_parent.is_dir()
            or _is_reparse_or_symlink(final_parent)
        ):
            raise RuntimeError(f"导出目标父目录无效: {final_dir.parent}")
        if os.path.lexists(final_dir) and _is_reparse_or_symlink(final_dir):
            raise RuntimeError(f"导出目标不能是链接或非目录: {final_dir}")
        _assert_replaceable_final_dir(final_dir)
        lock_path, lock_token, lock_identity = _acquire_transaction_lock(final_dir)
        final_generation: _FinalGeneration | None = None
        staging_dir: Path | None = None
        backup_dir: Path | None = None
        work_dir: Path | None = None
        final_dir_identity: _FilesystemIdentity | None = None
        staging_dir_identity: _FilesystemIdentity | None = None
        backup_dir_identity: _FilesystemIdentity | None = None
        work_dir_identity: _FilesystemIdentity | None = None
        try:
            final_generation = _capture_final_generation(final_dir)
            final_dir_identity = final_generation.directory
            staging_dir = Path(
                tempfile.mkdtemp(
                    prefix=f".{final_dir.name}.staging-",
                    dir=final_parent,
                )
            )
            if (
                not _is_same_non_reparse_directory(
                    staging_dir.parent, final_parent
                )
                or _is_reparse_or_symlink(staging_dir)
            ):
                raise RuntimeError(f"导出 staging 路径无效: {staging_dir}")
            staging_dir_identity = _filesystem_identity(staging_dir)
            token = staging_dir.name.removeprefix(f".{final_dir.name}.staging-")
            backup_dir = final_parent / f".{final_dir.name}.backup-{token}"
            backup_dir_identity = final_dir_identity
            work_dir = staging_dir / ".assetmaker-work"
            if os.path.lexists(backup_dir):
                raise RuntimeError(f"导出备份路径已存在: {backup_dir}")
            payload = epconfig.to_dict(normalize_paths=True)
            epconfig_bytes = json.dumps(
                payload, ensure_ascii=False, indent=4
            ).encode("utf-8")
            work_dir.mkdir()
            work_dir_identity = _filesystem_identity(work_dir)
            tasks = self._snapshot_tasks(
                staging_dir,
                logo_mat,
                overlay_mat,
                loop_render_session,
                intro_render_session,
                aux_images,
            )
            if not tasks:
                raise RuntimeError("No content to export")
            manifest = self._build_manifest(tasks, epconfig_bytes)
            atomic_write_bytes(staging_dir / "epconfig.json", epconfig_bytes)
            return _PreparedExportPackage(
                final_dir=final_dir,
                staging_dir=staging_dir,
                backup_dir=backup_dir,
                work_dir=work_dir,
                tasks=tasks,
                manifest=manifest,
                epconfig_bytes=epconfig_bytes,
                final_generation=final_generation,
                lock_path=lock_path,
                lock_token=lock_token,
                lock_identity=lock_identity,
                final_dir_identity=final_dir_identity,
                staging_dir_identity=staging_dir_identity,
                backup_dir_identity=backup_dir_identity,
                work_dir_identity=work_dir_identity,
            )
        except Exception:
            temporary = _PreparedExportPackage(
                final_dir=final_dir,
                staging_dir=staging_dir or final_parent / ".uncreated-staging",
                backup_dir=backup_dir or final_parent / ".uncreated-backup",
                work_dir=work_dir or final_parent / ".uncreated-work",
                tasks=(),
                manifest=(),
                epconfig_bytes=b"",
                final_generation=final_generation,
                lock_path=lock_path,
                lock_token=lock_token,
                lock_identity=lock_identity,
                final_dir_identity=final_dir_identity,
                staging_dir_identity=staging_dir_identity,
                backup_dir_identity=backup_dir_identity,
                work_dir_identity=work_dir_identity,
            )
            try:
                if staging_dir is not None:
                    ExportWorker._remove_tree(
                        temporary, staging_dir, f".{final_dir.name}.staging-"
                    )
            finally:
                ExportWorker._release_package_lock(temporary)
            raise

    @staticmethod
    def _snapshot_tasks(
        staging_dir: Path,
        logo_mat: Optional[np.ndarray],
        overlay_mat: Optional[np.ndarray],
        loop_render_session: Optional[RenderSession],
        intro_render_session: Optional[RenderSession],
        aux_images: Optional[list],
    ) -> tuple[ExportTask, ...]:
        tasks: list[ExportTask] = []

        def add_image(export_type: ExportType, filename: str, mat: np.ndarray) -> None:
            _validated_relative_path(filename)
            tasks.append(
                ExportTask(export_type, filename, np.ascontiguousarray(mat).copy())
            )

        if logo_mat is not None:
            add_image(ExportType.ICON, "icon.png", logo_mat)
        if overlay_mat is not None:
            add_image(ExportType.OVERLAY, "overlay.argb", overlay_mat)
        for filename, mat in aux_images or []:
            if mat is not None:
                add_image(ExportType.AUX_IMAGE, filename, mat)
        for export_type, filename, session in (
            (ExportType.LOOP_VIDEO, "loop.mp4", loop_render_session),
            (ExportType.INTRO_VIDEO, "intro.mp4", intro_render_session),
        ):
            if session is not None:
                tasks.append(
                    ExportTask(
                        export_type,
                        filename,
                        ExportService._snapshot_session_job(staging_dir, session),
                    )
                )
        return tuple(tasks)

    @staticmethod
    def _snapshot_session_job(staging_dir: Path, session: RenderSession) -> RenderSession:
        jobs_dir = staging_dir / ".assetmaker-work" / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        target = jobs_dir / f"{session.track}-{session.epoch}.json"
        if target.exists():
            raise RuntimeError(f"导出 job snapshot 已存在: {target}")
        try:
            source_bytes = Path(session.job_path).read_bytes()
        except OSError as exc:
            raise RuntimeError("导出 job snapshot 的源文件无法读取") from exc
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        if source_sha256 != session.job_sha256:
            raise RuntimeError("导出 job snapshot 前冻结 job 内容 hash 已变化")
        atomic_write_bytes(target, source_bytes)
        try:
            snapshot_sha256 = compute_job_sha256(target)
        except OSError as exc:
            raise RuntimeError("导出 job snapshot 无法复核") from exc
        if snapshot_sha256 != source_sha256:
            raise RuntimeError("导出 job snapshot 写入后的内容 hash 不一致")
        try:
            target.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
        except OSError:
            logger.warning("Unable to mark export job read-only: %s", target)
        job = load_render_job(target, for_export=True)
        if (
            job.api_version != session.selection.api_version
            or job.epoch != session.epoch
            or job.track != session.track
        ):
            raise RuntimeError("导出 job snapshot 与 RenderSession identity 不一致")
        return replace(
            session,
            job_path=str(target),
            job_sha256=snapshot_sha256,
        )

    @staticmethod
    def _build_manifest(
        tasks: tuple[ExportTask, ...], epconfig_bytes: bytes
    ) -> tuple[_ArtifactExpectation, ...]:
        expectations: list[_ArtifactExpectation] = []
        seen: set[str] = set()
        for task in tasks:
            relative_path = _validated_relative_path(task.output_path)
            if relative_path.casefold() in seen:
                raise RuntimeError(f"导出 manifest 有重复工件: {relative_path}")
            seen.add(relative_path.casefold())
            kind: Literal["png", "argb", "mp4", "json"]
            exact_size = None
            if task.export_type in (ExportType.ICON, ExportType.AUX_IMAGE):
                kind = "png"
            elif task.export_type in (ExportType.LOGO, ExportType.OVERLAY):
                kind = "argb"
                mat = np.asarray(task.data)
                exact_size = int(mat.shape[0] * mat.shape[1] * 4)
            else:
                kind = "mp4"
            expectations.append(_ArtifactExpectation(relative_path, kind, exact_size))
        payload = json.loads(epconfig_bytes.decode("utf-8"))
        references = _epconfig_references(payload)
        expected_paths = {item.relative_path for item in expectations}
        missing = references - expected_paths
        if missing:
            raise RuntimeError(f"epconfig.json 引用了未导出的文件: {sorted(missing)}")
        if "epconfig.json" in {item.relative_path for item in expectations}:
            raise RuntimeError("epconfig.json 不能作为普通导出任务")
        expectations.append(
            _ArtifactExpectation(
                "epconfig.json",
                "json",
                exact_size=len(epconfig_bytes),
                expected_sha256=hashlib.sha256(epconfig_bytes).hexdigest(),
            )
        )
        return tuple(expectations)

    def cancel(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()

    @pyqtSlot(int, str)
    def _on_worker_progress(self, progress: int, message: str) -> None:
        if self.sender() is self._worker:
            self.progress_updated.emit(progress, message)

    @pyqtSlot(str)
    def _on_worker_completed(self, message: str) -> None:
        self._store_worker_terminal(True, message)

    @pyqtSlot(str)
    def _on_worker_failed(self, message: str) -> None:
        self._store_worker_terminal(False, message)

    def _store_worker_terminal(self, succeeded: bool, message: str) -> None:
        worker = self.sender()
        if worker is not self._worker:
            logger.debug("Ignoring terminal result from a stale export worker")
            return
        if self._worker_terminal is not None:
            logger.warning("Ignoring duplicate terminal result from export worker")
            return
        self._worker_terminal = (succeeded, message)

    @pyqtSlot()
    def _on_worker_finished(self) -> None:
        worker = self.sender()
        if worker is not self._worker:
            logger.debug("Ignoring finished signal from a stale export worker")
            return

        terminal = self._worker_terminal
        self._worker_terminal = None
        self._worker = None
        worker.deleteLater()
        if terminal is None:
            self.export_failed.emit("Export worker exited without a terminal result")
            return
        succeeded, message = terminal
        if succeeded:
            self.export_completed.emit(message)
        else:
            self.export_failed.emit(message)
