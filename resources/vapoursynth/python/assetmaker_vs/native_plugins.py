"""冻结 runtime 的 native VapourSynth 插件显式加载与来源校验。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping


class NativePluginPolicyError(RuntimeError):
    """native plugin 目录、来源或 requirement 不符合冻结 runtime。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class NativePluginState:
    """一次 core 初始化期间可复核的 native plugin 加载结果。"""

    configured_dirs: tuple[Path, ...]
    builtin_plugin_dirs: tuple[Path, ...]
    plugin_sources: Mapping[str, Path | None]


def _canonical_directory(path: str | os.PathLike[str]) -> Path:
    try:
        directory = Path(path).resolve()
    except OSError as error:
        raise NativePluginPolicyError(
            "native_plugin.directory_invalid",
            f"无法规范化插件目录 {path!s}: {error}",
        ) from error
    if not directory.is_dir():
        raise NativePluginPolicyError(
            "native_plugin.directory_missing",
            f"插件目录不存在或不是目录: {directory}",
        )
    return directory


def canonical_native_plugin_dirs(
    paths: Iterable[str | os.PathLike[str]],
) -> tuple[Path, ...]:
    """按 native 加载策略规范化、保序去重配置目录。"""
    directories: list[Path] = []
    keys: set[str] = set()
    for path in paths:
        directory = _canonical_directory(path)
        key = os.path.normcase(str(directory))
        if key not in keys:
            directories.append(directory)
            keys.add(key)
    return tuple(directories)


def _optional_builtin_plugin_dirs(
    paths: Iterable[str | os.PathLike[str]],
) -> tuple[Path, ...]:
    """规范化 portable 内置目录；未随旧分发提供的目录保持可选。"""
    directories: list[Path] = []
    keys: set[str] = set()
    for path in paths:
        try:
            directory = Path(path).resolve()
        except OSError as error:
            raise NativePluginPolicyError(
                "native_plugin.directory_invalid",
                f"无法规范化内置插件目录 {path!s}: {error}",
            ) from error
        if not directory.exists():
            continue
        if not directory.is_dir():
            raise NativePluginPolicyError(
                "native_plugin.directory_invalid",
                f"内置插件路径不是目录: {directory}",
            )
        key = os.path.normcase(str(directory))
        if key not in keys:
            directories.append(directory)
            keys.add(key)
    return tuple(directories)


def _plugin_source(plugin: Any, namespace: str) -> Path | None:
    source = getattr(plugin, "plugin_path", None)
    if source is None:
        return None
    if not isinstance(source, str) or not source:
        raise NativePluginPolicyError(
            "native_plugin.source_invalid",
            f"namespace {namespace} 的 plugin_path 不是路径: {source!r}",
        )
    try:
        return Path(source).resolve()
    except OSError as error:
        raise NativePluginPolicyError(
            "native_plugin.source_invalid",
            f"无法规范化 namespace {namespace} 的 plugin_path {source}: {error}",
        ) from error


def _plugins_by_namespace(core: Any) -> dict[str, Any]:
    try:
        plugins = core.plugins()
        values = plugins.values() if isinstance(plugins, Mapping) else plugins
        result = {plugin.namespace: plugin for plugin in values}
    except NativePluginPolicyError:
        raise
    except BaseException as error:
        raise NativePluginPolicyError(
            "native_plugin.enumeration_failed",
            f"无法枚举已注册 VapourSynth plugins: {error}",
        ) from error
    if any(not isinstance(namespace, str) or not namespace for namespace in result):
        raise NativePluginPolicyError(
            "native_plugin.namespace_invalid", "VapourSynth plugin namespace 无效"
        )
    return result


def _sources(core: Any) -> dict[str, Path | None]:
    return {
        namespace: _plugin_source(plugin, namespace)
        for namespace, plugin in _plugins_by_namespace(core).items()
    }


def _is_within(path: Path, roots: Iterable[Path]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def _top_level_dlls(directory: Path) -> tuple[Path, ...]:
    """保持目录边界，仅检查 LoadAllPlugins 会看到的顶层 DLL 候选。"""
    try:
        return tuple(
            sorted(
                (
                    path.resolve()
                    for path in directory.iterdir()
                    if path.is_file() and path.suffix.casefold() == ".dll"
                ),
                key=lambda path: (path.name.casefold(), str(path).casefold()),
            )
        )
    except OSError as error:
        raise NativePluginPolicyError(
            "native_plugin.directory_unreadable",
            f"无法枚举插件目录 {directory}: {error}",
        ) from error


def _load_all_plugins_with_diagnostics(
    core: Any, directory: Path
) -> tuple[tuple[Any, str], ...]:
    """加载一个目录，并只在该调用期间捕获 R73 loader warning。"""
    messages: list[tuple[Any, str]] = []
    try:
        handle = core.add_log_handler(
            lambda level, message: messages.append((level, str(message)))
        )
    except BaseException as error:
        raise NativePluginPolicyError(
            "native_plugin.log_capture_unavailable",
            f"无法捕获插件加载诊断 {directory}: {error}",
        ) from error
    try:
        core.std.LoadAllPlugins(path=str(directory))
    except BaseException as error:
        raise NativePluginPolicyError(
            "native_plugin.load_failed",
            f"加载插件目录失败: {directory}: {error}",
        ) from error
    finally:
        try:
            core.remove_log_handler(handle)
        except BaseException as error:
            raise NativePluginPolicyError(
                "native_plugin.log_handler_cleanup_failed",
                f"无法移除插件加载诊断 handler {directory}: {error}",
            ) from error
    return tuple(messages)


_ID_CONFLICT_WARNING = re.compile(
    r"^Plugin (?P<candidate>.+?) "
    r"already loaded \((?P<identity>[^)]+)\)(?: from (?P<source>.+))?$",
    re.IGNORECASE,
)
_NAMESPACE_CONFLICT_WARNING = re.compile(
    r"^Plugin load of (?P<candidate>.+?) failed, namespace "
    r"(?P<namespace>.+?) already populated(?: by (?P<source>.+))?$",
    re.IGNORECASE,
)
_WINDOWS_LOAD_FAILURE_WARNING = re.compile(
    r"^Failed to load (?P<candidate>.+?)\. "
    r"GetLastError\(\) returned \d+\."
    r"(?: The file you tried to load or one of its dependencies is probably missing\.)?$",
    re.IGNORECASE,
)
_API_INCOMPATIBILITY_WARNING = re.compile(
    r"^Core only supports API R\d+\.\d+ but the loaded plugin requires API "
    r"R\d+\.\d+; Filename: (?P<candidate>.+?); Name: .+$",
    re.IGNORECASE,
)


def _is_warning_or_error(level: Any) -> bool:
    """只接受 VapourSynth warning 及更严重级别的 loader 诊断。"""
    if isinstance(level, bool):
        return False
    level_name = str(getattr(level, "name", level)).casefold()
    if any(name in level_name for name in ("warning", "error", "critical", "fatal")):
        return True
    level_value = getattr(level, "value", level)
    try:
        return int(level_value) >= 2
    except (TypeError, ValueError):
        return False


def _candidate_in_directory(candidate_text: str, candidates: Iterable[Path]) -> Path | None:
    """将 R73 消息的候选字段匹配到本次 LoadAllPlugins 的实际 DLL。"""
    try:
        message_path = Path(candidate_text).resolve()
    except OSError:
        return None
    message_key = os.path.normcase(str(message_path))
    return next(
        (
            candidate
            for candidate in candidates
            if os.path.normcase(str(candidate)) == message_key
        ),
        None,
    )


def _message_path(path_text: str) -> Path | None:
    try:
        return Path(path_text).resolve()
    except OSError:
        return None


def _same_path(first: Path, second: Path | None) -> bool:
    return second is not None and os.path.normcase(str(first)) == os.path.normcase(
        str(second)
    )


def _raise_loader_warning(
    directory: Path,
    messages: Iterable[tuple[Any, str]],
) -> None:
    """将 R73 已报告的插件失败/冲突转为有路径的冻结策略错误。

    R73 对无 plugin entry point 的相邻依赖 DLL 不写 warning；因此只处理
    warning/error 级别中有明确 loader 失败结构的消息。冲突消息中的 Plugin
    或 Plugin load of 字段是本次候选，from/by 字段是既有来源；二者相同才是
    同路径重复扫描。加载失败不因路径相同而被忽略。
    """
    candidates = _top_level_dlls(directory)
    for level, message in messages:
        if not _is_warning_or_error(level):
            continue
        detail = message.strip()
        for pattern in (_ID_CONFLICT_WARNING, _NAMESPACE_CONFLICT_WARNING):
            conflict = pattern.fullmatch(detail)
            if conflict is None:
                continue
            candidate = _candidate_in_directory(
                conflict.group("candidate"), candidates
            )
            source_text = conflict.group("source")
            source = _message_path(source_text) if source_text else None
            if candidate is None or _same_path(candidate, source):
                break
            raise NativePluginPolicyError(
                "native_plugin.conflict",
                "候选 DLL 与既有 plugin identity 或 namespace 冲突: "
                f"候选 {candidate}; 既有来源 {source}; VS: {detail}",
            )
        else:
            for pattern in (_WINDOWS_LOAD_FAILURE_WARNING, _API_INCOMPATIBILITY_WARNING):
                failure = pattern.fullmatch(detail)
                if failure is None:
                    continue
                candidate = _candidate_in_directory(
                    failure.group("candidate"), candidates
                )
                if candidate is not None:
                    raise NativePluginPolicyError(
                        "native_plugin.candidate_failed",
                        f"候选 DLL 加载失败: {candidate}; VS: {detail}",
                    )
                break


def configure_native_plugins(
    core: Any,
    native_plugin_dirs: Iterable[str | os.PathLike[str]],
    *,
    builtin_plugin_dirs: Iterable[str | os.PathLike[str]] = (),
) -> NativePluginState:
    """按冻结配置逐目录加载，拒绝可证明的同 DLL 冲突。

    ``LoadAllPlugins`` 本身不会为每个 DLL 报错；因此空目录和只含普通依赖
    DLL 的目录都是合法配置。真正的可用性在 requirement 校验时由 namespace、
    callable 与 plugin_path 一起确认。
    """
    directories = canonical_native_plugin_dirs(native_plugin_dirs)
    builtin_dirs = _optional_builtin_plugin_dirs(builtin_plugin_dirs)
    for directory in directories:
        messages = _load_all_plugins_with_diagnostics(core, directory)
        _raise_loader_warning(directory, messages)
    sources = _sources(core)
    return NativePluginState(
        configured_dirs=directories,
        builtin_plugin_dirs=builtin_dirs,
        plugin_sources=MappingProxyType(sources),
    )


def verify_native_plugin_requirements(
    core: Any,
    state: NativePluginState,
    requirements: Iterable[str],
) -> None:
    """确认 requirement callable 来自 builtin、配置目录或真正的内置 plugin。"""
    plugins = _plugins_by_namespace(core)
    for requirement in requirements:
        try:
            namespace, function_name = requirement.split(".", 1)
            plugin = plugins[namespace]
            function = getattr(plugin, function_name)
        except (AttributeError, KeyError, ValueError):
            function = None
            namespace = requirement.split(".", 1)[0]
        if not callable(function):
            raise NativePluginPolicyError(
                "native_plugin.requirement_missing",
                "缺少可调用 requirement "
                f"{requirement}；已检查目录: "
                f"{', '.join(map(str, state.configured_dirs)) or '(无)'}",
            )
        source = _plugin_source(plugins[namespace], namespace)
        if source is None:
            continue
        if _is_within(source, (*state.configured_dirs, *state.builtin_plugin_dirs)):
            continue
        raise NativePluginPolicyError(
            "native_plugin.source_mismatch",
            f"requirement {requirement} 来自未配置的 {source}，"
            f"配置目录: {', '.join(map(str, state.configured_dirs)) or '(无)'}",
        )


__all__ = [
    "NativePluginPolicyError",
    "NativePluginState",
    "canonical_native_plugin_dirs",
    "configure_native_plugins",
    "verify_native_plugin_requirements",
]
