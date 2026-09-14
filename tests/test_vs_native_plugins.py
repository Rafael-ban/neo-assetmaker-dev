"""F3-P3：配置的 native 插件目录必须以显式、可验证策略加载。"""

from __future__ import annotations

import hashlib
import tempfile
import types
import unittest
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _resolve_r79_test_layout(root: Path):
    """Return the validated shared R79 layout without importing VapourSynth."""
    from resources.vapoursynth.python.assetmaker_vs.runtime_layout import (
        RuntimeLayoutError,
        resolve_runtime_layout,
    )

    try:
        return resolve_runtime_layout(root)
    except RuntimeLayoutError:
        return None


def _copy_r79_application_fixture(app_root: Path):
    """Copy the complete checked R79 distribution and its shared scripts."""
    source_layout = _resolve_r79_test_layout(ROOT)
    if source_layout is None:
        raise AssertionError("source checkout has no complete R79 runtime")
    shutil.copytree(
        source_layout.runtime_root,
        app_root / "tools" / "media" / "runtime",
    )
    shutil.copytree(
        ROOT / "resources" / "vapoursynth",
        app_root / "resources" / "vapoursynth",
    )
    layout = _resolve_r79_test_layout(app_root)
    if layout is None:
        raise AssertionError("copied R79 runtime does not satisfy shared layout")
    return layout


def _source_harness_bootstrap(source_root: Path) -> str:
    """Anchor test-only source imports without a PYTHONPATH escape hatch."""
    return (
        "import sys\n"
        f"sys.path.insert(0, {str(source_root.resolve())!r})\n"
    )


def _fixture_process_environment(layout, app_root: Path) -> dict[str, str]:
    """Build a private pre-launch environment through the shared sanitizer."""
    from resources.vapoursynth.python.assetmaker_vs.runtime_layout import (
        sanitize_runtime_process_environment,
    )

    private_appdata = app_root / "private-appdata"
    private_appdata.mkdir(exist_ok=True)
    base_environment = dict(os.environ)
    for name in (
        "PYTHONHOME",
        "PYTHONPATH",
        "VAPOURSYNTH_CONF_PATH",
        "VAPOURSYNTH_EXTRA_PLUGIN_PATH",
        "VAPOURSYNTH_PLUGIN_PATH",
        "VAPOURSYNTH_PYTHON_PATH",
    ):
        base_environment[name] = str(app_root / "forbidden-search-path")
    environment = sanitize_runtime_process_environment(base_environment, layout)
    environment["APPDATA"] = str(private_appdata)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return environment


R79_LAYOUT = _resolve_r79_test_layout(ROOT)


class _Plugin:
    def __init__(self, namespace: str, plugin_path: str | None, functions=()):
        self.namespace = namespace
        self.plugin_path = plugin_path
        for name in functions:
            setattr(self, name, lambda: None)


class _Core:
    def __init__(
        self,
        by_directory: dict[Path, tuple[_Plugin, ...]],
        warnings_by_directory: dict[
            Path, tuple[str | tuple[str, str], ...]
        ]
        | None = None,
    ):
        self._by_directory = by_directory
        self._warnings_by_directory = warnings_by_directory or {}
        self._plugins: list[_Plugin] = []
        self.loaded_paths: list[Path] = []
        self.removed_log_handles: list[int] = []
        self._log_handlers: dict[int, object] = {}
        self._next_log_handle = 1
        self.std = types.SimpleNamespace(LoadAllPlugins=self._load_all)

    def _load_all(self, *, path: str) -> None:
        directory = Path(path).resolve()
        self.loaded_paths.append(directory)
        existing = {plugin.namespace for plugin in self._plugins}
        for plugin in self._by_directory.get(directory, ()):
            if plugin.namespace not in existing:
                self._plugins.append(plugin)
                existing.add(plugin.namespace)
                setattr(self, plugin.namespace, plugin)

        for event in self._warnings_by_directory.get(directory, ()):
            if isinstance(event, tuple):
                level, message = event
            else:
                level, message = "warning", event
            for handler in tuple(self._log_handlers.values()):
                handler(level, message)

    def add_log_handler(self, handler):
        handle = self._next_log_handle
        self._next_log_handle += 1
        self._log_handlers[handle] = handler
        return handle

    def remove_log_handler(self, handle) -> None:
        self.removed_log_handles.append(handle)
        self._log_handlers.pop(handle)

    def plugins(self):
        return iter(self._plugins)


class NativePluginPolicyTests(unittest.TestCase):
    def _api(self):
        try:
            from resources.vapoursynth.python.assetmaker_vs import native_plugins
        except ImportError as error:
            self.fail(f"P3 native plugin policy helper 尚未实现: {error}")
        return native_plugins

    def test_loads_normalized_directories_in_order_and_keeps_dependency_dlls_valid(self):
        """去重/顺序错误，或把未注册依赖 DLL 判成插件时应失败。"""
        configure_native_plugins = self._api().configure_native_plugins

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "插件 空格 一"
            second = root / "插件 中文 二"
            first.mkdir()
            second.mkdir()
            # 这不是 VS plugin，真实目录常包含这类相邻依赖 DLL。
            (second / "avcodec-60.dll").write_bytes(b"dependency")
            core = _Core(
                {
                    first.resolve(): (_Plugin("alpha", str(first / "alpha.dll"), ("Open",)),),
                    second.resolve(): (_Plugin("beta", str(second / "beta.dll"), ("Read",)),),
                }
            )

            state = configure_native_plugins(
                core,
                (str(first), str(first / "."), str(second)),
            )

            self.assertEqual(core.loaded_paths, [first.resolve(), second.resolve()])
            self.assertEqual(state.configured_dirs, (first.resolve(), second.resolve()))

    def test_reports_required_callable_with_unconfigured_external_source(self):
        """外部同 namespace 抢先注册时，不能只因 callable 存在就通过。"""
        api = self._api()
        NativePluginPolicyError = api.NativePluginPolicyError
        configure_native_plugins = api.configure_native_plugins
        verify_native_plugin_requirements = api.verify_native_plugin_requirements

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configured = root / "配置插件"
            configured.mkdir()
            other = root / "旧插件"
            other.mkdir()
            core = _Core({})
            old = _Plugin("source", str(other / "source.dll"), ("Open",))
            core._plugins.append(old)
            core.source = old
            state = configure_native_plugins(core, (str(configured),))

            with self.assertRaisesRegex(
                NativePluginPolicyError, "native_plugin.source_mismatch"
            ):
                verify_native_plugin_requirements(core, state, ("source.Open",))

    def test_reports_same_plugin_dll_in_later_directory_as_conflict(self):
        """LoadAllPlugins 静默跳过时，同名已注册 DLL 的第二来源仍须拒绝。"""
        api = self._api()
        NativePluginPolicyError = api.NativePluginPolicyError
        configure_native_plugins = api.configure_native_plugins

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "目录 一"
            second = root / "目录 二"
            first.mkdir()
            second.mkdir()
            (first / "source.dll").write_bytes(b"first")
            (second / "source.dll").write_bytes(b"second")
            core = _Core(
                {
                    first.resolve(): (_Plugin("source", str(first / "source.dll"), ("Open",)),),
                    second.resolve(): (_Plugin("source", str(second / "source.dll"), ("Open",)),),
                },
                {
                    second.resolve(): (
                        f"Plugin {second / 'source.dll'} already loaded "
                        f"(com.example.source) from {first / 'source.dll'}",
                    )
                },
            )

            with self.assertRaisesRegex(
                NativePluginPolicyError, "native_plugin.conflict"
            ):
                configure_native_plugins(core, (str(first), str(second)))

    def test_reports_renamed_duplicate_namespace_without_using_dll_basename(self):
        """I1：LoadAllPlugins warning 的改名冲突必须含候选与既有路径。"""
        api = self._api()
        NativePluginPolicyError = api.NativePluginPolicyError
        configure_native_plugins = api.configure_native_plugins
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "目录 一"
            second = root / "目录 二"
            first.mkdir()
            second.mkdir()
            original = first / "imwri.dll"
            renamed = second / "renamed-plugin.dll"
            original.write_bytes(b"plugin")
            renamed.write_bytes(b"plugin")
            core = _Core(
                {
                    first.resolve(): (_Plugin("imwri", str(original), ("Read",)),),
                    second.resolve(): (_Plugin("imwri", str(renamed), ("Read",)),),
                },
                {
                    second.resolve(): (
                        f"Plugin {renamed} already loaded (com.vapoursynth.imwri) "
                        f"from {original}",
                    )
                },
            )

            with self.assertRaisesRegex(NativePluginPolicyError, "native_plugin.conflict") as raised:
                configure_native_plugins(core, (str(first), str(second)))
            self.assertIn(str(renamed), str(raised.exception))
            self.assertIn(str(original), str(raised.exception))
            self.assertEqual(core.removed_log_handles, [1, 2])

    def test_reports_loader_failure_but_keeps_silent_dependency_dll_valid(self):
        """I3：仅 warning 中的坏候选拒绝，静默的普通依赖 DLL 保持合法。"""
        api = self._api()
        NativePluginPolicyError = api.NativePluginPolicyError
        configure_native_plugins = api.configure_native_plugins
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "含依赖与损坏项"
            directory.mkdir()
            dependency = directory / "avcodec-60.dll"
            broken = directory / "broken-plugin.dll"
            dependency.write_bytes(b"dependency")
            broken.write_bytes(b"broken")
            core = _Core(
                {},
                {
                    directory.resolve(): (
                        f"Failed to load {broken}. GetLastError() returned 193.",
                    )
                },
            )

            with self.assertRaisesRegex(
                NativePluginPolicyError, "native_plugin.candidate_failed"
            ) as raised:
                configure_native_plugins(core, (str(directory),))
            self.assertIn(str(broken), str(raised.exception))
            self.assertNotIn(str(dependency), str(raised.exception))
            self.assertEqual(core.removed_log_handles, [1])

    def test_accepts_same_actual_path_already_registered_before_directory_scan(self):
        """I1：相同实际路径的重复扫描不是跨来源插件冲突。"""
        configure_native_plugins = self._api().configure_native_plugins
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "同路径重复"
            directory.mkdir()
            plugin_path = directory / "same.dll"
            plugin_path.write_bytes(b"plugin")
            plugin = _Plugin("same", str(plugin_path), ("Open",))
            core = _Core(
                {directory.resolve(): (plugin,)},
                {
                    directory.resolve(): (
                        f"Plugin {plugin_path} already loaded "
                        f"(com.example.same) from {plugin_path}",
                    )
                },
            )
            core._plugins.append(plugin)
            core.same = plugin

            state = configure_native_plugins(core, (str(directory),))

            self.assertEqual(state.plugin_sources["same"], plugin_path.resolve())
            self.assertEqual(core.removed_log_handles, [1])

    def test_rejects_same_directory_renamed_candidate_when_original_is_preloaded(self):
        """I1：warning 的 Plugin 字段而非 from 字段才是冲突候选。"""
        api = self._api()
        NativePluginPolicyError = api.NativePluginPolicyError
        configure_native_plugins = api.configure_native_plugins
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "同目录改名冲突"
            directory.mkdir()
            original = directory / "a-original.dll"
            renamed = directory / "z-renamed.dll"
            original.write_bytes(b"original")
            renamed.write_bytes(b"renamed")
            existing = _Plugin("imwri", str(original), ("Read",))
            core = _Core(
                {directory.resolve(): (_Plugin("imwri", str(renamed), ("Read",)),)},
                {
                    directory.resolve(): (
                        f"Plugin {renamed} already loaded "
                        f"(com.vapoursynth.imwri) from {original}",
                    )
                },
            )
            core._plugins.append(existing)
            core.imwri = existing

            with self.assertRaisesRegex(
                NativePluginPolicyError, "native_plugin.conflict"
            ) as raised:
                configure_native_plugins(core, (str(directory),))

            self.assertIn(str(renamed), str(raised.exception))
            self.assertIn(str(original), str(raised.exception))
            self.assertEqual(core.removed_log_handles, [1])

    def test_ignores_non_failure_logs_that_name_a_loaded_candidate(self):
        """NB1：成功信息、调试及普通 warning 不能因路径存在而致命。"""
        configure_native_plugins = self._api().configure_native_plugins
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "成功日志"
            directory.mkdir()
            candidate = directory / "valid.dll"
            candidate.write_bytes(b"valid")
            core = _Core(
                {directory.resolve(): (_Plugin("valid", str(candidate), ("Open",)),)},
                {
                    directory.resolve(): (
                        ("information", f"Initialized successfully: {candidate}"),
                        ("debug", f"Debug details: {candidate}"),
                        ("warning", f"Optional setting ignored: {candidate}"),
                    )
                },
            )

            state = configure_native_plugins(core, (str(directory),))

            self.assertEqual(state.plugin_sources["valid"], candidate.resolve())
            self.assertEqual(core.removed_log_handles, [1])

    def test_rejects_namespace_collision_with_by_source(self):
        """NB2：R73 namespace 冲突使用 load-of/failed/by 模板。"""
        api = self._api()
        NativePluginPolicyError = api.NativePluginPolicyError
        configure_native_plugins = api.configure_native_plugins
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "namespace 冲突"
            directory.mkdir()
            candidate = directory / "candidate.dll"
            source = directory / "existing.dll"
            candidate.write_bytes(b"candidate")
            source.write_bytes(b"source")
            core = _Core(
                {},
                {
                    directory.resolve(): (
                        "Plugin load of "
                        f"{candidate} failed, namespace imwri already populated "
                        f"by {source}",
                    )
                },
            )

            with self.assertRaisesRegex(
                NativePluginPolicyError, "native_plugin.conflict"
            ) as raised:
                configure_native_plugins(core, (str(directory),))

            self.assertIn(str(candidate), str(raised.exception))
            self.assertIn(str(source), str(raised.exception))

    def test_rejects_internal_id_collision_without_source(self):
        """NB2：R73 内置 ID 冲突可省略 from，仍必须拒绝。"""
        api = self._api()
        NativePluginPolicyError = api.NativePluginPolicyError
        configure_native_plugins = api.configure_native_plugins
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "内置 ID 冲突"
            directory.mkdir()
            candidate = directory / "candidate.dll"
            candidate.write_bytes(b"candidate")
            core = _Core(
                {},
                {
                    directory.resolve(): (
                        f"Plugin {candidate} already loaded "
                        "(com.vapoursynth.builtin)",
                    )
                },
            )

            with self.assertRaisesRegex(
                NativePluginPolicyError, "native_plugin.conflict"
            ) as raised:
                configure_native_plugins(core, (str(directory),))

            self.assertIn(str(candidate), str(raised.exception))

    def test_rejects_windows_missing_dependency_126(self):
        """NB2：R73 的 126 缺依赖后缀仍须保留为候选失败。"""
        api = self._api()
        NativePluginPolicyError = api.NativePluginPolicyError
        configure_native_plugins = api.configure_native_plugins
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "缺依赖"
            directory.mkdir()
            candidate = directory / "candidate.dll"
            candidate.write_bytes(b"candidate")
            message = (
                f"Failed to load {candidate}. GetLastError() returned 126. "
                "The file you tried to load or one of its dependencies is "
                "probably missing."
            )
            core = _Core({}, {directory.resolve(): (message,)})

            with self.assertRaisesRegex(
                NativePluginPolicyError, "native_plugin.candidate_failed"
            ) as raised:
                configure_native_plugins(core, (str(directory),))

            self.assertIn(str(candidate), str(raised.exception))
            self.assertIn("probably missing", str(raised.exception))

    def test_rejects_plugin_api_incompatibility_with_filename(self):
        """NB2：R73 API 不兼容消息的 Filename 字段是失败候选。"""
        api = self._api()
        NativePluginPolicyError = api.NativePluginPolicyError
        configure_native_plugins = api.configure_native_plugins
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "API 不兼容"
            directory.mkdir()
            candidate = directory / "candidate.dll"
            candidate.write_bytes(b"candidate")
            message = (
                "Core only supports API R4.1 but the loaded plugin requires "
                f"API R5.0; Filename: {candidate}; Name: Candidate Plugin"
            )
            core = _Core({}, {directory.resolve(): (message,)})

            with self.assertRaisesRegex(
                NativePluginPolicyError, "native_plugin.candidate_failed"
            ) as raised:
                configure_native_plugins(core, (str(directory),))

            self.assertIn(str(candidate), str(raised.exception))
            self.assertIn("Candidate Plugin", str(raised.exception))

    def test_missing_optional_portable_builtin_directory_is_ignored(self):
        """I4：可选的 vs-coreplugins 缺失不能阻断显式配置目录加载。"""
        configure_native_plugins = self._api().configure_native_plugins
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configured = root / "配置目录"
            configured.mkdir()
            optional_missing = root / "vs-coreplugins"

            state = configure_native_plugins(
                _Core({}),
                (str(configured),),
                builtin_plugin_dirs=(str(optional_missing),),
            )

            self.assertEqual(state.configured_dirs, (configured.resolve(),))
            self.assertEqual(state.builtin_plugin_dirs, ())

    def test_worker_recheck_accepts_the_same_normalized_duplicate_directory(self):
        """I2：worker 复核必须沿用 shared helper 的保序去重语义。"""
        from config.vs_runtime import PluginConfig, VSRuntimeConfig
        from core.vs_runtime import vs_loader

        configure_native_plugins = self._api().configure_native_plugins
        with tempfile.TemporaryDirectory() as temporary:
            from tests.test_vs_runtime_layout import _build_r79_fixture

            root = Path(temporary)
            _build_r79_fixture(root)
            layout = vs_loader.resolve_runtime_layout(root)
            directory = root / "重复 目录"
            directory.mkdir()
            core = _Core({})
            state = configure_native_plugins(
                core,
                (
                    *layout.bundled_native_plugin_dirs,
                    str(directory),
                    str(directory),
                ),
            )
            old_state = vs_loader._native_plugin_state
            old_layout = vs_loader._loaded_layout
            vs_loader._native_plugin_state = state
            vs_loader._loaded_layout = layout
            self.addCleanup(setattr, vs_loader, "_native_plugin_state", old_state)
            self.addCleanup(setattr, vs_loader, "_loaded_layout", old_layout)

            vs_loader.verify_vapoursynth_native_plugins(
                types.SimpleNamespace(core=core),
                VSRuntimeConfig(
                    plugins=PluginConfig(
                        native_plugin_dirs=(str(directory), str(directory))
                    )
                ),
                [],
            )


class R79NativeFixtureDeterministicTests(unittest.TestCase):
    def test_runtime_gate_accepts_complete_r79_layout_and_rejects_flat_r73(self):
        from tests.test_vs_runtime_layout import _build_r79_fixture

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.assertIsNone(_resolve_r79_test_layout(root))
            (root / "tools" / "media").mkdir(parents=True)
            (root / "tools" / "media" / "vapoursynth.pyd").write_bytes(b"R73")
            self.assertIsNone(_resolve_r79_test_layout(root))
            shutil.rmtree(root / "tools")
            _build_r79_fixture(root)

            layout = _resolve_r79_test_layout(root)

            self.assertIsNotNone(layout)
            self.assertEqual(
                layout.binding.relative_to(root).as_posix(),
                "tools/media/runtime/Lib/site-packages/vapoursynth/vapoursynth.pyd",
            )
            self.assertEqual(
                tuple(
                    path.relative_to(root).as_posix()
                    for path in layout.bundled_native_plugin_dirs
                ),
                (
                    "tools/media/runtime/native-plugins/01-lsmas",
                    "tools/media/runtime/native-plugins/02-imwri",
                ),
            )

    def test_source_harness_bootstraps_root_from_sanitized_temporary_cwd(self):
        from tests.test_vs_runtime_layout import _build_r79_fixture

        with tempfile.TemporaryDirectory() as temporary:
            fixture_root = Path(temporary) / "fixture"
            _build_r79_fixture(fixture_root)
            layout = _resolve_r79_test_layout(fixture_root)
            self.assertIsNotNone(layout)
            app_root = fixture_root / "isolated app"
            app_root.mkdir()
            private_appdata = app_root / "private-appdata"
            environment = _fixture_process_environment(layout, app_root)
            unbootstrapped = subprocess.run(
                [sys.executable, "-I", "-S", "-c", "import core"],
                cwd=app_root,
                env=environment,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(unbootstrapped.returncode, 0)
            self.assertIn(b"No module named 'core'", unbootstrapped.stderr)

            bootstrapped = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-c",
                    _source_harness_bootstrap(ROOT)
                    + "import core\nprint(core.__file__)\n",
                ],
                cwd=app_root,
                env=environment,
                capture_output=True,
                check=False,
            )

            self.assertEqual(bootstrapped.returncode, 0, bootstrapped.stderr)
            self.assertEqual(
                Path(bootstrapped.stdout.decode("utf-8").strip()).resolve(),
                ROOT / "core" / "__init__.py",
            )
            self.assertFalse(
                any(name.upper() == "PYTHONPATH" for name in environment)
            )
            self.assertEqual(environment["APPDATA"], str(private_appdata))


@unittest.skipUnless(
    R79_LAYOUT is not None,
    "需要通过 shared runtime layout 校验的 R79 fixture",
)
class NativePluginPortableIntegrationTests(unittest.TestCase):
    def test_two_chinese_space_directories_load_real_plugins_without_original_autoload(self):
        """Fresh core 以生产函数首次加载两个额外目录中的真实插件。"""
        source_layout = R79_LAYOUT
        self.assertIsNotNone(source_layout)
        image = ROOT / "resources" / "class_icons" / "ak_logo.png"
        for required in (
            source_layout.binding,
            source_layout.core,
            source_layout.bundled_native_plugin_dirs[0] / "LSMASHSource.dll",
            source_layout.bundled_native_plugin_dirs[1] / "libimwri.dll",
            ROOT / "tools" / "media" / "avcodec-60.dll",
            ROOT / "tools" / "media" / "avutil-58.dll",
            ROOT / "tools" / "media" / "swresample-4.dll",
            image,
        ):
            self.assertTrue(required.is_file(), required)

        with tempfile.TemporaryDirectory() as temporary:
            app_root = (Path(temporary) / "独立 R79 应用").resolve()
            layout = _copy_r79_application_fixture(app_root)
            first = app_root / "插件 中文 一"
            second = app_root / "插件 空格 二"
            first.mkdir()
            second.mkdir()
            shutil.copy2(
                layout.bundled_native_plugin_dirs[0] / "LSMASHSource.dll",
                first / "LSMASHSource.dll",
            )
            shutil.copy2(
                layout.bundled_native_plugin_dirs[1] / "libimwri.dll",
                second / "libimwri.dll",
            )
            for dependency_name in (
                "avcodec-60.dll",
                "avutil-58.dll",
                "swresample-4.dll",
            ):
                dependency = ROOT / "tools" / "media" / dependency_name
                shutil.copy2(dependency, second / dependency.name)
            probe = app_root / "probe.py"
            probe.write_text(
                "from __future__ import annotations\n"
                + _source_harness_bootstrap(ROOT)
                + "import importlib.util\n"
                "import json\n"
                "import os\n"
                "import sys\n"
                "from pathlib import Path\n"
                "from resources.vapoursynth.python.assetmaker_vs.native_plugins import (\n"
                "    configure_native_plugins, verify_native_plugin_requirements,\n"
                ")\n"
                "from resources.vapoursynth.python.assetmaker_vs.runtime_layout import resolve_runtime_layout\n"
                f"root = Path({str(app_root)!r})\n"
                f"first = Path({str(first)!r})\n"
                f"second = Path({str(second)!r})\n"
                f"image = Path({str(image)!r})\n"
                "layout = resolve_runtime_layout(root)\n"
                "assert os.environ['VAPOURSYNTH_EXTRA_PLUGIN_PATH'] == ''\n"
                "assert not any(name.upper() in {'VAPOURSYNTH_PLUGIN_PATH', 'VAPOURSYNTH_PYTHON_PATH', 'VAPOURSYNTH_CONF_PATH', 'PYTHONHOME', 'PYTHONPATH'} for name in os.environ)\n"
                "assert Path(os.environ['APPDATA']).resolve().is_relative_to(root)\n"
                "handles = [os.add_dll_directory(str(p)) for p in (layout.vs_package_dir, layout.runtime_root, first, second)]\n"
                "spec = importlib.util.spec_from_file_location('vapoursynth', str(layout.package_entry), submodule_search_locations=[str(layout.vs_package_dir)])\n"
                "assert spec is not None and spec.loader is not None\n"
                "vs = importlib.util.module_from_spec(spec)\n"
                "sys.modules['vapoursynth'] = vs\n"
                "spec.loader.exec_module(vs)\n"
                "assert Path(sys.modules['vapoursynth.vapoursynth'].__file__).resolve() == layout.binding.resolve()\n"
                "assert vs.__version__.release_major == 79\n"
                "assert tuple(vs.__api_version__) == (4, 2)\n"
                "assert {'lsmas', 'imwri'}.isdisjoint({p.namespace for p in vs.core.plugins()})\n"
                "state = configure_native_plugins(vs.core, (first, second), builtin_plugin_dirs=layout.builtin_plugin_dirs)\n"
                "verify_native_plugin_requirements(vs.core, state, ('lsmas.LWLibavSource', 'imwri.Read'))\n"
                "frame = vs.core.imwri.Read(str(image)).get_frame(0)\n"
                "try:\n"
                "    payload = {\n"
                "        plugin.namespace: plugin.plugin_path\n"
                "        for plugin in vs.core.plugins()\n"
                "        if plugin.namespace in {'lsmas', 'imwri'}\n"
                "    }\n"
                "    payload['frame'] = [frame.width, frame.height]\n"
                "    payload['runtime'] = str(layout.runtime_root)\n"
                "    print(json.dumps(payload, ensure_ascii=True))\n"
                "finally:\n"
                "    frame.close()\n",
                encoding="utf-8",
            )
            environment = _fixture_process_environment(layout, app_root)
            result = subprocess.run(
                [sys.executable, "-I", "-X", "utf8", "-B", str(probe)],
                cwd=app_root,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(Path(payload["lsmas"]).resolve(), first / "LSMASHSource.dll")
            self.assertEqual(Path(payload["imwri"]).resolve(), second / "libimwri.dll")
            self.assertEqual(payload["frame"], [75, 35])
            self.assertEqual(Path(payload["runtime"]).resolve(), layout.runtime_root)

    def test_product_loader_rejects_bundled_plugin_copied_to_another_directory(self):
        """完整 bundle 先加载后，异路径同 identity 必须精确冲突。"""
        with tempfile.TemporaryDirectory() as temporary:
            app_root = (Path(temporary) / "冲突 R79 应用").resolve()
            layout = _copy_r79_application_fixture(app_root)
            extra = app_root / "额外 中文 空格"
            extra.mkdir()
            candidate = extra / "LSMASHSource.dll"
            shutil.copy2(
                layout.bundled_native_plugin_dirs[0] / candidate.name,
                candidate,
            )
            probe = app_root / "conflict_probe.py"
            probe.write_text(
                _source_harness_bootstrap(ROOT)
                + "import os\n"
                "from pathlib import Path\n"
                "from config.vs_runtime import PluginConfig, VSRuntimeConfig\n"
                "from core.vs_runtime.vs_loader import load_vapoursynth\n"
                f"root = Path({str(app_root)!r})\n"
                f"extra = Path({str(extra)!r})\n"
                "assert os.environ['VAPOURSYNTH_EXTRA_PLUGIN_PATH'] == ''\n"
                "assert not any(name.upper() in {'VAPOURSYNTH_PLUGIN_PATH', 'VAPOURSYNTH_PYTHON_PATH', 'VAPOURSYNTH_CONF_PATH', 'PYTHONHOME', 'PYTHONPATH'} for name in os.environ)\n"
                "assert Path(os.environ['APPDATA']).resolve().is_relative_to(root)\n"
                "load_vapoursynth(root, VSRuntimeConfig(plugins=PluginConfig(native_plugin_dirs=(str(extra),))))\n",
                encoding="utf-8",
            )
            environment = _fixture_process_environment(layout, app_root)
            result = subprocess.run(
                [sys.executable, "-I", "-X", "utf8", "-B", str(probe)],
                cwd=app_root,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("native_plugin.conflict", result.stderr)
            self.assertIn(str(candidate), result.stderr)
            self.assertIn(
                str(layout.bundled_native_plugin_dirs[0] / candidate.name),
                result.stderr,
            )

    def test_worker_and_fresh_vspipe_use_isolated_native_directories(self):
        """I5：worker IPC 与 fixed runner 均排除 autoload 后实际取 imwri 帧。"""
        from config.vs_runtime import PluginConfig, VSRuntimeConfig
        from core.media_pipeline import (
            VSPipeRenderRequest,
            build_vspipe_command,
            build_vspipe_render_env,
        )
        from core.vs_runtime.session import (
            RenderSession,
            ScriptSelection,
            compute_job_sha256,
            compute_script_bundle_hash,
        )
        from core.vs_runtime.snapshot import RuntimeSnapshot
        from core.vs_runtime.vs_loader import compute_runtime_fingerprint
        from core.vs_runtime.worker_process import SyncVSWorkerProcess
        from core.vs_runtime.script_header import parse_script_header

        source_layout = R79_LAYOUT
        self.assertIsNotNone(source_layout)
        image = ROOT / "resources" / "class_icons" / "ak_logo.png"
        for required in (
            source_layout.vspipe_executable,
            source_layout.binding,
            source_layout.core,
            source_layout.bundled_native_plugin_dirs[0] / "LSMASHSource.dll",
            source_layout.bundled_native_plugin_dirs[1] / "libimwri.dll",
            image,
        ):
            self.assertTrue(required.is_file(), required)

        with tempfile.TemporaryDirectory() as temporary:
            app_root = Path(temporary) / "isolated R79 应用"
            layout = _copy_r79_application_fixture(app_root)
            first, second = layout.bundled_native_plugin_dirs
            runtime = VSRuntimeConfig(
                plugins=PluginConfig(
                    native_plugin_dirs=(
                        str(first),
                        str(first),
                        str(second),
                        str(second),
                    )
                )
            )
            fingerprint = compute_runtime_fingerprint(app_root, runtime)
            project = app_root / "project 中文"
            project.mkdir()
            sources_marker = project / "native-sources.json"
            script = project / "pipeline.vpy"
            script.write_text(
                "# assetmaker-api: 1\n"
                "# assetmaker-mode: raw\n"
                "# assetmaker-capabilities: source\n"
                "# assetmaker-requires: lsmas.LWLibavSource, imwri.Read\n"
                "# assetmaker-editor-output: 0\n\n"
                "import json\n"
                "import os\n"
                "import sys\n"
                "from pathlib import Path\n"
                "import vapoursynth as vs\n\n"
                f"image = Path({str(image)!r})\n"
                f"marker = Path({str(sources_marker)!r})\n"
                "frame = vs.core.imwri.Read(str(image)).get_frame(0)\n"
                "try:\n"
                "    assert (frame.width, frame.height) == (75, 35)\n"
                "finally:\n"
                "    frame.close()\n"
                "marker.write_text(json.dumps({\n"
                "    'lsmas': vs.core.lsmas.plugin_path,\n"
                "    'imwri': vs.core.imwri.plugin_path,\n"
                "    'package_entry': str(Path(vs.__file__).resolve()),\n"
                "    'binding': str(Path(sys.modules['vapoursynth.vapoursynth'].__file__).resolve()),\n"
                "    'release_major': vs.__version__.release_major,\n"
                "    'api': list(vs.__api_version__),\n"
                "    'appdata': os.environ['APPDATA'],\n"
                "}), encoding='utf-8')\n"
                "base = vs.core.std.BlankClip(width=384, height=640, length=3, "
                "fpsnum=30000, fpsden=1001, format=vs.YUV420P8, "
                "color=[16, 128, 128])\n"
                "base = vs.core.std.SetFrameProps(base, _Matrix=6, _Transfer=6, "
                "_Primaries=6, _ColorRange=1)\n"
                "base.set_output(0)\n",
                encoding="utf-8",
            )
            job = project / "job.json"
            job.write_text(
                json.dumps(
                    {
                        "api_version": 1,
                        "epoch": 3,
                        "track": "loop",
                        "project_root": str(project),
                        "source": {"path": str(project / "source.mp4"), "kind": "video", "virtual_frame_count": None},
                        "timeline": {"start_frame": 0, "end_frame": 3, "fps": {"numerator": 30000, "denominator": 1001}},
                        "transform": {"rotation": 0, "crop": {"coordinate_space": "post_rotation_source_pixels", "x": 0, "y": 0, "width": 0, "height": 0}},
                        "output": {"profile": "360x640", "display_width": 360, "display_height": 640, "coded_width": 384, "coded_height": 640, "pixel_format": "YUV420P8", "matrix": "170m", "transfer": "170m", "primaries": "170m", "range": "limited", "final_rotate_180": False},
                        "paths": {"cache_dir": str(project / "cache")},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            worker_program = (
                _source_harness_bootstrap(ROOT)
                + "from pathlib import Path\n"
                f"assert Path(__import__('core').__file__).resolve() == Path({str(ROOT / 'core' / '__init__.py')!r}).resolve()\n"
                "owner = sys.stdout\n"
                "protocol_stream = owner.buffer\n"
                "sys.stdout = sys.stderr\n"
                "from core.vs_runtime.worker_main import main\n"
                f"raise SystemExit(main(protocol_stream=protocol_stream, app_dir=Path({str(app_root)!r})))\n"
            )
            snapshot = RuntimeSnapshot(str(app_root), runtime, fingerprint)
            fixture_environment = _fixture_process_environment(layout, app_root)
            worker_env = snapshot.worker_environment(fixture_environment)
            self.assertFalse(
                any(name.upper() == "PYTHONPATH" for name in worker_env)
            )
            self.assertEqual(
                Path(worker_env["APPDATA"]).resolve(),
                (app_root / "private-appdata").resolve(),
            )
            client = SyncVSWorkerProcess(
                app_dir=app_root,
                command=[
                    str(Path(sys.executable).resolve()),
                    "-X",
                    "utf8",
                    "-B",
                    "-c",
                    worker_program,
                ],
                env=worker_env,
            )
            self.addCleanup(client.close)
            session = RenderSession(
                epoch=3,
                track="loop",
                selection=ScriptSelection.from_header(
                    script, parse_script_header(script), compute_script_bundle_hash(script)
                ),
                job_path=str(job),
                job_sha256=compute_job_sha256(job),
                runtime_fingerprint=fingerprint,
            )
            self.assertEqual(client.start(timeout_ms=15_000)["operation"], "hello")
            self.assertEqual(client.load(session, timeout_ms=20_000).epoch, 3)
            frame = client.request_frame(
                epoch=3,
                index=0,
                surface="final",
                viewport=(384, 640),
                zoom_factor=1.0,
                pan=(0.5, 0.5),
                timeout_ms=15_000,
            )
            self.assertEqual(frame.shape, (640, 384, 3))
            worker_sources = json.loads(sources_marker.read_text(encoding="utf-8"))
            self.assertEqual(Path(worker_sources["lsmas"]).resolve(), first / "LSMASHSource.dll")
            self.assertEqual(Path(worker_sources["imwri"]).resolve(), second / "libimwri.dll")
            self.assertEqual(
                Path(worker_sources["package_entry"]).resolve(),
                layout.package_entry.resolve(),
            )
            self.assertEqual(
                Path(worker_sources["binding"]).resolve(), layout.binding.resolve()
            )
            self.assertEqual(worker_sources["release_major"], 79)
            self.assertEqual(worker_sources["api"], [4, 2])
            self.assertEqual(
                Path(worker_sources["appdata"]).resolve(),
                (app_root / "private-appdata").resolve(),
            )
            client.unload(timeout_ms=10_000)
            self.assertEqual(client.shutdown(timeout_ms=10_000)["operation"], "shutdown")

            sources_marker.unlink()
            request = VSPipeRenderRequest(
                runner_path=str(app_root / "resources" / "vapoursynth" / "assetmaker_runner.vpy"),
                script_path=str(script),
                job_path=str(job),
                expected_job_sha256=hashlib.sha256(job.read_bytes()).hexdigest(),
                api_version=1,
                mode="raw",
                app_dir=str(app_root),
                runtime=runtime,
                runtime_fingerprint=fingerprint,
            )
            env = build_vspipe_render_env(
                str(layout.vspipe_executable),
                app_dir=str(app_root),
                runtime=runtime,
                expected_fingerprint=fingerprint,
            )
            env["APPDATA"] = fixture_environment["APPDATA"]
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            self.assertEqual(env["VAPOURSYNTH_EXTRA_PLUGIN_PATH"], "")
            result = subprocess.run(
                build_vspipe_command(str(layout.vspipe_executable), request),
                cwd=app_root,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            runner_sources = json.loads(sources_marker.read_text(encoding="utf-8"))
            self.assertEqual(Path(runner_sources["lsmas"]).resolve(), first / "LSMASHSource.dll")
            self.assertEqual(Path(runner_sources["imwri"]).resolve(), second / "libimwri.dll")
            self.assertEqual(
                Path(runner_sources["package_entry"]).resolve(),
                layout.package_entry.resolve(),
            )
            self.assertEqual(
                Path(runner_sources["binding"]).resolve(), layout.binding.resolve()
            )
            self.assertEqual(runner_sources["release_major"], 79)
            self.assertEqual(runner_sources["api"], [4, 2])
            self.assertEqual(
                Path(runner_sources["appdata"]).resolve(),
                (app_root / "private-appdata").resolve(),
            )

            blocked_env = dict(env)
            blocked_env["VAPOURSYNTH_EXTRA_PLUGIN_PATH"] = str(
                ROOT / "外部 污染 native"
            )
            blocked = subprocess.run(
                build_vspipe_command(str(layout.vspipe_executable), request),
                cwd=app_root,
                env=blocked_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("runtime.native_plugin_env", blocked.stderr)


if __name__ == "__main__":
    unittest.main()
