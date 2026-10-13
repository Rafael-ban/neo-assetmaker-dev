"""Base freezing never requires or includes optional native/GPU extensions."""

from __future__ import annotations

import os
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import build


class MinimalBuildTests(unittest.TestCase):
    def test_freeze_uses_base_media_without_verifying_optional_addons(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            freezer = types.ModuleType("cx_Freeze")
            freezer.setup = Mock()
            freezer.Executable = Mock()
            media_files = [("base-runtime.dll", "tools/media/runtime/vapoursynth.dll")]
            with (
                patch.dict(sys.modules, {"cx_Freeze": freezer}),
                patch.object(sys, "path", list(sys.path)),
                patch.dict(os.environ),
                patch.object(build, "BUILD_DIR", str(root / "app")),
                patch.object(build, "_diagnose_build_env"),
                patch.object(build, "_verify_modules", return_value=True),
                patch.object(build, "_clean_pycache"),
                patch.object(build, "get_site_packages", return_value=str(root)),
                patch.object(build, "_collect_media_tool_include_files", return_value=media_files),
                patch.object(build, "_verify_frozen_media_tool_tree") as verify_media,
                patch("plugin_distribution.verify_plugins", side_effect=RuntimeError(
                    "rife: missing or invalid librife_windows_x86-64.dll"
                )) as verify_optional,
                patch("builtins.print"),
            ):
                self.assertTrue(build.run_cxfreeze(skip_flasher=True))
            verify_optional.assert_not_called()
            verify_media.assert_called_once()
            included = freezer.setup.call_args.kwargs["options"]["build_exe"]["include_files"]
            self.assertIn(media_files[0], included)
            destinations = [str(destination).replace("\\", "/") for _, destination in included]
            self.assertFalse(any(
                destination.startswith(("tools/vs-plugins", "tools/vs-rife"))
                for destination in destinations
            ))


if __name__ == "__main__":
    unittest.main()
