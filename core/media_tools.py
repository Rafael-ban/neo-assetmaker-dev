"""Media tool discovery for the bundled preview and export pipeline."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from utils.file_utils import get_app_dir
from config.vs_runtime import load_vs_runtime
from core.vs_runtime.session import resolve_worker_command


def _exe_names(base_name: str) -> tuple[str, ...]:
    if sys.platform == "win32" and not base_name.lower().endswith(".exe"):
        return (f"{base_name}.exe", base_name)
    return (base_name,)


def _candidate_dirs(app_dir: Path) -> tuple[Path, ...]:
    return (
        app_dir / "tools" / "media",
        app_dir / "media",
        app_dir / "tools",
        app_dir,
    )


def _find_tool(app_dir: Path, names: Iterable[str]) -> str:
    for directory in _candidate_dirs(app_dir):
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return str(candidate)

    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return ""


def _resolve_tool_path(path: str) -> Optional[Path]:
    if not path:
        return None
    candidate = Path(path)
    if candidate.is_file():
        return candidate
    found = shutil.which(path)
    if found:
        return Path(found)
    return None


def _prepend_env_value(env: dict[str, str], name: str, value: Path) -> None:
    if not value.exists():
        return
    current = env.get(name, "")
    prefix = str(value)
    env[name] = prefix + (os.pathsep + current if current else "")


def build_media_subprocess_env(tool_path: str) -> dict[str, str]:
    """为 x264/muxer 构建媒体工具环境，不承担 VSPipe runtime 配置。"""
    env = os.environ.copy()
    resolved = _resolve_tool_path(tool_path)
    media_dir = resolved.parent if resolved else Path(get_app_dir()) / "tools" / "media"

    _prepend_env_value(env, "PATH", media_dir)
    _prepend_env_value(env, "PYTHONPATH", media_dir / "Lib" / "site-packages")
    return env


@dataclass(frozen=True)
class MediaToolchain:
    """Resolved paths for the preview and export media toolchain."""

    vspipe_path: str = ""
    x264_path: str = ""
    muxer_path: str = ""

    @classmethod
    def discover(cls, app_dir: Optional[os.PathLike[str] | str] = None) -> "MediaToolchain":
        # Memoized: discover() runs dozens of Path.is_file() stats and used to be
        # called 2-3x per video load (preview + probe + export). Cache per resolved
        # root; call MediaToolchain.refresh() after installing tools at runtime.
        root = str(Path(app_dir)) if app_dir is not None else str(Path(get_app_dir()))
        return _discover_cached(root)

    @staticmethod
    def refresh() -> None:
        """清理媒体可执行文件发现缓存（例如运行时安装工具后）。"""
        _discover_cached.cache_clear()

    def missing_for_export(self) -> list[str]:
        missing = []
        if not self.vspipe_path:
            missing.append("VSPipe")
        if not self.x264_path:
            missing.append("x264-7mod")
        if not self.muxer_path:
            missing.append("MP4Box or lsmash-muxer")
        return missing

    def missing_for_preview(self) -> list[str]:
        """检查 worker/runtime 分发文件；绝不在调用进程 import VS。"""
        root = Path(get_app_dir()).resolve()
        missing: list[str] = []
        try:
            load_vs_runtime()
        except Exception as exc:
            missing.append(f"VS runtime config: {exc}")
        command = resolve_worker_command(root)
        worker_path = Path(command[-1])
        if not worker_path.is_file():
            missing.append(worker_path.name)
        required = (
            root / "tools" / "media" / "vapoursynth.pyd",
            root / "tools" / "media" / "vapoursynth.dll",
            root / "tools" / "media" / "portable.vs",
            root / "resources" / "vapoursynth" / "assetmaker_runner.vpy",
            root / "resources" / "vapoursynth" / "default_pipeline.vpy",
            root / "resources" / "vapoursynth" / "python" / "assetmaker_vs" / "__init__.py",
            root / "resources" / "vapoursynth" / "python" / "assetmaker_vs" / "executor.py",
            root / "resources" / "vapoursynth" / "python" / "assetmaker_vs" / "contract.py",
            root / "resources" / "vapoursynth" / "python" / "assetmaker_vs" / "display.py",
            root / "resources" / "vapoursynth" / "python" / "assetmaker_vs" / "job_api.py",
            root / "resources" / "vapoursynth" / "python" / "assetmaker_vs" / "script_header.py",
            root / "resources" / "vapoursynth" / "python" / "assetmaker_vs" / "runtime_fingerprint.py",
            root / "resources" / "vapoursynth" / "python" / "assetmaker_vs" / "core_resources.py",
        )
        missing.extend(path.name for path in required if not path.is_file())
        plugin_dir = root / "tools" / "media" / "vs-plugins"
        if not plugin_dir.is_dir():
            missing.append("vs-plugins")
        return missing

    def describe(self) -> str:
        parts = {
            "VSPipe": self.vspipe_path,
            "x264-7mod": self.x264_path,
            "MP4 muxer": self.muxer_path,
        }
        return ", ".join(f"{name}={'found' if path else 'missing'}" for name, path in parts.items())


@lru_cache(maxsize=8)
def _discover_cached(root: str) -> MediaToolchain:
    base = Path(root)
    return MediaToolchain(
        vspipe_path=_find_tool(base, _exe_names("VSPipe")),
        x264_path=_find_tool(base, ("x264-7mod.exe", "x264-7mod", "x264.exe", "x264")),
        muxer_path=_find_tool(
            base,
            (
                "MP4Box.exe",
                "MP4Box",
                "mp4box.exe",
                "mp4box",
                "lsmash-muxer.exe",
                "lsmash-muxer",
                "muxer.exe",
                "muxer",
            ),
        ),
    )
