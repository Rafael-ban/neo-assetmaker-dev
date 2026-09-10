import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
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
    def _patch_lstat(marked_path=None, failing_path=None):
        original_lstat = Path.lstat

        def controlled_lstat(path):
            candidate = Path(path)
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


if __name__ == "__main__":
    unittest.main()
