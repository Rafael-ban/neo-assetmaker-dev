"""Small offscreen checks for settings loading and preview zoom display."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

from tests.qt_harness import ensure_app


class SettingsAndZoomUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_app()

    def test_usb_ids_load_without_bool_set_text_error(self):
        from gui.widgets.settings_page import SettingsPage

        page = SettingsPage()
        page.load_settings({})
        self.assertEqual(page.usbControlerVID.text(), "0203")
        self.assertEqual(page.usbControlerPID.text(), "5678")

        page.load_settings({"usb_controler_vid": False,
                            "usb_controler_pid": False})
        self.assertEqual(page.usbControlerVID.text(), "0203")
        self.assertEqual(page.usbControlerPID.text(), "5678")

    def test_vs_zoomed_out_frame_keeps_worker_size(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        preview = VideoPreviewWidget()
        preview.video_label.resize(400, 300)
        preview._vs_active = True
        preview._zoom_factor = 0.5
        preview._display_frame(np.zeros((100, 200, 3), dtype=np.uint8))

        pixmap = preview.video_label.pixmap()
        self.assertIsNotNone(pixmap)
        self.assertEqual(pixmap.width(), 200)
        self.assertEqual(pixmap.height(), 100)
        self.assertFalse(preview._crop_interaction_enabled())


if __name__ == "__main__":
    unittest.main()
