"""Worker 与固定 VSPipe runner 共用的 VS runtime 身份校验。"""

from __future__ import annotations

import hashlib
import hmac
import json
import ntpath
import os
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


RUNTIME_CONFIG_ENV = "ASSETMAKER_VS_RUNTIME_CONFIG_JSON"
RUNTIME_FINGERPRINT_ENV = "ASSETMAKER_VS_RUNTIME_FINGERPRINT"
RUNTIME_APP_DIR_ENV = "ASSETMAKER_VS_APP_DIR"
PYTHON_DIRS_ENV = "ASSETMAKER_VS_PYTHON_DIRS_JSON"
RUNTIME_MEDIA_ROOT_ENV = "ASSETMAKER_VS_MEDIA_ROOT"
_CODE_SUFFIXES = frozenset({".py", ".pyc", ".pyd", ".dll", ".zip", ".whl"})
_PORTABLE_CORE_FILES = ("vapoursynth.pyd", "vapoursynth.dll", "portable.vs")
_RUNTIME_ERROR_MARKER = "ASSETMAKER_VS_ERROR:"


def _canonical_error_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _canonical_windows_path_key(value: str | os.PathLike[str]) -> str:
    """比较跨 host/portable Python 传递的同一 Windows 路径。"""
    return ntpath.normcase(ntpath.normpath(os.fspath(value).replace("/", "\\")))


class RuntimeFingerprintError(RuntimeError):
    """运行时环境与 RenderSession 身份不一致。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "runtime.invalid",
        field: str = "runtime",
        expected: Any = None,
        actual: Any = None,
        hint: str = "重新创建 RenderSession 并使用应用携带的 portable media runtime。",
    ) -> None:
        self.payload = {
            "code": code,
            "field": field,
            "expected": expected,
            "actual": actual,
            "hint": hint,
            "message": message,
        }
        super().__init__(message)

    def __str__(self) -> str:
        return _RUNTIME_ERROR_MARKER + _canonical_error_json(self.payload)


@dataclass(frozen=True)
class VerifiedRuntime:
    """runner 可读取但不可修改的 frozen runtime 与重算后的身份。"""

    runtime: Mapping[str, Any]
    fingerprint: str


def _record(digest: Any, label: str, data: bytes) -> None:
    encoded = label.encode("utf-8")
    digest.update(len(encoded).to_bytes(4, "big"))
    digest.update(encoded)
    digest.update(len(data).to_bytes(8, "big"))
    digest.update(data)


def _file(digest: Any, label: str, path: Path) -> None:
    try:
        size = path.stat().st_size
        encoded = label.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(size.to_bytes(8, "big"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise RuntimeFingerprintError(f"无法读取 runtime 文件 {path}: {exc}") from exc


def _code_files(root: Path) -> list[Path]:
    try:
        if not root.is_dir():
            raise RuntimeFingerprintError(f"runtime 代码目录不存在: {root}")
        files = [
            path for path in root.rglob("*")
            # __pycache__ 是解释器可再生的派生缓存，不是发行 runtime 的输入。
            # 顶层/非缓存目录的 .pyc 仍可能是实际随包发布的字节码，必须哈希。
            if path.is_file()
            and path.suffix.casefold() in _CODE_SUFFIXES
            and not any(
                part.casefold() == "__pycache__"
                for part in path.relative_to(root).parts
            )
        ]
    except OSError as exc:
        raise RuntimeFingerprintError(f"无法遍历 runtime 代码目录 {root}: {exc}") from exc
    return sorted(files, key=lambda path: path.relative_to(root).as_posix().casefold())


def canonical_runtime_json_bytes(runtime: Mapping[str, Any]) -> bytes:
    """编码唯一的跨 worker/VSPipe runtime JSON ABI。"""
    if not isinstance(runtime, Mapping):
        raise RuntimeFingerprintError(
            "runtime 配置必须是对象",
            code="runtime.config_type",
            field=RUNTIME_CONFIG_ENV,
            expected="JSON object",
            actual=type(runtime).__name__,
        )
    try:
        return json.dumps(
            dict(runtime), ensure_ascii=False, allow_nan=False,
            separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RuntimeFingerprintError(
            f"runtime 配置无法规范化: {exc}",
            code="runtime.config_canonicalization",
            field=RUNTIME_CONFIG_ENV,
            expected="可规范化的 JSON object",
            actual=type(runtime).__name__,
        ) from exc


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeFingerprintError(
            "runtime 配置字段必须是对象",
            code="runtime.config_schema",
            field=field,
            expected="object",
            actual=type(value).__name__,
        )
    return value


def _require_exact_keys(
    value: Mapping[str, Any], field: str, keys: frozenset[str]
) -> None:
    if set(value) != keys:
        raise RuntimeFingerprintError(
            "runtime 配置字段集合不匹配",
            code="runtime.config_schema",
            field=field,
            expected=sorted(keys),
            actual=sorted(value),
        )


def _require_int(value: Any, field: str, *, minimum: int) -> None:
    if type(value) is not int or value < minimum:
        raise RuntimeFingerprintError(
            "runtime 整数字段无效",
            code="runtime.config_schema",
            field=field,
            expected=f"integer >= {minimum}",
            actual=value,
        )


def _require_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise RuntimeFingerprintError(
            "runtime 插件目录必须是字符串数组",
            code="runtime.config_schema",
            field=field,
            expected="array of strings",
            actual=value,
        )
    return value


def _validate_runtime(runtime: Any) -> Mapping[str, Any]:
    data = _require_mapping(runtime, "runtime")
    _require_exact_keys(
        data,
        "runtime",
        frozenset({"schema_version", "worker", "core", "plugins", "scripts"}),
    )
    _require_int(data["schema_version"], "schema_version", minimum=1)
    if data["schema_version"] != 1:
        raise RuntimeFingerprintError(
            "runtime schema_version 不受支持",
            code="runtime.config_schema",
            field="schema_version",
            expected=1,
            actual=data["schema_version"],
        )
    worker = _require_mapping(data["worker"], "worker")
    _require_exact_keys(
        worker,
        "worker",
        frozenset({"startup_timeout_ms", "frame_timeout_ms", "shutdown_timeout_ms"}),
    )
    for name in worker:
        _require_int(worker[name], f"worker.{name}", minimum=0)
    core = _require_mapping(data["core"], "core")
    _require_exact_keys(core, "core", frozenset({"num_threads", "max_cache_size_mb"}))
    for name in core:
        _require_int(core[name], f"core.{name}", minimum=0)
    plugins = _require_mapping(data["plugins"], "plugins")
    _require_exact_keys(
        plugins,
        "plugins",
        frozenset({"native_plugin_dirs", "python_module_dirs"}),
    )
    _require_string_list(plugins["native_plugin_dirs"], "plugins.native_plugin_dirs")
    _require_string_list(plugins["python_module_dirs"], "plugins.python_module_dirs")
    scripts = _require_mapping(data["scripts"], "scripts")
    _require_exact_keys(scripts, "scripts", frozenset({"global_script_path"}))
    if not isinstance(scripts["global_script_path"], str):
        raise RuntimeFingerprintError(
            "runtime 全局脚本路径必须是字符串",
            code="runtime.config_schema",
            field="scripts.global_script_path",
            expected="string",
            actual=scripts["global_script_path"],
        )
    return data


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def compute_runtime_fingerprint(
    app_dir: str | os.PathLike[str], runtime: Mapping[str, Any]
) -> str:
    """对 portable core、默认/配置插件和 helper 源码执行稳定 SHA-256。"""
    root = Path(app_dir).resolve()
    media_dir = root / "tools" / "media"
    data = _validate_runtime(runtime)
    plugins = data["plugins"]
    native_dirs = _require_string_list(
        plugins["native_plugin_dirs"], "plugins.native_plugin_dirs"
    )
    python_dirs = _require_string_list(
        plugins["python_module_dirs"], "plugins.python_module_dirs"
    )

    digest = hashlib.sha256()
    _record(digest, "runtime.json", canonical_runtime_json_bytes(data))
    for filename in _PORTABLE_CORE_FILES:
        path = media_dir / filename
        if not path.is_file():
            raise RuntimeFingerprintError(f"portable VapourSynth 文件不存在: {path}")
        _file(digest, f"portable/{filename.casefold()}", path)

    directories: list[tuple[str, Path]] = [
        ("default-plugins", media_dir / "vs-plugins"),
        (
            "assetmaker-vs",
            root / "resources" / "vapoursynth" / "python" / "assetmaker_vs",
        ),
    ]
    coreplugins = media_dir / "vs-coreplugins"
    # coreplugins 是 portable runtime 的可选目录：缺失不改变旧安装行为，存在
    # 时却会被 native policy 许可，必须成为 session fingerprint 的输入。
    if coreplugins.is_dir():
        directories.append(("default-coreplugins", coreplugins))
    directories.extend(
        (f"native-{index}", Path(path).resolve())
        for index, path in enumerate(native_dirs)
    )
    directories.extend(
        (f"python-{index}", Path(path).resolve())
        for index, path in enumerate(python_dirs)
    )
    for category, directory in directories:
        for path in _code_files(directory):
            _file(
                digest,
                f"{category}/{path.relative_to(directory).as_posix()}",
                path,
            )
    return digest.hexdigest()


def verify_runtime_from_env(
    environment: Mapping[str, str] | None = None,
) -> VerifiedRuntime:
    """runner 在执行用户脚本前复算并核验它实际继承的 runtime 环境。"""
    env = os.environ if environment is None else environment
    raw_runtime = env.get(RUNTIME_CONFIG_ENV, "")
    expected = env.get(RUNTIME_FINGERPRINT_ENV, "")
    app_dir = env.get(RUNTIME_APP_DIR_ENV, "")
    try:
        runtime = json.loads(raw_runtime)
    except json.JSONDecodeError as exc:
        raise RuntimeFingerprintError(
            f"{RUNTIME_CONFIG_ENV} 不是合法 JSON",
            code="runtime.config_json",
            field=RUNTIME_CONFIG_ENV,
            expected="canonical JSON object",
            actual=raw_runtime,
        ) from exc
    runtime = _validate_runtime(runtime)
    if not isinstance(expected, str) or len(expected) != 64 or not app_dir:
        raise RuntimeFingerprintError(
            "缺少预期 runtime fingerprint 或 app_dir",
            code="runtime.identity_env",
            field=RUNTIME_FINGERPRINT_ENV if not expected else RUNTIME_APP_DIR_ENV,
            expected="SHA-256 fingerprint and absolute app_dir",
            actual=expected if not expected else app_dir,
        )
    plugins = _require_mapping(runtime["plugins"], "plugins")
    python_dirs = [
        str(Path(path))
        for path in _require_string_list(
            plugins["python_module_dirs"], "plugins.python_module_dirs"
        )
    ]
    if env.get("VAPOURSYNTH_EXTRA_PLUGIN_PATH", "") != "":
        raise RuntimeFingerprintError(
            "VSPipe native plugin 环境不得启用隐式 autoload",
            code="runtime.native_plugin_env",
            field="VAPOURSYNTH_EXTRA_PLUGIN_PATH",
            expected="",
            actual=env.get("VAPOURSYNTH_EXTRA_PLUGIN_PATH", ""),
        )
    if env.get(PYTHON_DIRS_ENV, "") != json.dumps(
        python_dirs, ensure_ascii=False, separators=(",", ":")
    ):
        raise RuntimeFingerprintError(
            "VSPipe Python plugin 环境与冻结 runtime 不一致",
            code="runtime.python_plugin_env",
            field=PYTHON_DIRS_ENV,
            expected=json.dumps(python_dirs, ensure_ascii=False, separators=(",", ":")),
            actual=env.get(PYTHON_DIRS_ENV, ""),
        )
    expected_media_root = str(Path(app_dir) / "tools" / "media")
    actual_media_root = env.get(RUNTIME_MEDIA_ROOT_ENV, "")
    if (
        not actual_media_root
        or _canonical_windows_path_key(actual_media_root)
        != _canonical_windows_path_key(expected_media_root)
    ):
        raise RuntimeFingerprintError(
            "VSPipe media root 与 app runtime root 不一致",
            code="runtime.media_root",
            field=RUNTIME_MEDIA_ROOT_ENV,
            expected=expected_media_root,
            actual=actual_media_root,
            hint="只允许使用 app_dir/tools/media/VSPipe.exe 启动固定 runner。",
        )
    actual = compute_runtime_fingerprint(app_dir, runtime)
    if not hmac.compare_digest(actual, expected):
        raise RuntimeFingerprintError(
            "VSPipe runtime fingerprint 与 RenderSession 不一致",
            code="runtime.fingerprint_mismatch",
            field=RUNTIME_FINGERPRINT_ENV,
            expected=expected,
            actual=actual,
            hint="runtime 配置或其覆盖的 portable/plugin/helper 代码已变化；请重新预检。",
        )
    return VerifiedRuntime(runtime=_freeze_json(runtime), fingerprint=actual)


__all__ = [
    "RUNTIME_APP_DIR_ENV",
    "RUNTIME_CONFIG_ENV",
    "RUNTIME_FINGERPRINT_ENV",
    "RUNTIME_MEDIA_ROOT_ENV",
    "RuntimeFingerprintError",
    "VerifiedRuntime",
    "canonical_runtime_json_bytes",
    "compute_runtime_fingerprint",
    "verify_runtime_from_env",
]
