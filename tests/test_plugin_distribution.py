import hashlib
import json
import shutil
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from plugin_distribution import (
    PluginDistributionError,
    clean_plugins,
    sync_plugins,
    verify_plugins,
)


def _entry(name: str, data: bytes) -> dict:
    return {
        "path": name,
        "member": name,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


class PluginDistributionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.app_dir = Path(self.temporary.name)
        self.archives = {}

    def _package(self, package_id, files, dependencies=(), adjacent_from=()):
        archive = self.app_dir / f"{package_id}.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            for name, data in files.items():
                zipped.writestr(name, data)
        url = f"https://example.test/{package_id}.zip"
        self.archives[url] = archive
        return {
            "id": package_id,
            "version": "1",
            "source": url,
            "url": url,
            "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "files": [_entry(name, data) for name, data in files.items()],
            "dependencies": list(dependencies),
            "namespaces": [package_id],
            "adjacent_from": list(adjacent_from),
        }

    def _manifest(self, packages):
        path = self.app_dir / "lock.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "target": "win64",
                    "install_root": "tools/vs-plugins",
                    "packages": packages,
                }
            ),
            encoding="utf-8",
        )
        return path

    def _download(self, url, destination):
        shutil.copyfile(self.archives[url], destination)

    def test_sync_repairs_partial_package_and_includes_adjacent_weights(self):
        weights = self._package("weights", {"weights.bin": b"model"})
        filter_package = self._package(
            "filter", {"filter.dll": b"binary"},
            dependencies=["weights"], adjacent_from=["weights"],
        )
        manifest = self._manifest([filter_package, weights])
        incomplete = self.app_dir / "tools/vs-plugins/filter"
        incomplete.mkdir(parents=True)
        (incomplete / "filter.dll").write_bytes(b"bad")
        (incomplete / "old.dll").write_bytes(b"old plugin")

        with patch("plugin_distribution._download", side_effect=self._download):
            files = sync_plugins(self.app_dir, manifest)

        self.assertEqual(len(files), 3)
        self.assertEqual(
            (incomplete / "weights.bin").read_bytes(),
            b"model",
        )
        self.assertFalse((incomplete / "old.dll").exists())
        self.assertEqual(verify_plugins(self.app_dir, manifest), files)
        with patch("plugin_distribution._download", side_effect=AssertionError):
            self.assertEqual(sync_plugins(self.app_dir, manifest), files)
        clean_plugins(self.app_dir)
        self.assertFalse((self.app_dir / "tools/vs-plugins").exists())

    def test_sync_reports_failure_after_installing_independent_package(self):
        broken = self._package("broken", {"broken.dll": b"bad"})
        good = self._package("good", {"good.dll": b"good"})
        manifest = self._manifest([broken, good])
        self.archives.pop(broken["url"])

        with patch("plugin_distribution._download", side_effect=self._download):
            with self.assertRaisesRegex(PluginDistributionError, "broken"):
                sync_plugins(self.app_dir, manifest)

        self.assertEqual(
            (self.app_dir / "tools/vs-plugins/good/good.dll").read_bytes(),
            b"good",
        )

    def test_sync_prunes_package_removed_from_lock(self):
        current = self._package("current", {"current.dll": b"current"})
        manifest = self._manifest([current])
        install_root = self.app_dir / "tools/vs-plugins"
        stale = install_root / "old_plugin"
        stale.mkdir(parents=True)
        (stale / "old.dll").write_bytes(b"old")
        abandoned = install_root / ".old_plugin.backup"
        abandoned.mkdir()
        (abandoned / "old.dll").write_bytes(b"old")

        with patch("plugin_distribution._download", side_effect=self._download):
            sync_plugins(self.app_dir, manifest)

        self.assertFalse(stale.exists())
        self.assertFalse(abandoned.exists())
        self.assertTrue((install_root / "current/current.dll").is_file())

    def test_raw_model_is_verified_and_copied_next_to_plugin_without_redownload(self):
        model = self._package("model", {"models/rife-v4.6/flownet.bin": b"weights"})
        model["format"] = "raw"
        model["archive_sha256"] = model["files"][0]["sha256"]
        raw = self.app_dir / "model.bin"
        raw.write_bytes(b"weights")
        self.archives[model["url"]] = raw
        plugin = self._package(
            "rife", {"rife.dll": b"plugin"}, dependencies=["model"], adjacent_from=["model"],
        )
        manifest = self._manifest([plugin, model])
        with patch("plugin_distribution._download", side_effect=self._download):
            files = sync_plugins(self.app_dir, manifest)
        model_copy = self.app_dir / "tools/vs-plugins/rife/models/rife-v4.6/flownet.bin"
        self.assertEqual(model_copy.read_bytes(), b"weights")
        self.assertEqual(verify_plugins(self.app_dir, manifest), files)
        with patch("plugin_distribution._download", side_effect=AssertionError("redownload")):
            self.assertEqual(sync_plugins(self.app_dir, manifest), files)

    def test_corrupt_raw_download_preserves_existing_package(self):
        model = self._package("model", {"models/flownet.param": b"new model"})
        model["format"] = "raw"
        model["archive_sha256"] = model["files"][0]["sha256"]
        raw = self.app_dir / "model.param"
        raw.write_bytes(b"wrong download")
        self.archives[model["url"]] = raw
        destination = self.app_dir / "tools/vs-plugins/model"
        existing = destination / "models/flownet.param"
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"old model")
        with patch("plugin_distribution._download", side_effect=self._download):
            with self.assertRaisesRegex(PluginDistributionError, "SHA-256 mismatch"):
                sync_plugins(self.app_dir, self._manifest([model]))
        self.assertEqual(existing.read_bytes(), b"old model")
        self.assertFalse((destination.parent / ".model.partial").exists())

    def test_raw_manifest_rejects_multiple_files_and_unknown_format(self):
        from plugin_distribution import load_manifest

        package = self._package("model", {"one.bin": b"one", "two.param": b"two"})
        package["format"] = "raw"
        with self.assertRaisesRegex(PluginDistributionError, "one file"):
            load_manifest(self.app_dir, self._manifest([package]))
        package["format"] = "unknown"
        with self.assertRaisesRegex(PluginDistributionError, "package format"):
            load_manifest(self.app_dir, self._manifest([package]))

    def test_selected_sync_installs_dependencies_and_preserves_unrelated_packages(self):
        weights = self._package("weights", {"weights.bin": b"weights"})
        rife = self._package("rife", {"rife.dll": b"rife"},
                             dependencies=["weights"], adjacent_from=["weights"])
        unused = self._package("unused", {"unused.dll": b"unused"})
        manifest = self._manifest([rife, weights, unused])
        old = self.app_dir / "tools/vs-plugins/legacy/keep.txt"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"keep")
        downloads = []

        def download(url, target):
            downloads.append(url)
            self._download(url, target)

        with patch("plugin_distribution._download", side_effect=download):
            files = sync_plugins(self.app_dir, manifest, package_ids=["rife"])
        self.assertEqual(set(downloads), {rife["url"], weights["url"]})
        self.assertEqual(len(files), 3)
        self.assertEqual(old.read_bytes(), b"keep")
        self.assertFalse((old.parent.parent / "unused").exists())
        self.assertEqual(verify_plugins(self.app_dir, manifest, package_ids=["rife"]), files)

    def test_unknown_selection_fails_before_creating_install_root(self):
        package = self._package("rife", {"rife.dll": b"rife"})
        with self.assertRaisesRegex(PluginDistributionError, "Unknown packages: missing"):
            sync_plugins(self.app_dir, self._manifest([package]), package_ids=["missing"])
        self.assertFalse((self.app_dir / "tools/vs-plugins").exists())

    def test_scoped_clean_is_rejected_before_deletion(self):
        from plugin_distribution import main

        with patch("plugin_distribution.clean_plugins") as clean, patch("sys.stderr"):
            with self.assertRaises(SystemExit) as raised:
                main(["clean", "--app-dir", str(self.app_dir), "--package", "rife"])
        self.assertEqual(raised.exception.code, 2)
        clean.assert_not_called()


if __name__ == "__main__":
    unittest.main()
