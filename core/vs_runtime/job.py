"""不可变 RenderJob 模型；wire 解析与校验由便携 helper 唯一实现。"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NoReturn

from core.file_utils import atomic_write_json
from resources.vapoursynth.python.assetmaker_vs import job_api as _wire


class RenderJobError(ValueError):
    """应用侧 RenderJob 错误，保留共享 ABI 的机器字段。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "job.adapter",
        field: str | None = None,
        path: str | None = None,
        expected: Any = None,
        actual: Any = None,
        hint: str | None = None,
    ) -> None:
        self.code = code
        self.field = field
        self.path = path
        self.expected = expected
        self.actual = actual
        self.hint = hint
        super().__init__(str(message))


def _raise_adapter(exc: _wire.JobAPIError) -> NoReturn:
    raise RenderJobError(
        str(exc),
        code=exc.code,
        field=exc.field,
        path=exc.path,
        expected=exc.expected,
        actual=exc.actual,
        hint=exc.hint,
    ) from exc


def _validate_section(section: str, payload: Any) -> None:
    try:
        _wire.validate_job_section(section, payload)
    except _wire.JobAPIError as exc:
        _raise_adapter(exc)


@dataclass(frozen=True)
class RationalFPS:
    numerator: int
    denominator: int

    def validate(self) -> None:
        _validate_section("fps", self.to_dict())

    def to_dict(self) -> dict[str, int]:
        return {
            "numerator": self.numerator,
            "denominator": self.denominator,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "RationalFPS":
        try:
            data = _wire.validate_job_section("fps", value)
        except _wire.JobAPIError as exc:
            _raise_adapter(exc)
        return cls(data["numerator"], data["denominator"])


@dataclass(frozen=True)
class SourceSpec:
    path: str
    kind: Literal["video", "image"]
    virtual_frame_count: int | None

    def validate(self) -> None:
        _validate_section("source", self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "virtual_frame_count": self.virtual_frame_count,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "SourceSpec":
        try:
            data = _wire.validate_job_section("source", value)
        except _wire.JobAPIError as exc:
            _raise_adapter(exc)
        return cls(data["path"], data["kind"], data["virtual_frame_count"])


@dataclass(frozen=True)
class TimelineSpec:
    start_frame: int
    end_frame: int | None
    fps: RationalFPS | None

    def validate(self) -> None:
        if self.fps is not None and not isinstance(self.fps, RationalFPS):
            raise RenderJobError(
                "timeline.fps 类型无效",
                code="job.type",
                field="timeline.fps",
                expected="RationalFPS or None",
                actual=type(self.fps).__name__,
            )
        _validate_section("timeline", self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "fps": None if self.fps is None else self.fps.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "TimelineSpec":
        try:
            data = _wire.validate_job_section("timeline", value)
        except _wire.JobAPIError as exc:
            _raise_adapter(exc)
        fps = None if data["fps"] is None else RationalFPS.from_dict(data["fps"])
        return cls(data["start_frame"], data["end_frame"], fps)


@dataclass(frozen=True)
class CropSpec:
    coordinate_space: Literal["post_rotation_source_pixels"]
    x: int
    y: int
    width: int
    height: int

    def validate(self) -> None:
        _validate_section("crop", self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinate_space": self.coordinate_space,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "CropSpec":
        try:
            data = _wire.validate_job_section("crop", value)
        except _wire.JobAPIError as exc:
            _raise_adapter(exc)
        return cls(
            data["coordinate_space"],
            data["x"],
            data["y"],
            data["width"],
            data["height"],
        )


@dataclass(frozen=True)
class TransformSpec:
    rotation: Literal[0, 90, 180, 270]
    crop: CropSpec

    def validate(self) -> None:
        if not isinstance(self.crop, CropSpec):
            raise RenderJobError(
                "transform.crop 类型无效",
                code="job.type",
                field="transform.crop",
                expected="CropSpec",
                actual=type(self.crop).__name__,
            )
        _validate_section("transform", self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {"rotation": self.rotation, "crop": self.crop.to_dict()}

    @classmethod
    def from_dict(cls, value: Any) -> "TransformSpec":
        try:
            data = _wire.validate_job_section("transform", value)
        except _wire.JobAPIError as exc:
            _raise_adapter(exc)
        return cls(data["rotation"], CropSpec.from_dict(data["crop"]))


@dataclass(frozen=True)
class PathSpec:
    cache_dir: str

    def validate(self) -> None:
        _validate_section("paths", self.to_dict())

    def to_dict(self) -> dict[str, str]:
        return {"cache_dir": self.cache_dir}

    @classmethod
    def from_dict(cls, value: Any) -> "PathSpec":
        try:
            data = _wire.validate_job_section("paths", value)
        except _wire.JobAPIError as exc:
            _raise_adapter(exc)
        return cls(data["cache_dir"])


@dataclass(frozen=True)
class OutputSpec:
    profile: Literal["360x640", "720x1280", "800x1280"]
    display_width: int
    display_height: int
    coded_width: int
    coded_height: int
    pixel_format: Literal["YUV420P8"] = "YUV420P8"
    matrix: str = "170m"
    transfer: str = "170m"
    primaries: str = "170m"
    range: Literal["limited", "full"] = "limited"
    final_rotate_180: bool = False

    @classmethod
    def from_profile(cls, profile: str) -> "OutputSpec":
        try:
            return cls._from_normalized(_wire.output_for_profile(profile))
        except _wire.JobAPIError as exc:
            _raise_adapter(exc)

    @classmethod
    def _from_normalized(cls, data: dict[str, Any]) -> "OutputSpec":
        return cls(
            profile=data["profile"],
            display_width=data["display_width"],
            display_height=data["display_height"],
            coded_width=data["coded_width"],
            coded_height=data["coded_height"],
            pixel_format=data["pixel_format"],
            matrix=data["matrix"],
            transfer=data["transfer"],
            primaries=data["primaries"],
            range=data["range"],
            final_rotate_180=data["final_rotate_180"],
        )

    def validate(self) -> None:
        _validate_section("output", self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "display_width": self.display_width,
            "display_height": self.display_height,
            "coded_width": self.coded_width,
            "coded_height": self.coded_height,
            "pixel_format": self.pixel_format,
            "matrix": self.matrix,
            "transfer": self.transfer,
            "primaries": self.primaries,
            "range": self.range,
            "final_rotate_180": self.final_rotate_180,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "OutputSpec":
        try:
            data = _wire.validate_job_section("output", value)
        except _wire.JobAPIError as exc:
            _raise_adapter(exc)
        return cls._from_normalized(data)


@dataclass(frozen=True)
class RenderJob:
    api_version: Literal[1]
    epoch: int
    track: Literal["loop", "intro"]
    project_root: str
    source: SourceSpec
    timeline: TimelineSpec
    transform: TransformSpec
    output: OutputSpec
    paths: PathSpec

    def _check_component_types(self) -> None:
        for value, expected_type, field in (
            (self.source, SourceSpec, "source"),
            (self.timeline, TimelineSpec, "timeline"),
            (self.transform, TransformSpec, "transform"),
            (self.output, OutputSpec, "output"),
            (self.paths, PathSpec, "paths"),
        ):
            if not isinstance(value, expected_type):
                raise RenderJobError(
                    f"{field} 类型无效",
                    code="job.type",
                    field=field,
                    expected=expected_type.__name__,
                    actual=type(value).__name__,
                )
        if self.timeline.fps is not None and not isinstance(
            self.timeline.fps, RationalFPS
        ):
            raise RenderJobError(
                "timeline.fps 类型无效",
                code="job.type",
                field="timeline.fps",
                expected="RationalFPS or None",
                actual=type(self.timeline.fps).__name__,
            )
        if not isinstance(self.transform.crop, CropSpec):
            raise RenderJobError(
                "transform.crop 类型无效",
                code="job.type",
                field="transform.crop",
                expected="CropSpec",
                actual=type(self.transform.crop).__name__,
            )

    def validate(self, *, for_export: bool = False) -> None:
        self._check_component_types()
        try:
            _wire.validate_job_payload(self.to_dict(), for_export=for_export)
        except _wire.JobAPIError as exc:
            _raise_adapter(exc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": self.api_version,
            "epoch": self.epoch,
            "track": self.track,
            "project_root": self.project_root,
            "source": self.source.to_dict(),
            "timeline": self.timeline.to_dict(),
            "transform": self.transform.to_dict(),
            "output": self.output.to_dict(),
            "paths": self.paths.to_dict(),
        }

    @classmethod
    def _from_normalized(cls, data: dict[str, Any]) -> "RenderJob":
        return cls(
            api_version=data["api_version"],
            epoch=data["epoch"],
            track=data["track"],
            project_root=data["project_root"],
            source=SourceSpec.from_dict(data["source"]),
            timeline=TimelineSpec.from_dict(data["timeline"]),
            transform=TransformSpec.from_dict(data["transform"]),
            output=OutputSpec.from_dict(data["output"]),
            paths=PathSpec.from_dict(data["paths"]),
        )

    @classmethod
    def from_dict(cls, value: Any) -> "RenderJob":
        try:
            data = _wire.validate_job_payload(value)
        except _wire.JobAPIError as exc:
            _raise_adapter(exc)
        return cls._from_normalized(data)


def load_render_job(
    path: str | os.PathLike[str], *, for_export: bool = False
) -> RenderJob:
    """从唯一 JSON 边界加载，再冻结为应用侧模型。"""
    try:
        data = _wire.load_job(path, for_export=for_export)
    except _wire.JobAPIError as exc:
        _raise_adapter(exc)
    return RenderJob._from_normalized(data)


_MOVEFILE_WRITE_THROUGH = 0x00000008
_WINDOWS_ALREADY_EXISTS = frozenset({80, 183})


def _move_file_ex_windows(source: Path, target: Path) -> None:
    """在 Windows 上原子移动完整文件且不覆盖目标。"""
    import ctypes
    from ctypes import wintypes

    move_file_ex = ctypes.WinDLL(
        "kernel32", use_last_error=True
    ).MoveFileExW
    move_file_ex.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
    )
    move_file_ex.restype = wintypes.BOOL
    if move_file_ex(str(source), str(target), _MOVEFILE_WRITE_THROUGH):
        return
    error_code = ctypes.get_last_error()
    raise OSError(error_code, ctypes.FormatError(error_code))


def _publish_complete_file(source: Path, target: Path) -> None:
    """从同目录发布完整文件且不覆盖目标。"""
    if os.name == "nt":
        _move_file_ex_windows(source, target)
        return
    os.link(source, target)
    source.unlink()


def write_render_job(job: RenderJob) -> Path:
    """原子新建 ``job-<epoch>.json``，拒绝复用旧 epoch。"""
    if not isinstance(job, RenderJob):
        raise RenderJobError("job 类型无效")
    job.validate()
    target = Path(job.paths.cache_dir) / f"job-{job.epoch}.json"
    temp_path: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".publish",
            dir=target.parent,
        )
        os.close(descriptor)
        temp_path = Path(temp_name)
        atomic_write_json(temp_path, job.to_dict(), indent=2)
        _publish_complete_file(temp_path, target)
        temp_path = None
    except FileExistsError as exc:
        raise RenderJobError(
            f"拒绝覆盖既有 RenderJob: {target.resolve()}"
        ) from exc
    except OSError as exc:
        error_code = getattr(exc, "winerror", None) or exc.errno
        if error_code in _WINDOWS_ALREADY_EXISTS:
            raise RenderJobError(
                f"拒绝覆盖既有 RenderJob: {target.resolve()}"
            ) from exc
        raise RenderJobError(f"{target.resolve()}: {exc}") from exc
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass
    return target


__all__ = [
    "CropSpec",
    "OutputSpec",
    "PathSpec",
    "RationalFPS",
    "RenderJob",
    "RenderJobError",
    "SourceSpec",
    "TimelineSpec",
    "TransformSpec",
    "load_render_job",
    "write_render_job",
]
