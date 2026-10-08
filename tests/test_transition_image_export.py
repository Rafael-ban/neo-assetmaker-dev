"""Transition images and normalized package references use the same names."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from config.epconfig import EPConfig, Transition, TransitionOptions, TransitionType
from core.export_service import ExportService
from gui.main_window import MainWindow


class TransitionImageExportTests(unittest.TestCase):
    def test_two_same_named_source_images_have_distinct_package_references(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            incoming = root / "incoming"
            looping = root / "looping"
            incoming.mkdir()
            looping.mkdir()
            self.assertTrue(cv2.imwrite(str(incoming / "logo.png"),
                                        np.full((24, 32, 4), 40, np.uint8)))
            self.assertTrue(cv2.imwrite(str(looping / "logo.png"),
                                        np.full((48, 64, 4), 180, np.uint8)))

            config = EPConfig(
                transition_in=Transition(
                    TransitionType.SWIPE,
                    TransitionOptions(image="incoming/logo.png"),
                ),
                transition_loop=Transition(
                    TransitionType.SWIPE,
                    TransitionOptions(image="looping/logo.png"),
                ),
            )
            collector = SimpleNamespace(_config=config, _base_dir=str(root))
            images = MainWindow._collect_transition_images(collector)
            self.assertEqual([name for name, _ in images],
                             ["transition_in.png", "transition_loop.png"])
            self.assertEqual([image.shape[:2] for _, image in images],
                             [(24, 32), (48, 64)])

            project = config.to_dict()
            self.assertEqual(project["transition_in"]["options"]["image"],
                             "incoming/logo.png")
            self.assertEqual(project["transition_loop"]["options"]["image"],
                             "looping/logo.png")

            payload = config.to_dict(normalize_paths=True)
            self.assertEqual(payload["transition_in"]["options"]["image"],
                             "transition_in.png")
            self.assertEqual(payload["transition_loop"]["options"]["image"],
                             "transition_loop.png")
            tasks = ExportService._snapshot_tasks(root, None, None, None, None, images)
            manifest = ExportService._build_manifest(
                tasks, json.dumps(payload).encode("utf-8"))
            self.assertEqual({item.relative_path for item in manifest},
                             {"transition_in.png", "transition_loop.png", "epconfig.json"})


if __name__ == "__main__":
    unittest.main()
