"""仅供独立 worker 使用的 portable VapourSynth loader。"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

from config.vs_runtime import VSRuntimeConfig
from resources.vapoursynth.python.assetmaker_vs.runtime_fingerprint import (
    RuntimeFingerprintError,
    compute_runtime_fingerprint as _compute_portable_runtime_fingerprint,
)


_load_lock = threading.Lock()
_loaded_module: Any | None = None
_dll_directory_handle: Any | None = None
_resource_baseline: Any | None = None
_native_plugin_state: Any | None = None


class VSLoaderError(RuntimeError):
    """portable VapourSynth runtime 缺失或无法加载。"""


def _validated_runtime(runtime: VSRuntimeConfig | dict[str, Any]) -> VSRuntimeConfig:
    if isinstance(runtime, VSRuntimeConfig):
        return VSRuntimeConfig.from_dict(runtime.to_dict())
    return VSRuntimeConfig.from_dict(runtime)


def compute_runtime_fingerprint(
    app_dir: str | os.PathLike[str],
    runtime: VSRuntimeConfig | dict[str, Any],
) -> str:
    """哈希规范指定的 runtime JSON、portable core 与代码/插件文件。"""
    config = _validated_runtime(runtime)
    try:
        return _compute_portable_runtime_fingerprint(app_dir, config.to_dict())
    except RuntimeFingerprintError as exc:
        raise VSLoaderError(str(exc)) from exc


def load_vapoursynth(
    app_dir: str | os.PathLike[str],
    runtime: VSRuntimeConfig | dict[str, Any],
) -> Any:
    """从应用 portable tree 显式加载 VS；调用者必须是 worker。"""
    global _dll_directory_handle, _loaded_module, _native_plugin_state, _resource_baseline
    config = _validated_runtime(runtime)
    media_dir = Path(app_dir).resolve() / "tools" / "media"
    pyd = media_dir / "vapoursynth.pyd"
    if sys.version_info < (3, 12):
        raise VSLoaderError("portable VapourSynth 需要 Python 3.12+")
    if not pyd.is_file():
        raise VSLoaderError(f"portable VapourSynth binding 不存在: {pyd}")

    with _load_lock:
        if _loaded_module is not None:
            return _loaded_module
        existing = sys.modules.get("vapoursynth")
        if existing is not None:
            raise VSLoaderError("worker 启动前已意外导入 vapoursynth")
        # P3：R79 会把该变量当作单一目录自动加载。配置目录只能在 binding
        # import 完成后由共享策略逐个 LoadAllPlugins，不能让 R79 先隐式加载。
        os.environ["VAPOURSYNTH_EXTRA_PLUGIN_PATH"] = ""
        os.environ["ASSETMAKER_VS_PYTHON_DIRS_JSON"] = json.dumps(
            list(config.plugins.python_module_dirs),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            _dll_directory_handle = os.add_dll_directory(str(media_dir))
            spec = importlib.util.spec_from_file_location("vapoursynth", str(pyd))
            if spec is None or spec.loader is None:
                raise VSLoaderError(f"无法为 {pyd} 创建 import spec")
            module = importlib.util.module_from_spec(spec)
            sys.modules["vapoursynth"] = module
            spec.loader.exec_module(module)
            from resources.vapoursynth.python.assetmaker_vs.core_resources import (
                CoreResourceBaseline,
            )
            from resources.vapoursynth.python.assetmaker_vs.native_plugins import (
                configure_native_plugins,
            )

            _native_plugin_state = configure_native_plugins(
                module.core,
                config.plugins.native_plugin_dirs,
                builtin_plugin_dirs=(
                    media_dir / "vs-plugins",
                    media_dir / "vs-coreplugins",
                ),
            )
            _resource_baseline = CoreResourceBaseline.capture(module.core)
        except VSLoaderError:
            sys.modules.pop("vapoursynth", None)
            raise
        except BaseException as exc:
            sys.modules.pop("vapoursynth", None)
            raise VSLoaderError(f"加载 portable VapourSynth 失败: {exc}") from exc
        _loaded_module = module
        return module


def restore_vapoursynth_resources(
    module: Any, runtime: VSRuntimeConfig | dict[str, Any]
) -> None:
    """每次执行用户脚本前恢复首次捕获的 core 基线或冻结配置。"""
    if _resource_baseline is None:
        raise VSLoaderError("VapourSynth core 资源基线尚未捕获")
    config = _validated_runtime(runtime)
    from resources.vapoursynth.python.assetmaker_vs.core_resources import (
        CoreResourceError,
        restore_core_resources,
    )

    try:
        restore_core_resources(module.core, config.core, _resource_baseline)
    except CoreResourceError as error:
        raise VSLoaderError(str(error)) from error


def verify_vapoursynth_native_plugins(
    module: Any,
    runtime: VSRuntimeConfig | dict[str, Any],
    requirements: list[str],
) -> None:
    """在用户脚本执行前核验 required callable 与实际 native plugin 来源。"""
    if _native_plugin_state is None:
        raise VSLoaderError("VapourSynth native plugin 状态尚未捕获")
    config = _validated_runtime(runtime)
    from resources.vapoursynth.python.assetmaker_vs.native_plugins import (
        canonical_native_plugin_dirs,
    )

    configured = canonical_native_plugin_dirs(config.plugins.native_plugin_dirs)
    if configured != _native_plugin_state.configured_dirs:
        raise VSLoaderError("VapourSynth native plugin 配置与加载状态不一致")
    from resources.vapoursynth.python.assetmaker_vs.native_plugins import (
        NativePluginPolicyError,
        verify_native_plugin_requirements,
    )

    try:
        verify_native_plugin_requirements(module.core, _native_plugin_state, requirements)
    except NativePluginPolicyError as error:
        raise VSLoaderError(str(error)) from error


__all__ = [
    "VSLoaderError",
    "compute_runtime_fingerprint",
    "load_vapoursynth",
    "restore_vapoursynth_resources",
    "verify_vapoursynth_native_plugins",
]
