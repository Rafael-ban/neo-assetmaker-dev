"""M3：冻结产物中统一配置页的保留与退休边界。"""
from __future__ import annotations

from collections.abc import Iterable
from io import BytesIO
from pathlib import PurePosixPath
from zipfile import ZipFile


REQUIRED_SETTINGS_ARTIFACTS = frozenset(
    {
        "arknightspassmaker.exe",
        "lib/gui/widgets/config_panel.pyc",
        "vs_worker.exe",
        "resources/class_icons/guard.png",
        "class_icons/guard.png",
    }
)
_RETIRED_MODULE_STEMS = frozenset({"basic_config_panel", "operator_db"})
_RETIRED_FILENAMES = frozenset({"character_table.json"})
PORTABLE_ARCHIVE_ROOT = "arknightspassmaker"


def _normalize_relative_path(path: str) -> str:
    """将冻结目录或 ZIP 成员名规范化为小写相对 POSIX 路径。"""
    candidate = path.strip().replace("\\", "/").replace("!", "/")
    pure_path = PurePosixPath(candidate)
    parts = pure_path.parts
    if (
        not parts
        or pure_path.is_absolute()
        or ".." in parts
        or parts[0].endswith(":")
    ):
        raise ValueError(f"冻结产物路径必须是安全的相对路径: {path!r}")
    return "/".join(parts).casefold()


def _find_retired_artifact(paths: Iterable[str]) -> str | None:
    for path in paths:
        for segment in path.split("/"):
            filename = segment.casefold()
            module_stem = filename.split(".", 1)[0]
            if module_stem in _RETIRED_MODULE_STEMS:
                return module_stem
            if filename in _RETIRED_FILENAMES:
                return filename
    return None


def _strip_portable_archive_root(member_name: str) -> str:
    """从真实便携 ZIP 成员移除固定的应用根目录。"""
    normalized_member = _normalize_relative_path(member_name)
    expected_prefix = f"{PORTABLE_ARCHIVE_ROOT}/"
    if not normalized_member.startswith(expected_prefix):
        raise ValueError(
            "便携 ZIP 成员必须位于 "
            f"ArknightsPassMaker/ 根目录: {member_name!r}"
        )
    return normalized_member.removeprefix(expected_prefix)


def _collect_library_zip_members(library_path: str, contents: bytes) -> set[str]:
    """展开 library.zip 内部文件，使退休模块检查不会停在外层 ZIP 名。"""
    members = set()
    with ZipFile(BytesIO(contents)) as library:
        for member in library.infolist():
            if member.is_dir():
                continue
            normalized_member = _normalize_relative_path(member.filename)
            members.add(f"{library_path}!/{normalized_member}")
    return members


def collect_portable_archive_paths(archive: ZipFile) -> frozenset[str]:
    """收集便携 ZIP 的 root-relative 文件和内部 library.zip 成员。"""
    paths = set()
    for member in archive.infolist():
        if member.is_dir():
            continue
        relative_path = _strip_portable_archive_root(member.filename)
        paths.add(relative_path)
        if PurePosixPath(relative_path).name == "library.zip":
            paths.update(_collect_library_zip_members(relative_path, archive.read(member)))
    return frozenset(paths)


def validate_settings_artifact_contract(paths: Iterable[str]) -> frozenset[str]:
    """验证冻结目录或 ZIP 成员清单不再分发基础设置专属产物。"""
    normalized_paths = frozenset(_normalize_relative_path(path) for path in paths)
    retired_artifact = _find_retired_artifact(normalized_paths)
    if retired_artifact is not None:
        raise ValueError(f"冻结产物仍包含退休项: {retired_artifact}")

    missing = REQUIRED_SETTINGS_ARTIFACTS - normalized_paths
    if missing:
        raise ValueError(
            "冻结产物缺少统一配置所需文件: " + ", ".join(sorted(missing))
        )
    return normalized_paths
