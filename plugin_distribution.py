"""Install the locked VapourSynth plugin set beside the packaged media runtime."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


_PACKAGE_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_INSTALL_ROOT = Path("tools/vs-plugins")
_LOCK_PATH = Path("resources/packaging/vs-plugins.json")


class PluginDistributionError(RuntimeError):
    """A locked plugin is missing, invalid, or could not be installed."""


def _safe_relative(value: str) -> Path:
    item = PurePosixPath(value)
    if not value or item.is_absolute() or any(part in ("", ".", "..") for part in item.parts):
        raise PluginDistributionError(f"Unsafe manifest path: {value!r}")
    if "\\" in value or ":" in value:
        raise PluginDistributionError(f"Unsafe manifest path: {value!r}")
    return Path(*item.parts)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(
    app_dir: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> dict:
    """Read the product lock, or an explicit lock used by a focused test."""
    base = Path(app_dir).resolve() if app_dir is not None else Path(__file__).resolve().parent
    source = Path(manifest_path).resolve() if manifest_path else base / _LOCK_PATH
    manifest = json.loads(source.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("target") != "win64":
        raise PluginDistributionError(f"Unsupported plugin manifest: {source}")
    if manifest.get("install_root") != _INSTALL_ROOT.as_posix():
        raise PluginDistributionError(f"Unexpected plugin install root: {source}")
    packages = manifest.get("packages")
    if not isinstance(packages, list):
        raise PluginDistributionError("Missing packages list")
    ids: set[str] = set()
    for package in packages:
        package_id = package.get("id")
        if not isinstance(package_id, str) or not _PACKAGE_ID.fullmatch(package_id):
            raise PluginDistributionError(f"Invalid package id: {package_id!r}")
        if package_id in ids:
            raise PluginDistributionError(f"Duplicate package id: {package_id}")
        ids.add(package_id)
        if not package.get("url", "").startswith("https://"):
            raise PluginDistributionError(f"Non-HTTPS source: {package_id}")
        if package.get("archive_sha256") is not None and not _SHA256.fullmatch(
            package["archive_sha256"]
        ):
            raise PluginDistributionError(f"Invalid archive hash: {package_id}")
        for file in package.get("files", []):
            _safe_relative(file["path"])
            _safe_relative(file["member"])
            if not _SHA256.fullmatch(file["sha256"]):
                raise PluginDistributionError(f"Invalid file hash: {package_id}")
    for package in packages:
        for dependency in (*package.get("dependencies", []), *package.get("adjacent_from", [])):
            if dependency not in ids:
                raise PluginDistributionError(
                    f"Unknown dependency {dependency!r} for {package['id']}"
                )
    return manifest


def _install_root(app_dir: str | Path) -> Path:
    base = Path(app_dir).resolve()
    root = base / _INSTALL_ROOT
    if root.is_symlink() or (root.exists() and root.is_junction()):
        raise PluginDistributionError(f"Plugin root is a link: {root}")
    return root


def _checked_path(root: Path, target: Path) -> Path:
    """Reject a junction or link that would redirect a managed path outside root."""
    resolved_root = root.resolve()
    resolved_target = target.resolve()
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError as error:
        raise PluginDistributionError(
            f"Managed plugin path escapes install root: {target}"
        ) from error
    if resolved_target == resolved_root:
        raise PluginDistributionError(f"Expected child of plugin root: {target}")
    return target


def _remove_managed_entry(root: Path, target: Path) -> None:
    _checked_path(root, target)
    if target.is_dir() and not target.is_symlink() and not target.is_junction():
        shutil.rmtree(target)
    elif target.exists() or target.is_symlink():
        target.unlink()


def _prune_unlocked(root: Path, package_ids: set[str]) -> None:
    recoverable = {
        f".{package_id}.{suffix}"
        for package_id in package_ids
        for suffix in ("partial", "backup")
    }
    for child in root.iterdir():
        if child.name not in package_ids and child.name not in recoverable:
            _remove_managed_entry(root, child)


def _expected_files(package: dict, packages: dict[str, dict]) -> list[dict]:
    files = list(package["files"])
    for dependency in package.get("adjacent_from", []):
        files.extend(packages[dependency]["files"])
    paths = [item["path"].casefold() for item in files]
    if len(paths) != len(set(paths)):
        raise PluginDistributionError(f"Adjacent file collision in {package['id']}")
    return files


def _verify_package(directory: Path, package: dict, packages: dict[str, dict]) -> list[Path]:
    result = []
    expected = _expected_files(package, packages)
    for item in expected:
        file = directory / _safe_relative(item["path"])
        if not file.is_file() or file.is_symlink() or _sha256(file) != item["sha256"]:
            raise PluginDistributionError(f"{package['id']}: missing or invalid {item['path']}")
        result.append(file)
    expected_names = {item["path"].casefold() for item in expected}
    actual_names = {
        file.relative_to(directory).as_posix().casefold()
        for file in directory.rglob("*")
        if file.is_file() or file.is_symlink() or file.is_junction()
    }
    if actual_names != expected_names:
        extras = sorted(actual_names - expected_names)
        raise PluginDistributionError(
            f"{package['id']}: unexpected package files: {', '.join(extras)}"
        )
    return result


def verify_plugins(
    app_dir: str | Path,
    manifest_path: str | Path | None = None,
) -> list[Path]:
    """Return every verified installed file, including adjacent weights and models."""
    manifest = load_manifest(app_dir, manifest_path)
    packages = {item["id"]: item for item in manifest["packages"]}
    root = _install_root(app_dir)
    result: list[Path] = []
    for package in manifest["packages"]:
        result.extend(_verify_package(root / package["id"], package, packages))
    return result


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "AssetMaker-plugin-sync"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                with destination.open("wb") as stream:
                    shutil.copyfileobj(response, stream, length=1024 * 1024)
            return
        except urllib.error.HTTPError as error:
            if error.code not in (408, 429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (
            urllib.error.URLError,
            http.client.RemoteDisconnected,
            TimeoutError,
            ConnectionError,
        ):
            if attempt == 2:
                raise
        destination.unlink(missing_ok=True)
        time.sleep(attempt + 1)


def _extract_selected(archive: Path, package: dict, output: Path, seven_zip: Path) -> None:
    suffix = PurePosixPath(package["url"].split("?", 1)[0]).suffix.lower()
    members = package["files"]
    if suffix in (".zip", ".whl"):
        with zipfile.ZipFile(archive) as zipped:
            for item in members:
                target = output / _safe_relative(item["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(item["member"]) as source, target.open("wb") as dest:
                    shutil.copyfileobj(source, dest)
    elif suffix == ".7z":
        if not seven_zip.is_file():
            raise PluginDistributionError(f"7z.exe is missing: {seven_zip}")
        unpacked = output / ".unpacked"
        unpacked.mkdir()
        command = [
            str(seven_zip), "x", "-y", f"-o{unpacked}", str(archive),
            *(item["member"] for item in members),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode:
            raise PluginDistributionError(
                f"{package['id']}: 7z exit {completed.returncode}: "
                f"{completed.stderr or completed.stdout}"
            )
        for item in members:
            source = unpacked / _safe_relative(item["member"])
            target = output / _safe_relative(item["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(source, target)
        _checked_path(output, unpacked)
        shutil.rmtree(unpacked)
    elif suffix == ".dll" and len(members) == 1:
        shutil.copyfile(archive, output / _safe_relative(members[0]["path"]))
    else:
        raise PluginDistributionError(f"Unsupported archive format: {package['url']}")


def _recover_package(root: Path, package_id: str) -> None:
    destination = root / package_id
    backup = root / f".{package_id}.backup"
    partial = root / f".{package_id}.partial"
    for target in (destination, backup, partial):
        _checked_path(root, target)
    if backup.exists() and not destination.exists():
        backup.rename(destination)
    if backup.exists():
        _remove_managed_entry(root, backup)
    if partial.exists():
        _remove_managed_entry(root, partial)


def _install_package(
    root: Path,
    package: dict,
    packages: dict[str, dict],
    downloads: Path,
    archive_cache: dict[str, Path],
    seven_zip: Path,
) -> str:
    package_id = package["id"]
    destination = root / package_id
    partial = root / f".{package_id}.partial"
    backup = root / f".{package_id}.backup"
    _recover_package(root, package_id)
    try:
        _verify_package(destination, package, packages)
        return "already verified"
    except PluginDistributionError:
        pass
    url = package["url"]
    if url not in archive_cache:
        archive = downloads / f"archive-{len(archive_cache)}"
        _download(url, archive)
        archive_cache[url] = archive
    archive = archive_cache[url]
    if package["archive_sha256"] and _sha256(archive) != package["archive_sha256"]:
        raise PluginDistributionError(f"{package_id}: archive SHA-256 mismatch")
    partial.mkdir()
    try:
        _extract_selected(archive, package, partial, seven_zip)
        for dependency in package.get("adjacent_from", []):
            for item in packages[dependency]["files"]:
                source = root / dependency / _safe_relative(item["path"])
                target = partial / _safe_relative(item["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        _verify_package(partial, package, packages)
        if destination.exists():
            _checked_path(root, destination)
            _checked_path(root, backup)
            destination.rename(backup)
        try:
            _checked_path(root, partial)
            _checked_path(root, destination)
            partial.rename(destination)
        except OSError:
            if backup.exists() and not destination.exists():
                backup.rename(destination)
            raise
        if backup.exists():
            _remove_managed_entry(root, backup)
        return "installed"
    finally:
        if partial.exists():
            _remove_managed_entry(root, partial)


def _ordered_packages(packages: dict[str, dict]) -> list[dict]:
    ordered = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(package_id: str) -> None:
        if package_id in visited:
            return
        if package_id in visiting:
            raise PluginDistributionError(f"Dependency cycle at {package_id}")
        visiting.add(package_id)
        package = packages[package_id]
        for dependency in package.get("dependencies", []):
            visit(dependency)
        ordered.append(package)
        visiting.remove(package_id)
        visited.add(package_id)

    for package_id in packages:
        visit(package_id)
    return ordered


def sync_plugins(
    app_dir: str | Path,
    manifest_path: str | Path | None = None,
) -> list[Path]:
    """Converge each package separately; continue independent packages after errors."""
    manifest = load_manifest(app_dir, manifest_path)
    packages = {item["id"]: item for item in manifest["packages"]}
    root = _install_root(app_dir)
    root.mkdir(parents=True, exist_ok=True)
    _prune_unlocked(root, set(packages))
    seven_zip = Path(app_dir).resolve() / "tools/media/7z.exe"
    failures: dict[str, str] = {}
    archive_cache: dict[str, Path] = {}
    with tempfile.TemporaryDirectory(prefix=".downloads-", dir=root) as temp:
        downloads = Path(temp)
        for index, package in enumerate(_ordered_packages(packages), start=1):
            package_id = package["id"]
            print(f"[{index}/{len(packages)}] {package_id}: start", flush=True)
            failed_dependency = next(
                (item for item in package.get("dependencies", []) if item in failures),
                None,
            )
            if failed_dependency:
                failures[package_id] = f"dependency {failed_dependency} failed"
                print(
                    f"[{index}/{len(packages)}] {package_id}: "
                    f"failed ({failures[package_id]})",
                    flush=True,
                )
                continue
            try:
                outcome = _install_package(
                    root, package, packages, downloads, archive_cache, seven_zip
                )
                print(
                    f"[{index}/{len(packages)}] {package_id}: {outcome}",
                    flush=True,
                )
            except (OSError, ValueError, KeyError, zipfile.BadZipFile, PluginDistributionError) as error:
                failures[package_id] = str(error)
                print(
                    f"[{index}/{len(packages)}] {package_id}: failed ({error})",
                    flush=True,
                )
    if failures:
        details = "\n".join(f"  {key}: {value}" for key, value in failures.items())
        raise PluginDistributionError(f"Plugin sync failed for {len(failures)} packages:\n{details}")
    return verify_plugins(app_dir, manifest_path)


def clean_plugins(app_dir: str | Path) -> None:
    """Remove only this tool's install root."""
    root = _install_root(app_dir)
    if root.exists():
        for child in root.iterdir():
            _remove_managed_entry(root, child)
        root.rmdir()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("sync", "verify", "clean"))
    parser.add_argument("--app-dir", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "sync":
            files = sync_plugins(args.app_dir, args.manifest)
        elif args.action == "verify":
            files = verify_plugins(args.app_dir, args.manifest)
        else:
            clean_plugins(args.app_dir)
            print("Plugin install root removed")
            return 0
    except (OSError, ValueError, KeyError, PluginDistributionError) as error:
        print(error, file=sys.stderr)
        return 1
    print(f"Verified {len(files)} plugin files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
