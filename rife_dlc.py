"""Package the verified optional RIFE files without loading native plugins."""

from __future__ import annotations

import argparse
import os
import tempfile
import zipfile
from pathlib import Path

from plugin_distribution import PluginDistributionError, load_manifest, verify_plugins


_MANIFEST = Path("resources/packaging/vs-rife.json")
_SCRIPT = Path("resources/vapoursynth/rife_pipeline.vpy")
_README_NAME = "RIFE-DLC-README.txt"
_README = """AssetMaker optional RIFE DLC

Extract this ZIP into the actual AssetMaker application folder, preserving paths.
The tools/vs-rife and resources folders must be inside that application folder.

Requirements: a Vulkan-capable GPU and working Vulkan driver, plus the Microsoft
Visual C++ v14 Redistributable (x64). Packaging does not require a GPU.

Edit the Windows local configuration override at:
%APPDATA%\\ArknightsPassMaker\\vapoursynth\\vs_runtime.user.json
Add the absolute <app>\\tools\\vs-rife\\rife directory to
plugins.native_plugin_dirs. Merge this setting into the existing JSON rather
than replacing your existing configuration. Replace <app> with the actual
application folder, and escape backslashes in JSON. For example:
{"plugins": {"native_plugin_dirs": ["C:\\\\AssetMaker\\\\tools\\\\vs-rife\\\\rife"]}}
Equivalent directory notation: native_plugin_dirs=<app>/tools/vs-rife/rife

Select VapourSynth 脚本 → 来源 → 全局本机脚本 and choose
resources/vapoursynth/rife_pipeline.vpy, or copy that script into your project
and select the project script. Edit the script's documented RIFE controls there.

The base application's default pipeline stays unchanged. RIFE is used only
when you select this optional script. Keep this manifest with the DLC so its
pinned binaries, models, dependencies, and licenses can be verified.
"""


def create_rife_dlc(app_dir: str | Path, output: str | Path) -> Path:
    """Create a standalone DLC ZIP from synced files; never overwrite output."""
    app = Path(app_dir).resolve()
    destination = Path(output).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"RIFE DLC output already exists: {destination}")

    manifest = app / _MANIFEST
    if load_manifest(app, manifest)["install_root"] != "tools/vs-rife":
        raise PluginDistributionError("RIFE DLC manifest must install into tools/vs-rife")
    files = verify_plugins(app, manifest)
    files.extend((manifest, app / _SCRIPT))
    members: dict[str, Path] = {}
    for source in files:
        # Also check the script/manifest; never follow a path outside the app.
        source.resolve().relative_to(app)
        if not source.is_file() or source.is_symlink():
            raise FileNotFoundError(f"RIFE DLC source is missing or linked: {source}")
        members[source.relative_to(app).as_posix()] = source

    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".partial", dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, source in sorted(members.items()):
                archive.write(source, name)
            archive.writestr(_README_NAME, _README)
        with zipfile.ZipFile(temporary) as archive:
            expected = set(members) | {_README_NAME}
            if set(archive.namelist()) != expected or len(archive.namelist()) != len(expected):
                raise PluginDistributionError("RIFE DLC ZIP has unexpected members")
            bad_member = archive.testzip()
            if bad_member is not None:
                raise PluginDistributionError(f"RIFE DLC ZIP failed CRC check: {bad_member}")
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"RIFE DLC output already exists: {destination}")
        temporary.rename(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        output = create_rife_dlc(args.app_dir, args.output)
    except (OSError, ValueError, PluginDistributionError, zipfile.BadZipFile) as error:
        parser.exit(1, f"RIFE DLC packaging failed: {error}\n")
    print(f"RIFE DLC ZIP: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
