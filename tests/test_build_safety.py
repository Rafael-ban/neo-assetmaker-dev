import io
import os
import stat
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import build


class _StatWithReparsePoint:
    def __init__(self, original):
        self._original = original
        self.st_file_attributes = (
            getattr(original, "st_file_attributes", 0)
            | stat.FILE_ATTRIBUTE_REPARSE_POINT
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


class BuildSafetyTests(unittest.TestCase):
    @staticmethod
    def _write(root, relative, content=b"fixture"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    @staticmethod
    def _patch_lstat(marked_path=None, failing_path=None, observed=None):
        original_lstat = Path.lstat

        def controlled_lstat(path):
            candidate = Path(path)
            if observed is not None:
                observed.append(candidate)
            if failing_path is not None and candidate == failing_path:
                raise PermissionError(f"cannot inspect {candidate}")
            result = original_lstat(path)
            if marked_path is not None and candidate == marked_path:
                return _StatWithReparsePoint(result)
            return result

        return patch.object(Path, "lstat", autospec=True, side_effect=controlled_lstat)

    def _assert_file_bytes(self, path, expected):
        self.assertTrue(path.exists(), f"应保留文件却已消失: {path}")
        self.assertEqual(path.read_bytes(), expected)

    @staticmethod
    @contextmanager
    def _mock_main(
        dist_dir,
        *,
        clean=False,
        no_portable=False,
        obfuscate=False,
        run_cxfreeze_result=False,
        clean_side_effect=None,
    ):
        args = SimpleNamespace(
            clean=clean,
            no_installer=True,
            no_portable=no_portable,
            obfuscate=obfuscate,
            skip_flasher=False,
        )
        with (
            patch.object(build, "parse_args", return_value=args),
            patch.object(build.os, "chdir"),
            patch.object(build, "DIST_DIR", os.fspath(dist_dir)),
            patch.object(
                build,
                "check_requirements",
                return_value=True,
            ) as requirements,
            patch.object(
                build,
                "clean_build",
                side_effect=clean_side_effect,
            ) as clean_build,
            patch.object(build, "prepare_obfuscated_source") as obfuscate_source,
            patch.object(
                build,
                "run_cxfreeze",
                return_value=run_cxfreeze_result,
            ) as run_cxfreeze,
            patch.object(build, "generate_install_manifest") as install_manifest,
            patch.object(build, "create_portable_archive") as portable_archive,
            patch.object(build, "create_installer") as installer,
        ):
            yield SimpleNamespace(
                requirements=requirements,
                clean_build=clean_build,
                obfuscate_source=obfuscate_source,
                run_cxfreeze=run_cxfreeze,
                install_manifest=install_manifest,
                portable_archive=portable_archive,
                installer=installer,
            )

    def test_cleanup_is_limited_to_source_roots_and_root_cache(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            removable = {
                "core/sub/__pycache__/core.pyc": b"core-cache",
                "config/__pycache__/config.pyc": b"config-cache",
                "gui/widgets/__pycache__/widget.pyc": b"gui-cache",
                "utils/__pycache__/util.pyc": b"utils-cache",
                "_mext/ui/__pycache__/extension.pyc": b"extension-cache",
                "tests/__pycache__/test_sample.pyc": b"tests-cache",
                (
                    "resources/vapoursynth/python/package/"
                    "__pycache__/runtime.pyc"
                ): b"vs-python-cache",
                "__pycache__/build.pyc": b"root-cache",
            }
            preserved = {
                "artifacts/u0-r79/__pycache__/proof.pyc": b"evidence",
                "tools/media/runtime/__pycache__/runtime.pyc": b"runtime",
                ".superpowers/sdd/plan/__pycache__/note.pyc": b"history",
                ".venv/Lib/__pycache__/thirdparty.pyc": b"dependency",
                "unknown/__pycache__/user.pyc": b"user",
                "resources/other/__pycache__/asset.pyc": b"other-resource",
            }
            source_file = self._write(root, "core/sub/module.py", b"source")
            for relative, content in removable.items():
                self._write(root, relative, content)
            for relative, content in preserved.items():
                self._write(root, relative, content)

            build._clean_pycache(root)

            for relative in removable:
                self.assertFalse((root / relative).parent.exists(), relative)
            self.assertEqual(source_file.read_bytes(), b"source")
            for relative, content in preserved.items():
                self._assert_file_bytes(root / relative, content)

    def test_cleanup_is_idempotent_when_no_cache_exists(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write(root, "core/module.py", b"source")

            build._clean_pycache(root)
            build._clean_pycache(root)

            self.assertEqual((root / "core/module.py").read_bytes(), b"source")

    def test_reparse_source_root_is_not_entered(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_file = self._write(root, "core/__pycache__/owned.pyc")

            with self._patch_lstat(marked_path=root / "core"):
                build._clean_pycache(root)

            self._assert_file_bytes(cache_file, b"fixture")

    def test_reparse_ancestor_of_nested_source_root_is_not_entered(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_file = self._write(
                root,
                "resources/vapoursynth/python/__pycache__/binding.pyc",
            )

            with self._patch_lstat(marked_path=root / "resources/vapoursynth"):
                build._clean_pycache(root)

            self._assert_file_bytes(cache_file, b"fixture")

    def test_reparse_source_subdirectory_is_pruned(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            preserved = self._write(
                root,
                "core/reparse-child/__pycache__/linked.pyc",
                b"linked-cache",
            )
            removed = self._write(
                root,
                "core/ordinary/__pycache__/ordinary.pyc",
                b"ordinary-cache",
            )

            with self._patch_lstat(marked_path=root / "core/reparse-child"):
                build._clean_pycache(root)

            self._assert_file_bytes(preserved, b"linked-cache")
            self.assertFalse(removed.parent.exists())

    def test_reparse_cache_directory_is_preserved(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_file = self._write(root, "core/__pycache__/owned.pyc")

            with self._patch_lstat(marked_path=cache_file.parent):
                build._clean_pycache(root)

            self._assert_file_bytes(cache_file, b"fixture")

    def test_reparse_entry_inside_cache_preserves_entire_cache(self):
        for entry_kind in ("directory", "file"):
            with self.subTest(entry_kind=entry_kind), TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                first_file = self._write(
                    root,
                    "core/__pycache__/a-first.pyc",
                    b"first",
                )
                if entry_kind == "directory":
                    marked = root / "core/__pycache__/z-marked"
                    marked.mkdir()
                    self._write(root, "core/__pycache__/z-marked/nested.pyc")
                else:
                    marked = self._write(
                        root,
                        "core/__pycache__/z-marked.pyc",
                        b"marked",
                    )

                with self._patch_lstat(marked_path=marked):
                    build._clean_pycache(root)

                self._assert_file_bytes(first_file, b"first")
                self.assertTrue(marked.exists())

    def test_lstat_failure_inside_cache_preserves_entire_cache(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_file = self._write(
                root,
                "core/__pycache__/a-first.pyc",
                b"first",
            )
            failing_file = self._write(
                root,
                "core/__pycache__/z-unreadable.pyc",
                b"unreadable",
            )

            with self._patch_lstat(failing_path=failing_file):
                build._clean_pycache(root)

            self._assert_file_bytes(first_file, b"first")
            self._assert_file_bytes(failing_file, b"unreadable")

    def test_unavailable_windows_attributes_preserve_entire_cache(self):
        for error_type in (AttributeError, OSError):
            with self.subTest(error=error_type.__name__), TemporaryDirectory() as temp:
                root = Path(temp)
                ordinary = self._write(root, "core/__pycache__/a-first.pyc", b"first")
                unknown = self._write(root, "core/__pycache__/z-unknown.pyc", b"unknown")
                original_lstat = Path.lstat

                def controlled_lstat(path):
                    result = original_lstat(path)
                    if path == unknown:
                        return _StatWithUnavailableAttributes(result, error_type)
                    return result

                with patch.object(Path, "lstat", controlled_lstat):
                    try:
                        build._clean_pycache(root)
                    except (AttributeError, OSError) as exc:
                        self.fail(f"Attribute inspection must preserve cache: {exc}")

                self._assert_file_bytes(ordinary, b"first")
                self._assert_file_bytes(unknown, b"unknown")

    def test_scandir_failure_inside_cache_preserves_entire_cache(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_file = self._write(
                root,
                "core/__pycache__/a-first.pyc",
                b"first",
            )
            blocked_dir = root / "core/__pycache__/z-blocked"
            blocked_file = self._write(blocked_dir, "nested.pyc", b"blocked")
            original_scandir = os.scandir

            def controlled_scandir(path):
                if Path(path) == blocked_dir:
                    raise PermissionError(f"cannot enumerate {path}")
                return original_scandir(path)

            with patch("build.os.scandir", side_effect=controlled_scandir):
                try:
                    build._clean_pycache(root)
                except PermissionError as exc:
                    self.fail(f"缓存预检读取失败应保全而不是传播: {exc}")

            self._assert_file_bytes(first_file, b"first")
            self._assert_file_bytes(blocked_file, b"blocked")

    def test_existing_build_output_aborts_before_media_check_or_cleanup(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            build_output = root / "existing-build"
            build_output.mkdir()
            sentinel = self._write(build_output, "user.bin", b"preserve")

            with (
                patch.object(build, "BUILD_DIR", str(build_output)),
                patch.object(build, "_collect_media_tool_include_files") as collect,
                patch.object(build, "_clean_pycache") as cleanup,
                patch.object(
                    build,
                    "_diagnose_build_env",
                    side_effect=AssertionError("existing output guard was bypassed"),
                ) as diagnose,
            ):
                try:
                    result = build.run_cxfreeze(source_root=root)
                except AssertionError as exc:
                    self.fail(str(exc))

            self.assertFalse(result)
            collect.assert_not_called()
            cleanup.assert_not_called()
            diagnose.assert_not_called()
            self._assert_file_bytes(sentinel, b"preserve")

    def test_main_rejects_existing_portable_outputs_before_heavy_stages(self):
        for output_kind in ("archive", "temporary"):
            for obfuscate in (False, True):
                with (
                    self.subTest(output_kind=output_kind, obfuscate=obfuscate),
                    TemporaryDirectory() as temp,
                ):
                    root = Path(temp)
                    dist_dir = root / "dist"
                    dist_dir.mkdir()
                    archive = dist_dir / build.portable_archive_name()
                    output = (
                        archive
                        if output_kind == "archive"
                        else archive.with_suffix(".zip.tmp")
                    )
                    output.write_bytes(b"preserve-existing-portable")
                    stdout = io.StringIO()

                    with (
                        self._mock_main(dist_dir, obfuscate=obfuscate) as mocked,
                        redirect_stdout(stdout),
                        self.assertRaises(SystemExit) as raised,
                    ):
                        build.main()

                    self.assertEqual(raised.exception.code, 1)
                    mocked.clean_build.assert_not_called()
                    mocked.obfuscate_source.assert_not_called()
                    mocked.run_cxfreeze.assert_not_called()
                    mocked.install_manifest.assert_not_called()
                    mocked.portable_archive.assert_not_called()
                    mocked.installer.assert_not_called()
                    self._assert_file_bytes(output, b"preserve-existing-portable")
                    self.assertIn(
                        "Portable archive preflight failed",
                        stdout.getvalue(),
                    )

    def test_main_no_portable_ignores_existing_portable_outputs(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            dist_dir = root / "dist"
            dist_dir.mkdir()
            archive = dist_dir / build.portable_archive_name()
            temporary = archive.with_suffix(".zip.tmp")
            archive.write_bytes(b"preserve-archive")
            temporary.write_bytes(b"preserve-temporary")
            stdout = io.StringIO()

            with (
                self._mock_main(
                    dist_dir,
                    no_portable=True,
                    run_cxfreeze_result=True,
                ) as mocked,
                redirect_stdout(stdout),
            ):
                build.main()

            mocked.clean_build.assert_not_called()
            mocked.obfuscate_source.assert_not_called()
            mocked.run_cxfreeze.assert_called_once_with(
                skip_flasher=False,
                source_root=None,
            )
            mocked.install_manifest.assert_called_once_with()
            mocked.portable_archive.assert_not_called()
            mocked.installer.assert_not_called()
            self._assert_file_bytes(archive, b"preserve-archive")
            self._assert_file_bytes(temporary, b"preserve-temporary")
            self.assertIn("Portable archive skipped", stdout.getvalue())

    def test_main_clean_runs_before_portable_preflight(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            dist_dir = root / "dist"
            dist_dir.mkdir()
            archive = dist_dir / build.portable_archive_name()
            archive.write_bytes(b"remove-on-explicit-clean")

            def simulate_clean():
                archive.unlink()
                dist_dir.rmdir()

            with (
                self._mock_main(
                    dist_dir,
                    clean=True,
                    clean_side_effect=simulate_clean,
                ) as mocked,
                redirect_stdout(io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                build.main()

            self.assertEqual(raised.exception.code, 1)
            mocked.clean_build.assert_called_once_with()
            mocked.obfuscate_source.assert_not_called()
            mocked.run_cxfreeze.assert_called_once_with(
                skip_flasher=False,
                source_root=None,
            )
            mocked.install_manifest.assert_not_called()
            mocked.portable_archive.assert_not_called()
            mocked.installer.assert_not_called()
            self.assertFalse(dist_dir.exists())

    def test_main_missing_dist_preflight_does_not_create_it(self):
        with TemporaryDirectory() as temp:
            dist_dir = Path(temp) / "missing" / "dist"

            with (
                self._mock_main(dist_dir) as mocked,
                redirect_stdout(io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                build.main()

            self.assertEqual(raised.exception.code, 1)
            mocked.clean_build.assert_not_called()
            mocked.obfuscate_source.assert_not_called()
            mocked.run_cxfreeze.assert_called_once_with(
                skip_flasher=False,
                source_root=None,
            )
            mocked.install_manifest.assert_not_called()
            mocked.portable_archive.assert_not_called()
            mocked.installer.assert_not_called()
            self.assertFalse(dist_dir.exists())

    def test_main_rejects_unsafe_portable_ancestor_without_descending(self):
        for failure_kind in ("reparse", "unknown-attributes", "permission-error"):
            with self.subTest(failure_kind=failure_kind), TemporaryDirectory() as temp:
                root = Path(temp)
                marked = root / "output-boundary"
                dist_dir = marked / "ordinary" / "dist"
                dist_dir.mkdir(parents=True)
                observed = []
                original_lstat = Path.lstat
                original_lexists = os.path.lexists

                def controlled_lstat(path):
                    candidate = Path(path)
                    observed.append(candidate)
                    if candidate != marked:
                        return original_lstat(path)
                    if failure_kind == "reparse":
                        return _StatWithReparsePoint(original_lstat(path))
                    if failure_kind == "unknown-attributes":
                        return _StatWithUnavailableAttributes(
                            original_lstat(path),
                            AttributeError,
                        )
                    raise PermissionError(f"cannot inspect {candidate}")

                def controlled_lexists(path):
                    candidate = Path(path)
                    observed.append(candidate)
                    return original_lexists(path)

                stdout = io.StringIO()
                with (
                    self._mock_main(dist_dir) as mocked,
                    patch.object(
                        Path,
                        "lstat",
                        autospec=True,
                        side_effect=controlled_lstat,
                    ),
                    patch.object(
                        build.os.path,
                        "lexists",
                        side_effect=controlled_lexists,
                    ),
                    redirect_stdout(stdout),
                    self.assertRaises(SystemExit) as raised,
                ):
                    build.main()

                self.assertEqual(raised.exception.code, 1)
                mocked.clean_build.assert_not_called()
                mocked.obfuscate_source.assert_not_called()
                mocked.run_cxfreeze.assert_not_called()
                mocked.install_manifest.assert_not_called()
                mocked.portable_archive.assert_not_called()
                mocked.installer.assert_not_called()
                self.assertIn(marked, observed)
                descendants = [
                    path
                    for path in observed
                    if path != marked and marked in path.parents
                ]
                self.assertEqual(descendants, [])
                self.assertIn("Portable archive preflight failed", stdout.getvalue())

    def test_main_treats_explicit_file_not_found_as_missing_ancestor(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            marked = root / "vanished-boundary"
            dist_dir = marked / "ordinary" / "dist"
            dist_dir.mkdir(parents=True)
            observed = []
            original_lstat = Path.lstat

            def controlled_lstat(path):
                candidate = Path(path)
                observed.append(candidate)
                if candidate == marked:
                    raise FileNotFoundError(f"vanished before inspection: {candidate}")
                return original_lstat(path)

            with (
                self._mock_main(dist_dir) as mocked,
                patch.object(
                    Path,
                    "lstat",
                    autospec=True,
                    side_effect=controlled_lstat,
                ),
                redirect_stdout(io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                build.main()

            self.assertEqual(raised.exception.code, 1)
            mocked.clean_build.assert_not_called()
            mocked.obfuscate_source.assert_not_called()
            mocked.run_cxfreeze.assert_called_once_with(
                skip_flasher=False,
                source_root=None,
            )
            mocked.install_manifest.assert_not_called()
            mocked.portable_archive.assert_not_called()
            mocked.installer.assert_not_called()
            self.assertIn(marked, observed)
            descendants = [
                path
                for path in observed
                if path != marked and marked in path.parents
            ]
            self.assertEqual(descendants, [])
            self.assertTrue(dist_dir.exists())

    def test_main_rejects_unreadable_portable_targets_before_heavy_stages(self):
        for output_kind in ("archive", "temporary"):
            with self.subTest(output_kind=output_kind), TemporaryDirectory() as temp:
                root = Path(temp)
                dist_dir = root / "dist"
                dist_dir.mkdir()
                archive = dist_dir / build.portable_archive_name()
                output = (
                    archive
                    if output_kind == "archive"
                    else archive.with_suffix(".zip.tmp")
                )
                output.write_bytes(b"unreadable-existing-output")
                original_lstat = Path.lstat
                original_lexists = os.path.lexists

                def controlled_lstat(path):
                    candidate = Path(path)
                    if candidate == output:
                        raise PermissionError(f"cannot inspect {candidate}")
                    return original_lstat(path)

                def controlled_lexists(path):
                    if Path(path) == output:
                        return False
                    return original_lexists(path)

                stdout = io.StringIO()
                with (
                    self._mock_main(dist_dir) as mocked,
                    patch.object(
                        Path,
                        "lstat",
                        autospec=True,
                        side_effect=controlled_lstat,
                    ),
                    patch.object(
                        build.os.path,
                        "lexists",
                        side_effect=controlled_lexists,
                    ),
                    redirect_stdout(stdout),
                    self.assertRaises(SystemExit) as raised,
                ):
                    build.main()

                self.assertEqual(raised.exception.code, 1)
                mocked.clean_build.assert_not_called()
                mocked.obfuscate_source.assert_not_called()
                mocked.run_cxfreeze.assert_not_called()
                mocked.install_manifest.assert_not_called()
                mocked.portable_archive.assert_not_called()
                mocked.installer.assert_not_called()
                self._assert_file_bytes(output, b"unreadable-existing-output")
                self.assertIn("Portable archive preflight failed", stdout.getvalue())

    def test_portable_archive_rejects_unreadable_output_before_writing(self):
        for output_kind in ("archive", "temporary"):
            with self.subTest(output_kind=output_kind), TemporaryDirectory() as temp:
                root = Path(temp)
                source_root = root / "ArknightsPassMaker"
                self._write(source_root, "app.exe", b"app")
                dist_dir = root / "dist"
                dist_dir.mkdir()
                archive = dist_dir / build.portable_archive_name(
                    "9.9.9-output-read-error"
                )
                output = (
                    archive
                    if output_kind == "archive"
                    else archive.with_suffix(".zip.tmp")
                )
                output.write_bytes(b"unreadable-existing-output")
                original_lstat = Path.lstat
                original_lexists = os.path.lexists

                def controlled_lstat(path):
                    candidate = Path(path)
                    if candidate == output:
                        raise PermissionError(f"cannot inspect {candidate}")
                    return original_lstat(path)

                def controlled_lexists(path):
                    if Path(path) == output:
                        return False
                    return original_lexists(path)

                with (
                    patch.object(
                        Path,
                        "lstat",
                        autospec=True,
                        side_effect=controlled_lstat,
                    ),
                    patch.object(
                        build.os.path,
                        "lexists",
                        side_effect=controlled_lexists,
                    ),
                    patch.object(
                        build,
                        "ZipFile",
                        side_effect=OSError("archive writing must not start"),
                    ) as zip_file,
                    self.assertRaises(OSError),
                ):
                    build.create_portable_archive(
                        build_dir=source_root,
                        dist_dir=dist_dir,
                        version="9.9.9-output-read-error",
                    )

                zip_file.assert_not_called()
                self._assert_file_bytes(output, b"unreadable-existing-output")

    def test_portable_archive_rejects_reparse_grandparent_without_descending(self):
        for boundary_kind in ("source", "output"):
            with (
                self.subTest(boundary_kind=boundary_kind),
                TemporaryDirectory() as temp,
            ):
                root = Path(temp)
                source_root = (
                    root
                    / "source-boundary"
                    / "ordinary"
                    / "ArknightsPassMaker"
                )
                self._write(source_root, "app.exe", b"app")
                output_root = root / "output-boundary" / "ordinary" / "dist"
                output_root.mkdir(parents=True)
                marked = (
                    source_root.parent.parent
                    if boundary_kind == "source"
                    else output_root.parent.parent
                )
                observed = []
                original_lexists = os.path.lexists

                def controlled_lexists(path):
                    observed.append(Path(path))
                    return original_lexists(path)

                with (
                    self._patch_lstat(marked_path=marked, observed=observed),
                    patch.object(
                        build.os.path,
                        "lexists",
                        side_effect=controlled_lexists,
                    ),
                ):
                    with self.assertRaises(OSError):
                        build.create_portable_archive(
                            build_dir=source_root,
                            dist_dir=output_root,
                            version="9.9.9-ancestor-test",
                        )

                descendants = [
                    path
                    for path in observed
                    if path != marked and marked in path.parents
                ]
                self.assertIn(marked, observed)
                self.assertEqual(descendants, [])
                archive = output_root / build.portable_archive_name(
                    "9.9.9-ancestor-test"
                )
                self.assertFalse(archive.exists())
                self.assertFalse(archive.with_suffix(".zip.tmp").exists())


if __name__ == "__main__":
    unittest.main()
