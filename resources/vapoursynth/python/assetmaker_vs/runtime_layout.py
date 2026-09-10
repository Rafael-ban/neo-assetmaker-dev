"""R79 portable VapourSynth 产品运行时的唯一固定布局。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


_CORE_FILTER_NAMES = (
    "libvapoursynthfilters.dll",
    "libvapoursynthfilters_avx2.dll",
    "libvapoursynthfilters_zn4.dll",
)
_FLAT_R73_ENTRIES = (
    "vapoursynth.pyd",
    "vapoursynth.dll",
    "portable.vs",
    "VSPipe.exe",
    "VSScript.dll",
    "vs-plugins",
    "vs-coreplugins",
)
_IGNORED_DIRECTORY_NAMES = frozenset({"__pycache__", "logs", "samples"})
_IGNORED_SUFFIXES = frozenset({".log", ".lwi"})
_CONTROLLED_PROCESS_ENVIRONMENT_NAMES = frozenset(
    {
        "PATH",
        "PYTHONHOME",
        "PYTHONPATH",
        "VAPOURSYNTH_CONF_PATH",
        "VAPOURSYNTH_EXTRA_PLUGIN_PATH",
        "VAPOURSYNTH_PLUGIN_PATH",
        "VAPOURSYNTH_PYTHON_PATH",
    }
)
_DISTRIBUTION_RELATIVE_FILES = (
    "Lib/site-packages/vapoursynth-79.dist-info/METADATA",
    "Lib/site-packages/vapoursynth-79.dist-info/RECORD",
    "Lib/site-packages/vapoursynth-79.dist-info/WHEEL",
    "Lib/site-packages/vapoursynth-79.dist-info/entry_points.txt",
    "Lib/site-packages/vapoursynth-79.dist-info/licenses/COPYING.LESSER",
    "Lib/site-packages/vapoursynth/__init__.py",
    "Lib/site-packages/vapoursynth/__main__.py",
    "Lib/site-packages/vapoursynth/_cli.py",
    "Lib/site-packages/vapoursynth/_shell.py",
    "Lib/site-packages/vapoursynth/_utils.py",
    "Lib/site-packages/vapoursynth/include/VSConstants4.h",
    "Lib/site-packages/vapoursynth/include/VSHelper4.h",
    "Lib/site-packages/vapoursynth/include/VSScript4.h",
    "Lib/site-packages/vapoursynth/include/VapourSynth4.h",
    "Lib/site-packages/vapoursynth/libvapoursynth.dll",
    "Lib/site-packages/vapoursynth/libvapoursynth.pdb",
    "Lib/site-packages/vapoursynth/libvapoursynthfilters.dll",
    "Lib/site-packages/vapoursynth/libvapoursynthfilters.pdb",
    "Lib/site-packages/vapoursynth/libvapoursynthfilters_avx2.dll",
    "Lib/site-packages/vapoursynth/libvapoursynthfilters_avx2.pdb",
    "Lib/site-packages/vapoursynth/libvapoursynthfilters_zn4.dll",
    "Lib/site-packages/vapoursynth/libvapoursynthfilters_zn4.pdb",
    "Lib/site-packages/vapoursynth/pkgconfig/vapoursynth.pc",
    "Lib/site-packages/vapoursynth/plugins/avscompat.dll",
    "Lib/site-packages/vapoursynth/vapoursynth.pyd",
    "Lib/site-packages/vapoursynth/vapoursynth.pyi",
    "Lib/site-packages/vapoursynth/vspipe.exe",
    "Lib/site-packages/vapoursynth/vsscript.dll",
    "Lib/site-packages/vapoursynth/vsscript.pdb",
    "Lib/site-packages/vapoursynth/vsvfw.dll",
    "_asyncio.pyd",
    "_bz2.pyd",
    "_ctypes.pyd",
    "_decimal.pyd",
    "_elementtree.pyd",
    "_hashlib.pyd",
    "_lzma.pyd",
    "_msi.pyd",
    "_multiprocessing.pyd",
    "_overlapped.pyd",
    "_queue.pyd",
    "_socket.pyd",
    "_sqlite3.pyd",
    "_ssl.pyd",
    "_uuid.pyd",
    "_wmi.pyd",
    "_zoneinfo.pyd",
    "concrt140.dll",
    "libcrypto-3.dll",
    "libffi-8.dll",
    "libssl-3.dll",
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll",
    "msvcp140_codecvt_ids.dll",
    "native-plugins/01-lsmas/LSMASHSource.dll",
    "native-plugins/02-imwri/libimwri.dll",
    "pyexpat.pyd",
    "python.cat",
    "python.exe",
    "python3.dll",
    "python312._pth",
    "python312.dll",
    "python312.zip",
    "pythonw.exe",
    "select.pyd",
    "sqlite3.dll",
    "unicodedata.pyd",
    "vccorlib140.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "vcruntime140_threads.dll",
    "winsound.pyd",
)


class RuntimeLayoutError(RuntimeError):
    """产品目录不是完整、单一的 R79 portable 分发。"""


@dataclass(frozen=True)
class RuntimeLayout:
    """从 ``app_dir`` 唯一派生的 R79 runtime 路径集合。"""

    app_dir: Path
    media_root: Path
    runtime_root: Path
    python_executable: Path
    python_abi_forwarder: Path
    python_library: Path
    python_stdlib_zip: Path
    python_path_file: Path
    site_packages: Path
    vs_package_dir: Path
    package_entry: Path
    binding: Path
    core: Path
    core_filters: tuple[Path, ...]
    vsscript_library: Path
    vspipe_executable: Path
    builtin_plugin_dirs: tuple[Path, ...]
    bundled_native_plugin_dirs: tuple[Path, ...]
    wheel_metadata_dir: Path
    wheel_metadata: Path
    wheel_record: Path

    def identity_files(self) -> tuple[Path, ...]:
        """返回实际 runtime 分发文件，按相对路径 Ordinal 稳定排序。"""
        try:
            files = (
                path.resolve()
                for path in self.runtime_root.rglob("*")
                if path.is_file()
                and not _is_ignored_identity_file(path, self.runtime_root)
            )
            return tuple(
                sorted(
                    files,
                    key=lambda path: path.relative_to(self.runtime_root).as_posix(),
                )
            )
        except OSError as exc:
            raise RuntimeLayoutError(
                f"无法枚举 R79 runtime 分发: {self.runtime_root}: {exc}"
            ) from exc


def _is_ignored_identity_file(path: Path, runtime_root: Path) -> bool:
    relative = path.relative_to(runtime_root)
    return (
        any(part.casefold() in _IGNORED_DIRECTORY_NAMES for part in relative.parts)
        or path.suffix.casefold() in _IGNORED_SUFFIXES
    )


def _layout_for(app_dir: str | os.PathLike[str]) -> RuntimeLayout:
    root = Path(app_dir).resolve()
    media = root / "tools" / "media"
    runtime = media / "runtime"
    site_packages = runtime / "Lib" / "site-packages"
    package = site_packages / "vapoursynth"
    metadata_dir = site_packages / "vapoursynth-79.dist-info"
    return RuntimeLayout(
        app_dir=root,
        media_root=media,
        runtime_root=runtime,
        python_executable=runtime / "python.exe",
        python_abi_forwarder=runtime / "python3.dll",
        python_library=runtime / "python312.dll",
        python_stdlib_zip=runtime / "python312.zip",
        python_path_file=runtime / "python312._pth",
        site_packages=site_packages,
        vs_package_dir=package,
        package_entry=package / "__init__.py",
        binding=package / "vapoursynth.pyd",
        core=package / "libvapoursynth.dll",
        core_filters=tuple(package / name for name in _CORE_FILTER_NAMES),
        vsscript_library=package / "vsscript.dll",
        vspipe_executable=package / "vspipe.exe",
        builtin_plugin_dirs=(package / "plugins",),
        bundled_native_plugin_dirs=(
            runtime / "native-plugins" / "01-lsmas",
            runtime / "native-plugins" / "02-imwri",
        ),
        wheel_metadata_dir=metadata_dir,
        wheel_metadata=metadata_dir / "METADATA",
        wheel_record=metadata_dir / "RECORD",
    )


def _required_files(layout: RuntimeLayout) -> tuple[Path, ...]:
    return tuple(
        layout.runtime_root / Path(relative)
        for relative in _DISTRIBUTION_RELATIVE_FILES
    )


def _validate_layout(layout: RuntimeLayout) -> None:
    flat_entries = tuple(
        (layout.media_root / name).resolve()
        for name in _FLAT_R73_ENTRIES
        if (layout.media_root / name).exists()
    )
    if flat_entries:
        state = "混合 R73/R79" if layout.runtime_root.is_dir() else "flat R73"
        raise RuntimeLayoutError(
            f"拒绝 {state} VapourSynth 布局: "
            + ", ".join(map(str, flat_entries))
        )
    if not layout.runtime_root.is_dir():
        raise RuntimeLayoutError(f"R79 runtime 目录不存在: {layout.runtime_root}")
    for path in _required_files(layout):
        if not path.is_file():
            raise RuntimeLayoutError(f"R79 runtime 必需文件不存在: {path.resolve()}")
    try:
        metadata = layout.wheel_metadata.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RuntimeLayoutError(
            f"无法读取 R79 wheel metadata: {layout.wheel_metadata}: {exc}"
        ) from exc
    version_lines = {
        line.partition(":")[2].strip()
        for line in metadata.splitlines()
        if line.casefold().startswith("version:")
    }
    if version_lines != {"79"}:
        raise RuntimeLayoutError(
            f"R79 wheel metadata 版本不匹配: {layout.wheel_metadata}; "
            f"实际 {sorted(version_lines)!r}"
        )
    try:
        vsscript_runtime = layout.vsscript_library.parents[3]
    except IndexError as exc:
        raise RuntimeLayoutError(
            f"VSScript 路径无法四级回溯到 embedded runtime: "
            f"{layout.vsscript_library}"
        ) from exc
    if vsscript_runtime != layout.runtime_root:
        raise RuntimeLayoutError(
            f"VSScript 四级父目录不是 embedded runtime: "
            f"{layout.vsscript_library} -> {vsscript_runtime}"
        )


def resolve_runtime_layout(
    app_dir: str | os.PathLike[str],
) -> RuntimeLayout:
    """解析并完整预检唯一支持的冻结 R79 产品布局。"""
    layout = _layout_for(app_dir)
    _validate_layout(layout)
    return layout


def sanitize_runtime_process_environment(
    base_environment: Mapping[str, str], layout: RuntimeLayout
) -> dict[str, str]:
    """复制并收敛 worker/VSPipe 启动前的 VS 与 Python 搜索环境。"""
    env = dict(base_environment)
    system_root = Path(
        next(
            (
                value
                for name, value in env.items()
                if name.upper() == "SYSTEMROOT"
            ),
            r"C:\Windows",
        )
    )
    for name in tuple(env):
        if name.upper() in _CONTROLLED_PROCESS_ENVIRONMENT_NAMES:
            env.pop(name)
    path_entries = (
        layout.vs_package_dir,
        layout.runtime_root,
        system_root / "System32",
        system_root,
    )
    env["PATH"] = os.pathsep.join(
        dict.fromkeys(str(path) for path in path_entries)
    )
    env["VAPOURSYNTH_EXTRA_PLUGIN_PATH"] = ""
    return env


__all__ = [
    "RuntimeLayout",
    "RuntimeLayoutError",
    "resolve_runtime_layout",
    "sanitize_runtime_process_environment",
]
