import hashlib
import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from plugin_distribution import PluginDistributionError
from rife_dlc import create_rife_dlc


class RifeDlcTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.app = Path(self.temporary.name) / "app"
        self.output = Path(self.temporary.name) / "RIFE-DLC.zip"
        self.payload = {
            "rife": {"rife.dll": b"plugin", "LICENSE": b"plugin license"},
            "model": {"model.bin": b"model"},
            "runtime": {"runtime.dll": b"runtime"},
            "license": {"LICENSE": b"runtime license"},
        }
        packages = []
        self.expected = {}
        for package_id, files in self.payload.items():
            packages.append({
                "id": package_id,
                "url": f"https://example.test/{package_id}.zip",
                "files": [{
                    "path": name, "member": name,
                    "sha256": hashlib.sha256(data).hexdigest(),
                } for name, data in files.items()],
                "adjacent_from": ["model", "runtime"] if package_id == "rife" else [],
            })
            for name, data in files.items():
                self._write(f"tools/vs-rife/{package_id}/{name}", data)
        for package_id in ("model", "runtime"):
            for name, data in self.payload[package_id].items():
                self._write(f"tools/vs-rife/rife/{name}", data)
        self._write("resources/packaging/vs-rife.json", json.dumps({
            "schema_version": 1, "target": "win64", "install_root": "tools/vs-rife",
            "packages": packages,
        }).encode())
        self._write("resources/vapoursynth/rife_pipeline.vpy", b"# optional RIFE script\n")

    def _write(self, name, data):
        path = self.app / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.expected[name] = data

    def test_exact_contents_exclude_base_and_other_extensions(self):
        self._write("AssetMaker.exe", b"base executable")
        self._write("tools/vs-plugins/general/general.dll", b"other extension")
        self._write("tools/vapoursynth/VapourSynth.dll", b"base runtime")
        self._write("resources/vapoursynth/default_pipeline.vpy", b"base script")
        for name in list(self.expected):
            if name not in {
                "resources/packaging/vs-rife.json", "resources/vapoursynth/rife_pipeline.vpy",
            } and not name.startswith("tools/vs-rife/"):
                self.expected.pop(name)
        self.assertEqual(create_rife_dlc(self.app, self.output), self.output)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(set(archive.namelist()), set(self.expected) | {"RIFE-DLC-README.txt"})
            self.assertIsNone(archive.testzip())
            for name, data in self.expected.items():
                self.assertEqual(archive.read(name), data)
            readme = archive.read("RIFE-DLC-README.txt").decode("utf-8")
            self.assertIn("native_plugin_dirs=<app>/tools/vs-rife/rife", readme)
            self.assertIn("全局本机脚本", readme)
        self.assertFalse(list(self.output.parent.glob("*.partial")))

    def test_incomplete_addon_leaves_no_output(self):
        (self.app / "tools/vs-rife/rife/model.bin").unlink()
        with self.assertRaises(PluginDistributionError):
            create_rife_dlc(self.app, self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.output.parent.glob("*.partial")))

    def test_existing_output_is_preserved_even_with_invalid_sources(self):
        self.output.write_bytes(b"user archive")
        (self.app / "resources/packaging/vs-rife.json").unlink()
        with self.assertRaisesRegex(FileExistsError, "already exists"):
            create_rife_dlc(self.app, self.output)
        self.assertEqual(self.output.read_bytes(), b"user archive")

    def test_missing_script_leaves_no_output(self):
        (self.app / "resources/vapoursynth/rife_pipeline.vpy").unlink()
        with self.assertRaises(FileNotFoundError):
            create_rife_dlc(self.app, self.output)
        self.assertFalse(self.output.exists())

    def test_write_failure_cleans_partial_output(self):
        with patch("rife_dlc.zipfile.ZipFile.write", side_effect=OSError("read failed")):
            with self.assertRaisesRegex(OSError, "read failed"):
                create_rife_dlc(self.app, self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.output.parent.glob("*.partial")))

if __name__ == "__main__":
    unittest.main()
