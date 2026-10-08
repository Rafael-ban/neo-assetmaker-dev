"""RenderJob JSON 的便携、唯一 wire 解析实现。"""

from __future__ import annotations

import json
import ntpath
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import AssetmakerVSError


PYTHON_DIRS_ENV = "ASSETMAKER_VS_PYTHON_DIRS_JSON"
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:\\")
_INVALID_COMPONENT_CHARS = frozenset('<>:"|?*')
_RESERVED_DEVICE_RE = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])$",
    re.IGNORECASE,
)
_PROFILE_OUTPUTS: dict[str, dict[str, Any]] = {
    "360x640": {
        "profile": "360x640",
        "display_width": 360,
        "display_height": 640,
        "coded_width": 360,
        "coded_height": 640,
        "pixel_format": "YUV420P8",
        "matrix": "170m",
        "transfer": "170m",
        "primaries": "170m",
        "range": "limited",
        "final_rotate_180": False,
    },
    "720x1080": {
        "profile": "720x1080",
        "display_width": 720,
        "display_height": 1080,
        "coded_width": 720,
        "coded_height": 1080,
        "pixel_format": "YUV420P8",
        "matrix": "170m",
        "transfer": "170m",
        "primaries": "170m",
        "range": "limited",
        "final_rotate_180": False,
    },
}


class JobAPIError(AssetmakerVSError):
    """RenderJob 文件、字段或阶段语义错误。"""


def _fail(
    message: str,
    *,
    code: str,
    field: str,
    expected: Any = None,
    actual: Any = None,
) -> None:
    raise JobAPIError(
        message,
        code=code,
        field=field,
        expected=expected,
        actual=actual,
    )


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(
            f"{field} 必须是对象",
            code="job.type",
            field=field,
            expected="object",
            actual=type(value).__name__,
        )
    return value


def _keys(data: dict[str, Any], required: set[str], field: str) -> None:
    unknown = sorted(set(data) - required)
    missing = sorted(required - set(data))
    if unknown:
        _fail(
            f"{field} 包含未知字段: {', '.join(unknown)}",
            code="job.unknown",
            field=f"{field}.{unknown[0]}" if field != "job" else unknown[0],
            expected=sorted(required),
            actual=unknown,
        )
    if missing:
        _fail(
            f"{field} 缺少必需字段: {', '.join(missing)}",
            code="job.required",
            field=f"{field}.{missing[0]}" if field != "job" else missing[0],
            expected=missing,
        )


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _fail(
            f"{field} 必须是 >= {minimum} 的整数",
            code="job.integer",
            field=field,
            expected=f"integer >= {minimum}",
            actual=value,
        )
    return value


def _is_valid_component(component: str) -> bool:
    if (
        not component
        or component in (".", "..")
        or component.endswith((".", " "))
        or any(ord(char) < 32 for char in component)
        or any(char in _INVALID_COMPONENT_CHARS for char in component)
    ):
        return False
    device_stem = component.split(".", 1)[0]
    return _RESERVED_DEVICE_RE.fullmatch(device_stem) is None


def is_canonical_windows_absolute_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "/" in value or "\0" in value:
        return False
    if value.startswith(("\\\\?\\", "\\\\.\\")):
        return False
    if _DRIVE_PREFIX_RE.match(value):
        components = value[3:].split("\\") if len(value) > 3 else []
    elif value.startswith("\\\\"):
        components = value[2:].split("\\")
        if len(components) < 2:
            return False
    else:
        return False
    return (
        not any(not _is_valid_component(item) for item in components)
        and ntpath.normpath(value) == value
    )


def _path(value: Any, field: str) -> str:
    if not is_canonical_windows_absolute_path(value):
        _fail(
            f"{field} 必须是 canonical Windows drive/UNC 绝对路径: {value!r}",
            code="job.path",
            field=field,
            expected="canonical Windows drive/UNC absolute path",
            actual=value,
        )
    return value


def output_for_profile(profile: str) -> dict[str, Any]:
    try:
        return deepcopy(_PROFILE_OUTPUTS[profile])
    except (KeyError, TypeError):
        _fail(
            f"未知 output.profile: {profile!r}",
            code="job.output.profile",
            field="output.profile",
            expected=sorted(_PROFILE_OUTPUTS),
            actual=profile,
        )
    raise AssertionError("unreachable")


def _validate_fps(value: Any) -> dict[str, Any]:
    data = _object(value, "timeline.fps")
    _keys(data, {"numerator", "denominator"}, "timeline.fps")
    _integer(data["numerator"], "timeline.fps.numerator", minimum=1)
    _integer(data["denominator"], "timeline.fps.denominator", minimum=1)
    return data


def _validate_source(value: Any) -> dict[str, Any]:
    data = _object(value, "source")
    _keys(data, {"path", "kind", "virtual_frame_count"}, "source")
    _path(data["path"], "source.path")
    kind = data["kind"]
    if kind not in ("video", "image"):
        _fail(
            f"未知 source.kind: {kind!r}",
            code="job.source.kind",
            field="source.kind",
            expected=["video", "image"],
            actual=kind,
        )
    count = data["virtual_frame_count"]
    if kind == "video" and count is not None:
        _fail(
            "video 的 source.virtual_frame_count 必须为 null",
            code="job.source.virtual_frame_count",
            field="source.virtual_frame_count",
            expected=None,
            actual=count,
        )
    if kind == "image":
        _integer(count, "source.virtual_frame_count", minimum=1)
    return data


def _validate_timeline(value: Any) -> dict[str, Any]:
    data = _object(value, "timeline")
    _keys(data, {"start_frame", "end_frame", "fps"}, "timeline")
    start = _integer(data["start_frame"], "timeline.start_frame")
    end = data["end_frame"]
    if end is not None:
        _integer(end, "timeline.end_frame", minimum=1)
        if end <= start:
            _fail(
                "resolved timeline.end_frame 必须大于 start_frame",
                code="job.timeline.order",
                field="timeline.end_frame",
                expected=f"> {start}",
                actual=end,
            )
    if data["fps"] is not None:
        _validate_fps(data["fps"])
    return data


def _validate_crop(value: Any) -> dict[str, Any]:
    data = _object(value, "transform.crop")
    fields = {"coordinate_space", "x", "y", "width", "height"}
    _keys(data, fields, "transform.crop")
    space = data["coordinate_space"]
    if space != "post_rotation_source_pixels":
        _fail(
            f"未知 crop.coordinate_space: {space!r}",
            code="job.crop.coordinate_space",
            field="transform.crop.coordinate_space",
            expected="post_rotation_source_pixels",
            actual=space,
        )
    for name in ("x", "y", "width", "height"):
        _integer(data[name], f"transform.crop.{name}")
    full_frame = data["width"] == 0 and data["height"] == 0
    explicit = data["width"] > 0 and data["height"] > 0
    if not (full_frame or explicit):
        _fail(
            "crop width/height 必须同时为 0，或同时为正整数",
            code="job.crop.size",
            field="transform.crop.width",
            expected="both zero or both positive",
            actual=[data["width"], data["height"]],
        )
    return data


def _validate_transform(value: Any) -> dict[str, Any]:
    data = _object(value, "transform")
    _keys(data, {"rotation", "crop"}, "transform")
    rotation = data["rotation"]
    if type(rotation) is not int or rotation not in (0, 90, 180, 270):
        _fail(
            f"未知 transform.rotation: {rotation!r}",
            code="job.transform.rotation",
            field="transform.rotation",
            expected=[0, 90, 180, 270],
            actual=rotation,
        )
    _validate_crop(data["crop"])
    return data


def _validate_output(value: Any) -> dict[str, Any]:
    data = _object(value, "output")
    fields = {
        "profile",
        "display_width",
        "display_height",
        "coded_width",
        "coded_height",
        "pixel_format",
        "matrix",
        "transfer",
        "primaries",
        "range",
        "final_rotate_180",
    }
    _keys(data, fields, "output")
    expected = output_for_profile(data["profile"])
    for name in ("display_width", "display_height", "coded_width", "coded_height"):
        _integer(data[name], f"output.{name}", minimum=1)
    geometry_fields = (
        "display_width",
        "display_height",
        "coded_width",
        "coded_height",
        "final_rotate_180",
    )
    if any(data[name] != expected[name] for name in geometry_fields):
        _fail(
            f"output 几何参数与 profile {data['profile']!r} 不一致",
            code="job.output.geometry",
            field="output.profile",
            expected={name: expected[name] for name in geometry_fields},
            actual={name: data[name] for name in geometry_fields},
        )
    if not isinstance(data["final_rotate_180"], bool):
        _fail(
            "output.final_rotate_180 必须是布尔值",
            code="job.output.final_rotate_180",
            field="output.final_rotate_180",
            expected="boolean",
            actual=data["final_rotate_180"],
        )
    if data["pixel_format"] != "YUV420P8":
        _fail(
            f"未知 output.pixel_format: {data['pixel_format']!r}",
            code="job.output.pixel_format",
            field="output.pixel_format",
            expected="YUV420P8",
            actual=data["pixel_format"],
        )
    for name in ("matrix", "transfer", "primaries"):
        if not isinstance(data[name], str) or not data[name]:
            _fail(
                f"output.{name} 必须是非空字符串",
                code=f"job.output.{name}",
                field=f"output.{name}",
                expected="non-empty string",
                actual=data[name],
            )
    if data["range"] not in ("limited", "full"):
        _fail(
            f"未知 output.range: {data['range']!r}",
            code="job.output.range",
            field="output.range",
            expected=["limited", "full"],
            actual=data["range"],
        )
    return data


def _validate_paths(value: Any) -> dict[str, Any]:
    data = _object(value, "paths")
    _keys(data, {"cache_dir"}, "paths")
    _path(data["cache_dir"], "paths.cache_dir")
    return data


def validate_job_section(section: str, value: Any) -> dict[str, Any]:
    validators = {
        "fps": _validate_fps,
        "source": _validate_source,
        "timeline": _validate_timeline,
        "crop": _validate_crop,
        "transform": _validate_transform,
        "output": _validate_output,
        "paths": _validate_paths,
    }
    try:
        validator = validators[section]
    except KeyError as exc:
        raise ValueError(f"unknown job section: {section}") from exc
    return deepcopy(validator(deepcopy(value)))


def validate_job_payload(
    value: Any, *, for_export: bool = False
) -> dict[str, Any]:
    data = deepcopy(_object(value, "job"))
    fields = {
        "api_version",
        "epoch",
        "track",
        "project_root",
        "source",
        "timeline",
        "transform",
        "output",
        "paths",
    }
    _keys(data, fields, "job")
    if type(data["api_version"]) is not int or data["api_version"] != 1:
        _fail(
            f"未知 api_version: {data['api_version']!r}",
            code="job.api_version",
            field="api_version",
            expected=1,
            actual=data["api_version"],
        )
    _integer(data["epoch"], "epoch")
    if data["track"] not in ("loop", "intro"):
        _fail(
            f"未知 track: {data['track']!r}",
            code="job.track",
            field="track",
            expected=["loop", "intro"],
            actual=data["track"],
        )
    _path(data["project_root"], "project_root")
    source = _validate_source(data["source"])
    timeline = _validate_timeline(data["timeline"])
    _validate_transform(data["transform"])
    _validate_output(data["output"])
    _validate_paths(data["paths"])
    if source["kind"] == "image":
        end = timeline["end_frame"]
        fps = timeline["fps"]
        count = source["virtual_frame_count"]
        if end is None or fps is None:
            _fail(
                "image timeline 的 end_frame/fps 必须已解析",
                code="job.image.timeline",
                field="timeline.end_frame",
                expected="resolved image timeline",
                actual={"end_frame": end, "fps": fps},
            )
        start = timeline["start_frame"]
        if not (0 <= start < end <= count):
            _fail(
                "image timeline 必须满足 0 <= start < end <= virtual_frame_count",
                code="job.image.timeline_range",
                field="timeline.end_frame",
                expected=f"{start} < end <= {count}",
                actual=end,
            )
    if for_export and (
        timeline["end_frame"] is None or timeline["fps"] is None
    ):
        _fail(
            "导出作业必须解析 timeline.end_frame 和 fps",
            code="job.export.timeline",
            field="timeline.end_frame",
            expected="resolved export timeline",
            actual={
                "end_frame": timeline["end_frame"],
                "fps": timeline["fps"],
            },
        )
    return data


def load_job(
    path: str | os.PathLike[str], *, for_export: bool = False
) -> dict[str, Any]:
    source_path = Path(path)
    absolute = str(source_path.resolve())
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise JobAPIError(
            str(exc),
            code="job.io",
            field="job",
            path=absolute,
            actual=str(exc),
        ) from exc
    try:
        return validate_job_payload(payload, for_export=for_export)
    except JobAPIError as exc:
        raise exc.with_path(absolute) from exc


def runtime_python_dirs_from_env() -> tuple[Path, ...]:
    raw = os.environ.get(PYTHON_DIRS_ENV, "[]")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise JobAPIError(
            f"{PYTHON_DIRS_ENV} 不是合法 JSON: {exc}",
            code="runtime.python_dirs",
            field=PYTHON_DIRS_ENV,
            actual=raw,
        ) from exc
    if not isinstance(payload, list) or any(
        not isinstance(item, str) or not item for item in payload
    ):
        raise JobAPIError(
            f"{PYTHON_DIRS_ENV} 必须是非空字符串数组",
            code="runtime.python_dirs",
            field=PYTHON_DIRS_ENV,
            expected="string array",
            actual=payload,
        )
    result: list[Path] = []
    seen: set[str] = set()
    for item in payload:
        resolved = Path(item).resolve()
        key = os.path.normcase(str(resolved))
        if key not in seen:
            seen.add(key)
            result.append(resolved)
    return tuple(result)


__all__ = [
    "JobAPIError",
    "PYTHON_DIRS_ENV",
    "is_canonical_windows_absolute_path",
    "load_job",
    "output_for_profile",
    "runtime_python_dirs_from_env",
    "validate_job_payload",
    "validate_job_section",
]
