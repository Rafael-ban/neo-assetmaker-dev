"""U1-A：R79 portable 产品布局与 runtime 身份确定性门禁。"""

from __future__ import annotations

import tempfile
import unittest
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path
from unittest import mock


_MANDATORY_FILES = {
    "python.exe": b"python-exe",
    "pythonw.exe": b"pythonw-exe",
    "python3.dll": b"python-abi",
    "python312.dll": b"python-library",
    "python312.zip": b"stdlib",
    "python312._pth": b"python-path",
    "Lib/site-packages/vapoursynth/__init__.py": b"from .vapoursynth import *\n",
    "Lib/site-packages/vapoursynth/_utils.py": b"",
    "Lib/site-packages/vapoursynth/vapoursynth.pyd": b"binding",
    "Lib/site-packages/vapoursynth/libvapoursynth.dll": b"core",
    "Lib/site-packages/vapoursynth/libvapoursynthfilters.dll": b"filters",
    "Lib/site-packages/vapoursynth/libvapoursynthfilters_avx2.dll": b"filters-avx2",
    "Lib/site-packages/vapoursynth/libvapoursynthfilters_zn4.dll": b"filters-zn4",
    "Lib/site-packages/vapoursynth/vsscript.dll": b"vsscript",
    "Lib/site-packages/vapoursynth/vspipe.exe": b"vspipe",
    "Lib/site-packages/vapoursynth/plugins/avscompat.dll": b"avscompat",
    "Lib/site-packages/vapoursynth-79.dist-info/METADATA": (
        b"Metadata-Version: 2.4\nName: VapourSynth\nVersion: 79\n"
    ),
    "Lib/site-packages/vapoursynth-79.dist-info/RECORD": b"record\n",
    "Lib/site-packages/vapoursynth-79.dist-info/WHEEL": b"Tag: cp312-abi3-win_amd64\n",
    "native-plugins/01-lsmas/LSMASHSource.dll": b"lsmas",
    "native-plugins/02-imwri/libimwri.dll": b"imwri",
}
_EXTRA_DISTRIBUTION_FILES = (
    "Lib/site-packages/vapoursynth-79.dist-info/entry_points.txt",
    "Lib/site-packages/vapoursynth-79.dist-info/licenses/COPYING.LESSER",
    "Lib/site-packages/vapoursynth/__main__.py",
    "Lib/site-packages/vapoursynth/_cli.py",
    "Lib/site-packages/vapoursynth/_shell.py",
    "Lib/site-packages/vapoursynth/include/VSConstants4.h",
    "Lib/site-packages/vapoursynth/include/VSHelper4.h",
    "Lib/site-packages/vapoursynth/include/VSScript4.h",
    "Lib/site-packages/vapoursynth/include/VapourSynth4.h",
    "Lib/site-packages/vapoursynth/libvapoursynth.pdb",
    "Lib/site-packages/vapoursynth/libvapoursynthfilters.pdb",
    "Lib/site-packages/vapoursynth/libvapoursynthfilters_avx2.pdb",
    "Lib/site-packages/vapoursynth/libvapoursynthfilters_zn4.pdb",
    "Lib/site-packages/vapoursynth/pkgconfig/vapoursynth.pc",
    "Lib/site-packages/vapoursynth/vapoursynth.pyi",
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
    "pyexpat.pyd",
    "python.cat",
    "select.pyd",
    "sqlite3.dll",
    "unicodedata.pyd",
    "vccorlib140.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "vcruntime140_threads.dll",
    "winsound.pyd",
)


def _build_r79_fixture(root: Path, *, identity_count: int = 74) -> Path:
    runtime = root / "tools" / "media" / "runtime"
    for relative, content in _MANDATORY_FILES.items():
        path = runtime / Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    for relative in _EXTRA_DISTRIBUTION_FILES:
        path = runtime / Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode("utf-8"))
    extra_count = identity_count - len(_MANDATORY_FILES) - len(
        _EXTRA_DISTRIBUTION_FILES
    )
    if extra_count < 0:
        raise AssertionError("identity_count 小于必需资产数")
    for index in range(extra_count):
        path = runtime / "embedded" / f"asset-{index:02d}.pyd"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"asset-{index}".encode("ascii"))

    helper = root / "resources" / "vapoursynth" / "python" / "assetmaker_vs"
    helper.mkdir(parents=True)
    (helper / "contract.py").write_text("VALUE = 1\n", encoding="utf-8")
    return runtime


def _runtime_layout_api():
    try:
        from resources.vapoursynth.python.assetmaker_vs.runtime_layout import (
            RuntimeLayoutError,
            resolve_runtime_layout,
        )
    except ModuleNotFoundError as exc:
        raise AssertionError("R79 RuntimeLayout 尚未实现") from exc
    return RuntimeLayoutError, resolve_runtime_layout


class RuntimeLayoutTests(unittest.TestCase):
    def test_resolves_frozen_r79_paths_and_all_74_distribution_files(self):
        """错误实现会继续返回 flat 路径或漏掉非最小启动资产。"""
        _error, resolve_runtime_layout = _runtime_layout_api()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime = _build_r79_fixture(root)

            layout = resolve_runtime_layout(root)

            package = runtime / "Lib" / "site-packages" / "vapoursynth"
            self.assertEqual(layout.runtime_root, runtime.resolve())
            self.assertEqual(layout.package_entry, package / "__init__.py")
            self.assertEqual(layout.binding, package / "vapoursynth.pyd")
            self.assertEqual(layout.core, package / "libvapoursynth.dll")
            self.assertEqual(layout.vspipe_executable, package / "vspipe.exe")
            self.assertEqual(layout.vsscript_library, package / "vsscript.dll")
            self.assertEqual(
                layout.bundled_native_plugin_dirs,
                (
                    runtime / "native-plugins" / "01-lsmas",
                    runtime / "native-plugins" / "02-imwri",
                ),
            )
            self.assertEqual(len(layout.identity_files()), 74)
            self.assertEqual(
                layout.vsscript_library.parents[3], layout.runtime_root
            )

    def test_rejects_each_missing_execution_asset_with_exact_path(self):
        """漏掉任一执行闭包资产时必须在启动上游前给出准确路径。"""
        RuntimeLayoutError, resolve_runtime_layout = _runtime_layout_api()
        missing_relatives = (
            "python.exe",
            "python3.dll",
            "python312.dll",
            "python312.zip",
            "python312._pth",
            "Lib/site-packages/vapoursynth/__init__.py",
            "Lib/site-packages/vapoursynth/vapoursynth.pyd",
            "Lib/site-packages/vapoursynth/libvapoursynth.dll",
            "Lib/site-packages/vapoursynth/libvapoursynthfilters.dll",
            "Lib/site-packages/vapoursynth/libvapoursynthfilters_avx2.dll",
            "Lib/site-packages/vapoursynth/libvapoursynthfilters_zn4.dll",
            "Lib/site-packages/vapoursynth/vsscript.dll",
            "Lib/site-packages/vapoursynth/vspipe.exe",
            "Lib/site-packages/vapoursynth/plugins/avscompat.dll",
            "Lib/site-packages/vapoursynth-79.dist-info/METADATA",
            "Lib/site-packages/vapoursynth-79.dist-info/RECORD",
            "native-plugins/01-lsmas/LSMASHSource.dll",
            "native-plugins/02-imwri/libimwri.dll",
        )
        for relative in missing_relatives:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                runtime = _build_r79_fixture(root)
                missing = runtime / Path(relative)
                missing.unlink()

                with self.assertRaises(RuntimeLayoutError) as raised:
                    resolve_runtime_layout(root)

                self.assertIn(str(missing.resolve()), str(raised.exception))

    def test_rejects_flat_only_mixed_and_non_r79_distributions(self):
        """不得从旧 flat、混合树或其它版本 metadata 静默选一个继续。"""
        RuntimeLayoutError, resolve_runtime_layout = _runtime_layout_api()
        with tempfile.TemporaryDirectory() as temp_dir:
            flat_root = Path(temp_dir)
            media = flat_root / "tools" / "media"
            media.mkdir(parents=True)
            (media / "vapoursynth.pyd").write_bytes(b"r73")
            with self.assertRaisesRegex(RuntimeLayoutError, "flat"):
                resolve_runtime_layout(flat_root)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime = _build_r79_fixture(root)
            flat = root / "tools" / "media" / "vapoursynth.dll"
            flat.write_bytes(b"r73")
            with self.assertRaises(RuntimeLayoutError) as raised:
                resolve_runtime_layout(root)
            self.assertIn(str(flat.resolve()), str(raised.exception))

            flat.unlink()
            metadata = runtime / "Lib/site-packages/vapoursynth-79.dist-info/METADATA"
            metadata.write_text("Name: VapourSynth\nVersion: 78\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeLayoutError, "R79"):
                resolve_runtime_layout(root)

    def test_identity_ignores_generated_cache_logs_samples_and_lwi(self):
        """派生缓存与探针产物不能造成会话身份漂移。"""
        _error, resolve_runtime_layout = _runtime_layout_api()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime = _build_r79_fixture(root)
            before = resolve_runtime_layout(root).identity_files()
            ignored = (
                runtime / "__pycache__" / "generated.pyc",
                runtime / "logs" / "vspipe.log",
                runtime / "samples" / "probe.y4m",
                runtime / "native-plugins" / "01-lsmas" / "index.lwi",
            )
            for path in ignored:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"generated")

            self.assertEqual(resolve_runtime_layout(root).identity_files(), before)


class R79RuntimeFingerprintTests(unittest.TestCase):
    def test_fingerprint_tracks_distribution_and_ignores_pycache(self):
        """核心、manifest、VSScript、Python 与 builtin 篡改均必须被检测。"""
        from config.vs_runtime import VSRuntimeConfig
        from core.vs_runtime.vs_loader import compute_runtime_fingerprint

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime = _build_r79_fixture(root)
            initial = compute_runtime_fingerprint(root, VSRuntimeConfig())
            cache = runtime / "__pycache__" / "generated.pyc"
            cache.parent.mkdir()
            cache.write_bytes(b"cache")
            self.assertEqual(
                compute_runtime_fingerprint(root, VSRuntimeConfig()), initial
            )

            mutations = (
                "Lib/site-packages/vapoursynth/libvapoursynthfilters_zn4.dll",
                "Lib/site-packages/vapoursynth-79.dist-info/RECORD",
                "Lib/site-packages/vapoursynth/vsscript.dll",
                "python3.dll",
                "Lib/site-packages/vapoursynth/plugins/avscompat.dll",
            )
            previous = initial
            for relative in mutations:
                path = runtime / Path(relative)
                path.write_bytes(path.read_bytes() + b"-changed")
                current = compute_runtime_fingerprint(root, VSRuntimeConfig())
                self.assertNotEqual(current, previous, relative)
                previous = current


class R79ConsumerWiringTests(unittest.TestCase):
    def test_export_gate_preserves_exact_layout_preflight_error(self):
        """缺失非 VSPipe 资产时，导出 gate 不能退化成泛化的 VSPipe 缺失。"""
        from core.media_tools import MediaToolchain

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime = _build_r79_fixture(root)
            missing = runtime / "libcrypto-3.dll"
            missing.unlink()
            MediaToolchain.refresh()
            self.addCleanup(MediaToolchain.refresh)

            toolchain = MediaToolchain.discover(root)

            self.assertEqual(len(toolchain.missing_for_export()), 3)
            self.assertIn(
                str(missing.resolve()), toolchain.missing_for_export()[0]
            )

    def test_media_discovery_uses_package_vspipe_and_never_path_fallback(self):
        """VSPipe 只能来自同一已验证 layout；PATH 上的另一个 VS 不得入选。"""
        from core.media_tools import MediaToolchain

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime = _build_r79_fixture(root)
            media = root / "tools" / "media"
            (media / "x264-7mod.exe").write_bytes(b"x264")
            (media / "MP4Box.exe").write_bytes(b"mux")
            external = root / "external" / "VSPipe.exe"
            external.parent.mkdir()
            external.write_bytes(b"external")
            MediaToolchain.refresh()
            self.addCleanup(MediaToolchain.refresh)

            with mock.patch("core.media_tools.shutil.which", return_value=str(external)):
                toolchain = MediaToolchain.discover(root)

            self.assertEqual(
                Path(toolchain.vspipe_path),
                runtime
                / "Lib"
                / "site-packages"
                / "vapoursynth"
                / "vspipe.exe",
            )
            self.assertEqual(Path(toolchain.x264_path), media / "x264-7mod.exe")
            self.assertEqual(Path(toolchain.muxer_path), media / "MP4Box.exe")

    def test_vspipe_env_uses_layout_and_clears_external_vs_python_search(self):
        """错误接线会接受 flat exe 或把宿主 VS/Python 搜索路径传给 VSPipe。"""
        from config.vs_runtime import PluginConfig, VSRuntimeConfig
        from core.media_pipeline import build_vspipe_render_env
        from resources.vapoursynth.python.assetmaker_vs.runtime_fingerprint import (
            RUNTIME_MEDIA_ROOT_ENV,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_root = _build_r79_fixture(root)
            package = runtime_root / "Lib" / "site-packages" / "vapoursynth"
            configured = root / "配置 插件"
            configured.mkdir()
            runtime = VSRuntimeConfig(
                plugins=PluginConfig(python_module_dirs=(str(configured),))
            )
            inherited = {
                "VAPOURSYNTH_PLUGIN_PATH": "external-plugin",
                "VAPOURSYNTH_EXTRA_PLUGIN_PATH": "external-extra",
                "VAPOURSYNTH_PYTHON_PATH": "external-python",
                "VAPOURSYNTH_CONF_PATH": "external-config",
                "PYTHONHOME": "external-home",
                "PYTHONPATH": "external-path",
            }
            with mock.patch.dict(os.environ, inherited, clear=False):
                env = build_vspipe_render_env(
                    str(package / "vspipe.exe"),
                    app_dir=str(root),
                    runtime=runtime,
                    expected_fingerprint="a" * 64,
                )

            for name in (
                "VAPOURSYNTH_PLUGIN_PATH",
                "VAPOURSYNTH_PYTHON_PATH",
                "VAPOURSYNTH_CONF_PATH",
                "PYTHONHOME",
                "PYTHONPATH",
            ):
                self.assertNotIn(name, env)
            self.assertEqual(env["VAPOURSYNTH_EXTRA_PLUGIN_PATH"], "")
            self.assertEqual(
                json.loads(env["ASSETMAKER_VS_PYTHON_DIRS_JSON"]),
                [str(configured)],
            )
            self.assertEqual(env[RUNTIME_MEDIA_ROOT_ENV], str(root / "tools/media"))
            path_entries = env["PATH"].split(os.pathsep)
            self.assertEqual(path_entries[:2], [str(package), str(runtime_root)])

    def test_mixed_case_pythonpath_does_not_reach_absolute_python_child(self):
        """Windows 混合大小写 PYTHONPATH 不得进入解释器启动时的 sys.path。"""
        from resources.vapoursynth.python.assetmaker_vs.runtime_layout import (
            resolve_runtime_layout,
            sanitize_runtime_process_environment,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _build_r79_fixture(root)
            sentinel = root / "mixed-pythonpath-sentinel"
            sentinel.mkdir()
            base_environment = dict(os.environ)
            for name in tuple(base_environment):
                if name.casefold() == "pythonpath":
                    base_environment.pop(name)
            base_environment["pythonpath"] = str(sentinel)
            env = sanitize_runtime_process_environment(
                base_environment, resolve_runtime_layout(root)
            )
            code = (
                "import json, os, sys; "
                f"sentinel=os.path.normcase(os.path.abspath({str(sentinel)!r})); "
                "paths=[os.path.normcase(os.path.abspath(path)) "
                "for path in sys.path if path]; "
                "print(json.dumps({'pythonpath_keys': [name for name in os.environ "
                "if name.casefold() == 'pythonpath'], "
                "'sentinel_in_sys_path': sentinel in paths}))"
            )

            child = subprocess.run(
                [str(Path(sys.executable).resolve()), "-B", "-c", code],
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

        self.assertEqual(child.returncode, 0, child.stderr)
        payload = json.loads(child.stdout)
        self.assertEqual(payload["pythonpath_keys"], [])
        self.assertFalse(payload["sentinel_in_sys_path"])

    def test_worker_loader_executes_official_package_entry_and_all_native_dirs(self):
        """错误 loader 会直接载入 pyd，或漏掉随包/配置 native 目录。"""
        from config.vs_runtime import PluginConfig, VSRuntimeConfig
        from core.vs_runtime import vs_loader

        class FakeStd:
            def __init__(self) -> None:
                self.loaded: list[str] = []

            def LoadAllPlugins(self, *, path: str) -> None:
                self.loaded.append(path)

        class FakeCore:
            def __init__(self) -> None:
                self.std = FakeStd()
                self.num_threads = 4
                self.max_cache_size = 1024

            def add_log_handler(self, _callback):
                return object()

            def remove_log_handler(self, _handle) -> None:
                return None

            def plugins(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_root = _build_r79_fixture(root)
            package = runtime_root / "Lib" / "site-packages" / "vapoursynth"
            configured = (root / "用户原生一", root / "用户原生二")
            for directory in configured:
                directory.mkdir()
            runtime = VSRuntimeConfig(
                plugins=PluginConfig(
                    native_plugin_dirs=tuple(map(str, configured))
                )
            )
            core = FakeCore()
            module = SimpleNamespace(core=core)
            seen: dict[str, object] = {}

            def fake_spec(name, location, **kwargs):
                seen["name"] = name
                seen["location"] = location
                seen["search"] = kwargs.get("submodule_search_locations")
                return SimpleNamespace(
                    loader=SimpleNamespace(exec_module=lambda _module: None)
                )

            handles: list[str] = []

            def fake_add_dll_directory(path: str):
                handles.append(path)
                return SimpleNamespace(close=lambda: None)

            previous_vs = sys.modules.pop("vapoursynth", None)
            self.addCleanup(
                lambda: sys.modules.__setitem__("vapoursynth", previous_vs)
                if previous_vs is not None
                else sys.modules.pop("vapoursynth", None)
            )
            with (
                mock.patch.dict(os.environ, {}, clear=False),
                mock.patch.object(vs_loader, "_loaded_module", None),
                mock.patch.object(vs_loader, "_dll_directory_handles", (), create=True),
                mock.patch.object(vs_loader, "_loaded_layout", None),
                mock.patch.object(vs_loader, "_resource_baseline", None),
                mock.patch.object(vs_loader, "_native_plugin_state", None),
                mock.patch(
                    "core.vs_runtime.vs_loader.importlib.util.spec_from_file_location",
                    side_effect=fake_spec,
                ),
                mock.patch(
                    "core.vs_runtime.vs_loader.importlib.util.module_from_spec",
                    return_value=module,
                ),
                mock.patch(
                    "core.vs_runtime.vs_loader.os.add_dll_directory",
                    side_effect=fake_add_dll_directory,
                ),
            ):
                loaded = vs_loader.load_vapoursynth(root, runtime)

            self.assertIs(loaded, module)
            self.assertEqual(seen["name"], "vapoursynth")
            self.assertEqual(seen["location"], str(package / "__init__.py"))
            self.assertEqual(seen["search"], [str(package)])
            expected_native = [
                runtime_root / "native-plugins" / "01-lsmas",
                runtime_root / "native-plugins" / "02-imwri",
                *configured,
            ]
            self.assertEqual(core.std.loaded, list(map(str, expected_native)))
            self.assertIn(str(package), handles)
            self.assertIn(str(runtime_root), handles)


if __name__ == "__main__":
    unittest.main()
