import concurrent.futures
import hashlib
import json
import multiprocessing
import os
import tempfile
import threading
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator, ValidationError

from config.vs_runtime import (
    VSRuntimeConfig,
    VSRuntimeConfigError,
    load_vs_runtime,
    save_vs_runtime_override,
)
from core.vs_runtime.migration import migrate_legacy_vsconfig_once


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "vs_runtime.schema.json"
CONFIG_PATH = ROOT / "config" / "vs_runtime.json"
RUNTIME_SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _save_override_process(path, patch, barrier, results):
    barrier.wait(timeout=10)
    try:
        save_vs_runtime_override(path, patch)
    except Exception as exc:
        results.put(("error", str(exc)))
    else:
        results.put(("ok", ""))


def _save_override_signal_process(path, patch, started, results):
    started.set()
    try:
        save_vs_runtime_override(path, patch)
    except Exception as exc:
        results.put(("error", str(exc)))
    else:
        results.put(("ok", ""))


def _migration_with_paused_target_read_process(
    legacy_path,
    user_path,
    marker_path,
    read_started,
    release_read,
    results,
):
    import config.vs_runtime as runtime_module
    import core.vs_runtime.migration as migration_module

    target = Path(user_path).resolve()
    original_runtime_read = runtime_module._read_json
    original_migration_read = migration_module._read_object

    def pause(path, payload):
        if Path(path).resolve() == target:
            read_started.set()
            if not release_read.wait(timeout=10):
                raise TimeoutError("migration target read was not released")
        return payload

    def runtime_read(path):
        return pause(path, original_runtime_read(path))

    def migration_read(path, location):
        return pause(path, original_migration_read(path, location))

    runtime_module._read_json = runtime_read
    migration_module._read_object = migration_read
    try:
        report = migration_module.migrate_legacy_vsconfig_once(
            legacy_path, user_path, marker_path
        )
    except Exception as exc:
        results.put(("error", str(exc)))
    else:
        results.put(("ok", report.applied))


def _synchronized_migration_process(
    legacy_path,
    user_path,
    marker_path,
    barrier,
    results,
):
    import config.vs_runtime as runtime_module
    import core.vs_runtime.migration as migration_module

    original_lock = runtime_module._override_lock

    @contextmanager
    def synchronized_lock(path):
        barrier.wait(timeout=10)
        with original_lock(path):
            yield

    runtime_module._override_lock = synchronized_lock
    try:
        report = migration_module.migrate_legacy_vsconfig_once(
            legacy_path, user_path, marker_path
        )
    except Exception as exc:
        results.put(("error", str(exc)))
    else:
        results.put(("ok", report.applied))


def _override_file_lock_is_held(path):
    lock_path = Path(path).with_name(f".{Path(path).name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return True
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return False

        import fcntl

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False


class VSRuntimeContractTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def write_json(self, payload, name="vs_runtime.json"):
        path = self.root / name
        path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return path

    def test_shipped_runtime_matches_schema_and_model(self):
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

        Draft202012Validator.check_schema(RUNTIME_SCHEMA)
        Draft202012Validator(RUNTIME_SCHEMA).validate(payload)
        self.assertEqual(VSRuntimeConfig.from_dict(payload).to_dict(), payload)

    def test_filter_policy_is_not_a_runtime_field(self):
        serialized = json.dumps(VSRuntimeConfig().to_dict())
        for key in (
            "resampler_kernel",
            "output_format",
            "image_source_format",
            "matrix_s",
            "heuristic",
            "required_plugins",
        ):
            with self.subTest(key=key):
                self.assertNotIn(key, serialized)

    def test_missing_file_returns_defaults(self):
        missing = self.root / "missing.json"
        self.assertEqual(load_vs_runtime(missing), VSRuntimeConfig())

    def test_existing_but_invalid_file_fails_loudly(self):
        path = self.write_json({"schema_version": 999})

        with self.assertRaisesRegex(
            VSRuntimeConfigError, str(path.resolve()).replace("\\", "\\\\")
        ):
            load_vs_runtime(path)

    def test_malformed_json_fails_with_absolute_path(self):
        path = self.root / "broken.json"
        path.write_text("{", encoding="utf-8")

        with self.assertRaises(VSRuntimeConfigError) as raised:
            load_vs_runtime(path)

        self.assertIn(str(path.resolve()), str(raised.exception))

    def test_unknown_negative_and_wrong_type_values_are_rejected(self):
        invalid_payloads = (
            {"unknown": True},
            {"worker": {"frame_timeout_ms": -1}},
            {"core": {"num_threads": True}},
            {"plugins": {"native_plugin_dirs": [1]}},
            {"scripts": {"global_script_path": None}},
        )
        for index, payload in enumerate(invalid_payloads):
            with self.subTest(payload=payload):
                path = self.write_json(payload, f"invalid-{index}.json")
                with self.assertRaises(VSRuntimeConfigError):
                    load_vs_runtime(path)

    def test_python_model_rejects_non_json_plugin_sequence(self):
        payload = VSRuntimeConfig().to_dict()
        payload["plugins"]["native_plugin_dirs"] = ("vs-plugins",)

        with self.assertRaises(VSRuntimeConfigError):
            VSRuntimeConfig.from_dict(payload)

    def test_user_override_changes_global_script_without_writing_shipped_file(self):
        shipped_path = self.write_json(
            VSRuntimeConfig().to_dict(), "shipped.json"
        )
        user_path = self.root / "vapoursynth" / "vs_runtime.user.json"
        shipped_before = shipped_path.read_bytes()

        save_vs_runtime_override(
            user_path,
            {"scripts": {"global_script_path": r"D:\VS\pipeline.vpy"}},
        )
        merged = load_vs_runtime(shipped_path, user_path)

        self.assertEqual(
            merged.scripts.global_script_path, r"D:\VS\pipeline.vpy"
        )
        self.assertEqual(shipped_path.read_bytes(), shipped_before)
        self.assertEqual(
            json.loads(user_path.read_text(encoding="utf-8")),
            {"scripts": {"global_script_path": r"D:\VS\pipeline.vpy"}},
        )
        self.assertFalse(list(user_path.parent.glob(f".{user_path.name}.*.tmp")))

    def test_nested_override_merges_without_resetting_siblings(self):
        shipped = VSRuntimeConfig().to_dict()
        shipped["worker"]["startup_timeout_ms"] = 25_000
        shipped_path = self.write_json(shipped, "shipped.json")
        user_path = self.write_json(
            {"worker": {"frame_timeout_ms": 30_000}}, "user.json"
        )

        merged = load_vs_runtime(shipped_path, user_path)

        self.assertEqual(merged.worker.startup_timeout_ms, 25_000)
        self.assertEqual(merged.worker.frame_timeout_ms, 30_000)
        self.assertEqual(merged.worker.shutdown_timeout_ms, 3_000)

    def test_runtime_paths_share_one_canonical_windows_wire_contract(self):
        valid_payloads = []
        for script_path, plugin_path in (
            (r"D:\VS\pipeline.vpy", r"D:\VS\plugins"),
            (r"\\server\share\pipeline.vpy", r"\\server\share\plugins"),
            ("", r"D:\VS\plugins"),
        ):
            payload = VSRuntimeConfig().to_dict()
            payload["scripts"]["global_script_path"] = script_path
            payload["plugins"]["native_plugin_dirs"] = [plugin_path]
            valid_payloads.append(payload)
        for payload in valid_payloads:
            with self.subTest(valid=payload):
                Draft202012Validator(RUNTIME_SCHEMA).validate(payload)
                VSRuntimeConfig.from_dict(payload)

        invalid_paths = (
            "D:/VS/pipeline.vpy",
            r"D:\VS\..\pipeline.vpy",
            r"\VS\pipeline.vpy",
            r"relative\pipeline.vpy",
            r"D:\bad?name\pipeline.vpy",
        )
        for invalid_path in invalid_paths:
            payload = VSRuntimeConfig().to_dict()
            payload["scripts"]["global_script_path"] = invalid_path
            with self.subTest(global_script_path=invalid_path):
                with self.assertRaises(ValidationError):
                    Draft202012Validator(RUNTIME_SCHEMA).validate(payload)
                with self.assertRaises(VSRuntimeConfigError):
                    VSRuntimeConfig.from_dict(payload)

            payload = VSRuntimeConfig().to_dict()
            payload["plugins"]["native_plugin_dirs"] = [invalid_path]
            with self.subTest(plugin_dir=invalid_path):
                with self.assertRaises(ValidationError):
                    Draft202012Validator(RUNTIME_SCHEMA).validate(payload)
                with self.assertRaises(VSRuntimeConfigError):
                    VSRuntimeConfig.from_dict(payload)

    def test_runtime_paths_reject_windows_alias_and_invalid_components(self):
        cases = (
            ("drive-nul", "D:\\bad\0name\\pipeline.vpy", True),
            ("drive-control", "D:\\bad\x1fname\\pipeline.vpy", True),
            ("drive-trailing-dot", r"D:\trail.\pipeline.vpy", True),
            ("drive-trailing-space", "D:\\trail \\pipeline.vpy", True),
            ("drive-device", r"D:\NUL\pipeline.vpy", False),
            ("drive-device-extension", r"D:\LPT1.log\pipeline.vpy", False),
            ("unc-nul", "\\\\server\\share\\bad\0name\\pipeline.vpy", True),
            ("unc-control", "\\\\server\\share\\bad\x01name\\pipeline.vpy", True),
            ("unc-trailing-dot", r"\\server\share\trail.\pipeline.vpy", True),
            ("unc-trailing-space", "\\\\server\\share\\trail \\pipeline.vpy", True),
            ("unc-device", r"\\server\share\COM9.txt\pipeline.vpy", False),
            ("extended-device", r"\\?\D:\VS\pipeline.vpy", True),
            ("win32-device", r"\\.\PhysicalDrive0", True),
        )
        validator = Draft202012Validator(RUNTIME_SCHEMA)
        for name, invalid_path, schema_can_reject in cases:
            payload = VSRuntimeConfig().to_dict()
            payload["scripts"]["global_script_path"] = invalid_path
            with self.subTest(name=name, layer="schema"):
                if schema_can_reject:
                    with self.assertRaises(ValidationError):
                        validator.validate(payload)
                else:
                    validator.validate(payload)
            with self.subTest(name=name, layer="model"):
                with self.assertRaises(VSRuntimeConfigError):
                    VSRuntimeConfig.from_dict(payload)

            payload = VSRuntimeConfig().to_dict()
            payload["plugins"]["native_plugin_dirs"] = [invalid_path]
            with self.subTest(name=name, layer="plugin-schema"):
                if schema_can_reject:
                    with self.assertRaises(ValidationError):
                        validator.validate(payload)
                else:
                    validator.validate(payload)
            with self.subTest(name=name, layer="plugin-model"):
                with self.assertRaises(VSRuntimeConfigError):
                    VSRuntimeConfig.from_dict(payload)

    def test_runtime_paths_reject_superscript_com_lpt_devices(self):
        cases = (
            ("drive-com1", r"D:\COM¹\pipeline.vpy"),
            ("drive-com2-extension", r"D:\com².log\pipeline.vpy"),
            ("drive-lpt3", r"D:\LpT³\pipeline.vpy"),
            ("unc-lpt1-extension", r"\\server\share\LPT¹.txt\pipeline.vpy"),
            ("unc-lpt2", r"\\server\share\lpt²\pipeline.vpy"),
            ("unc-com3-extension", r"\\server\share\CoM³.bin\pipeline.vpy"),
        )
        validator = Draft202012Validator(RUNTIME_SCHEMA)
        for name, invalid_path in cases:
            payload = VSRuntimeConfig().to_dict()
            payload["scripts"]["global_script_path"] = invalid_path
            with self.subTest(name=name, layer="schema"):
                validator.validate(payload)
            with self.subTest(name=name, layer="model"):
                with self.assertRaises(VSRuntimeConfigError):
                    VSRuntimeConfig.from_dict(payload)

            payload = VSRuntimeConfig().to_dict()
            payload["plugins"]["native_plugin_dirs"] = [invalid_path]
            with self.subTest(name=name, layer="plugin-schema"):
                validator.validate(payload)
            with self.subTest(name=name, layer="plugin-model"):
                with self.assertRaises(VSRuntimeConfigError):
                    VSRuntimeConfig.from_dict(payload)

    def test_save_patch_canonicalizes_paths(self):
        path = self.root / "vs_runtime.user.json"
        save_vs_runtime_override(
            path,
            {
                "plugins": {"native_plugin_dirs": ["D:/VS/./plugins"]},
                "scripts": {"global_script_path": "D:/VS/./pipeline.vpy"},
            },
        )

        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["plugins"]["native_plugin_dirs"], [r"D:\VS\plugins"]
        )
        self.assertEqual(
            payload["scripts"]["global_script_path"], r"D:\VS\pipeline.vpy"
        )

    def test_save_patch_preserves_existing_fields(self):
        path = self.root / "vs_runtime.user.json"
        save_vs_runtime_override(
            path,
            {
                "worker": {"frame_timeout_ms": 12_345},
                "plugins": {"native_plugin_dirs": [r"D:\VS\plugins"]},
            },
        )

        save_vs_runtime_override(
            path,
            {"scripts": {"global_script_path": r"D:\VS\pipeline.vpy"}},
        )

        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            payload.get("worker", {}).get("frame_timeout_ms"), 12_345
        )
        self.assertEqual(
            payload.get("plugins", {}).get("native_plugin_dirs"),
            [r"D:\VS\plugins"],
        )

    def test_save_patch_rejects_non_object(self):
        with self.assertRaises(VSRuntimeConfigError):
            save_vs_runtime_override(self.root / "invalid.user.json", [])

    def test_concurrent_thread_patches_do_not_lose_fields(self):
        path = self.root / "thread.user.json"
        barrier = threading.Barrier(2)
        patches = (
            {"worker": {"frame_timeout_ms": 12_345}},
            {"core": {"num_threads": 4}},
        )

        def apply_patch(patch):
            barrier.wait(timeout=10)
            save_vs_runtime_override(path, patch)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(apply_patch, patches))

        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            payload.get("worker", {}).get("frame_timeout_ms"), 12_345
        )
        self.assertEqual(payload.get("core", {}).get("num_threads"), 4)

    def test_concurrent_process_patches_do_not_lose_fields(self):
        path = self.root / "process.user.json"
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(2)
        results = context.Queue()
        patches = (
            {"worker": {"frame_timeout_ms": 12_345}},
            {"core": {"num_threads": 4}},
        )
        processes = [
            context.Process(
                target=_save_override_process,
                args=(path, patch, barrier, results),
            )
            for patch in patches
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=15)
            self.assertFalse(process.is_alive(), "patch 进程未按时退出")
            self.assertEqual(process.exitcode, 0)
        outcomes = [results.get(timeout=5) for _ in processes]
        self.assertEqual([state for state, _ in outcomes], ["ok", "ok"])

        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            payload.get("worker", {}).get("frame_timeout_ms"), 12_345
        )
        self.assertEqual(payload.get("core", {}).get("num_threads"), 4)

    def test_float_schema_version_is_rejected_by_model_and_loader(self):
        payload = VSRuntimeConfig().to_dict()
        payload["schema_version"] = 1.0
        path = self.write_json(payload, "float-version.json")

        with self.assertRaises(VSRuntimeConfigError):
            VSRuntimeConfig.from_dict(payload)
        with self.assertRaises(VSRuntimeConfigError):
            load_vs_runtime(path)


class LegacyVSConfigMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.legacy_path = self.root / "vsconfig.json"
        self.user_path = self.root / "vapoursynth" / "vs_runtime.user.json"
        self.marker_path = self.root / "vapoursynth" / "migration.json"

    def write_legacy(self):
        payload = {
            "version": 1,
            "required_plugins": ["lsmas", "imwri"],
            "extra_plugin_dirs": [r"D:\VS\plugins"],
            "image_source_format": "RGB24",
            "output_format": "YUV420P8",
            "resampler_kernel": "Lanczos",
            "colour": {
                "matrix_s": "709",
                "heuristic": {
                    "height_threshold": 720,
                    "hd_matrix": 1,
                    "sd_matrix": 6,
                },
            },
            "core": {"num_threads": 4, "max_cache_size_mb": 512},
            "unrelated": "leave behind",
        }
        self.legacy_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    def test_only_allowlisted_runtime_fields_are_migrated(self):
        self.write_legacy()
        legacy_before = self.legacy_path.read_bytes()
        expected_hash = hashlib.sha256(legacy_before).hexdigest()

        report = migrate_legacy_vsconfig_once(
            self.legacy_path, self.user_path, self.marker_path
        )

        self.assertTrue(report.applied)
        self.assertEqual(
            report.migrated_fields,
            (
                "core.num_threads",
                "core.max_cache_size_mb",
                "extra_plugin_dirs",
            ),
        )
        self.assertEqual(
            report.ignored_fields,
            (
                "required_plugins",
                "image_source_format",
                "output_format",
                "resampler_kernel",
                "colour",
            ),
        )
        self.assertEqual(report.source_hash, expected_hash)
        self.assertEqual(self.legacy_path.read_bytes(), legacy_before)
        self.assertEqual(
            json.loads(self.user_path.read_text(encoding="utf-8")),
            {
                "core": {"num_threads": 4, "max_cache_size_mb": 512},
                "plugins": {"native_plugin_dirs": [r"D:\VS\plugins"]},
            },
        )
        marker = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertEqual(marker, {"source_hash": expected_hash})

    def test_same_source_hash_is_not_applied_twice(self):
        self.write_legacy()
        first = migrate_legacy_vsconfig_once(
            self.legacy_path, self.user_path, self.marker_path
        )
        user_before = self.user_path.read_bytes()

        second = migrate_legacy_vsconfig_once(
            self.legacy_path, self.user_path, self.marker_path
        )

        self.assertTrue(first.applied)
        self.assertFalse(second.applied)
        self.assertEqual(second.source_hash, first.source_hash)
        self.assertEqual(self.user_path.read_bytes(), user_before)

    def test_existing_user_override_wins_over_legacy_values(self):
        self.write_legacy()
        self.user_path.parent.mkdir(parents=True)
        self.user_path.write_text(
            json.dumps(
                {
                    "core": {"num_threads": 9},
                    "plugins": {
                        "native_plugin_dirs": [r"D:\User\plugins"]
                    },
                }
            ),
            encoding="utf-8",
        )

        migrate_legacy_vsconfig_once(
            self.legacy_path, self.user_path, self.marker_path
        )

        payload = json.loads(self.user_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["core"]["num_threads"], 9)
        self.assertEqual(payload["core"]["max_cache_size_mb"], 512)
        self.assertEqual(
            payload["plugins"]["native_plugin_dirs"], [r"D:\User\plugins"]
        )

    def test_changed_hash_reapplies_and_migrates_new_allowlisted_field(self):
        self.legacy_path.write_text(
            json.dumps({"core": {"num_threads": 4}}), encoding="utf-8"
        )
        first = migrate_legacy_vsconfig_once(
            self.legacy_path, self.user_path, self.marker_path
        )
        self.legacy_path.write_text(
            json.dumps(
                {"core": {"num_threads": 6, "max_cache_size_mb": 768}}
            ),
            encoding="utf-8",
        )

        second = migrate_legacy_vsconfig_once(
            self.legacy_path, self.user_path, self.marker_path
        )

        self.assertTrue(second.applied)
        self.assertNotEqual(second.source_hash, first.source_hash)
        payload = json.loads(self.user_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["core"]["num_threads"], 4)
        self.assertEqual(payload["core"]["max_cache_size_mb"], 768)

    def test_corrupt_marker_and_target_fail_loudly_with_path(self):
        self.write_legacy()
        self.marker_path.parent.mkdir(parents=True)
        self.marker_path.write_text("{", encoding="utf-8")
        with self.assertRaises(VSRuntimeConfigError) as marker_error:
            migrate_legacy_vsconfig_once(
                self.legacy_path, self.user_path, self.marker_path
            )
        self.assertIn(str(self.marker_path.resolve()), str(marker_error.exception))

        self.marker_path.unlink()
        self.user_path.write_text("{", encoding="utf-8")
        with self.assertRaises(VSRuntimeConfigError) as target_error:
            migrate_legacy_vsconfig_once(
                self.legacy_path, self.user_path, self.marker_path
            )
        self.assertIn(str(self.user_path.resolve()), str(target_error.exception))

    def test_same_hash_marker_validates_target_before_short_circuit(self):
        self.write_legacy()
        migrate_legacy_vsconfig_once(
            self.legacy_path, self.user_path, self.marker_path
        )
        self.user_path.write_text("{", encoding="utf-8")

        with self.assertRaises(VSRuntimeConfigError) as raised:
            migrate_legacy_vsconfig_once(
                self.legacy_path, self.user_path, self.marker_path
            )

        self.assertIn(str(self.user_path.resolve()), str(raised.exception))

    def test_same_hash_marker_rebuilds_missing_target(self):
        self.write_legacy()
        first = migrate_legacy_vsconfig_once(
            self.legacy_path, self.user_path, self.marker_path
        )
        self.user_path.unlink()

        second = migrate_legacy_vsconfig_once(
            self.legacy_path, self.user_path, self.marker_path
        )

        self.assertTrue(first.applied)
        self.assertTrue(second.applied)
        self.assertEqual(second.source_hash, first.source_hash)
        payload = json.loads(self.user_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["core"]["num_threads"], 4)
        self.assertEqual(payload["core"]["max_cache_size_mb"], 512)

    def test_covered_invalid_legacy_value_does_not_block_missing_field(self):
        self.legacy_path.write_text(
            json.dumps(
                {
                    "core": {
                        "num_threads": "bad",
                        "max_cache_size_mb": 512,
                    }
                }
            ),
            encoding="utf-8",
        )
        self.user_path.parent.mkdir(parents=True)
        self.user_path.write_text(
            json.dumps({"core": {"num_threads": 9}}),
            encoding="utf-8",
        )

        report = migrate_legacy_vsconfig_once(
            self.legacy_path, self.user_path, self.marker_path
        )

        self.assertTrue(report.applied)
        self.assertEqual(
            report.migrated_fields, ("core.max_cache_size_mb",)
        )
        payload = json.loads(self.user_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["core"]["num_threads"], 9)
        self.assertEqual(payload["core"]["max_cache_size_mb"], 512)

    def test_same_hash_valid_target_does_not_reinterpret_bad_legacy(self):
        legacy_payload = {
            "core": {
                "num_threads": "bad",
                "max_cache_size_mb": 512,
            }
        }
        self.legacy_path.write_text(
            json.dumps(legacy_payload), encoding="utf-8"
        )
        source_hash = hashlib.sha256(self.legacy_path.read_bytes()).hexdigest()
        self.user_path.parent.mkdir(parents=True)
        self.user_path.write_text(
            json.dumps(
                {"core": {"num_threads": 9, "max_cache_size_mb": 512}}
            ),
            encoding="utf-8",
        )
        self.marker_path.write_text(
            json.dumps({"source_hash": source_hash}), encoding="utf-8"
        )

        report = migrate_legacy_vsconfig_once(
            self.legacy_path, self.user_path, self.marker_path
        )

        self.assertFalse(report.applied)
        self.assertEqual(report.migrated_fields, ())
        self.assertEqual(report.ignored_fields, ())
        self.assertEqual(report.source_hash, source_hash)

    def test_same_hash_thread_migrations_have_exactly_one_applier(self):
        import config.vs_runtime as runtime_module

        self.write_legacy()
        barrier = threading.Barrier(2)
        original_lock = runtime_module._override_lock

        @contextmanager
        def synchronized_lock(path):
            barrier.wait(timeout=10)
            with original_lock(path):
                yield

        def migrate():
            return migrate_legacy_vsconfig_once(
                self.legacy_path, self.user_path, self.marker_path
            )

        with mock.patch.object(
            runtime_module, "_override_lock", new=synchronized_lock
        ):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                reports = list(pool.map(lambda _index: migrate(), range(2)))

        self.assertEqual([report.applied for report in reports].count(True), 1)
        marker = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertEqual(marker["source_hash"], reports[0].source_hash)

    def test_same_hash_process_migrations_have_exactly_one_applier(self):
        self.write_legacy()
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(2)
        results = context.Queue()
        processes = [
            context.Process(
                target=_synchronized_migration_process,
                args=(
                    self.legacy_path,
                    self.user_path,
                    self.marker_path,
                    barrier,
                    results,
                ),
            )
            for _index in range(2)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=15)
            self.assertFalse(process.is_alive(), "迁移进程未按时退出")
            self.assertEqual(process.exitcode, 0)

        outcomes = [results.get(timeout=5) for _process in processes]
        self.assertEqual([state for state, _value in outcomes], ["ok", "ok"])
        self.assertEqual(
            [applied for _state, applied in outcomes].count(True), 1
        )

    def test_different_hash_marker_cannot_be_overwritten_out_of_order(self):
        import config.vs_runtime as runtime_module
        import core.vs_runtime.migration as migration_module

        legacy_a = self.root / "legacy-a.json"
        legacy_b = self.root / "legacy-b.json"
        legacy_a.write_text(
            json.dumps({"core": {"num_threads": 3}}), encoding="utf-8"
        )
        legacy_b.write_text(
            json.dumps({"core": {"num_threads": 7}}), encoding="utf-8"
        )
        hash_a = hashlib.sha256(legacy_a.read_bytes()).hexdigest()
        hash_b = hashlib.sha256(legacy_b.read_bytes()).hexdigest()
        first_at_marker = threading.Event()
        release_first = threading.Event()
        real_marker_write = migration_module.atomic_write_json

        def ordered_marker_write(path, payload, *, indent=2):
            if Path(path) == self.marker_path and payload == {
                "source_hash": hash_a
            }:
                first_at_marker.set()
                self.assertTrue(release_first.wait(timeout=10))
            real_marker_write(path, payload, indent=indent)

        reports = {}

        def migrate(name, source):
            reports[name] = migrate_legacy_vsconfig_once(
                source, self.user_path, self.marker_path
            )

        with mock.patch.object(
            migration_module,
            "atomic_write_json",
            side_effect=ordered_marker_write,
        ):
            first = threading.Thread(
                target=migrate, args=("first", legacy_a)
            )
            first.start()
            self.assertTrue(first_at_marker.wait(timeout=10))
            shared_lock = runtime_module._thread_lock_for(self.user_path)
            first_holds_lock = not shared_lock.acquire(blocking=False)
            if not first_holds_lock:
                shared_lock.release()
            second = threading.Thread(
                target=migrate, args=("second", legacy_b)
            )
            second.start()
            if not first_holds_lock:
                second.join(timeout=10)
                self.assertFalse(second.is_alive())
            release_first.set()
            first.join(timeout=10)
            second.join(timeout=10)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertTrue(reports["first"].applied)
        self.assertTrue(reports["second"].applied)
        marker = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertEqual(marker, {"source_hash": hash_b})

    def test_migration_and_save_thread_race_keeps_latest_patch(self):
        import config.vs_runtime as runtime_module
        import core.vs_runtime.migration as migration_module

        self.write_legacy()
        save_vs_runtime_override(
            self.user_path, {"core": {"num_threads": 9}}
        )
        read_started = threading.Event()
        release_read = threading.Event()
        target = self.user_path.resolve()
        original_runtime_read = runtime_module._read_json
        original_migration_read = migration_module._read_object

        def pause(path, payload):
            if (
                threading.current_thread().name == "migration"
                and Path(path).resolve() == target
            ):
                read_started.set()
                self.assertTrue(release_read.wait(timeout=10))
            return payload

        def runtime_read(path):
            return pause(path, original_runtime_read(path))

        def migration_read(path, location):
            return pause(path, original_migration_read(path, location))

        migration_result = []

        def migrate():
            migration_result.append(
                migrate_legacy_vsconfig_once(
                    self.legacy_path, self.user_path, self.marker_path
                )
            )

        with mock.patch.object(
            runtime_module, "_read_json", side_effect=runtime_read
        ), mock.patch.object(
            migration_module, "_read_object", side_effect=migration_read
        ):
            migration_thread = threading.Thread(
                target=migrate, name="migration"
            )
            migration_thread.start()
            self.assertTrue(read_started.wait(timeout=10))
            shared_lock = runtime_module._thread_lock_for(self.user_path)
            migration_holds_lock = not shared_lock.acquire(blocking=False)
            if not migration_holds_lock:
                shared_lock.release()
                save_vs_runtime_override(
                    self.user_path, {"core": {"num_threads": 10}}
                )
                release_read.set()
                migration_thread.join(timeout=10)
            else:
                save_thread = threading.Thread(
                    target=save_vs_runtime_override,
                    args=(
                        self.user_path,
                        {"core": {"num_threads": 10}},
                    ),
                )
                save_thread.start()
                release_read.set()
                migration_thread.join(timeout=10)
                save_thread.join(timeout=10)
                self.assertFalse(save_thread.is_alive())

        self.assertFalse(migration_thread.is_alive())
        self.assertEqual(len(migration_result), 1)
        payload = json.loads(self.user_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["core"]["num_threads"], 10)
        self.assertEqual(payload["core"]["max_cache_size_mb"], 512)

    def test_migration_and_save_process_race_keeps_latest_patch(self):
        self.write_legacy()
        save_vs_runtime_override(
            self.user_path, {"core": {"num_threads": 9}}
        )
        context = multiprocessing.get_context("spawn")
        read_started = context.Event()
        release_read = context.Event()
        migration_results = context.Queue()
        save_started = context.Event()
        save_results = context.Queue()
        migration_process = context.Process(
            target=_migration_with_paused_target_read_process,
            args=(
                self.legacy_path,
                self.user_path,
                self.marker_path,
                read_started,
                release_read,
                migration_results,
            ),
        )
        migration_process.start()
        self.assertTrue(read_started.wait(timeout=10))
        migration_holds_lock = _override_file_lock_is_held(self.user_path)
        save_process = context.Process(
            target=_save_override_signal_process,
            args=(
                self.user_path,
                {"core": {"num_threads": 10}},
                save_started,
                save_results,
            ),
        )
        save_process.start()
        self.assertTrue(save_started.wait(timeout=10))
        if not migration_holds_lock:
            save_process.join(timeout=10)
            self.assertFalse(save_process.is_alive())
        release_read.set()
        migration_process.join(timeout=15)
        save_process.join(timeout=15)
        self.assertFalse(migration_process.is_alive())
        self.assertFalse(save_process.is_alive())
        self.assertEqual(migration_process.exitcode, 0)
        self.assertEqual(save_process.exitcode, 0)
        self.assertEqual(migration_results.get(timeout=5)[0], "ok")
        self.assertEqual(save_results.get(timeout=5)[0], "ok")

        payload = json.loads(self.user_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["core"]["num_threads"], 10)
        self.assertEqual(payload["core"]["max_cache_size_mb"], 512)

    def test_relative_legacy_plugin_dir_is_canonicalized_from_install_root(self):
        install_root = self.root / "ArknightsPassMaker"
        legacy_path = install_root / "config" / "vsconfig.json"
        user_path = self.root / "user" / "vs_runtime.user.json"
        marker_path = self.root / "user" / "migration.json"
        legacy_path.parent.mkdir(parents=True)
        legacy_path.write_text(
            json.dumps({"extra_plugin_dirs": ["vs-plugins"]}),
            encoding="utf-8",
        )

        migrate_legacy_vsconfig_once(legacy_path, user_path, marker_path)

        payload = json.loads(user_path.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["plugins"]["native_plugin_dirs"],
            [str((install_root / "tools" / "media" / "vs-plugins").resolve())],
        )

    def test_relative_legacy_source_uses_resolved_install_root(self):
        worktree_temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(worktree_temp.cleanup)
        install_root = Path(worktree_temp.name).resolve() / "ArknightsPassMaker"
        legacy_path = install_root / "config" / "vsconfig.json"
        user_path = self.root / "user-relative" / "vs_runtime.user.json"
        marker_path = self.root / "user-relative" / "migration.json"
        legacy_path.parent.mkdir(parents=True)
        legacy_path.write_text(
            json.dumps({"extra_plugin_dirs": ["vs-plugins"]}),
            encoding="utf-8",
        )
        relative_legacy = os.path.relpath(legacy_path, Path.cwd())

        try:
            migrate_legacy_vsconfig_once(
                relative_legacy, user_path, marker_path
            )
        except Exception as exc:
            self.fail(
                "relative legacy source leaked an exception: "
                f"{type(exc).__name__}: {exc}"
            )

        payload = json.loads(user_path.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["plugins"]["native_plugin_dirs"],
            [str((install_root / "tools" / "media" / "vs-plugins").resolve())],
        )

    def test_legacy_source_path_errors_are_wrapped(self):
        invalid_paths = (
            (".", str(Path.cwd().resolve())),
            ("bad\0vsconfig.json", "bad"),
        )
        for legacy_path, expected_path in invalid_paths:
            with self.subTest(legacy_path=repr(legacy_path)):
                with self.assertRaises(VSRuntimeConfigError) as raised:
                    migrate_legacy_vsconfig_once(
                        legacy_path, self.user_path, self.marker_path
                    )
                self.assertIn(expected_path, str(raised.exception))

    def test_legacy_source_resolve_runtime_error_is_wrapped(self):
        resolve_error = RuntimeError("symlink loop")
        with mock.patch.object(Path, "resolve", side_effect=resolve_error):
            with self.assertRaises(VSRuntimeConfigError) as raised:
                migrate_legacy_vsconfig_once(
                    self.legacy_path, self.user_path, self.marker_path
                )

        self.assertIn(str(self.legacy_path), str(raised.exception))
        self.assertIn(str(resolve_error), str(raised.exception))
        self.assertFalse(self.user_path.exists())
        self.assertFalse(self.marker_path.exists())

    def test_missing_relative_legacy_source_remains_a_noop(self):
        worktree_temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(worktree_temp.cleanup)
        missing = Path(worktree_temp.name).resolve() / "missing.json"
        relative_missing = os.path.relpath(missing, Path.cwd())

        report = migrate_legacy_vsconfig_once(
            relative_missing, self.user_path, self.marker_path
        )

        self.assertFalse(report.applied)
        self.assertEqual(report.source_hash, "")
        self.assertFalse(self.user_path.exists())
        self.assertFalse(self.marker_path.exists())

    def test_global_script_patch_after_migration_keeps_migrated_fields(self):
        self.write_legacy()
        migrate_legacy_vsconfig_once(
            self.legacy_path, self.user_path, self.marker_path
        )

        save_vs_runtime_override(
            self.user_path,
            {"scripts": {"global_script_path": r"D:\VS\pipeline.vpy"}},
        )

        payload = json.loads(self.user_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["core"]["num_threads"], 4)
        self.assertEqual(
            payload["plugins"]["native_plugin_dirs"],
            [r"D:\VS\plugins"],
        )
        self.assertEqual(
            payload["scripts"]["global_script_path"], r"D:\VS\pipeline.vpy"
        )

    def test_missing_legacy_file_is_a_noop(self):
        report = migrate_legacy_vsconfig_once(
            self.legacy_path, self.user_path, self.marker_path
        )

        self.assertFalse(report.applied)
        self.assertEqual(report.migrated_fields, ())
        self.assertEqual(report.ignored_fields, ())
        self.assertEqual(report.source_hash, "")
        self.assertFalse(self.user_path.exists())
        self.assertFalse(self.marker_path.exists())


class CoreResourceBaselineTests(unittest.TestCase):
    def test_zero_uses_captured_baseline_and_nonzero_overrides_it(self):
        """配置 0 必须恢复真实基线，而不能写入 0 或沿用脚本污染。"""
        from resources.vapoursynth.python.assetmaker_vs.core_resources import (
            CoreResourceBaseline,
            restore_core_resources,
        )

        core = types.SimpleNamespace(num_threads=7, max_cache_size=37)
        baseline = CoreResourceBaseline.capture(core)
        core.num_threads = 99
        core.max_cache_size = 199

        restore_core_resources(
            core,
            {"num_threads": 0, "max_cache_size_mb": 0},
            baseline,
        )
        self.assertEqual((core.num_threads, core.max_cache_size), (7, 37))

        restore_core_resources(
            core,
            {"num_threads": 2, "max_cache_size_mb": 101},
            baseline,
        )
        self.assertEqual((core.num_threads, core.max_cache_size), (2, 101))


if __name__ == "__main__":
    unittest.main()
