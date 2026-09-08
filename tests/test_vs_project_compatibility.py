import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.qt_harness import ensure_app

from config.epconfig import EPConfig
from config.epconfig import VSScriptState
from config.vs_runtime import VSRuntimeConfig
from core.vs_runtime.job import RationalFPS
from core.vs_runtime.script_header import ScriptHeader
from core.vs_runtime.session import ScriptSelection
from gui.widgets.video_preview import PreviewRenderContext, VideoPreviewWidget


def setUpModule():
    ensure_app()


_SOURCE_ONLY_HEADER = """# assetmaker-api: 1
# assetmaker-mode: compatible
# assetmaker-capabilities: source
# assetmaker-requires:
# assetmaker-editor-output: 0
"""


class VSProjectCompatibilityTests(unittest.TestCase):
    def _context(self, root: Path) -> PreviewRenderContext:
        script = root / "pipeline.vpy"
        script.write_text(_SOURCE_ONLY_HEADER, encoding="utf-8")
        header = ScriptHeader(1, "compatible", ("source",), (), 0)
        return PreviewRenderContext(
            project_root=str(root),
            track="loop",
            selection=ScriptSelection.from_header(script, header, "a" * 64),
            cache_dir=str(root / "cache"),
        )

    def test_legacy_script_state_defaults_to_builtin_and_export_never_serializes_editor(self):
        legacy = EPConfig.from_dict({"loop": {"file": "loop.mp4"}})
        self.assertEqual(legacy.editor.vs_script.source, "builtin")
        self.assertEqual(legacy.editor.vs_script.path, "")

        project = legacy.to_dict()
        self.assertNotIn("editor", project)
        normalized = legacy.to_dict(normalize_paths=True)
        self.assertNotIn("editor", normalized)
        self.assertNotIn("trusted", str(normalized).lower())
        self.assertNotIn("hash", str(normalized).lower())

    def test_compatible_source_only_uses_output0_and_never_applies_editor_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            widget = VideoPreviewWidget()
            widget.set_render_context(self._context(root))
            widget.video_path = str(root / "source.mp4")
            widget.video_path and Path(widget.video_path).write_bytes(b"source")
            widget.video_width = 1920
            widget.video_height = 1080
            widget.total_frames = 120
            widget._fps_rational = RationalFPS(30, 1)
            widget._metadata_resolved = True
            widget._timeline_start = 12
            widget._timeline_end_exclusive = 80
            widget.cropbox = [10, 20, 300, 400]
            widget._rotation = 90

            self.assertFalse(widget.set_rotation(180))
            self.assertFalse(widget.set_cropbox(1, 2, 3, 4))
            self.assertFalse(widget.set_timeline_range(20, 40))
            job = widget._make_render_job(bootstrap=False)
            self.assertEqual(job.transform.rotation, 0)
            self.assertEqual(job.transform.crop.width, 0)
            self.assertEqual(job.timeline.start_frame, 0)
            self.assertEqual(job.timeline.end_frame, 120)

    def test_untrusted_project_context_blocks_both_worker_loads_and_export_sessions(self):
        worker_factory = mock.Mock()
        loop = VideoPreviewWidget(worker_client_factory=worker_factory)
        intro = VideoPreviewWidget(worker_client_factory=worker_factory)
        for widget in (loop, intro):
            widget.set_execution_blocked("脚本未获信任")
            self.assertFalse(widget.load_video(__file__))
            self.assertIn("脚本未获信任", widget.video_label.text())
            with self.assertRaisesRegex(RuntimeError, "脚本未获信任"):
                widget.flush_render_job()
        worker_factory.assert_not_called()

    def test_main_window_wires_verified_project_script_to_both_previews(self):
        from gui.main_window import MainWindow
        from core.vs_runtime.snapshot import RuntimeSnapshot

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script = root / "vapoursynth" / "pipeline.vpy"
            script.parent.mkdir()
            script.write_text(_SOURCE_ONLY_HEADER, encoding="utf-8")
            header = ScriptHeader(1, "compatible", ("source",), (), 0)
            config = EPConfig()
            config.editor.vs_script = VSScriptState(
                "project", "vapoursynth/pipeline.vpy"
            )
            window = MainWindow.__new__(MainWindow)
            window._base_dir = str(root)
            window._app_dir = str(root)
            window._config = config
            window.video_preview = mock.Mock()
            window.intro_preview = mock.Mock()
            window.vs_script_panel = mock.Mock()
            window._timeline_preview = None
            window._set_script_export_enabled = mock.Mock()
            window._apply_timeline_capability_controls = mock.Mock()
            store = mock.Mock()
            store.is_trusted.return_value = True
            snapshot = RuntimeSnapshot(
                app_dir=str(root), runtime=VSRuntimeConfig(), fingerprint="b" * 64
            )

            with (
                mock.patch(
                    "gui.main_window.RuntimeSnapshot.resolve", return_value=snapshot
                ) as resolve_snapshot,
                mock.patch(
                    "gui.main_window.resolve_script_reference", return_value=script
                ) as resolve,
                mock.patch(
                    "gui.main_window.compute_script_bundle_hash",
                    return_value="a" * 64,
                ),
                mock.patch("gui.main_window.parse_script_header", return_value=header),
                mock.patch("gui.main_window.script_bundle_code_files", return_value=(
                    "pipeline.vpy",
                )),
                mock.patch("gui.main_window.ProjectTrustStore", return_value=store),
            ):
                MainWindow._configure_preview_render_contexts(window)

            resolve.assert_called_once()
            resolve_snapshot.assert_called_once_with(window._app_dir)
            store.is_trusted.assert_called_once_with(script.parent, "a" * 64)
            loop_context = window.video_preview.set_render_context.call_args.args[0]
            intro_context = window.intro_preview.set_render_context.call_args.args[0]
            self.assertEqual(loop_context.track, "loop")
            self.assertEqual(intro_context.track, "intro")
            self.assertEqual(loop_context.selection.script_path, str(script.resolve()))
            self.assertEqual(intro_context.selection.script_path, str(script.resolve()))
            self.assertEqual(loop_context.header, header)
            self.assertEqual(intro_context.header, header)
            self.assertIs(loop_context.runtime_snapshot, snapshot)
            self.assertIs(intro_context.runtime_snapshot, snapshot)
            self.assertTrue(window._script_ready)
            window._set_script_export_enabled.assert_called_once_with(True)


if __name__ == "__main__":
    unittest.main()
