"""脚本执行边界的 VapourSynth core 资源恢复策略。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class CoreResourceError(RuntimeError):
    """core 资源值无法完整恢复。"""


@dataclass(frozen=True)
class CoreResourceBaseline:
    """首次接触 core 时的实际默认值，绝不从用户脚本后的状态推断。"""

    num_threads: int
    max_cache_size: int

    @classmethod
    def capture(cls, core: Any) -> "CoreResourceBaseline":
        try:
            return cls(
                num_threads=int(core.num_threads),
                max_cache_size=int(core.max_cache_size),
            )
        except BaseException as error:
            raise CoreResourceError("无法捕获 VapourSynth core 默认资源值") from error


def restore_core_resources(
    core: Any,
    runtime_core: Mapping[str, Any] | Any,
    baseline: CoreResourceBaseline,
) -> None:
    """在每个用户脚本前恢复默认或冻结配置，失败时不允许开始新图。"""
    try:
        requested_threads = runtime_core["num_threads"]
        requested_cache = runtime_core["max_cache_size_mb"]
    except TypeError:
        requested_threads = runtime_core.num_threads
        requested_cache = runtime_core.max_cache_size_mb
    if type(requested_threads) is not int or type(requested_cache) is not int:
        raise CoreResourceError("VapourSynth core 资源配置必须是整数")
    effective_threads = requested_threads or baseline.num_threads
    effective_cache = requested_cache or baseline.max_cache_size
    try:
        core.num_threads = effective_threads
        core.max_cache_size = effective_cache
    except BaseException as error:
        raise CoreResourceError(
            "无法在执行用户脚本前恢复 VapourSynth core 资源值"
        ) from error


__all__ = ["CoreResourceBaseline", "CoreResourceError", "restore_core_resources"]
