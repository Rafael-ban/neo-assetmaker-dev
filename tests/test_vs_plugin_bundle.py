"""Bundled tutorial plugins participate in both runtime consumers."""

import tempfile
import unittest
from pathlib import Path

from config.vs_runtime import VSRuntimeConfig
from resources.vapoursynth.python.assetmaker_vs.runtime_fingerprint import (
    compute_runtime_fingerprint,
)
from resources.vapoursynth.python.assetmaker_vs.runtime_layout import (
    resolve_runtime_layout,
    sanitize_runtime_process_environment,
)
from tests.test_vs_runtime_layout import _build_r79_fixture


class PluginBundleLayoutTests(unittest.TestCase):
    def test_package_dirs_and_dependency_search_are_shared(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _build_r79_fixture(root)
            plugins = root / "tools" / "vs-plugins"
            for name in ("z-filter", "a-dependency", ".installing"):
                (plugins / name).mkdir(parents=True)

            layout = resolve_runtime_layout(root)
            self.assertEqual(
                [path.name for path in layout.bundled_native_plugin_dirs],
                ["01-lsmas", "02-imwri", "a-dependency", "z-filter"],
            )
            environment = sanitize_runtime_process_environment({}, layout)
            self.assertIn(str(plugins / "a-dependency"), environment["PATH"])
            self.assertNotIn(".installing", environment["PATH"])

    def test_plugin_models_are_part_of_the_frozen_runtime_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _build_r79_fixture(root)
            helper = root / "resources/vapoursynth/python/assetmaker_vs"
            helper.mkdir(parents=True, exist_ok=True)
            (helper / "__init__.py").write_text("", encoding="utf-8")
            plugin = root / "tools/vs-plugins/example"
            plugin.mkdir(parents=True)
            (plugin / "filter.dll").write_bytes(b"fixture")
            model = plugin / "weights.bin"
            model.write_bytes(b"first model")
            runtime = VSRuntimeConfig().to_dict()
            before = compute_runtime_fingerprint(root, runtime)
            model.write_bytes(b"updated model")
            self.assertNotEqual(before, compute_runtime_fingerprint(root, runtime))
