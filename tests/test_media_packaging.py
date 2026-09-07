import ast
import json
import shutil
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

try:
    from tests.helpers.settings_artifact_contract import (
        validate_settings_artifact_contract,
    )
except ModuleNotFoundError:
    validate_settings_artifact_contract = None

try:
    from tests.helpers.settings_artifact_contract import (
        collect_portable_archive_paths,
    )
except ImportError:
    collect_portable_archive_paths = None


def _read_inno_files_entries(path):
    entries = []
    in_files = False
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if line.startswith("["):
            in_files = line.casefold() == "[files]"
            continue
        if not in_files or not line or line.startswith(";"):
            continue
        entry = {}
        for fragment in line.split(";"):
            key, value = fragment.split(":", 1)
            entry[key.strip().casefold()] = value.strip().strip('"')
        entries.append(entry)
    return entries


def _simulate_inno_files(entries, installer_root, app_dir):
    for entry in entries:
        source_value = entry["source"].replace("\\", "/")
        flags = set(entry.get("flags", "").casefold().split())
        destination = entry["destdir"].replace("{app}", str(app_dir))
        destination_path = Path(destination.replace("\\", "/"))
        excludes = {
            value.strip().casefold()
            for value in entry.get("excludes", "").split(",")
            if value.strip()
        }
        if source_value.endswith("/*"):
            source_base = installer_root / source_value[:-2]
            candidates = source_base.rglob("*")
        else:
            source_base = (installer_root / source_value).parent
            candidates = (installer_root / source_value,)
        for source in candidates:
            if not source.is_file():
                continue
            relative = source.relative_to(source_base)
            wire_relative = str(relative).replace("/", "\\").casefold()
            if wire_relative in excludes:
                continue
            target = destination_path / relative
            if "onlyifdoesntexist" in flags and target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


class MediaPackagingTests(unittest.TestCase):
    _RETAINED_SETTINGS_ARTIFACTS = {
        "ArknightsPassMaker.exe",
        "lib/gui/widgets/config_panel.pyc",
        "vs_worker.exe",
        "resources/class_icons/guard.png",
        "class_icons/guard.png",
    }

    def setUp(self):
        self.build_source = Path("build.py").read_text(encoding="utf-8")

    def test_settings_artifact_contract_accepts_retained_entrypoints(self):
        """打包清单保留统一配置页、主程序、worker 与职业图标。"""
        self.assertIsNotNone(
            validate_settings_artifact_contract,
            "M3 打包清单 checker 尚未实现",
        )

        accepted = validate_settings_artifact_contract(
            self._RETAINED_SETTINGS_ARTIFACTS
        )

        self.assertEqual(
            accepted,
            frozenset(
                {
                    "arknightspassmaker.exe",
                    "lib/gui/widgets/config_panel.pyc",
                    "vs_worker.exe",
                    "resources/class_icons/guard.png",
                    "class_icons/guard.png",
                }
            ),
        )

    def test_settings_artifact_contract_rejects_retired_module_in_pycache(self):
        """退休面板不能借 __pycache__ 目录遗留在冻结包中。"""
        self.assertIsNotNone(
            validate_settings_artifact_contract,
            "M3 打包清单 checker 尚未实现",
        )

        manifest = self._RETAINED_SETTINGS_ARTIFACTS | {
            "lib/gui/widgets/__pycache__/basic_config_panel.cpython-312.pyc"
        }

        with self.assertRaisesRegex(ValueError, "basic_config_panel"):
            validate_settings_artifact_contract(manifest)

    def test_settings_artifact_contract_rejects_operator_db_in_renamed_directory(
        self,
    ):
        """退休干员数据库不能通过更换父目录继续分发。"""
        self.assertIsNotNone(
            validate_settings_artifact_contract,
            "M3 打包清单 checker 尚未实现",
        )

        manifest = self._RETAINED_SETTINGS_ARTIFACTS | {
            "lib/compat_payload/operator_db.pyc"
        }

        with self.assertRaisesRegex(ValueError, "operator_db"):
            validate_settings_artifact_contract(manifest)

    def test_settings_artifact_contract_rejects_retired_database_in_zip_member(self):
        """退休数据库不能作为 ZIP 内部成员被目录改名绕过。"""
        self.assertIsNotNone(
            validate_settings_artifact_contract,
            "M3 打包清单 checker 尚未实现",
        )

        manifest = self._RETAINED_SETTINGS_ARTIFACTS | {
            "payload.zip!/renamed/data/character_table.json"
        }

        with self.assertRaisesRegex(ValueError, "character_table.json"):
            validate_settings_artifact_contract(manifest)

    def test_settings_artifact_contract_requires_config_panel(self):
        """删除保留 ConfigPanel 会让冻结包失去唯一配置入口。"""
        self.assertIsNotNone(
            validate_settings_artifact_contract,
            "M3 打包清单 checker 尚未实现",
        )

        manifest = self._RETAINED_SETTINGS_ARTIFACTS - {
            "lib/gui/widgets/config_panel.pyc"
        }

        with self.assertRaisesRegex(ValueError, "config_panel"):
            validate_settings_artifact_contract(manifest)

    def test_settings_artifact_contract_requires_worker(self):
        """删除 worker 可执行文件会破坏冻结版的 VPY 渲染路径。"""
        self.assertIsNotNone(
            validate_settings_artifact_contract,
            "M3 打包清单 checker 尚未实现",
        )

        manifest = self._RETAINED_SETTINGS_ARTIFACTS - {"vs_worker.exe"}

        with self.assertRaisesRegex(ValueError, "vs_worker.exe"):
            validate_settings_artifact_contract(manifest)

    def test_settings_artifact_contract_requires_runtime_class_icon(self):
        """根目录职业图标缺失会破坏运行时相对路径读取。"""
        self.assertIsNotNone(
            validate_settings_artifact_contract,
            "M3 打包清单 checker 尚未实现",
        )

        manifest = self._RETAINED_SETTINGS_ARTIFACTS - {
            "class_icons/guard.png"
        }

        with self.assertRaisesRegex(ValueError, "class_icons/guard.png"):
            validate_settings_artifact_contract(manifest)

    def _create_portable_settings_archive(self, root, library_members=()):
        """用 build.py 的真实便携归档函数生成最小冻结树。"""
        from build import create_portable_archive

        frozen = root / "ArknightsPassMaker"
        for relative in self._RETAINED_SETTINGS_ARTIFACTS:
            artifact = frozen / relative
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b"artifact")

        library_members = tuple(library_members)
        if library_members:
            library_path = frozen / "lib" / "library.zip"
            library_path.parent.mkdir(parents=True, exist_ok=True)
            with BytesIO() as payload:
                with ZipFile(payload, mode="w") as library:
                    for name, content in library_members:
                        library.writestr(name, content)
                library_path.write_bytes(payload.getvalue())

        return create_portable_archive(
            build_dir=frozen,
            dist_dir=root / "dist",
            version="9.9.9-settings-contract",
        )

    def test_portable_archive_adapter_strips_only_the_named_root(self):
        """真实便携 ZIP 的固定根前缀须转为冻结树相对清单。"""
        self.assertIsNotNone(
            collect_portable_archive_paths,
            "M3 便携 ZIP 清单适配器尚未实现",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            archive = self._create_portable_settings_archive(Path(temp_dir))
            with ZipFile(archive) as package:
                paths = collect_portable_archive_paths(package)

        self.assertEqual(
            paths,
            frozenset(path.casefold() for path in self._RETAINED_SETTINGS_ARTIFACTS),
        )

    def test_portable_archive_adapter_rejects_unexpected_top_level_root(self):
        """适配器不得把任意首段误当作便携包根目录。"""
        self.assertIsNotNone(
            collect_portable_archive_paths,
            "M3 便携 ZIP 清单适配器尚未实现",
        )

        with BytesIO() as payload:
            with ZipFile(payload, mode="w") as package:
                package.writestr("unexpected-root/vs_worker.exe", b"worker")
            with ZipFile(BytesIO(payload.getvalue())) as package:
                with self.assertRaisesRegex(ValueError, "ArknightsPassMaker"):
                    collect_portable_archive_paths(package)

    def test_portable_archive_adapter_expands_retired_library_member(self):
        """library.zip 内的退休字节码也必须被退役契约拒绝。"""
        self.assertIsNotNone(
            collect_portable_archive_paths,
            "M3 便携 ZIP 清单适配器尚未实现",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            archive = self._create_portable_settings_archive(
                Path(temp_dir),
                library_members=(("legacy/operator_db.pyc", b"bytecode"),),
            )
            with ZipFile(archive) as package:
                paths = collect_portable_archive_paths(package)

        self.assertIn("lib/library.zip!/legacy/operator_db.pyc", paths)
        with self.assertRaisesRegex(ValueError, "operator_db"):
            validate_settings_artifact_contract(paths)

    def test_build_keeps_shared_class_icons_include_mapping(self):
        """职业图标必须以正确的源目录到目标目录映射进入冻结包。"""
        tree = ast.parse(self.build_source, filename="build.py")
        include_mappings = {
            (node.elts[0].value, node.elts[1].value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Tuple)
            and len(node.elts) == 2
            and all(
                isinstance(element, ast.Constant)
                and isinstance(element.value, str)
                for element in node.elts
            )
        }

        self.assertIn(("resources/class_icons", "class_icons"), include_mappings)

    def test_build_script_does_not_package_removed_media_dependencies(self):
        # mpv.exe joined this list when preview moved to in-process VapourSynth:
        # it was ~117MB of the installer and nothing loads it any more.
        for removed_token in ("ffmpeg.exe", "ffprobe.exe", "av.libs", "ffmpeg-sdk",
                              "mpv.exe"):
            with self.subTest(removed_token=removed_token):
                self.assertNotIn(removed_token, self.build_source)

    def test_build_script_keeps_media_tool_candidates(self):
        expected_tokens = (
            "VSPipe.exe",
            "x264-7mod.exe",
            "mp4box.exe",
            "lsmash",
        )
        for expected_token in expected_tokens:
            with self.subTest(expected_token=expected_token):
                self.assertIn(expected_token, self.build_source)

    def test_contract_manifest_has_existing_absolute_sources(self):
        from build import _collect_vs_contract_include_files

        entries = _collect_vs_contract_include_files(Path.cwd())

        self.assertEqual(
            [destination for _, destination in entries],
            [
                "config/vs_runtime.json",
                "schemas/vs_runtime.schema.json",
                "schemas/vs_job.schema.json",
            ],
        )
        for source, _ in entries:
            with self.subTest(source=source):
                self.assertTrue(Path(source).is_absolute())
                self.assertTrue(Path(source).is_file())

    def test_missing_required_contract_aborts_collection(self):
        from build import _collect_vs_contract_include_files

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for relative in (
                "config/vs_runtime.json",
                "schemas/vs_runtime.schema.json",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(
                FileNotFoundError, "schemas.vs_job.schema.json"
            ):
                _collect_vs_contract_include_files(root)

    def test_worker_support_manifest_requires_every_runtime_helper_file(self):
        from build import _collect_vs_worker_support_files

        entries = _collect_vs_worker_support_files(Path.cwd())
        destinations = [destination for _, destination in entries]

        helper_root = "resources/vapoursynth/python/assetmaker_vs"
        self.assertEqual(
            destinations,
            [
                "resources/vapoursynth/assetmaker_runner.vpy",
                "resources/vapoursynth/default_pipeline.vpy",
                *[
                    f"{helper_root}/{filename}"
                    for filename in (
                        "__init__.py",
                        "job_api.py",
                        "script_header.py",
                        "executor.py",
                        "contract.py",
                        "display.py",
                        "runtime_fingerprint.py",
                    )
                ],
            ],
        )
        for source, _destination in entries:
            self.assertTrue(Path(source).is_absolute())
            self.assertTrue(Path(source).is_file())

    def test_missing_worker_helper_aborts_collection(self):
        from build import VS_WORKER_SUPPORT_FILES, _collect_vs_worker_support_files

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index, missing_relative in enumerate(VS_WORKER_SUPPORT_FILES):
                case_root = root / str(index)
                for relative in VS_WORKER_SUPPORT_FILES:
                    if relative == missing_relative:
                        continue
                    path = case_root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("# test", encoding="utf-8")

                with self.assertRaises(FileNotFoundError) as raised:
                    _collect_vs_worker_support_files(case_root)
                self.assertIn(Path(missing_relative).name, str(raised.exception))

    def test_ci_extracts_media_before_tests_and_self_tests_frozen_worker(self):
        workflow = Path(".github/workflows/build-app.yml").read_text(
            encoding="utf-8"
        )

        self.assertLess(
            workflow.index("- name: Extract media tools"),
            workflow.index("- name: Run Python tests"),
        )
        self.assertIn("vs_worker.exe", workflow)
        self.assertIn("SyncVSWorkerProcess", workflow)
        self.assertIn("--self-test", workflow)
        for artifact in (
            "ArknightsPassMaker/tools/media/vapoursynth.pyd",
            "ArknightsPassMaker/tools/media/vapoursynth.dll",
            "ArknightsPassMaker/tools/media/portable.vs",
            "ArknightsPassMaker/tools/media/vs-plugins/LSMASHSource.dll",
            "ArknightsPassMaker/tools/media/vs-plugins/libimwri.dll",
            "ArknightsPassMaker/resources/vapoursynth/python/assetmaker_vs/job_api.py",
            "ArknightsPassMaker/resources/vapoursynth/python/assetmaker_vs/display.py",
            "ArknightsPassMaker/resources/vapoursynth/python/assetmaker_vs/runtime_fingerprint.py",
        ):
            with self.subTest(artifact=artifact):
                self.assertIn(artifact, workflow)
        self.assertIn('"中文自测" in message', workflow)
        self.assertIn("len(message) > 65_536", workflow)
        self.assertIn("$retiredArtifacts", workflow)
        self.assertIn("旧 VS 文件被分发", workflow)

    def test_portable_archive_contains_the_complete_frozen_tree(self):
        from build import create_portable_archive

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frozen = root / "ArknightsPassMaker"
            (frozen / "config").mkdir(parents=True)
            (frozen / "nested").mkdir()
            (frozen / "ArknightsPassMaker.exe").write_bytes(b"main")
            (frozen / "config" / "vs_runtime.json").write_text(
                '{"schema_version": 1}', encoding="utf-8"
            )
            (frozen / "nested" / "vs_worker.exe").write_bytes(b"worker")

            archive = create_portable_archive(
                build_dir=frozen,
                dist_dir=root / "dist",
                version="9.9.9-test",
            )

            self.assertEqual(
                archive.name,
                "ArknightsPassMaker-v9.9.9-test-windows-portable.zip",
            )
            self.assertFalse(archive.with_suffix(".zip.tmp").exists())
            with ZipFile(archive) as package:
                self.assertEqual(
                    set(package.namelist()),
                    {
                        "ArknightsPassMaker/ArknightsPassMaker.exe",
                        "ArknightsPassMaker/config/vs_runtime.json",
                        "ArknightsPassMaker/nested/vs_worker.exe",
                    },
                )

    def test_ci_publishes_installer_and_portable_only_for_artifact_builds(self):
        workflow = Path(".github/workflows/build-app.yml").read_text(
            encoding="utf-8"
        )
        release = Path(".github/workflows/release.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("build_installer:", workflow)
        self.assertIn("upload_artifact:", workflow)
        self.assertIn("inputs.build_installer == true", workflow)
        self.assertIn("inputs.upload_artifact == true", workflow)
        self.assertIn("--no-installer", workflow)
        self.assertIn("--no-portable", workflow)
        self.assertIn("*-windows-portable.zip", workflow)
        self.assertIn("compression-level: 0", workflow)
        self.assertIn("Expand-Archive", workflow)
        self.assertIn("build_installer: true", release)
        self.assertIn("upload_artifact: true", release)
        self.assertIn("*-windows-portable.zip", release)
        self.assertIn("sha256sum *.exe *-windows-portable.zip", release)

    def test_installer_upgrade_preserves_legacy_until_migration(self):
        from core.vs_runtime.migration import migrate_legacy_vsconfig_once

        entries = _read_inno_files_entries(Path("installer.iss"))
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = root / "ArknightsPassMaker"
            app_dir = root / "installed"
            shipped_runtime = payload / "config" / "vs_runtime.json"
            shipped_runtime.parent.mkdir(parents=True)
            shipped_runtime.write_text('{"schema_version":1}', encoding="utf-8")

            installed_legacy = app_dir / "config" / "vsconfig.json"
            installed_runtime = app_dir / "config" / "vs_runtime.json"
            installed_legacy.parent.mkdir(parents=True)
            legacy_bytes = (
                b'{"core":{"num_threads":7,"max_cache_size_mb":256}}'
            )
            installed_legacy.write_bytes(legacy_bytes)
            installed_runtime.write_text("old-runtime", encoding="utf-8")

            _simulate_inno_files(entries, root, app_dir)

            self.assertEqual(installed_legacy.read_bytes(), legacy_bytes)
            self.assertEqual(
                installed_runtime.read_text(encoding="utf-8"),
                '{"schema_version":1}',
            )
            user_path = root / "user" / "vs_runtime.user.json"
            report = migrate_legacy_vsconfig_once(
                installed_legacy,
                user_path,
                root / "user" / "migration.json",
            )
            self.assertTrue(report.applied)
            self.assertEqual(
                json.loads(user_path.read_text(encoding="utf-8"))["core"][
                    "num_threads"
                ],
                7,
            )

    def test_installer_fresh_install_does_not_receive_legacy_default(self):
        entries = _read_inno_files_entries(Path("installer.iss"))
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "ArknightsPassMaker" / "config" / "vs_runtime.json"
            source.parent.mkdir(parents=True)
            source.write_text('{"schema_version":1}', encoding="utf-8")
            app_dir = root / "installed"

            _simulate_inno_files(entries, root, app_dir)

            self.assertFalse(
                (app_dir / "config" / "vsconfig.json").exists(),
                "新安装不应再分发已废弃的 vsconfig.json",
            )
        installer = Path("installer.iss").read_text(encoding="utf-8-sig")
        self.assertNotIn(
            'Source: "ArknightsPassMaker\\config\\vsconfig.json"', installer
        )

    def test_runtime_source_does_not_reference_removed_ffmpeg_stack(self):
        disallowed_tokens = (
            "import av",
            "av.open",
            "av.VideoFrame",
            "ffmpeg-next",
            "ffmpeg-sdk",
            "ffmpeg.exe",
            "ffprobe",
            "libx264",
        )
        source_roots = (
            Path("core"),
            Path("gui"),
            Path("simulator") / "src",
            Path("simulator") / "Cargo.toml",
            Path("pyproject.toml"),
        )
        offenders = []

        for root in source_roots:
            paths = [root] if root.is_file() else root.rglob("*")
            for path in paths:
                if path.suffix not in {".py", ".rs", ".toml"}:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                for token in disallowed_tokens:
                    if token in text:
                        offenders.append(f"{path}:{token}")

        self.assertEqual([], offenders)

    def test_runtime_source_has_no_executable_mpv_references(self):
        """Regression lock: mpv must not come back as a code dependency.

        Matches *identifiers and command strings*, not the word "mpv" — several
        docstrings still explain why the VapourSynth design replaced the mpv one,
        and that history is worth keeping.
        """
        disallowed_tokens = (
            "mpv_path",           # toolchain field / constructor arg
            "mpv.exe",
            "_mpv_process",
            "_mpv_socket",
            "_send_mpv_command",
            "_MpvMetadataSession",
            "_MpvSurface",
            "input-ipc-server",   # mpv CLI
            "screenshot-to-file",
            "video-rotate",
            "MpvLaunchWorker",
        )
        offenders = []
        for root in (Path("core"), Path("gui"), Path("config"), Path("utils"),
                     Path("main.py"), Path("build.py")):
            paths = [root] if root.is_file() else root.rglob("*.py")
            for path in paths:
                text = path.read_text(encoding="utf-8", errors="ignore")
                for token in disallowed_tokens:
                    if token in text:
                        offenders.append(f"{path}:{token}")

        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
