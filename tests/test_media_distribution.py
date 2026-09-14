import contextlib
import hashlib
import io
import json
import os
import stat
import unittest
import warnings
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import build
from media_distribution import (
    ArchiveVerification,
    MediaDistributionError,
    MediaManifest,
    MediaTreeVerification,
    create_media_archive,
    extract_media_archive,
    load_manifest,
    main,
    validate_media_tree,
    verify_media_archive,
)
from resources.vapoursynth.python.assetmaker_vs import runtime_layout


class _StatWithReparsePoint:
    def __init__(self, original):
        self._original = original
        self.st_file_attributes = (
            original.st_file_attributes | stat.FILE_ATTRIBUTE_REPARSE_POINT
        )

    def __getattr__(self, name):
        return getattr(self._original, name)


class _StatWithUnavailableAttributes:
    def __init__(self, original, error_type):
        self._original = original
        self._error_type = error_type

    def __getattr__(self, name):
        if name == "st_file_attributes":
            raise self._error_type("st_file_attributes unavailable")
        return getattr(self._original, name)


class MediaDistributionTests(unittest.TestCase):
    maxDiff = None

    @staticmethod
    def _sha256(data):
        return hashlib.sha256(data).hexdigest().upper()

    @classmethod
    def _fixture(cls, root, extra_files=None):
        app_dir = root / "app"
        media_root = app_dir / "tools" / "media"
        payloads = {}
        for relative in runtime_layout._DISTRIBUTION_RELATIVE_FILES:
            content = f"fixture:{relative}".encode()
            if relative.endswith("vapoursynth-79.dist-info/METADATA"):
                content = (
                    b"Metadata-Version: 2.1\n"
                    b"Name: VapourSynth\n"
                    b"Version: 79\n"
                )
            payloads[f"runtime/{relative}"] = content
        payloads["encoder.exe"] = b"encoder-fixture"
        payloads.update(extra_files or {})

        records = []
        for relative in sorted(payloads, key=lambda value: value.encode("utf-8")):
            content = payloads[relative]
            target = media_root / Path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            records.append(
                {
                    "path": relative,
                    "size": len(content),
                    "sha256": cls._sha256(content),
                    "source_id": "fixture",
                }
            )

        manifest_data = {
            "schema": 1,
            "asset_id": "media-tools-r79-v1",
            "runtime_version": 79,
            "sources": [
                {
                    "id": "fixture",
                    "description": "unit-test fixture",
                    "evidence": [],
                }
            ],
            "files": records,
        }
        manifest_path = root / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return app_dir, manifest_path, payloads, manifest_data

    @staticmethod
    def _write_manifest(path, data):
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def _zip_bytes(cls, manifest, replacements=None, extras=(), duplicate=None):
        replacements = replacements or {}
        stream = io.BytesIO()
        with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
            for record in manifest.records:
                content = replacements.get(record.path)
                if content is None:
                    content = f"fixture:{record.path.removeprefix('runtime/')}".encode()
                    if record.path.endswith(
                        "vapoursynth-79.dist-info/METADATA"
                    ):
                        content = (
                            b"Metadata-Version: 2.1\n"
                            b"Name: VapourSynth\n"
                            b"Version: 79\n"
                        )
                    if record.path == "encoder.exe":
                        content = b"encoder-fixture"
                archive.writestr(f"tools/media/{record.path}", content)
            for name, content, info in extras:
                archive.writestr(info or name, content)
            if duplicate is not None:
                record = next(item for item in manifest.records if item.path == duplicate)
                content = replacements.get(record.path, b"duplicate")
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    archive.writestr(f"tools/media/{record.path}", content)
        return stream.getvalue()

    @staticmethod
    def _patched_lstat(
        marked_path=None,
        unknown_path=None,
        error_type=AttributeError,
        observed=None,
    ):
        original_lstat = Path.lstat

        def controlled_lstat(path):
            candidate = Path(path)
            if observed is not None:
                observed.append(candidate)
            result = original_lstat(path)
            if candidate == marked_path:
                return _StatWithReparsePoint(result)
            if candidate == unknown_path:
                return _StatWithUnavailableAttributes(result, error_type)
            return result

        return patch.object(Path, "lstat", autospec=True, side_effect=controlled_lstat)

    def test_load_manifest_exposes_validated_immutable_contract(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _app_dir, manifest_path, _payloads, _data = self._fixture(root)

            manifest = load_manifest(manifest_path)

        self.assertIsInstance(manifest, MediaManifest)
        self.assertEqual(manifest.asset_id, "media-tools-r79-v1")
        self.assertEqual(manifest.runtime_version, 79)
        self.assertIsInstance(manifest.records, tuple)
        self.assertEqual(manifest.records[-1].path, "runtime/winsound.pyd")
        self.assertEqual(manifest.sources[0].source_id, "fixture")

    def test_manifest_rejects_unsafe_paths_and_case_collisions(self):
        invalid_paths = (
            "",
            "/absolute.dll",
            "C:/drive.dll",
            "//server/share.dll",
            "runtime\\bad.dll",
            "runtime//bad.dll",
            "runtime/./bad.dll",
            "runtime/../bad.dll",
            "runtime/file.dll:stream",
            "runtime/CON.txt",
            "runtime/trailing. ",
        )
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _app_dir, manifest_path, _payloads, data = self._fixture(root)
            template = dict(data["files"][0])
            for index, invalid in enumerate(invalid_paths):
                with self.subTest(path=invalid):
                    case_data = dict(data)
                    case_data["files"] = [dict(template, path=invalid)]
                    case_path = root / f"invalid-{index}.json"
                    self._write_manifest(case_path, case_data)
                    with self.assertRaises((MediaDistributionError, ValueError)):
                        load_manifest(case_path)

            collision_data = dict(data)
            collision_data["files"] = [
                dict(template, path="runtime/Name.dll"),
                dict(template, path="runtime/name.dll"),
            ]
            collision_path = root / "collision.json"
            self._write_manifest(collision_path, collision_data)
            with self.assertRaisesRegex(
                (MediaDistributionError, ValueError), "(?i)case|duplicate"
            ):
                load_manifest(collision_path)

    def test_manifest_rejects_invalid_size_hash_source_and_order(self):
        mutations = (
            ("bool-size", {"size": True}, "size"),
            ("negative-size", {"size": -1}, "size"),
            ("bad-hash", {"sha256": "0" * 63}, "sha-?256"),
            (
                "whitespace-hash",
                {"sha256": "00" * 15 + " " + "00" * 16 + " "},
                "sha-?256",
            ),
            ("missing-source", {"source_id": "unknown"}, "source"),
        )
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _app_dir, _manifest_path, _payloads, data = self._fixture(root)
            for name, mutation, message in mutations:
                with self.subTest(name=name):
                    case_data = dict(data)
                    case_data["files"] = [dict(data["files"][0], **mutation)]
                    case_path = root / f"{name}.json"
                    self._write_manifest(case_path, case_data)
                    with self.assertRaisesRegex(
                        (MediaDistributionError, ValueError), f"(?i){message}"
                    ):
                        load_manifest(case_path)

            unordered = dict(data)
            unordered["files"] = list(reversed(data["files"]))
            unordered_path = root / "unordered.json"
            self._write_manifest(unordered_path, unordered)
            with self.assertRaisesRegex(
                (MediaDistributionError, ValueError), "(?i)order|sorted"
            ):
                load_manifest(unordered_path)

    def test_validate_tree_returns_exact_files_and_reports_ignored_cache(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app_dir, manifest_path, payloads, _data = self._fixture(root)
            cache = app_dir / "tools/media/runtime/__pycache__/cache.pyc"
            cache.parent.mkdir(parents=True)
            cache.write_bytes(b"cache")
            optimized = app_dir / "tools/media/stale.pyo"
            optimized.write_bytes(b"optimized")

            result = validate_media_tree(app_dir, load_manifest(manifest_path))

        self.assertIsInstance(result, MediaTreeVerification)
        self.assertEqual(result.file_count, len(payloads))
        self.assertEqual(result.total_bytes, sum(map(len, payloads.values())))
        self.assertEqual(result.files, tuple(sorted(result.files, key=str)))
        self.assertTrue(all(path.is_absolute() for path in result.files))
        self.assertEqual(
            result.ignored,
            ("runtime/__pycache__", "stale.pyo"),
        )

    def test_validate_tree_rejects_missing_changed_and_extra_files(self):
        cases = ("missing", "changed", "extra", "cache-dll")
        for case in cases:
            with self.subTest(case=case), TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                app_dir, manifest_path, _payloads, _data = self._fixture(root)
                media_root = app_dir / "tools/media"
                if case == "missing":
                    (media_root / "encoder.exe").unlink()
                elif case == "changed":
                    (media_root / "encoder.exe").write_bytes(b"changed")
                elif case == "cache-dll":
                    cache_file = media_root / "runtime/__pycache__/unknown.dll"
                    cache_file.parent.mkdir(parents=True)
                    cache_file.write_bytes(b"unknown")
                else:
                    (media_root / "unknown.log").write_bytes(b"unknown")

                with self.assertRaises(MediaDistributionError):
                    validate_media_tree(app_dir, load_manifest(manifest_path))

    def test_validate_tree_defers_complete_layout_and_flat_rejection_to_helper(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app_dir, manifest_path, _payloads, data = self._fixture(root)
            flat = app_dir / "tools/media/vapoursynth.dll"
            flat.write_bytes(b"flat")
            data["files"].append(
                {
                    "path": "vapoursynth.dll",
                    "size": 4,
                    "sha256": self._sha256(b"flat"),
                    "source_id": "fixture",
                }
            )
            data["files"].sort(key=lambda item: item["path"].encode("utf-8"))
            self._write_manifest(manifest_path, data)

            with self.assertRaisesRegex(MediaDistributionError, "(?i)flat|R73"):
                validate_media_tree(app_dir, load_manifest(manifest_path))

    def test_validate_tree_rejects_reparse_and_unknown_windows_attributes(self):
        for mode in ("reparse", "missing-attributes", "attribute-read-error"):
            with self.subTest(mode=mode), TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                app_dir, manifest_path, _payloads, _data = self._fixture(root)
                target = app_dir / "tools/media/encoder.exe"
                kwargs = {"marked_path": target}
                if mode != "reparse":
                    kwargs = {
                        "unknown_path": target,
                        "error_type": (
                            AttributeError
                            if mode == "missing-attributes"
                            else OSError
                        ),
                    }
                with self._patched_lstat(**kwargs):
                    with self.assertRaises(MediaDistributionError):
                        validate_media_tree(app_dir, load_manifest(manifest_path))

    def test_validate_tree_rejects_read_failure(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app_dir, manifest_path, _payloads, _data = self._fixture(root)
            target = app_dir / "tools/media/encoder.exe"
            original_open = Path.open

            def controlled_open(path, *args, **kwargs):
                if Path(path) == target:
                    raise PermissionError(f"cannot read {path}")
                return original_open(path, *args, **kwargs)

            with patch.object(Path, "open", controlled_open):
                with self.assertRaises(MediaDistributionError):
                    validate_media_tree(app_dir, load_manifest(manifest_path))

    def test_build_collector_uses_manifest_order_and_exact_destinations(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app_dir, manifest_path, payloads, _data = self._fixture(root)

            entries = build._collect_media_tool_include_files(
                project_root=app_dir,
                manifest_path=manifest_path,
            )

        expected_destinations = [
            f"tools/media/{relative}"
            for relative in sorted(payloads, key=lambda value: value.encode("utf-8"))
        ]
        self.assertEqual([destination for _, destination in entries], expected_destinations)
        self.assertTrue(all(Path(source).is_absolute() for source, _ in entries))

    def test_frozen_media_verification_rejects_missing_manifest_member(self):
        """cx_Freeze 返回后仍须逐项校验冻结目录，而非只相信源树。"""
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app_dir, manifest_path, _payloads, _data = self._fixture(root)

            verified = build._verify_frozen_media_tool_tree(
                app_dir,
                manifest_path,
            )
            self.assertEqual(verified.file_count, 75)

            missing = app_dir / "tools/media/runtime/vcruntime140.dll"
            missing.unlink()
            with self.assertRaises(MediaDistributionError):
                build._verify_frozen_media_tool_tree(app_dir, manifest_path)

    def test_create_archive_is_reproducible_and_verifies_every_member(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app_dir, manifest_path, payloads, _data = self._fixture(root)
            manifest = load_manifest(manifest_path)
            first_path = root / "media-tools-r79-v1-a.zip"
            second_path = root / "media-tools-r79-v1-b.zip"

            first = create_media_archive(app_dir, manifest, first_path)
            second = create_media_archive(app_dir, manifest, second_path)
            checked = verify_media_archive(first_path, manifest, first.sha256)
            first_bytes = first_path.read_bytes()
            second_bytes = second_path.read_bytes()

        self.assertIsInstance(first, ArchiveVerification)
        self.assertEqual(first, second)
        self.assertEqual(first, checked)
        self.assertEqual(first.file_count, len(payloads))
        self.assertEqual(first.total_bytes, sum(map(len, payloads.values())))
        self.assertEqual(first_bytes, second_bytes)

    def test_create_archive_preserves_existing_archive_and_temporary_file(self):
        for occupied in ("archive", "temporary"):
            with self.subTest(occupied=occupied), TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                app_dir, manifest_path, _payloads, _data = self._fixture(root)
                manifest = load_manifest(manifest_path)
                archive_path = root / "media-tools-r79-v1.zip"
                temporary_path = archive_path.with_suffix(".zip.tmp")
                target = archive_path if occupied == "archive" else temporary_path
                target.write_bytes(b"old-user-archive")

                with self.assertRaises((FileExistsError, MediaDistributionError)):
                    create_media_archive(app_dir, manifest, archive_path)

                self.assertEqual(target.read_bytes(), b"old-user-archive")
                other = temporary_path if occupied == "archive" else archive_path
                self.assertFalse(other.exists())

    def test_archive_boundaries_reject_reparse_grandparent_without_descending(self):
        for operation in ("source", "input", "output"):
            with (
                self.subTest(operation=operation),
                TemporaryDirectory() as temp_dir,
            ):
                root = Path(temp_dir)
                fixture_root = root / "source-boundary" / "ordinary"
                app_dir, manifest_path, _payloads, _data = self._fixture(
                    fixture_root
                )
                manifest = load_manifest(manifest_path)
                archive_path = (
                    root
                    / "archive-boundary"
                    / "ordinary"
                    / "media-tools-r79-v1.zip"
                )
                archive_path.parent.mkdir(parents=True)
                expected_sha256 = None
                if operation == "input":
                    expected_sha256 = create_media_archive(
                        app_dir,
                        manifest,
                        archive_path,
                    ).sha256

                marked = {
                    "source": app_dir.parent.parent,
                    "input": archive_path.parent.parent,
                    "output": archive_path.parent.parent,
                }[operation]
                observed = []
                with self._patched_lstat(
                    marked_path=marked,
                    observed=observed,
                ):
                    with self.assertRaises(MediaDistributionError):
                        if operation == "source":
                            create_media_archive(
                                app_dir,
                                manifest,
                                root / "safe-output.zip",
                            )
                        elif operation == "input":
                            verify_media_archive(
                                archive_path,
                                manifest,
                                expected_sha256,
                            )
                        else:
                            create_media_archive(
                                app_dir,
                                manifest,
                                archive_path,
                            )

                descendants = [
                    path
                    for path in observed
                    if path != marked and marked in path.parents
                ]
                self.assertIn(marked, observed)
                self.assertEqual(descendants, [])
                if operation == "input":
                    self.assertTrue(archive_path.is_file())
                else:
                    selected = (
                        root / "safe-output.zip"
                        if operation == "source"
                        else archive_path
                    )
                    self.assertFalse(selected.exists())
                    self.assertFalse(selected.with_suffix(".zip.tmp").exists())

    def test_verify_archive_rejects_bad_expected_sha_before_members(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app_dir, manifest_path, _payloads, _data = self._fixture(root)
            manifest = load_manifest(manifest_path)
            archive_path = root / "media-tools-r79-v1.zip"
            created = create_media_archive(app_dir, manifest, archive_path)

            with self.assertRaisesRegex(MediaDistributionError, "(?i)archive.*sha"):
                verify_media_archive(archive_path, manifest, "0" * 64)

        self.assertNotEqual(created.sha256, "0" * 64)

    def test_verify_archive_rejects_whitespace_in_expected_sha(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app_dir, manifest_path, _payloads, _data = self._fixture(root)
            manifest = load_manifest(manifest_path)
            archive_path = root / "media-tools-r79-v1.zip"
            create_media_archive(app_dir, manifest, archive_path)
            malformed_sha256 = "00" * 15 + " " + "00" * 16 + " "

            with self.assertRaisesRegex(
                MediaDistributionError,
                "64 hexadecimal digits",
            ):
                verify_media_archive(
                    archive_path,
                    manifest,
                    malformed_sha256,
                )

    def test_verify_archive_rejects_wrong_missing_extra_and_duplicate_members(self):
        cases = ("wrong", "missing", "extra", "duplicate")
        for case in cases:
            with self.subTest(case=case), TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                _app_dir, manifest_path, _payloads, _data = self._fixture(root)
                manifest = load_manifest(manifest_path)
                first = manifest.records[0]
                replacements = {first.path: b"wrong"} if case == "wrong" else {}
                extras = (
                    (("tools/media/extra.dll", b"extra", None),)
                    if case == "extra"
                    else ()
                )
                duplicate = first.path if case == "duplicate" else None
                payload = self._zip_bytes(
                    manifest,
                    replacements=replacements,
                    extras=extras,
                    duplicate=duplicate,
                )
                if case == "missing":
                    with ZipFile(io.BytesIO(payload)) as source:
                        reduced = io.BytesIO()
                        with ZipFile(reduced, "w", ZIP_DEFLATED) as target:
                            for index, info in enumerate(source.infolist()):
                                if index:
                                    target.writestr(info, source.read(info))
                        payload = reduced.getvalue()
                archive_path = root / f"{case}.zip"
                archive_path.write_bytes(payload)
                actual_sha = self._sha256(payload)

                with self.assertRaises(MediaDistributionError):
                    verify_media_archive(archive_path, manifest, actual_sha)

    def test_verify_archive_rejects_escape_directory_and_symlink_members(self):
        cases = ("escape", "directory", "symlink")
        for case in cases:
            with self.subTest(case=case), TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                _app_dir, manifest_path, _payloads, _data = self._fixture(root)
                manifest = load_manifest(manifest_path)
                if case == "escape":
                    extra = ("../escape.dll", b"escape", None)
                elif case == "directory":
                    info = ZipInfo("tools/media/runtime/")
                    info.external_attr = (stat.S_IFDIR | 0o755) << 16
                    extra = (info.filename, b"", info)
                else:
                    info = ZipInfo("tools/media/link.dll")
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    extra = (info.filename, b"target", info)
                payload = self._zip_bytes(manifest, extras=(extra,))
                archive_path = root / f"{case}.zip"
                archive_path.write_bytes(payload)

                with self.assertRaises(MediaDistributionError):
                    verify_media_archive(
                        archive_path,
                        manifest,
                        self._sha256(payload),
                    )

    def test_extract_invalid_archive_never_creates_target(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app_dir, manifest_path, _payloads, _data = self._fixture(root)
            manifest = load_manifest(manifest_path)
            target_app = root / "extract"
            target_app.mkdir()
            payload = self._zip_bytes(
                manifest,
                replacements={manifest.records[0].path: b"wrong"},
            )
            archive_path = root / "bad.zip"
            archive_path.write_bytes(payload)

            with self.assertRaises(MediaDistributionError):
                extract_media_archive(
                    archive_path,
                    target_app,
                    manifest,
                    self._sha256(payload),
                )

            self.assertFalse((target_app / "tools/media").exists())
            self.assertTrue((app_dir / "tools/media").exists())

    def test_extract_rejects_existing_target_and_reparse_ancestor_without_changes(self):
        for case in ("existing", "reparse"):
            with self.subTest(case=case), TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                source_app, manifest_path, _payloads, _data = self._fixture(root)
                manifest = load_manifest(manifest_path)
                archive_path = root / "media-tools-r79-v1.zip"
                created = create_media_archive(source_app, manifest, archive_path)
                target_app = root / "target"
                tools = target_app / "tools"
                tools.mkdir(parents=True)
                sentinel = target_app / "keep.txt"
                sentinel.write_bytes(b"keep")
                context = contextlib.nullcontext()
                if case == "existing":
                    media = tools / "media"
                    media.mkdir()
                    (media / "user.bin").write_bytes(b"user")
                else:
                    context = self._patched_lstat(marked_path=tools)

                with context:
                    with self.assertRaises((FileExistsError, MediaDistributionError)):
                        extract_media_archive(
                            archive_path,
                            target_app,
                            manifest,
                            created.sha256,
                        )

                self.assertEqual(sentinel.read_bytes(), b"keep")
                if case == "existing":
                    self.assertEqual((tools / "media/user.bin").read_bytes(), b"user")
                else:
                    self.assertFalse((tools / "media").exists())

    def test_extract_rejects_tools_reparse_before_probing_media_child(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_app, manifest_path, _payloads, _data = self._fixture(root)
            manifest = load_manifest(manifest_path)
            archive_path = root / "media-tools-r79-v1.zip"
            created = create_media_archive(source_app, manifest, archive_path)
            target_app = root / "target"
            tools_root = target_app / "tools"
            media_root = tools_root / "media"
            tools_root.mkdir(parents=True)
            observed_lexists = []
            original_lexists = os.path.lexists

            def observe_lexists(path):
                observed_lexists.append(Path(path))
                return original_lexists(path)

            with (
                self._patched_lstat(marked_path=tools_root),
                patch(
                    "media_distribution.os.path.lexists",
                    side_effect=observe_lexists,
                ),
            ):
                with self.assertRaises(MediaDistributionError):
                    extract_media_archive(
                        archive_path,
                        target_app,
                        manifest,
                        created.sha256,
                    )

            self.assertNotIn(media_root, observed_lexists)
            self.assertFalse(media_root.exists())

    def test_extract_valid_archive_recreates_exact_bytes(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_app, manifest_path, payloads, _data = self._fixture(root)
            manifest = load_manifest(manifest_path)
            archive_path = root / "media-tools-r79-v1.zip"
            created = create_media_archive(source_app, manifest, archive_path)
            target_app = root / "target"
            target_app.mkdir()

            extracted = extract_media_archive(
                archive_path,
                target_app,
                manifest,
                created.sha256,
            )

            self.assertEqual(extracted, created)
            for relative, content in payloads.items():
                self.assertEqual(
                    (target_app / "tools/media" / Path(relative)).read_bytes(),
                    content,
                )

    def test_cli_emits_one_json_document_and_stable_exit_codes(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app_dir, manifest_path, payloads, _data = self._fixture(root)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = main(
                    [
                        "verify-tree",
                        "--app-dir",
                        str(app_dir),
                        "--manifest",
                        str(manifest_path),
                    ]
                )

            result = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(result["file_count"], len(payloads))
            self.assertEqual(stdout.getvalue().count("\n"), 1)

            (app_dir / "tools/media/encoder.exe").write_bytes(b"changed")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = main(
                    [
                        "verify-tree",
                        "--app-dir",
                        str(app_dir),
                        "--manifest",
                        str(manifest_path),
                    ]
                )
            self.assertEqual(exit_code, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertTrue(stderr.getvalue().strip())

    def test_cli_missing_arguments_help_version_and_interrupt(self):
        for argv, expected in ((["verify-tree"], 2), (["--help"], 0), (["--version"], 0)):
            with self.subTest(argv=argv):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    self.assertEqual(main(argv), expected)

        with patch("media_distribution.load_manifest", side_effect=KeyboardInterrupt):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = main(
                    [
                        "verify-tree",
                        "--app-dir",
                        "app",
                        "--manifest",
                        "manifest.json",
                    ]
                )
            self.assertEqual(result, 130)
            self.assertEqual(stdout.getvalue(), "")
            self.assertTrue(stderr.getvalue().strip())


if __name__ == "__main__":
    unittest.main()
