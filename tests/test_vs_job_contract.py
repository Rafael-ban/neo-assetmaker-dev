import concurrent.futures
import ctypes
import json
import multiprocessing
import os
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator, ValidationError

from core.vs_runtime import (
    CropSpec,
    OutputSpec,
    PathSpec,
    RationalFPS,
    RenderJob,
    RenderJobError,
    SourceSpec,
    TimelineSpec,
    TransformSpec,
    load_render_job,
    write_render_job,
)


ROOT = Path(__file__).resolve().parents[1]
JOB_SCHEMA = json.loads(
    (ROOT / "schemas" / "vs_job.schema.json").read_text(encoding="utf-8")
)


def _process_write_job(payload, barrier, results):
    """Spawn-safe contender that preserves real atomic-write side effects."""
    from core.vs_runtime import job as job_module

    real_atomic_write = job_module.atomic_write_json

    def synchronized_write(path, value, *, indent=2):
        barrier.wait(timeout=10)
        real_atomic_write(path, value, indent=indent)

    job_module.atomic_write_json = synchronized_write
    job = RenderJob.from_dict(payload)
    try:
        write_render_job(job)
    except RenderJobError:
        results.put(("error", job.track))
    else:
        results.put(("ok", job.track))


def make_job(
    *,
    source_path=r"D:\media\loop.mp4",
    source_kind="video",
    virtual_frame_count=None,
    start_frame=0,
    end_frame=120,
    fps=RationalFPS(30_000, 1_001),
    track="loop",
    epoch=7,
    cache_dir=r"D:\cache\assetmaker",
    crop=CropSpec("post_rotation_source_pixels", 0, 0, 0, 0),
    profile="360x640",
):
    return RenderJob(
        api_version=1,
        epoch=epoch,
        track=track,
        project_root=r"D:\素材\黍",
        source=SourceSpec(
            path=source_path,
            kind=source_kind,
            virtual_frame_count=virtual_frame_count,
        ),
        timeline=TimelineSpec(
            start_frame=start_frame,
            end_frame=end_frame,
            fps=fps,
        ),
        transform=TransformSpec(rotation=90, crop=crop),
        output=OutputSpec.from_profile(profile),
        paths=PathSpec(cache_dir=cache_dir),
    )


class RenderJobContractTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name).resolve()

    def test_schema_is_valid_and_rejects_unknown_nested_fields(self):
        Draft202012Validator.check_schema(JOB_SCHEMA)
        payload = make_job().to_dict()
        payload["timeline"]["guessed"] = True

        with self.assertRaises(ValidationError):
            Draft202012Validator(JOB_SCHEMA).validate(payload)
        with self.assertRaises(RenderJobError):
            RenderJob.from_dict(payload)

    def test_bootstrap_nulls_are_preview_only(self):
        bootstrap = make_job(end_frame=None, fps=None)

        bootstrap.validate(for_export=False)
        with self.assertRaises(RenderJobError):
            bootstrap.validate(for_export=True)

    def test_fraction_unicode_track_and_crop_space_roundtrip(self):
        job = make_job(source_path=r"D:\素材\黍\loop.mp4", track="intro")

        payload = job.to_dict()
        Draft202012Validator(JOB_SCHEMA).validate(payload)

        self.assertEqual(RenderJob.from_dict(payload), job)
        self.assertEqual(payload["track"], "intro")
        self.assertEqual(
            payload["timeline"]["fps"],
            {"numerator": 30_000, "denominator": 1_001},
        )
        self.assertEqual(
            payload["transform"]["crop"]["coordinate_space"],
            "post_rotation_source_pixels",
        )

    def test_output_is_derived_from_resolution_specs(self):
        compact = OutputSpec.from_profile("360x640")
        large = OutputSpec.from_profile("720x1280")
        wide = OutputSpec.from_profile("800x1280")

        self.assertEqual(
            (compact.display_width, compact.display_height), (360, 640)
        )
        self.assertEqual((compact.coded_width, compact.coded_height), (360, 640))
        self.assertEqual(
            (large.display_width, large.display_height), (720, 1280)
        )
        self.assertEqual((large.coded_width, large.coded_height), (720, 1280))
        self.assertEqual((wide.display_width, wide.display_height), (800, 1280))
        self.assertEqual((wide.coded_width, wide.coded_height), (800, 1280))
        with self.assertRaises(RenderJobError):
            OutputSpec.from_profile("1080x1920")
        with self.assertRaises(RenderJobError):
            OutputSpec.from_profile("720x1080")

    def test_screen_profiles_match_both_schemas(self):
        from config.constants import RESOLUTION_SPECS
        from config.epconfig import ScreenType

        expected = {"360x640", "720x1280", "800x1280"}
        epconfig_schema = json.loads(
            (ROOT / "schemas" / "epconfig.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(RESOLUTION_SPECS), expected)
        self.assertEqual({member.value for member in ScreenType}, expected)
        self.assertEqual(set(epconfig_schema["properties"]["screen"]["enum"]), expected)
        self.assertEqual(
            set(JOB_SCHEMA["$defs"]["output"]["properties"]["profile"]["enum"]),
            expected,
        )

    def test_every_profile_has_full_schema_roundtrip(self):
        expected_outputs = {
            "360x640": {
                "profile": "360x640",
                "display_width": 360,
                "display_height": 640,
                "coded_width": 360,
                "coded_height": 640,
                "pixel_format": "YUV420P8",
                "matrix": "170m",
                "transfer": "170m",
                "primaries": "170m",
                "range": "limited",
                "final_rotate_180": False,
            },
            "720x1280": {
                "profile": "720x1280",
                "display_width": 720,
                "display_height": 1280,
                "coded_width": 720,
                "coded_height": 1280,
                "pixel_format": "YUV420P8",
                "matrix": "170m",
                "transfer": "170m",
                "primaries": "170m",
                "range": "limited",
                "final_rotate_180": False,
            },
            "800x1280": {
                "profile": "800x1280",
                "display_width": 800,
                "display_height": 1280,
                "coded_width": 800,
                "coded_height": 1280,
                "pixel_format": "YUV420P8",
                "matrix": "170m",
                "transfer": "170m",
                "primaries": "170m",
                "range": "limited",
                "final_rotate_180": False,
            },
        }
        for profile, expected in expected_outputs.items():
            with self.subTest(profile=profile):
                job = make_job(profile=profile)
                payload = job.to_dict()
                Draft202012Validator(JOB_SCHEMA).validate(payload)
                self.assertEqual(payload["output"], expected)
                self.assertEqual(RenderJob.from_dict(payload), job)

    def test_mismatched_profile_dimensions_are_rejected(self):
        job = make_job()
        invalid_output = replace(job.output, coded_width=384)

        with self.assertRaises(RenderJobError):
            replace(job, output=invalid_output).validate()

    def test_python_model_rejects_boolean_rotation_and_float_dimensions(self):
        job = make_job()
        invalid_jobs = (
            replace(job, transform=replace(job.transform, rotation=False)),
            replace(job, output=replace(job.output, display_width=360.0)),
        )

        for invalid in invalid_jobs:
            with self.subTest(job=invalid):
                with self.assertRaises(RenderJobError):
                    invalid.validate()

    def test_python_model_wraps_invalid_nested_dataclass_types(self):
        job = make_job()
        invalid_jobs = (
            replace(job, timeline=replace(job.timeline, fps={})),
            replace(job, transform=replace(job.transform, crop={})),
        )

        for invalid in invalid_jobs:
            with self.subTest(job=invalid):
                with self.assertRaises(RenderJobError):
                    invalid.validate()

        with self.assertRaises(RenderJobError):
            OutputSpec.from_profile([])

    def test_video_and_image_virtual_frame_rules_are_enforced(self):
        with self.assertRaises(RenderJobError):
            make_job(virtual_frame_count=120).validate()
        with self.assertRaises(RenderJobError):
            make_job(
                source_path=r"D:\media\still.png",
                source_kind="image",
                virtual_frame_count=None,
            ).validate()

        image = make_job(
            source_path=r"D:\media\still.png",
            source_kind="image",
            virtual_frame_count=240,
            end_frame=120,
        )
        image.validate(for_export=True)
        restored = RenderJob.from_dict(image.to_dict())
        self.assertEqual(restored.source.virtual_frame_count, 240)
        self.assertEqual(restored.timeline.end_frame, 120)

    def test_image_timeline_is_always_resolved_and_within_virtual_count(self):
        invalid_jobs = (
            make_job(
                source_path=r"D:\media\still.png",
                source_kind="image",
                virtual_frame_count=10,
                end_frame=None,
                fps=None,
            ),
            make_job(
                source_path=r"D:\media\still.png",
                source_kind="image",
                virtual_frame_count=10,
                end_frame=11,
            ),
            make_job(
                source_path=r"D:\media\still.png",
                source_kind="image",
                virtual_frame_count=10,
                start_frame=10,
                end_frame=11,
            ),
        )
        for invalid in invalid_jobs:
            with self.subTest(job=invalid):
                with self.assertRaises(RenderJobError):
                    invalid.validate(for_export=False)

        null_payload = invalid_jobs[0].to_dict()
        with self.assertRaises(ValidationError):
            Draft202012Validator(JOB_SCHEMA).validate(null_payload)

        valid = make_job(
            source_path=r"D:\media\still.png",
            source_kind="image",
            virtual_frame_count=10,
            start_frame=3,
            end_frame=10,
        )
        valid.validate(for_export=False)
        valid.validate(for_export=True)

    def test_timeline_crop_rotation_and_paths_are_strict(self):
        invalid_jobs = (
            make_job(start_frame=-1),
            make_job(start_frame=8, end_frame=8),
            make_job(fps=RationalFPS(0, 1)),
            replace(make_job(), transform=replace(make_job().transform, rotation=45)),
            make_job(crop=CropSpec("post_rotation_source_pixels", -1, 0, 0, 0)),
            make_job(crop=CropSpec("post_rotation_source_pixels", 0, 0, 10, 0)),
            make_job(source_path=r"D:\media\..\loop.mp4"),
            replace(make_job(), project_root="relative"),
            replace(make_job(), paths=PathSpec(cache_dir="relative")),
        )
        for job in invalid_jobs:
            with self.subTest(job=job):
                with self.assertRaises(RenderJobError):
                    job.validate()

    def test_job_paths_share_canonical_windows_wire_contract(self):
        valid_paths = (
            r"D:\素材\黍\loop.mp4",
            r"\\server\share\素材\loop.mp4",
        )
        for valid_path in valid_paths:
            with self.subTest(valid=valid_path):
                job = make_job(source_path=valid_path)
                Draft202012Validator(JOB_SCHEMA).validate(job.to_dict())
                job.validate()

        invalid_paths = (
            "D:/media/loop.mp4",
            r"D:\media\..\loop.mp4",
            r"\media\loop.mp4",
            r"relative\loop.mp4",
            r"D:\bad?name\loop.mp4",
        )
        for invalid_path in invalid_paths:
            payload = make_job().to_dict()
            payload["source"]["path"] = invalid_path
            with self.subTest(invalid=invalid_path):
                with self.assertRaises(ValidationError):
                    Draft202012Validator(JOB_SCHEMA).validate(payload)
                with self.assertRaises(RenderJobError):
                    RenderJob.from_dict(payload)

    def test_job_paths_reject_windows_alias_and_invalid_components(self):
        cases = (
            ("drive-nul", "D:\\bad\0name\\file.vpy", True),
            ("drive-control", "D:\\bad\x1fname\\file.vpy", True),
            ("drive-trailing-dot", r"D:\trail.\file.vpy", True),
            ("drive-trailing-space", "D:\\trail \\file.vpy", True),
            ("drive-device", r"D:\CON\file.vpy", False),
            ("drive-device-extension", r"D:\con.txt\file.vpy", False),
            ("unc-nul", "\\\\server\\share\\bad\0name\\file.vpy", True),
            ("unc-control", "\\\\server\\share\\bad\x01name\\file.vpy", True),
            ("unc-trailing-dot", r"\\server\share\trail.\file.vpy", True),
            ("unc-trailing-space", "\\\\server\\share\\trail \\file.vpy", True),
            ("unc-device", r"\\server\share\AUX.txt\file.vpy", False),
            ("extended-device", r"\\?\D:\media\file.vpy", True),
            ("win32-device", r"\\.\PhysicalDrive0", True),
        )
        validator = Draft202012Validator(JOB_SCHEMA)
        for name, invalid_path, schema_can_reject in cases:
            payload = make_job().to_dict()
            payload["source"]["path"] = invalid_path
            with self.subTest(name=name, layer="schema"):
                if schema_can_reject:
                    with self.assertRaises(ValidationError):
                        validator.validate(payload)
                else:
                    validator.validate(payload)
            with self.subTest(name=name, layer="model"):
                with self.assertRaises(RenderJobError):
                    RenderJob.from_dict(payload)

    def test_job_paths_reject_superscript_com_lpt_devices(self):
        invalid_paths = (
            r"D:\COM¹\file.vpy",
            r"D:\com².log\file.vpy",
            r"D:\LpT³\file.vpy",
            r"\\server\share\LPT¹.txt\file.vpy",
            r"\\server\share\lpt²\file.vpy",
            r"\\server\share\CoM³.bin\file.vpy",
        )
        validator = Draft202012Validator(JOB_SCHEMA)
        for invalid_path in invalid_paths:
            payload = make_job().to_dict()
            payload["source"]["path"] = invalid_path
            with self.subTest(path=invalid_path, layer="schema"):
                validator.validate(payload)
            with self.subTest(path=invalid_path, layer="model"):
                with self.assertRaises(RenderJobError):
                    RenderJob.from_dict(payload)

    def test_float_api_version_and_rotation_tokens_are_rejected(self):
        invalid_payloads = []
        api_payload = make_job().to_dict()
        api_payload["api_version"] = 1.0
        invalid_payloads.append(api_payload)
        rotation_payload = make_job().to_dict()
        rotation_payload["transform"]["rotation"] = 90.0
        invalid_payloads.append(rotation_payload)

        for index, payload in enumerate(invalid_payloads):
            with self.subTest(payload=payload):
                with self.assertRaises(RenderJobError):
                    RenderJob.from_dict(payload)
                path = self.root / f"float-token-{index}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(RenderJobError):
                    load_render_job(path)

    def test_write_uses_epoch_filename_and_never_overwrites(self):
        job = make_job(cache_dir=str(self.root), epoch=42)

        path = write_render_job(job)
        before = path.read_bytes()

        self.assertEqual(path, self.root / "job-42.json")
        self.assertEqual(load_render_job(path, for_export=True), job)
        with self.assertRaises(RenderJobError):
            write_render_job(replace(job, track="intro"))
        self.assertEqual(path.read_bytes(), before)

    @unittest.skipUnless(os.name == "nt", "Windows publish contract")
    def test_windows_publish_uses_write_through_without_replace(self):
        job = make_job(cache_dir=str(self.root), epoch=43)

        def move_file(source, target, flags):
            os.rename(source, target)
            return 1

        move = mock.Mock(side_effect=move_file)
        kernel32 = mock.Mock()
        kernel32.MoveFileExW = move
        with mock.patch.object(ctypes, "WinDLL", return_value=kernel32):
            path = write_render_job(job)

        self.assertEqual(load_render_job(path, for_export=True), job)
        move.assert_called_once()
        source, target, flags = move.call_args.args
        self.assertEqual(Path(target), path)
        self.assertEqual(flags, 0x00000008)
        self.assertFalse(Path(source).exists())
        self.assertFalse(list(self.root.glob(".*.publish")))

    @unittest.skipUnless(os.name == "nt", "Windows publish contract")
    def test_win32_publish_conflicts_map_to_existing_job_error(self):
        for epoch, error_code in ((44, 80), (45, 183)):
            job = make_job(cache_dir=str(self.root), epoch=epoch)
            kernel32 = mock.Mock()
            kernel32.MoveFileExW.return_value = 0
            with self.subTest(error_code=error_code), mock.patch.object(
                ctypes, "WinDLL", return_value=kernel32
            ), mock.patch.object(
                ctypes, "get_last_error", return_value=error_code
            ), mock.patch.object(
                ctypes, "FormatError", return_value="already exists"
            ):
                with self.assertRaisesRegex(
                    RenderJobError, "拒绝覆盖既有 RenderJob"
                ):
                    write_render_job(job)
            self.assertFalse((self.root / f"job-{epoch}.json").exists())
            self.assertFalse(list(self.root.glob(".*.publish")))

    @unittest.skipUnless(os.name == "nt", "Windows publish contract")
    def test_win32_publish_failure_reports_target_and_cleans_temp(self):
        job = make_job(cache_dir=str(self.root), epoch=46)
        target = self.root / "job-46.json"
        kernel32 = mock.Mock()
        kernel32.MoveFileExW.return_value = 0
        with mock.patch.object(
            ctypes, "WinDLL", return_value=kernel32
        ), mock.patch.object(
            ctypes, "get_last_error", return_value=50
        ), mock.patch.object(
            ctypes, "FormatError", return_value="not supported"
        ):
            with self.assertRaises(RenderJobError) as raised:
                write_render_job(job)

        self.assertIn(str(target.resolve()), str(raised.exception))
        self.assertIn("not supported", str(raised.exception))
        self.assertFalse(target.exists())
        self.assertFalse(list(self.root.glob(".*.publish")))

    @unittest.skipUnless(os.name == "nt", "Windows publish contract")
    def test_publish_cleanup_error_does_not_mask_win32_failure(self):
        job = make_job(cache_dir=str(self.root), epoch=47)
        kernel32 = mock.Mock()
        kernel32.MoveFileExW.return_value = 0
        with mock.patch.object(
            ctypes, "WinDLL", return_value=kernel32
        ), mock.patch.object(
            ctypes, "get_last_error", return_value=50
        ), mock.patch.object(
            ctypes, "FormatError", return_value="publish unsupported"
        ), mock.patch.object(
            Path, "unlink", side_effect=PermissionError("cleanup denied")
        ):
            with self.assertRaises(Exception) as raised:
                write_render_job(job)

        self.assertIsInstance(raised.exception, RenderJobError)
        self.assertIn("publish unsupported", str(raised.exception))
        self.assertNotIn("cleanup denied", str(raised.exception))

    @unittest.skipUnless(os.name == "nt", "Windows publish contract")
    def test_successful_publish_needs_no_post_publish_cleanup(self):
        job = make_job(cache_dir=str(self.root), epoch=48)

        with mock.patch.object(
            Path, "unlink", side_effect=PermissionError("cleanup denied")
        ) as unlink:
            try:
                path = write_render_job(job)
            except Exception as exc:
                self.fail(
                    "successful publish attempted cleanup: "
                    f"{type(exc).__name__}: {exc}"
                )

        self.assertEqual(load_render_job(path, for_export=True), job)
        unlink.assert_not_called()
        self.assertFalse(list(self.root.glob(".*.publish")))

    def test_same_epoch_thread_race_has_exactly_one_winner(self):
        loop = make_job(cache_dir=str(self.root), epoch=77, track="loop")
        intro = replace(loop, track="intro")
        barrier = threading.Barrier(2)
        from core.vs_runtime import job as job_module

        real_atomic_write = job_module.atomic_write_json

        def synchronized_write(path, payload, *, indent=2):
            barrier.wait(timeout=10)
            real_atomic_write(path, payload, indent=indent)

        def contend(job):
            try:
                write_render_job(job)
            except RenderJobError:
                return "error", job.track
            return "ok", job.track

        with mock.patch.object(
            job_module, "atomic_write_json", side_effect=synchronized_write
        ):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(contend, (loop, intro)))

        self.assertEqual([state for state, _ in results].count("ok"), 1)
        winner = next(track for state, track in results if state == "ok")
        saved = load_render_job(self.root / "job-77.json", for_export=True)
        self.assertEqual(saved.track, winner)

    def test_same_epoch_process_race_has_exactly_one_winner(self):
        loop = make_job(cache_dir=str(self.root), epoch=88, track="loop")
        intro = replace(loop, track="intro")
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(2)
        results = context.Queue()
        processes = [
            context.Process(
                target=_process_write_job,
                args=(job.to_dict(), barrier, results),
            )
            for job in (loop, intro)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=15)
            self.assertFalse(process.is_alive(), "竞争进程未按时退出")
            self.assertEqual(process.exitcode, 0)

        outcomes = [results.get(timeout=5) for _ in processes]
        self.assertEqual([state for state, _ in outcomes].count("ok"), 1)
        winner = next(track for state, track in outcomes if state == "ok")
        saved = load_render_job(self.root / "job-88.json", for_export=True)
        self.assertEqual(saved.track, winner)

    def test_load_reports_absolute_path_for_invalid_json(self):
        path = self.root / "job-1.json"
        path.write_text("{", encoding="utf-8")

        with self.assertRaises(RenderJobError) as raised:
            load_render_job(path)

        self.assertIn(str(path), str(raised.exception))


if __name__ == "__main__":
    unittest.main()
