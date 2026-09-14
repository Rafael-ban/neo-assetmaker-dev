"""R79 媒体工具分发清单、可复现归档与安全解包入口。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile, ZipInfo

from resources.vapoursynth.python.assetmaker_vs.runtime_layout import (
    RuntimeLayoutError,
    resolve_runtime_layout,
)


ASSET_ID = "media-tools-r79-v1"
RUNTIME_VERSION = 79
SCHEMA_VERSION = 1
_ARCHIVE_PREFIX = "tools/media/"
_BUFFER_SIZE = 1024 * 1024
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)
_WINDOWS_INVALID_CHARACTERS = frozenset('<>:"|?*')


class MediaDistributionError(RuntimeError):
    """媒体分发清单、树或归档不满足发布合同。"""


@dataclass(frozen=True)
class MediaSource:
    source_id: str
    description: str
    evidence: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class MediaFileRecord:
    path: str
    size: int
    sha256: str
    source_id: str


@dataclass(frozen=True)
class MediaManifest:
    asset_id: str
    runtime_version: int
    records: tuple[MediaFileRecord, ...]
    sources: tuple[MediaSource, ...]


@dataclass(frozen=True)
class MediaTreeVerification:
    files: tuple[Path, ...]
    ignored: tuple[str, ...]
    file_count: int
    total_bytes: int


@dataclass(frozen=True)
class ArchiveVerification:
    sha256: str
    file_count: int
    total_bytes: int


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MediaDistributionError(f"{label} must be a JSON object")
    return value


def _validate_sha256(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(
            character not in "0123456789abcdefABCDEF"
            for character in value
        )
    ):
        raise MediaDistributionError(
            f"{label} SHA-256 must be 64 hexadecimal digits"
        )
    return value.upper()


def _validate_relative_path(value: Any, label: str = "manifest path") -> str:
    if not isinstance(value, str) or not value:
        raise MediaDistributionError(f"{label} must be a non-empty string")
    if value.startswith(("/", "\\")) or "\\" in value:
        raise MediaDistributionError(
            f"{label} is not a POSIX relative path: {value!r}"
        )
    if any(ord(character) < 32 for character in value):
        raise MediaDistributionError(
            f"{label} contains a control character: {value!r}"
        )
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise MediaDistributionError(
            f"{label} contains an unsafe segment: {value!r}"
        )
    for part in parts:
        if part[-1] in {".", " "}:
            raise MediaDistributionError(
                f"{label} contains a trailing dot or space: {value!r}"
            )
        if any(character in _WINDOWS_INVALID_CHARACTERS for character in part):
            raise MediaDistributionError(
                f"{label} contains a Windows-invalid character: {value!r}"
            )
        if part.partition(".")[0].upper() in _WINDOWS_RESERVED_NAMES:
            raise MediaDistributionError(
                f"{label} contains a Windows-reserved name: {value!r}"
            )
    return value


def load_manifest(manifest_path: Path) -> MediaManifest:
    """读取并严格验证schema 1的R79媒体分发清单。"""
    path = Path(manifest_path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MediaDistributionError(
            f"cannot read media manifest {path}: {exc}"
        ) from exc
    root = _require_mapping(document, "manifest")
    if root.get("schema") != SCHEMA_VERSION or isinstance(
        root.get("schema"), bool
    ):
        raise MediaDistributionError(
            f"manifest schema must be {SCHEMA_VERSION}"
        )
    if root.get("asset_id") != ASSET_ID:
        raise MediaDistributionError(
            f"manifest asset_id must be {ASSET_ID!r}"
        )
    runtime_version = root.get("runtime_version")
    if runtime_version != RUNTIME_VERSION or isinstance(runtime_version, bool):
        raise MediaDistributionError(
            f"manifest runtime_version must be {RUNTIME_VERSION}"
        )

    raw_sources = root.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise MediaDistributionError(
            "manifest sources must be a non-empty array"
        )
    sources = []
    source_ids = set()
    for index, raw_source in enumerate(raw_sources):
        source = _require_mapping(raw_source, f"sources[{index}]")
        source_id = source.get("id")
        description = source.get("description")
        evidence = source.get("evidence")
        if not isinstance(source_id, str) or not source_id:
            raise MediaDistributionError(
                f"sources[{index}].id must be non-empty"
            )
        if source_id.casefold() in source_ids:
            raise MediaDistributionError(f"duplicate source id: {source_id}")
        if not isinstance(description, str) or not description:
            raise MediaDistributionError(
                f"sources[{index}].description must be non-empty"
            )
        if not isinstance(evidence, list) or not all(
            isinstance(item, dict) for item in evidence
        ):
            raise MediaDistributionError(
                f"sources[{index}].evidence must be an array"
            )
        source_ids.add(source_id.casefold())
        sources.append(
            MediaSource(
                source_id=source_id,
                description=description,
                evidence=tuple(dict(item) for item in evidence),
            )
        )

    raw_files = root.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise MediaDistributionError(
            "manifest files must be a non-empty array"
        )
    records = []
    casefolded_paths = set()
    for index, raw_record in enumerate(raw_files):
        record = _require_mapping(raw_record, f"files[{index}]")
        relative = _validate_relative_path(
            record.get("path"), f"files[{index}].path"
        )
        folded = relative.casefold()
        if folded in casefolded_paths:
            raise MediaDistributionError(
                f"duplicate case-insensitive path: {relative}"
            )
        size = record.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise MediaDistributionError(
                f"files[{index}].size must be a non-negative int"
            )
        digest = _validate_sha256(
            record.get("sha256"), f"files[{index}]"
        )
        source_id = record.get("source_id")
        if (
            not isinstance(source_id, str)
            or source_id.casefold() not in source_ids
        ):
            raise MediaDistributionError(
                f"files[{index}].source_id references an unknown source"
            )
        casefolded_paths.add(folded)
        records.append(
            MediaFileRecord(
                path=relative,
                size=size,
                sha256=digest,
                source_id=source_id,
            )
        )
    ordered_paths = sorted(
        (record.path for record in records),
        key=lambda value: value.encode("utf-8"),
    )
    if [record.path for record in records] != ordered_paths:
        raise MediaDistributionError(
            "manifest files must be sorted by UTF-8 path bytes"
        )
    return MediaManifest(
        asset_id=ASSET_ID,
        runtime_version=runtime_version,
        records=tuple(records),
        sources=tuple(sources),
    )


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _checked_lstat(
    path: Path,
    expected_kind: str | None = None,
    *,
    missing_ok: bool = False,
) -> os.stat_result | None:
    try:
        result = path.lstat()
    except FileNotFoundError as exc:
        if missing_ok:
            return None
        raise MediaDistributionError(
            f"cannot inspect filesystem node {path}: {exc}"
        ) from exc
    except OSError as exc:
        raise MediaDistributionError(
            f"cannot inspect filesystem node {path}: {exc}"
        ) from exc
    if stat.S_ISLNK(result.st_mode):
        raise MediaDistributionError(f"symbolic link is forbidden: {path}")
    if os.name == "nt":
        try:
            attributes = result.st_file_attributes
        except (AttributeError, OSError) as exc:
            raise MediaDistributionError(
                f"Windows file attributes are unavailable: {path}"
            ) from exc
        if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise MediaDistributionError(
                f"Windows reparse object is forbidden: {path}"
            )
    if expected_kind == "directory" and not stat.S_ISDIR(result.st_mode):
        raise MediaDistributionError(f"expected directory: {path}")
    if expected_kind == "file" and not stat.S_ISREG(result.st_mode):
        raise MediaDistributionError(f"expected ordinary file: {path}")
    if expected_kind is None and not (
        stat.S_ISREG(result.st_mode) or stat.S_ISDIR(result.st_mode)
    ):
        raise MediaDistributionError(f"unsupported filesystem node: {path}")
    return result


def _scan_ignored_tree(root: Path) -> None:
    pending = [root]
    while pending:
        directory = pending.pop()
        _checked_lstat(directory, "directory")
        try:
            with os.scandir(directory) as entries:
                children = [Path(entry.path) for entry in entries]
        except OSError as exc:
            raise MediaDistributionError(
                f"cannot enumerate ignored cache {directory}: {exc}"
            ) from exc
        for child in children:
            child_stat = _checked_lstat(child)
            if stat.S_ISDIR(child_stat.st_mode):
                pending.append(child)
            elif child.suffix.casefold() not in {".pyc", ".pyo"}:
                raise MediaDistributionError(
                    "non-cache file is forbidden inside __pycache__: "
                    f"{child}"
                )


def _scan_media_tree(
    media_root: Path,
) -> tuple[dict[str, Path], tuple[str, ...]]:
    found = {}
    folded = set()
    ignored = []
    pending = [media_root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                children = sorted(
                    (Path(entry.path) for entry in entries),
                    key=lambda path: path.name.encode("utf-8"),
                )
        except OSError as exc:
            raise MediaDistributionError(
                f"cannot enumerate media tree {directory}: {exc}"
            ) from exc
        for child in children:
            child_stat = _checked_lstat(child)
            relative = child.relative_to(media_root).as_posix()
            if stat.S_ISDIR(child_stat.st_mode):
                if child.name.casefold() == "__pycache__":
                    _scan_ignored_tree(child)
                    ignored.append(relative)
                else:
                    pending.append(child)
                continue
            if child.suffix.casefold() in {".pyc", ".pyo"}:
                ignored.append(relative)
                continue
            relative_folded = relative.casefold()
            if relative_folded in folded:
                raise MediaDistributionError(
                    "media tree has duplicate case-insensitive path: "
                    f"{relative}"
                )
            folded.add(relative_folded)
            found[relative] = child
    return found, tuple(
        sorted(ignored, key=lambda value: value.encode("utf-8"))
    )


def _hash_path(
    path: Path,
    expected_size: int | None = None,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(_BUFFER_SIZE):
                total += len(chunk)
                if expected_size is not None and total > expected_size:
                    raise MediaDistributionError(
                        f"file exceeds declared size {expected_size}: {path}"
                    )
                digest.update(chunk)
    except MediaDistributionError:
        raise
    except OSError as exc:
        raise MediaDistributionError(
            f"cannot read file {path}: {exc}"
        ) from exc
    return digest.hexdigest().upper(), total


def validate_media_tree(
    app_dir: Path,
    manifest: MediaManifest,
) -> MediaTreeVerification:
    """校验app_dir/tools/media的集合、大小、SHA和既有R79固定布局。"""
    app_root = _absolute(Path(app_dir))
    tools_root = app_root / "tools"
    media_root = tools_root / "media"
    _validate_target_ancestors(
        app_root,
        expected_kind="directory",
        required=True,
    )
    for directory in (tools_root, media_root):
        _checked_lstat(directory, "directory")
    found, ignored = _scan_media_tree(media_root)
    expected_paths = tuple(record.path for record in manifest.records)
    actual_paths = tuple(
        sorted(found, key=lambda value: value.encode("utf-8"))
    )
    if actual_paths != expected_paths:
        missing = sorted(set(expected_paths) - set(actual_paths))
        extra = sorted(set(actual_paths) - set(expected_paths))
        raise MediaDistributionError(
            f"media file set mismatch; missing={missing!r}; extra={extra!r}"
        )

    files = []
    for record in manifest.records:
        source = found[record.path]
        file_stat = _checked_lstat(source, "file")
        if file_stat.st_size != record.size:
            raise MediaDistributionError(
                f"media size mismatch for {record.path}: "
                f"expected {record.size}, got {file_stat.st_size}"
            )
        actual_sha256, actual_size = _hash_path(source, record.size)
        if actual_size != record.size or actual_sha256 != record.sha256:
            raise MediaDistributionError(
                f"media SHA-256 mismatch for {record.path}: "
                f"expected {record.sha256}, got {actual_sha256}"
            )
        files.append(source.absolute())
    try:
        resolve_runtime_layout(app_root)
    except RuntimeLayoutError as exc:
        raise MediaDistributionError(
            f"R79 runtime layout rejected: {exc}"
        ) from exc
    return MediaTreeVerification(
        files=tuple(files),
        ignored=ignored,
        file_count=len(files),
        total_bytes=sum(record.size for record in manifest.records),
    )


def _archive_temporary_path(archive_path: Path) -> Path:
    return archive_path.with_suffix(".zip.tmp")


def _require_new_output(path: Path) -> None:
    if os.path.lexists(path):
        raise FileExistsError(
            f"output already exists and will not be overwritten: {path}"
        )


def _write_archive_member(
    archive: ZipFile,
    info: ZipInfo,
    source: Path,
    record: MediaFileRecord,
) -> None:
    _checked_lstat(source, "file")
    digest = hashlib.sha256()
    total = 0
    try:
        with (
            source.open("rb") as input_stream,
            archive.open(info, "w") as output_stream,
        ):
            while chunk := input_stream.read(_BUFFER_SIZE):
                total += len(chunk)
                if total > record.size:
                    raise MediaDistributionError(
                        "source changed while archiving "
                        f"{record.path}: size exceeds manifest"
                    )
                digest.update(chunk)
                output_stream.write(chunk)
    except MediaDistributionError:
        raise
    except OSError as exc:
        raise MediaDistributionError(
            f"cannot archive media file {record.path}: {exc}"
        ) from exc
    actual_sha256 = digest.hexdigest().upper()
    if total != record.size or actual_sha256 != record.sha256:
        raise MediaDistributionError(
            f"source changed while archiving {record.path}: "
            f"size={total}, sha256={actual_sha256}"
        )


def create_media_archive(
    app_dir: Path,
    manifest: MediaManifest,
    archive_path: Path,
) -> ArchiveVerification:
    """从完整验证的媒体树独占创建确定性ZIP，不覆盖历史输出。"""
    tree = validate_media_tree(app_dir, manifest)
    target = _absolute(Path(archive_path))
    temporary = _archive_temporary_path(target)
    _validate_target_ancestors(
        target.parent,
        expected_kind="directory",
        required=True,
    )
    _require_new_output(target)
    _require_new_output(temporary)
    try:
        with ZipFile(
            temporary,
            mode="x",
            compression=ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=True,
        ) as archive:
            for source, record in zip(
                tree.files,
                manifest.records,
                strict=True,
            ):
                info = ZipInfo(
                    f"{_ARCHIVE_PREFIX}{record.path}",
                    _ZIP_TIMESTAMP,
                )
                info.compress_type = ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                _write_archive_member(archive, info, source, record)
        verification = _verify_media_archive_path(
            temporary,
            manifest,
            None,
        )
        _require_new_output(target)
        temporary.rename(target)
        return verification
    except (MediaDistributionError, OSError, BadZipFile):
        raise
    except Exception as exc:
        raise MediaDistributionError(
            f"cannot create media archive {target}: {exc}"
        ) from exc


def _zip_member_is_symlink(info: ZipInfo) -> bool:
    return stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK


def _verify_media_archive_stream(
    stream: BinaryIO,
    archive_path: Path,
    manifest: MediaManifest,
    expected_sha256: str | None,
) -> ArchiveVerification:
    digest = hashlib.sha256()
    stream.seek(0)
    while chunk := stream.read(_BUFFER_SIZE):
        digest.update(chunk)
    actual_archive_sha256 = digest.hexdigest().upper()
    if expected_sha256 is not None:
        expected = _validate_sha256(expected_sha256, "expected archive")
        if actual_archive_sha256 != expected:
            raise MediaDistributionError(
                "archive SHA-256 mismatch: "
                f"expected {expected}, got {actual_archive_sha256}"
            )

    stream.seek(0)
    try:
        with ZipFile(stream, "r") as archive:
            members = {}
            exact_names = set()
            for info in archive.infolist():
                if info.is_dir():
                    raise MediaDistributionError(
                        "archive directory member is forbidden: "
                        f"{info.filename!r}"
                    )
                if _zip_member_is_symlink(info):
                    raise MediaDistributionError(
                        "archive symbolic link is forbidden: "
                        f"{info.filename!r}"
                    )
                full_name = _validate_relative_path(
                    info.filename,
                    "archive member",
                )
                if not full_name.startswith(_ARCHIVE_PREFIX):
                    raise MediaDistributionError(
                        f"archive member escapes {_ARCHIVE_PREFIX!r}: "
                        f"{full_name!r}"
                    )
                relative = _validate_relative_path(
                    full_name.removeprefix(_ARCHIVE_PREFIX),
                    "archive media member",
                )
                folded = full_name.casefold()
                if folded in members:
                    raise MediaDistributionError(
                        "duplicate case-insensitive archive member: "
                        f"{full_name}"
                    )
                members[folded] = (full_name, relative, info)
                exact_names.add(full_name)

            for full_name in exact_names:
                components = full_name.split("/")
                for index in range(1, len(components)):
                    prefix = "/".join(components[:index]).casefold()
                    if prefix in members:
                        raise MediaDistributionError(
                            f"archive file/directory collision: {full_name}"
                        )

            expected_names = tuple(
                f"{_ARCHIVE_PREFIX}{record.path}"
                for record in manifest.records
            )
            if exact_names != set(expected_names):
                missing = sorted(set(expected_names) - exact_names)
                extra = sorted(exact_names - set(expected_names))
                raise MediaDistributionError(
                    "archive member set mismatch; "
                    f"missing={missing!r}; extra={extra!r}"
                )

            total_bytes = 0
            for record, expected_name in zip(
                manifest.records,
                expected_names,
                strict=True,
            ):
                _full_name, _relative, info = members[
                    expected_name.casefold()
                ]
                if info.file_size != record.size:
                    raise MediaDistributionError(
                        "archive declared size mismatch for "
                        f"{record.path}: expected {record.size}, "
                        f"got {info.file_size}"
                    )
                member_digest = hashlib.sha256()
                actual_size = 0
                with archive.open(info, "r") as member_stream:
                    while chunk := member_stream.read(_BUFFER_SIZE):
                        actual_size += len(chunk)
                        if actual_size > record.size:
                            raise MediaDistributionError(
                                "archive member exceeds size limit: "
                                f"{record.path}"
                            )
                        member_digest.update(chunk)
                actual_sha256 = member_digest.hexdigest().upper()
                if (
                    actual_size != record.size
                    or actual_sha256 != record.sha256
                ):
                    raise MediaDistributionError(
                        "archive member SHA-256 mismatch for "
                        f"{record.path}: size={actual_size}, "
                        f"sha256={actual_sha256}"
                    )
                total_bytes += actual_size
    except MediaDistributionError:
        raise
    except (BadZipFile, OSError, RuntimeError) as exc:
        raise MediaDistributionError(
            f"invalid media archive {archive_path}: {exc}"
        ) from exc
    return ArchiveVerification(
        sha256=actual_archive_sha256,
        file_count=len(manifest.records),
        total_bytes=total_bytes,
    )


def _verify_media_archive_path(
    archive_path: Path,
    manifest: MediaManifest,
    expected_sha256: str | None,
) -> ArchiveVerification:
    path = _absolute(archive_path)
    _validate_target_ancestors(
        path,
        expected_kind="file",
        required=True,
    )
    try:
        with path.open("rb") as stream:
            return _verify_media_archive_stream(
                stream,
                path,
                manifest,
                expected_sha256,
            )
    except MediaDistributionError:
        raise
    except OSError as exc:
        raise MediaDistributionError(
            f"cannot read media archive {path}: {exc}"
        ) from exc


def verify_media_archive(
    archive_path: Path,
    manifest: MediaManifest,
    expected_sha256: str,
) -> ArchiveVerification:
    """先校验整体SHA，再流式校验ZIP完整成员集合和逐文件内容。"""
    return _verify_media_archive_path(
        Path(archive_path),
        manifest,
        expected_sha256,
    )


def _validate_target_ancestors(
    target: Path,
    *,
    expected_kind: str,
    required: bool,
) -> None:
    """从路径根逐级非跟随校验，绝不先探测未验证祖先的后代。"""
    selected = _absolute(target)
    nodes = (*reversed(selected.parents), selected)
    for node in nodes:
        node_kind = expected_kind if node == selected else "directory"
        result = _checked_lstat(
            node,
            node_kind,
            missing_ok=True,
        )
        if result is not None:
            continue
        if not required:
            return
        raise FileNotFoundError(
            f"required filesystem path does not exist: {selected}"
        )


def extract_media_archive(
    archive_path: Path,
    app_dir: Path,
    manifest: MediaManifest,
    expected_sha256: str,
) -> ArchiveVerification:
    """完整验证ZIP后，将其独占解包到新建的app_dir/tools/media。"""
    archive = _absolute(Path(archive_path))
    _validate_target_ancestors(
        archive,
        expected_kind="file",
        required=True,
    )
    try:
        with archive.open("rb") as archive_stream:
            verification = _verify_media_archive_stream(
                archive_stream,
                archive,
                manifest,
                expected_sha256,
            )
            app_root = _absolute(Path(app_dir))
            _validate_target_ancestors(
                app_root,
                expected_kind="directory",
                required=True,
            )
            tools_root = app_root / "tools"
            media_root = tools_root / "media"
            tools_status = _checked_lstat(
                tools_root,
                "directory",
                missing_ok=True,
            )
            if tools_status is None:
                tools_root.mkdir()
                _checked_lstat(tools_root, "directory")
            if os.path.lexists(media_root):
                raise FileExistsError(
                    "media target already exists and will not be overwritten: "
                    f"{media_root}"
                )
            media_root.mkdir()

            archive_stream.seek(0)
            with ZipFile(archive_stream, "r") as package:
                member_by_name = {
                    info.filename: info for info in package.infolist()
                }
                for record in manifest.records:
                    info = member_by_name[
                        f"{_ARCHIVE_PREFIX}{record.path}"
                    ]
                    target = media_root.joinpath(*record.path.split("/"))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    total = 0
                    with (
                        package.open(info, "r") as source,
                        target.open("xb") as output,
                    ):
                        while chunk := source.read(_BUFFER_SIZE):
                            total += len(chunk)
                            if total > record.size:
                                raise MediaDistributionError(
                                    "archive changed during extraction: "
                                    f"{record.path}"
                                )
                            digest.update(chunk)
                            output.write(chunk)
                    if (
                        total != record.size
                        or digest.hexdigest().upper() != record.sha256
                    ):
                        raise MediaDistributionError(
                            "archive changed during extraction: "
                            f"{record.path}"
                        )
            return verification
    except (MediaDistributionError, FileExistsError, FileNotFoundError):
        raise
    except (BadZipFile, OSError, RuntimeError) as exc:
        raise MediaDistributionError(
            f"cannot extract media archive {archive}: {exc}"
        ) from exc


class _ArgumentParser(argparse.ArgumentParser):
    def exit(
        self,
        status: int = 0,
        message: str | None = None,
    ) -> None:
        if message:
            self._print_message(message, sys.stderr)
        raise SystemExit(status)


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="media_distribution.py",
        description="Validate and distribute the fixed R79 media tool asset",
    )
    parser.add_argument("--version", action="version", version=ASSET_ID)
    subparsers = parser.add_subparsers(dest="command", required=True)

    tree = subparsers.add_parser("verify-tree")
    tree.add_argument("--app-dir", type=Path, required=True)
    tree.add_argument("--manifest", type=Path, required=True)

    create = subparsers.add_parser("create-archive")
    create.add_argument("--app-dir", type=Path, required=True)
    create.add_argument("--manifest", type=Path, required=True)
    create.add_argument("--archive", type=Path, required=True)

    verify = subparsers.add_parser("verify-archive")
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--sha256", required=True)

    extract = subparsers.add_parser("extract-archive")
    extract.add_argument("--archive", type=Path, required=True)
    extract.add_argument("--app-dir", type=Path, required=True)
    extract.add_argument("--manifest", type=Path, required=True)
    extract.add_argument("--sha256", required=True)
    return parser


def _tree_result(result: MediaTreeVerification) -> dict[str, Any]:
    return {
        "files": [str(path) for path in result.files],
        "ignored": list(result.ignored),
        "file_count": result.file_count,
        "total_bytes": result.total_bytes,
    }


def _archive_result(result: ArchiveVerification) -> dict[str, Any]:
    return {
        "sha256": result.sha256,
        "file_count": result.file_count,
        "total_bytes": result.total_bytes,
    }


def main(argv: list[str] | None = None) -> int:
    """运行非交互CLI；stdout仅输出一次成功JSON。"""
    parser = _build_parser()
    try:
        try:
            arguments = parser.parse_args(argv)
        except SystemExit as exc:
            return int(exc.code or 0)
        manifest = load_manifest(arguments.manifest)
        if arguments.command == "verify-tree":
            payload = _tree_result(
                validate_media_tree(arguments.app_dir, manifest)
            )
        elif arguments.command == "create-archive":
            payload = _archive_result(
                create_media_archive(
                    arguments.app_dir,
                    manifest,
                    arguments.archive,
                )
            )
        elif arguments.command == "verify-archive":
            payload = _archive_result(
                verify_media_archive(
                    arguments.archive,
                    manifest,
                    arguments.sha256,
                )
            )
        else:
            payload = _archive_result(
                extract_media_archive(
                    arguments.archive,
                    arguments.app_dir,
                    manifest,
                    arguments.sha256,
                )
            )
        print(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except KeyboardInterrupt:
        print("operation cancelled", file=sys.stderr)
        return 130
    except (MediaDistributionError, OSError, ValueError, BadZipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


__all__ = [
    "ArchiveVerification",
    "MediaDistributionError",
    "MediaFileRecord",
    "MediaManifest",
    "MediaSource",
    "MediaTreeVerification",
    "create_media_archive",
    "extract_media_archive",
    "load_manifest",
    "main",
    "validate_media_tree",
    "verify_media_archive",
]


if __name__ == "__main__":
    raise SystemExit(main())
