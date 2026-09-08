import builtins
import io
import hashlib
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from config.vs_runtime import (
    CoreConfig,
    PluginConfig,
    VSRuntimeConfig,
    WorkerConfig,
    load_vs_runtime,
)
from core.media_tools import MediaToolchain
from core.vs_runtime.script_header import parse_script_header
from core.vs_runtime.protocol import (
    MAX_MESSAGE_BYTES,
    MessageDecoder,
    ProtocolError,
    encode_message,
)
from core.vs_runtime.session import (
    GENERATION_STAGING_ROOT_ENV,
    GenerationStagingRoot,
    RenderSession,
    ScriptBundleSnapshot,
    ScriptSelection,
    compute_job_sha256,
    compute_script_bundle_hash,
)
from core.vs_runtime.vs_loader import (
    VSLoaderError,
    compute_runtime_fingerprint,
)
from core.vs_runtime.worker_process import (
    STAGING_CLEANUP_ERROR_CODE,
    SyncVSWorkerProcess,
    WorkerCrashedError,
    WorkerProcess,
    WorkerProcessError,
    WorkerRequestError,
)


ROOT = Path(__file__).resolve().parents[1]
TOOLCHAIN = MediaToolchain.discover(str(ROOT))


class ParentProcessIsolationTests(unittest.TestCase):
    """Worker loader tests share a Qt parent and must never load local VS."""

    def test_parent_does_not_import_vapoursynth_or_legacy_vs_engine(self):
        self.assertNotIn("vapoursynth", sys.modules)
        self.assertNotIn("core.vs_engine", sys.modules)


def _write_job(path: Path, *, epoch: int = 3, track: str = "loop") -> None:
    root = path.parent.resolve()
    payload = {
        "api_version": 1,
        "epoch": epoch,
        "track": track,
        "project_root": str(root),
        "source": {
            "path": str(root / "source.mp4"),
            "kind": "video",
            "virtual_frame_count": None,
        },
        "timeline": {
            "start_frame": 0,
            "end_frame": 3,
            "fps": {"numerator": 30000, "denominator": 1001},
        },
        "transform": {
            "rotation": 0,
            "crop": {
                "coordinate_space": "post_rotation_source_pixels",
                "x": 0,
                "y": 0,
                "width": 0,
                "height": 0,
            },
        },
        "output": {
            "profile": "360x640",
            "display_width": 360,
            "display_height": 640,
            "coded_width": 384,
            "coded_height": 640,
            "pixel_format": "YUV420P8",
            "matrix": "170m",
            "transfer": "170m",
            "primaries": "170m",
            "range": "limited",
            "final_rotate_180": False,
        },
        "paths": {"cache_dir": str(root / "cache")},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _valid_script(*, compatible: bool = True, extra: str = "") -> str:
    mode = "compatible" if compatible else "raw"
    editor_output = 1 if compatible else 0
    editor = (
        "editor = core.resize.Bicubic(base, format=vs.RGB24)\n"
        "editor.set_output(1)\n"
        if compatible
        else ""
    )
    return (
        "# assetmaker-api: 1\n"
        f"# assetmaker-mode: {mode}\n"
        "# assetmaker-capabilities: source\n"
        "# assetmaker-requires:\n"
        f"# assetmaker-editor-output: {editor_output}\n\n"
        "import vapoursynth as vs\n"
        "core = vs.core\n"
        "base = core.std.BlankClip(\n"
        "    width=384, height=640, length=3,\n"
        "    fpsnum=30000, fpsden=1001, format=vs.YUV420P8,\n"
        "    color=[81, 90, 240],\n"
        ")\n"
        "base = core.std.SetFrameProps(\n"
        "    base, _Matrix=6, _Transfer=6, _Primaries=6, _ColorRange=1\n"
        ")\n"
        + extra
        + "base.set_output(0)\n"
        + editor
    )


def _session(
    script: Path,
    job: Path,
    *,
    epoch: int = 3,
    runtime: VSRuntimeConfig | None = None,
) -> RenderSession:
    header = parse_script_header(script)
    runtime = load_vs_runtime() if runtime is None else runtime
    return RenderSession(
        epoch=epoch,
        track="loop",
        selection=ScriptSelection.from_header(
            script,
            header,
            compute_script_bundle_hash(script),
        ),
        job_path=str(job.resolve()),
        job_sha256=compute_job_sha256(job),
        runtime_fingerprint=compute_runtime_fingerprint(ROOT, runtime),
    )


def _metadata_wire(*, epoch: int = 7, mode: str = "raw") -> dict:
    return {
        "epoch": epoch,
        "mode": mode,
        "capabilities": ["source"],
        "output0": {
            "width": 384,
            "height": 640,
            "num_frames": 3,
            "fps_num": 30_000,
            "fps_den": 1_001,
            "pixel_format": "YUV420P8",
            "matrix": "170m",
            "transfer": "170m",
            "primaries": "170m",
            "range": "limited",
        },
        "editor": None,
    }


class VSRuntimeFingerprintTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.media = self.root / "tools" / "media"
        self.plugins = self.media / "vs-plugins"
        self.plugins.mkdir(parents=True)
        self.helper = (
            self.root
            / "resources"
            / "vapoursynth"
            / "python"
            / "assetmaker_vs"
        )
        self.helper.mkdir(parents=True)
        (self.helper / "contract.py").write_text(
            "VALUE = 1\n", encoding="utf-8"
        )
        for name, content in (
            ("vapoursynth.pyd", b"pyd-v1"),
            ("vapoursynth.dll", b"dll-v1"),
            ("portable.vs", b""),
        ):
            (self.media / name).write_bytes(content)

    def test_fingerprint_changes_for_runtime_and_code_but_ignores_non_code(self):
        configured = self.root / "configured-plugins"
        configured.mkdir()
        plugin = self.plugins / "source.dll"
        module = configured / "helper.py"
        non_code = configured / "lookup.png"
        plugin.write_bytes(b"plugin-v1")
        module.write_text("VALUE = 1\n", encoding="utf-8")
        non_code.write_bytes(b"image-v1")
        runtime = VSRuntimeConfig(
            plugins=PluginConfig(native_plugin_dirs=(str(configured),))
        )

        initial = compute_runtime_fingerprint(self.root, runtime)
        non_code.write_bytes(b"image-v2")
        self.assertEqual(compute_runtime_fingerprint(self.root, runtime), initial)

        plugin.write_bytes(b"plugin-v2")
        after_plugin = compute_runtime_fingerprint(self.root, runtime)
        self.assertNotEqual(after_plugin, initial)

        module.write_text("VALUE = 2\n", encoding="utf-8")
        after_module = compute_runtime_fingerprint(self.root, runtime)
        self.assertNotEqual(after_module, after_plugin)

        changed_runtime = VSRuntimeConfig(
            worker=WorkerConfig(frame_timeout_ms=12_345),
            plugins=runtime.plugins,
        )
        self.assertNotEqual(
            compute_runtime_fingerprint(self.root, changed_runtime), after_module
        )

    def test_fingerprint_is_deterministic_across_file_creation_order(self):
        first = self.plugins / "z.dll"
        second = self.plugins / "a.py"
        first.write_bytes(b"z")
        second.write_bytes(b"a")
        runtime = VSRuntimeConfig()

        before = compute_runtime_fingerprint(self.root, runtime)
        first_bytes = first.read_bytes()
        second_bytes = second.read_bytes()
        first.unlink()
        second.unlink()
        second.write_bytes(second_bytes)
        first.write_bytes(first_bytes)

        self.assertEqual(compute_runtime_fingerprint(self.root, runtime), before)

    def test_fingerprint_tracks_bundled_helper_code_but_not_helper_data(self):
        runtime = VSRuntimeConfig()
        data = self.helper / "lookup.png"
        data.write_bytes(b"one")
        before = compute_runtime_fingerprint(self.root, runtime)

        data.write_bytes(b"two")
        self.assertEqual(compute_runtime_fingerprint(self.root, runtime), before)

        (self.helper / "contract.py").write_text(
            "VALUE = 2\n", encoding="utf-8"
        )
        self.assertNotEqual(
            compute_runtime_fingerprint(self.root, runtime), before
        )

    def test_fingerprint_ignores_generated_pycache_but_tracks_released_pyc(self):
        """解释器缓存可再生，不是 runtime 发行契约；顶层发布字节码仍是。"""
        runtime = VSRuntimeConfig()
        before = compute_runtime_fingerprint(self.root, runtime)
        cache_dir = self.helper / "__pycache__"
        generated = cache_dir / "round2_generated.cpython-312.pyc"
        cache_dir_created = not cache_dir.exists()
        cache_dir.mkdir(exist_ok=True)
        try:
            generated.write_bytes(b"generated-by-interpreter-cache")
            self.assertEqual(compute_runtime_fingerprint(self.root, runtime), before)
        finally:
            generated.unlink(missing_ok=True)
            if cache_dir_created:
                cache_dir.rmdir()

        released_pyc = self.helper / "released_helper.pyc"
        try:
            released_pyc.write_bytes(b"released-bytecode-v1")
            self.assertNotEqual(compute_runtime_fingerprint(self.root, runtime), before)
        finally:
            released_pyc.unlink(missing_ok=True)

    def test_missing_portable_core_file_fails_loudly(self):
        (self.media / "portable.vs").unlink()

        with self.assertRaises(VSLoaderError) as raised:
            compute_runtime_fingerprint(self.root, VSRuntimeConfig())

        self.assertIn("portable.vs", str(raised.exception))

    def test_importing_loader_does_not_import_vapoursynth(self):
        self.assertNotIn("vapoursynth", sys.modules)
        self.assertNotIn("core.vs_engine", sys.modules)


class WorkerProcessTransportTests(unittest.TestCase):
    def _mark_transport_alive(self, process):
        fake = mock.Mock()
        fake.poll.return_value = None
        process._process = fake
        process._exit_event.clear()

    def test_gui_facing_send_only_enqueues_and_never_writes_pipe(self):
        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        self._mark_transport_alive(process)

        request_id = process.send_request({"type": "load", "value": "x" * 4096})

        generation, encoded = process._write_queue.get_nowait()
        self.assertEqual(generation, process.generation)
        self.assertGreater(len(encoded), 4096)
        self.assertIn(request_id, process._pending)
        self.assertFalse(process._process.stdin.write.called)

    def test_host_writer_completes_short_pipe_writes(self):
        class ShortStream:
            def __init__(self):
                self.data = bytearray()
                self.flush_count = 0

            def write(self, chunk):
                chunk = bytes(chunk)
                count = max(1, len(chunk) // 2)
                self.data.extend(chunk[:count])
                return count

            def flush(self):
                self.flush_count += 1

        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        fake = mock.Mock()
        fake.stdin = ShortStream()
        fake.poll.return_value = None
        process._process = fake
        process._generation = 3
        process._exit_event.clear()
        writes = queue.Queue()
        payload = encode_message({"type": "log", "message": "黍" * 200})
        writes.put((3, payload))
        writes.put(None)

        process._writer_loop(fake, 3, writes)

        self.assertEqual(bytes(fake.stdin.data), payload)
        self.assertEqual(fake.stdin.flush_count, 1)

    def test_stdout_reader_fails_transport_on_unexpected_processing_error(self):
        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        fake = mock.Mock()
        fake.stdout = io.BytesIO(
            encode_message({"type": "ready", "request_id": 1})
        )
        done = threading.Event()

        with (
            mock.patch.object(
                process, "_handle_message", side_effect=MemoryError("copy")
            ),
            mock.patch.object(process, "_fail_transport") as fail_transport,
        ):
            process._stdout_loop(fake, 3, done)

        self.assertTrue(done.is_set())
        fail_transport.assert_called_once()
        self.assertIn("MemoryError", fail_transport.call_args.args[1])

    def test_stderr_reader_bounds_a_line_without_newlines(self):
        class ChunkStream:
            def __init__(self):
                self.chunks = iter([b"x" * 65_536] * 5 + [b""])

            def read(self, _size):
                return next(self.chunks)

        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        fake = mock.Mock()
        fake.stderr = ChunkStream()
        done = threading.Event()

        process._stderr_loop(fake, 3, done, process._stderr_tail)

        self.assertTrue(done.is_set())
        self.assertGreater(len(process._stderr_tail), 1)
        self.assertLessEqual(
            max(len(item.encode("utf-8")) for item in process._stderr_tail),
            65_600,
        )

    def test_old_stderr_reader_cannot_write_into_current_generation_tail(self):
        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        old_tail = deque(maxlen=64)
        current_tail = deque(["new-generation"], maxlen=64)
        process._stderr_tail = current_tail
        fake = mock.Mock()
        fake.stderr = io.BytesIO(b"old-generation\n")
        done = threading.Event()

        try:
            process._stderr_loop(fake, 1, done, old_tail)
        except TypeError as error:
            self.fail(f"stderr reader 必须显式接收本代 deque: {error}")

        self.assertTrue(done.is_set())
        self.assertEqual(tuple(old_tail), ("old-generation",))
        self.assertEqual(tuple(current_tail), ("new-generation",))

    def test_default_app_dir_does_not_depend_on_process_cwd(self):
        from utils.file_utils import get_app_dir

        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                os.chdir(temp_dir)
                process = WorkerProcess()
            finally:
                os.chdir(previous)

        expected = Path(get_app_dir()).resolve()
        self.assertEqual(process.app_dir, expected)
        self.assertEqual(Path(process.command[-1]), expected / "vs_worker.py")

    def test_encode_failure_does_not_leave_a_pending_request(self):
        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        self._mark_transport_alive(process)

        with self.assertRaises(ProtocolError):
            process.send_request(
                {"type": "load", "value": "x" * (MAX_MESSAGE_BYTES + 1)}
            )

        self.assertEqual(process._pending, {})

    def test_popen_uses_one_binary_no_window_transport(self):
        fake = mock.Mock()
        fake.stdin = io.BytesIO()
        fake.stdout = io.BytesIO()
        fake.stderr = io.BytesIO()
        fake.pid = 321
        fake.wait.return_value = 0
        fake.poll.return_value = None
        command = [str(Path(sys.executable).resolve()), str(ROOT / "vs_worker.py")]
        process = WorkerProcess(command=command, self_test=True)

        with mock.patch(
            "core.vs_runtime.worker_process.subprocess.Popen", return_value=fake
        ) as popen:
            process.start()
            process.wait(timeout_ms=1_000)

        args, kwargs = popen.call_args
        self.assertEqual(args[0], [*command, "--self-test"])
        self.assertFalse(kwargs["shell"])
        self.assertIs(kwargs["stdin"], subprocess.PIPE)
        self.assertIs(kwargs["stdout"], subprocess.PIPE)
        self.assertIs(kwargs["stderr"], subprocess.PIPE)
        self.assertNotIn("text", kwargs)
        if sys.platform == "win32":
            self.assertEqual(
                kwargs["creationflags"], subprocess.CREATE_NO_WINDOW
            )

    def test_exit_listener_can_restart_without_old_waiter_poisoning_new_generation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            marker = Path(temp_dir) / "first-run"
            roots = Path(temp_dir) / "generation-roots.txt"
            child = (
                "import os,pathlib,sys,time\n"
                f"marker=pathlib.Path({str(marker)!r})\n"
                f"roots=pathlib.Path({str(roots)!r})\n"
                f"root=pathlib.Path(os.environ[{GENERATION_STAGING_ROOT_ENV!r}])\n"
                "with roots.open('a', encoding='utf-8') as stream:\n"
                "    stream.write(str(root) + '\\n')\n"
                "if marker.exists():\n"
                "    time.sleep(60)\n"
                "else:\n"
                "    marker.touch()\n"
                "    sys.exit(23)\n"
            )
            process = WorkerProcess(
                app_dir=ROOT,
                command=[str(Path(sys.executable).resolve()), "-B", "-c", child],
            )
            self.addCleanup(process.close)
            restarted = threading.Event()
            errors = []

            def restart_on_exit(event):
                if event["type"] != "worker_crashed" or event["generation"] != 1:
                    return
                try:
                    process.start()
                except BaseException as error:
                    errors.append(error)
                finally:
                    restarted.set()

            process.add_listener(restart_on_exit)
            process.start()

            self.assertTrue(restarted.wait(10), "旧 worker 未产生退出事件")
            self.assertEqual(errors, [])
            self.assertEqual(process.generation, 2)
            self.assertTrue(process.alive)
            deadline = time.monotonic() + 5
            while (
                (not roots.exists() or len(roots.read_text(encoding="utf-8").splitlines()) < 2)
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            self.assertTrue(roots.is_file())
            old_root, new_root = [
                Path(line)
                for line in roots.read_text(encoding="utf-8").splitlines()
            ]
            self.assertFalse(old_root.exists())
            self.assertTrue(new_root.is_dir())

    def test_spawn_failure_does_not_poison_next_start(self):
        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        self.addCleanup(
            lambda: process._generation_staging
            and process._generation_staging.close()
        )
        previous_process = mock.Mock()
        previous_process.poll.return_value = 23
        previous_exit = threading.Event()
        previous_exit.set()
        process._process = previous_process
        process._generation = 4
        process._exit_event = previous_exit

        replacement = mock.Mock()
        replacement.stdin = io.BytesIO()
        replacement.stdout = io.BytesIO()
        replacement.stderr = io.BytesIO()
        replacement.pid = 4321
        replacement.poll.return_value = None

        with (
            mock.patch(
                "core.vs_runtime.worker_process.subprocess.Popen",
                side_effect=[OSError("spawn failed"), replacement],
            ) as popen,
            mock.patch("threading.Thread.start"),
        ):
            with self.assertRaises(WorkerProcessError):
                process.start()

            self.assertEqual(process.generation, 4)
            self.assertIs(process._process, previous_process)
            self.assertIs(process._exit_event, previous_exit)
            self.assertEqual(process.start(), 5)

        failed_env = popen.call_args_list[0].kwargs["env"]
        self.assertIsInstance(failed_env, dict)
        self.assertIn(GENERATION_STAGING_ROOT_ENV, failed_env)
        self.assertFalse(
            Path(failed_env[GENERATION_STAGING_ROOT_ENV]).exists()
        )
        self.assertTrue(process.alive)

    def test_marker_write_failure_is_wrapped_before_popen_and_root_is_cleaned(self):
        class MarkerWriteError(OSError):
            pass

        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        events = []
        process.add_listener(events.append)
        roots = []
        real_mkdtemp = tempfile.mkdtemp

        def capture_mkdtemp(*args, **kwargs):
            root = real_mkdtemp(*args, **kwargs)
            roots.append(Path(root).resolve())
            return root

        with (
            mock.patch(
                "core.vs_runtime.session.tempfile.mkdtemp",
                side_effect=capture_mkdtemp,
            ),
            mock.patch(
                "core.vs_runtime.session.Path.write_text",
                autospec=True,
                side_effect=MarkerWriteError("marker write denied"),
            ),
            mock.patch(
                "core.vs_runtime.worker_process.subprocess.Popen"
            ) as popen,
        ):
            with self.assertRaises(WorkerProcessError) as raised:
                process.start()

        self.assertEqual(len(roots), 1)
        self.assertFalse(roots[0].exists())
        self.assertIn("worker.staging_identity_failed", str(raised.exception))
        self.assertIn("marker write denied", str(raised.exception))
        self.assertEqual(process.generation, 0)
        self.assertIsNone(process._process)
        self.assertIsNone(process._generation_staging)
        self.assertEqual(events, [])
        popen.assert_not_called()

    def test_marker_and_cleanup_failure_retains_owner_with_safe_diagnostic(self):
        class HostileMarkerError(OSError):
            def __str__(self):
                raise ValueError("broken marker error string")

        class HostileCleanupError(PermissionError):
            def __str__(self):
                raise ValueError("broken cleanup error string")

        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        events = []
        process.add_listener(events.append)
        roots = []
        real_mkdtemp = tempfile.mkdtemp
        real_remove = ScriptBundleSnapshot._remove_tree

        def capture_mkdtemp(*args, **kwargs):
            root = real_mkdtemp(*args, **kwargs)
            roots.append(Path(root).resolve())
            return root

        try:
            with (
                mock.patch(
                    "core.vs_runtime.session.tempfile.mkdtemp",
                    side_effect=capture_mkdtemp,
                ),
                mock.patch(
                    "core.vs_runtime.session.Path.write_text",
                    autospec=True,
                    side_effect=HostileMarkerError(),
                ),
                mock.patch.object(
                    ScriptBundleSnapshot,
                    "_remove_tree",
                    side_effect=HostileCleanupError(),
                ),
                mock.patch(
                    "core.vs_runtime.worker_process.subprocess.Popen"
                ) as popen,
            ):
                with self.assertRaises(WorkerProcessError) as raised:
                    process.start()

            message = str(raised.exception)
            self.assertIn("worker.staging_identity_failed", message)
            self.assertIn("HostileMarkerError", message)
            self.assertIn("worker.staging_cleanup_failed", message)
            self.assertIn("HostileCleanupError", message)
            self.assertEqual(len(roots), 1)
            self.assertTrue(roots[0].is_dir())
            self.assertIsNotNone(process._generation_staging)
            self.assertEqual(
                process._generation_staging.root_path.resolve(), roots[0]
            )
            self.assertEqual(process.generation, 0)
            self.assertIsNone(process._process)
            self.assertEqual(events, [])
            popen.assert_not_called()
        finally:
            for root in roots:
                if root.exists():
                    real_remove(root)

    def test_next_start_cleans_retained_owner_before_allocating_fresh_root(self):
        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        roots = []
        real_mkdtemp = tempfile.mkdtemp
        real_write_text = Path.write_text
        real_remove = ScriptBundleSnapshot._remove_tree
        write_calls = 0
        remove_calls = 0

        def capture_mkdtemp(*args, **kwargs):
            if roots:
                self.assertFalse(
                    roots[0].exists(),
                    "必须先清理 retained owner，才能创建新 generation root",
                )
            root = real_mkdtemp(*args, **kwargs)
            roots.append(Path(root).resolve())
            return root

        def fail_first_marker(path, data, *args, **kwargs):
            nonlocal write_calls
            write_calls += 1
            if write_calls == 1:
                raise OSError("first marker failed")
            return real_write_text(path, data, *args, **kwargs)

        def fail_first_remove(root):
            nonlocal remove_calls
            remove_calls += 1
            if remove_calls == 1:
                raise PermissionError("first cleanup failed")
            return real_remove(root)

        try:
            with (
                mock.patch(
                    "core.vs_runtime.session.tempfile.mkdtemp",
                    side_effect=capture_mkdtemp,
                ),
                mock.patch(
                    "core.vs_runtime.session.Path.write_text",
                    autospec=True,
                    side_effect=fail_first_marker,
                ),
                mock.patch.object(
                    ScriptBundleSnapshot,
                    "_remove_tree",
                    side_effect=fail_first_remove,
                ),
                mock.patch(
                    "core.vs_runtime.worker_process.subprocess.Popen",
                    side_effect=OSError("fresh spawn reached"),
                ) as popen,
            ):
                with self.assertRaises(WorkerProcessError) as first:
                    process.start()
                with self.assertRaisesRegex(
                    WorkerProcessError, "fresh spawn reached"
                ):
                    process.start()

            self.assertIn("worker.staging_cleanup_failed", str(first.exception))
            self.assertEqual(len(roots), 2)
            self.assertFalse(roots[0].exists())
            self.assertFalse(roots[1].exists())
            self.assertIsNone(process._generation_staging)
            self.assertEqual(process.generation, 0)
            self.assertEqual(popen.call_count, 1)
        finally:
            for root in roots:
                if root.exists():
                    real_remove(root)

    def test_next_start_keeps_retained_owner_when_cleanup_still_fails(self):
        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        roots = []
        real_mkdtemp = tempfile.mkdtemp
        real_remove = ScriptBundleSnapshot._remove_tree

        def capture_mkdtemp(*args, **kwargs):
            root = real_mkdtemp(*args, **kwargs)
            roots.append(Path(root).resolve())
            return root

        try:
            with (
                mock.patch(
                    "core.vs_runtime.session.tempfile.mkdtemp",
                    side_effect=capture_mkdtemp,
                ),
                mock.patch(
                    "core.vs_runtime.session.Path.write_text",
                    autospec=True,
                    side_effect=OSError("marker failed"),
                ),
                mock.patch.object(
                    ScriptBundleSnapshot,
                    "_remove_tree",
                    side_effect=PermissionError("cleanup still denied"),
                ),
                mock.patch(
                    "core.vs_runtime.worker_process.subprocess.Popen"
                ) as popen,
            ):
                with self.assertRaises(WorkerProcessError):
                    process.start()
                retained = process._generation_staging
                with self.assertRaises(WorkerProcessError) as second:
                    process.start()

            self.assertIn(
                "worker.staging_cleanup_failed", str(second.exception)
            )
            self.assertEqual(len(roots), 1)
            self.assertIs(process._generation_staging, retained)
            self.assertTrue(roots[0].is_dir())
            self.assertEqual(process.generation, 0)
            popen.assert_not_called()
        finally:
            for root in roots:
                if root.exists():
                    real_remove(root)

    def test_retained_cleanup_result_is_not_inferred_from_error_text(self):
        class HostileCleanupError(PermissionError):
            def __str__(self):
                raise ValueError("broken cleanup error string")

        for error_type in (PermissionError, HostileCleanupError):
            with self.subTest(error_type=error_type.__name__):
                process = WorkerProcess(
                    command=[
                        str(Path(sys.executable).resolve()),
                        "-B",
                        "-c",
                        "pass",
                    ]
                )
                process._generation = 4
                staging = mock.Mock()
                staging.close.side_effect = [
                    error_type(),
                    error_type(),
                    None,
                ]
                process._generation_staging = staging
                events = []
                process.add_listener(events.append)

                with (
                    mock.patch.object(
                        GenerationStagingRoot,
                        "create",
                        side_effect=AssertionError("allocated a fresh root"),
                    ) as create_root,
                    mock.patch(
                        "core.vs_runtime.worker_process.subprocess.Popen"
                    ) as popen,
                ):
                    for _attempt in range(2):
                        with self.assertRaises(WorkerProcessError) as raised:
                            process.start()
                        self.assertIs(process._generation_staging, staging)
                        self.assertEqual(
                            getattr(raised.exception, "code", None),
                            STAGING_CLEANUP_ERROR_CODE,
                        )
                        self.assertIn(
                            STAGING_CLEANUP_ERROR_CODE,
                            str(raised.exception),
                        )
                        self.assertIn(
                            error_type.__name__, str(raised.exception)
                        )

                    create_root.assert_not_called()
                    popen.assert_not_called()
                    self.assertEqual(process.generation, 4)

                process.close()

                self.assertEqual(staging.close.call_count, 3)
                self.assertIsNone(process._generation_staging)
                self.assertEqual(events, [])

    def test_close_retries_retained_owner_without_async_terminal(self):
        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        events = []
        process.add_listener(events.append)
        roots = []
        real_mkdtemp = tempfile.mkdtemp
        real_remove = ScriptBundleSnapshot._remove_tree
        remove_calls = 0

        def capture_mkdtemp(*args, **kwargs):
            root = real_mkdtemp(*args, **kwargs)
            roots.append(Path(root).resolve())
            return root

        def fail_first_remove(root):
            nonlocal remove_calls
            remove_calls += 1
            if remove_calls == 1:
                raise PermissionError("initial cleanup denied")
            return real_remove(root)

        try:
            with (
                mock.patch(
                    "core.vs_runtime.session.tempfile.mkdtemp",
                    side_effect=capture_mkdtemp,
                ),
                mock.patch(
                    "core.vs_runtime.session.Path.write_text",
                    autospec=True,
                    side_effect=OSError("marker failed"),
                ),
                mock.patch.object(
                    ScriptBundleSnapshot,
                    "_remove_tree",
                    side_effect=fail_first_remove,
                ),
                mock.patch(
                    "core.vs_runtime.worker_process.subprocess.Popen"
                ) as popen,
            ):
                with self.assertRaises(WorkerProcessError):
                    process.start()
                self.assertTrue(roots[0].is_dir())
                process.close()

            self.assertFalse(roots[0].exists())
            self.assertIsNone(process._generation_staging)
            self.assertEqual(process.generation, 0)
            self.assertEqual(events, [])
            popen.assert_not_called()
        finally:
            for root in roots:
                if root.exists():
                    real_remove(root)

    def test_staging_identity_failure_before_popen_is_wrapped_and_cleaned(self):
        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        created = []
        real_create = GenerationStagingRoot.create

        def capture_create():
            staging = real_create()
            created.append(staging)
            return staging

        raised = None
        with (
            mock.patch.object(
                GenerationStagingRoot,
                "create",
                side_effect=capture_create,
            ),
            mock.patch.object(
                GenerationStagingRoot,
                "to_environment",
                autospec=True,
                side_effect=OSError("ownership marker unreadable"),
            ),
            mock.patch(
                "core.vs_runtime.worker_process.subprocess.Popen"
            ) as popen,
        ):
            try:
                process.start()
            except BaseException as error:
                raised = error
            finally:
                for staging in created:
                    if staging.root_path.exists():
                        staging.close()

        self.assertIsInstance(raised, WorkerProcessError)
        self.assertIn("worker 启动准备失败", str(raised))
        self.assertIn("ownership marker unreadable", str(raised))
        self.assertEqual(len(created), 1)
        self.assertFalse(created[0].root_path.exists())
        popen.assert_not_called()
        self.assertEqual(process.generation, 0)

    def test_spawn_failure_wraps_generation_cleanup_failure(self):
        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        original_close = GenerationStagingRoot.close
        environment = None
        raised = None
        with (
            mock.patch(
                "core.vs_runtime.worker_process.subprocess.Popen",
                side_effect=OSError("spawn unavailable"),
            ) as popen,
            mock.patch.object(
                GenerationStagingRoot,
                "close",
                autospec=True,
                side_effect=PermissionError("generation root is locked"),
            ),
        ):
            try:
                process.start()
            except BaseException as error:
                raised = error
            finally:
                environment = popen.call_args.kwargs["env"]

        root = Path(environment[GENERATION_STAGING_ROOT_ENV])
        try:
            self.assertIsInstance(raised, WorkerProcessError)
            self.assertIn("spawn unavailable", str(raised))
            self.assertIn("worker.staging_cleanup_failed", str(raised))
            self.assertEqual(process.generation, 0)
            self.assertIsNone(process._process)
        finally:
            if root.exists():
                original_close(
                    GenerationStagingRoot.from_environment(environment)
                )

    def test_missing_pipe_wraps_generation_cleanup_failure_after_reaping(self):
        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        child = mock.Mock()
        child.stdin = None
        child.stdout = io.BytesIO()
        child.stderr = io.BytesIO()
        child.poll.return_value = None

        def stop_child():
            child.poll.return_value = 1

        child.terminate.side_effect = stop_child
        child.kill.side_effect = stop_child
        child.wait.return_value = 1
        original_close = GenerationStagingRoot.close
        environment = None
        raised = None
        with (
            mock.patch(
                "core.vs_runtime.worker_process.subprocess.Popen",
                return_value=child,
            ) as popen,
            mock.patch.object(
                GenerationStagingRoot,
                "close",
                autospec=True,
                side_effect=PermissionError("generation root is locked"),
            ),
        ):
            try:
                process.start()
            except BaseException as error:
                raised = error
            finally:
                environment = popen.call_args.kwargs["env"]

        root = Path(environment[GENERATION_STAGING_ROOT_ENV])
        try:
            self.assertIsInstance(raised, WorkerProcessError)
            self.assertIn("PIPE 创建失败", str(raised))
            self.assertIn("worker.staging_cleanup_failed", str(raised))
            self.assertTrue(child.wait.called)
            self.assertGreaterEqual(
                child.terminate.call_count + child.kill.call_count, 1
            )
            self.assertEqual(process.generation, 0)
            self.assertIsNone(process._process)
        finally:
            if root.exists():
                original_close(
                    GenerationStagingRoot.from_environment(environment)
                )

    def test_thread_start_failure_reaps_child_and_allows_fresh_generation(self):
        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        self.addCleanup(
            lambda: process._generation_staging
            and process._generation_staging.close()
        )

        def fake_process(pid):
            child = mock.Mock()
            child.stdin = io.BytesIO()
            child.stdout = io.BytesIO()
            child.stderr = io.BytesIO()
            child.pid = pid
            child.poll.return_value = None

            def terminate():
                child.poll.return_value = 1

            child.terminate.side_effect = terminate
            child.wait.return_value = 1
            return child

        failed_child = fake_process(1001)
        replacement = fake_process(1002)
        starts = [None, RuntimeError("thread unavailable"), None, None, None, None]

        with (
            mock.patch(
                "core.vs_runtime.worker_process.subprocess.Popen",
                side_effect=[failed_child, replacement],
            ) as popen,
            mock.patch("threading.Thread.start", side_effect=starts),
        ):
            with self.assertRaises(WorkerProcessError):
                process.start()

            self.assertFalse(process.alive)
            self.assertTrue(process._exit_event.is_set())
            failed_child.terminate.assert_called_once()
            self.assertEqual(process.start(), 2)

        failed_env = popen.call_args_list[0].kwargs["env"]
        self.assertIsInstance(failed_env, dict)
        self.assertIn(GENERATION_STAGING_ROOT_ENV, failed_env)
        self.assertFalse(
            Path(failed_env[GENERATION_STAGING_ROOT_ENV]).exists()
        )
        self.assertTrue(process.alive)

    def test_thread_construction_failure_reaps_unpublished_child_and_root(self):
        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        child = mock.Mock()
        child.stdin = io.BytesIO()
        child.stdout = io.BytesIO()
        child.stderr = io.BytesIO()
        child.pid = 1003
        child.poll.return_value = None

        def terminate():
            child.poll.return_value = 1

        child.terminate.side_effect = terminate
        child.wait.return_value = 1
        environment = None
        with (
            mock.patch(
                "core.vs_runtime.worker_process.subprocess.Popen",
                return_value=child,
            ) as popen,
            mock.patch(
                "core.vs_runtime.worker_process.threading.Thread",
                side_effect=RuntimeError("thread constructor unavailable"),
            ),
        ):
            try:
                with self.assertRaises(WorkerProcessError) as raised:
                    process.start()
            finally:
                environment = popen.call_args.kwargs["env"]
                root = Path(environment[GENERATION_STAGING_ROOT_ENV])
                if root.exists():
                    GenerationStagingRoot.from_environment(environment).close()

        self.assertIn("worker 协议线程构造失败", str(raised.exception))
        child.terminate.assert_called_once()
        self.assertFalse(Path(environment[GENERATION_STAGING_ROOT_ENV]).exists())
        self.assertEqual(process.generation, 0)
        self.assertIsNone(process._process)
        self.assertFalse(process.settling)

    def test_thread_start_cleanup_failure_is_terminal_and_does_not_poison_restart(self):
        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        events = []
        process.add_listener(events.append)

        child = mock.Mock()
        child.stdin = io.BytesIO()
        child.stdout = io.BytesIO()
        child.stderr = io.BytesIO()
        child.pid = 1004
        child.poll.return_value = None

        def terminate():
            child.poll.return_value = 1

        child.terminate.side_effect = terminate
        child.wait.return_value = 1
        original_close = GenerationStagingRoot.close
        close_calls = 0

        def fail_first_close(staging):
            nonlocal close_calls
            close_calls += 1
            if close_calls == 1:
                raise PermissionError("generation root is locked")
            return original_close(staging)

        environment = None
        with (
            mock.patch(
                "core.vs_runtime.worker_process.subprocess.Popen",
                side_effect=[child, OSError("fresh spawn reached")],
            ) as popen,
            mock.patch(
                "core.vs_runtime.worker_process.threading.Thread.start",
                side_effect=[None, RuntimeError("thread start unavailable")],
            ),
            mock.patch.object(
                GenerationStagingRoot,
                "close",
                autospec=True,
                side_effect=fail_first_close,
            ),
        ):
            try:
                with self.assertRaises(WorkerProcessError) as first:
                    process.start()

                self.assertTrue(process._exit_event.is_set())
                self.assertFalse(process.settling)
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["type"], "worker_crashed")
                self.assertEqual(
                    events[0]["code"], "worker.staging_cleanup_failed"
                )
                self.assertIn(
                    "worker.staging_cleanup_failed", str(first.exception)
                )

                with self.assertRaisesRegex(
                    WorkerProcessError, "fresh spawn reached"
                ):
                    process.start()
            finally:
                environment = popen.call_args_list[0].kwargs["env"]
                root = Path(environment[GENERATION_STAGING_ROOT_ENV])
                if root.exists():
                    original_close(
                        GenerationStagingRoot.from_environment(environment)
                    )

        self.assertEqual(process.generation, 1)
        self.assertEqual(len(events), 1)
        self.assertFalse(Path(environment[GENERATION_STAGING_ROOT_ENV]).exists())

    def test_thread_start_hostile_error_is_wrapped_and_terminal(self):
        class HostileStartError(RuntimeError):
            def __str__(self):
                raise ValueError("broken thread start error string")

        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        events = []
        process.add_listener(events.append)

        child = mock.Mock()
        child.stdin = io.BytesIO()
        child.stdout = io.BytesIO()
        child.stderr = io.BytesIO()
        child.pid = 1005
        child.poll.return_value = None

        def terminate():
            child.poll.return_value = 1

        child.terminate.side_effect = terminate
        child.wait.return_value = 1
        environment = None
        with (
            mock.patch(
                "core.vs_runtime.worker_process.subprocess.Popen",
                return_value=child,
            ) as popen,
            mock.patch(
                "core.vs_runtime.worker_process.threading.Thread.start",
                side_effect=HostileStartError(),
            ),
        ):
            try:
                with self.assertRaises(WorkerProcessError) as raised:
                    process.start()
            finally:
                environment = popen.call_args.kwargs["env"]
                root = Path(environment[GENERATION_STAGING_ROOT_ENV])
                if root.exists():
                    GenerationStagingRoot.from_environment(environment).close()

        self.assertIn("HostileStartError", str(raised.exception))
        child.terminate.assert_called_once()
        self.assertTrue(process._exit_event.is_set())
        self.assertFalse(process.settling)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "worker_crashed")
        self.assertEqual(events[0]["code"], "worker.start_failed")
        self.assertFalse(Path(environment[GENERATION_STAGING_ROOT_ENV]).exists())

    def test_close_cleanup_failure_is_a_single_non_throwing_terminal(self):
        class CapturedEvent:
            def __init__(self):
                self.value = False

            def is_set(self):
                return self.value

            def set(self):
                self.value = True

            def wait(self, _timeout):
                return self.value

        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        child = mock.Mock()
        child.poll.return_value = None
        child.stdin = io.BytesIO()
        child.stdout = io.BytesIO()
        child.stderr = io.BytesIO()
        exit_event = CapturedEvent()
        staging = mock.Mock()
        staging.close.side_effect = PermissionError("generation root is locked")
        process._generation = 1
        process._process = child
        process._exit_event = exit_event
        process._generation_staging = staging
        events = []
        process.add_listener(events.append)

        process.close()
        process.close()

        self.assertTrue(exit_event.is_set())
        self.assertFalse(process.settling)
        self.assertTrue(child.wait.called)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "worker_crashed")
        self.assertEqual(
            events[0]["code"], "worker.staging_cleanup_failed"
        )
        self.assertIn("generation root is locked", events[0]["message"])

    def test_expected_exit_cleanup_failure_is_a_stable_crash_terminal(self):
        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        child = mock.Mock()
        child.wait.return_value = 0
        child.poll.return_value = 0
        child.stdin = io.BytesIO()
        child.stdout = io.BytesIO()
        child.stderr = io.BytesIO()
        stdout_done = threading.Event()
        stderr_done = threading.Event()
        stdout_done.set()
        stderr_done.set()
        write_queue = queue.Queue()
        exit_event = threading.Event()
        staging = mock.Mock()
        staging.close.side_effect = PermissionError("generation root is locked")
        process._generation = 1
        process._process = child
        process._exit_event = exit_event
        process._generation_staging = staging
        process._expected_exit = True
        events = []
        process.add_listener(events.append)

        process._waiter_loop(
            child,
            1,
            stdout_done,
            stderr_done,
            write_queue,
            exit_event,
            deque(),
            staging,
        )

        self.assertTrue(exit_event.is_set())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "worker_crashed")
        self.assertEqual(
            events[0]["code"], "worker.staging_cleanup_failed"
        )
        self.assertEqual(events[0]["exit_code"], 0)
        self.assertIn("generation root is locked", events[0]["message"])

    def test_missing_binary_pipe_removes_unpublished_generation_root(self):
        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        child = mock.Mock()
        child.stdin = None
        child.stdout = io.BytesIO()
        child.stderr = io.BytesIO()
        child.poll.return_value = None
        child.wait.return_value = 1

        with (
            mock.patch(
                "core.vs_runtime.worker_process.subprocess.Popen",
                return_value=child,
            ) as popen,
            self.assertRaises(WorkerProcessError),
        ):
            process.start()

        environment = popen.call_args.kwargs["env"]
        self.assertIsInstance(environment, dict)
        self.assertIn(GENERATION_STAGING_ROOT_ENV, environment)
        self.assertFalse(Path(environment[GENERATION_STAGING_ROOT_ENV]).exists())

    def test_child_helper_import_failure_removes_generation_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root_record = Path(temp_dir) / "helper-failure-root.txt"
            child = (
                "import os\n"
                "from pathlib import Path\n"
                f"root=Path(os.environ[{GENERATION_STAGING_ROOT_ENV!r}])\n"
                "snapshot=root/'snapshot-helper-failure'\n"
                "(snapshot/'bundle').mkdir(parents=True)\n"
                "(snapshot/'bundle'/'pipeline.vpy').write_text('# code')\n"
                "(snapshot/'job.json').write_text('{}')\n"
                f"Path({str(root_record)!r}).write_text(str(root), encoding='utf-8')\n"
                "raise ImportError('helper import failed')\n"
            )
            process = WorkerProcess(
                app_dir=ROOT,
                command=[str(Path(sys.executable).resolve()), "-B", "-c", child],
            )
            self.addCleanup(process.close)

            process.start()
            code = process.wait(timeout_ms=10_000)

            self.assertNotEqual(code, 0)
            self.assertTrue(root_record.is_file())
            generation_root = Path(root_record.read_text(encoding="utf-8"))
            self.assertFalse(generation_root.exists())

    def test_close_removes_live_child_generation_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root_record = Path(temp_dir) / "close-root.txt"
            child = (
                "import os,time\n"
                "from pathlib import Path\n"
                f"root=Path(os.environ[{GENERATION_STAGING_ROOT_ENV!r}])\n"
                "(root/'snapshot-live'/'bundle').mkdir(parents=True)\n"
                f"Path({str(root_record)!r}).write_text(str(root), encoding='utf-8')\n"
                "time.sleep(60)\n"
            )
            process = WorkerProcess(
                app_dir=ROOT,
                command=[str(Path(sys.executable).resolve()), "-B", "-c", child],
            )
            process.start()
            deadline = time.monotonic() + 5
            while not root_record.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(root_record.is_file())
            generation_root = Path(root_record.read_text(encoding="utf-8"))

            process.close()

            self.assertFalse(generation_root.exists())

    def test_close_prevents_exit_listener_from_restarting_transport(self):
        process = WorkerProcess(
            app_dir=ROOT,
            command=[
                str(Path(sys.executable).resolve()),
                "-B",
                "-c",
                "import time; time.sleep(60)",
            ],
        )
        self.addCleanup(process.close)
        listener_done = threading.Event()
        errors = []

        def restart_on_exit(event):
            if event["generation"] != 1 or event["type"] not in {
                "worker_crashed",
                "worker_exited",
            }:
                return
            try:
                process.start()
            except BaseException as error:
                errors.append(error)
            finally:
                listener_done.set()

        process.add_listener(restart_on_exit)
        process.start()
        process.close()

        self.assertTrue(listener_done.wait(5))
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], WorkerProcessError)
        self.assertEqual(process.generation, 1)
        self.assertFalse(process.alive)

    def test_source_self_test_uses_real_framed_unicode_pipe_and_exits_zero(self):
        client = SyncVSWorkerProcess(app_dir=ROOT, self_test=True)
        self.addCleanup(client.close)

        ready = client.start(timeout_ms=10_000)

        self.assertEqual(ready["type"], "ready")
        self.assertEqual(ready["api_version"], 1)
        self.assertTrue(client.logs)
        self.assertIn("中文自测", client.logs[0][1])
        self.assertGreater(len(client.logs[0][1]), 65_536)

        response = client.shutdown(timeout_ms=10_000)
        self.assertEqual(response["operation"], "shutdown")
        self.assertEqual(client.wait(timeout_ms=10_000), 0)
        self.assertNotIn("vapoursynth", sys.modules)
        self.assertNotIn("core.vs_engine", sys.modules)

    def test_response_schema_rejects_unknown_fields(self):
        child = (
            "import sys,time; "
            "from core.vs_runtime.protocol import encode_message; "
            "sys.stdout.buffer.write(encode_message({"
            "'type':'ready','request_id':1,'api_version':1,"
            "'operation':'hello','unexpected':True})); "
            "sys.stdout.buffer.flush(); time.sleep(2)"
        )
        client = SyncVSWorkerProcess(
            app_dir=ROOT,
            command=[str(Path(sys.executable).resolve()), "-B", "-c", child],
        )
        self.addCleanup(client.close)

        with self.assertRaises(WorkerCrashedError) as raised:
            client.start(timeout_ms=5_000)

        self.assertIn("协议", str(raised.exception))

    def test_non_load_requests_reject_load_specific_error_terminals(self):
        cases = (
            ({"type": "hello", "api_version": 1}, "script_error"),
            (
                {
                    "type": "request_plane_digest",
                    "epoch": 7,
                    "index": 0,
                    "surface": "final",
                },
                "contract_error",
            ),
        )
        for request, response_type in cases:
            with self.subTest(request=request["type"], response=response_type):
                process = WorkerProcess(
                    command=[
                        str(Path(sys.executable).resolve()),
                        "-B",
                        "-c",
                        "pass",
                    ]
                )
                self._mark_transport_alive(process)
                request_id = process.send_request(request)

                with self.assertRaisesRegex(ProtocolError, "非法响应"):
                    process._handle_message(
                        {"type": response_type, "request_id": request_id},
                        process.generation,
                    )

                self.assertIn(request_id, process._pending)

    def test_metadata_nested_identity_must_match_load_request(self):
        for field in ("epoch", "mode"):
            with self.subTest(field=field):
                process = WorkerProcess(
                    command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
                )
                self._mark_transport_alive(process)
                request_id = process.send_request(
                    {"type": "load", "epoch": 7, "mode": "raw"}
                )
                wire = _metadata_wire(epoch=7, mode="raw")
                wire[field] = 8 if field == "epoch" else "compatible"

                with self.assertRaisesRegex(ProtocolError, "metadata"):
                    process._handle_message(
                        {
                            "type": "metadata",
                            "request_id": request_id,
                            "epoch": 7,
                            "metadata": wire,
                        },
                        process.generation,
                    )

    def test_send_request_cannot_cross_worker_generation_during_encode(self):
        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        self._mark_transport_alive(process)
        process._generation = 1
        encoding = threading.Event()
        release = threading.Event()
        result = []

        def blocked_encode(message):
            encoding.set()
            release.wait(5)
            return encode_message(message)

        def send():
            try:
                result.append(process.send_request({"type": "unload"}))
            except BaseException as error:
                result.append(error)

        with mock.patch(
            "core.vs_runtime.worker_process.encode_message",
            side_effect=blocked_encode,
        ):
            thread = threading.Thread(target=send)
            thread.start()
            self.assertTrue(encoding.wait(2))
            with process._state_lock:
                process._generation = 2
                process._write_queue = queue.Queue()
                process._exit_event = threading.Event()
                process._process = mock.Mock()
                process._process.poll.return_value = None
            release.set()
            thread.join(5)

        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], WorkerProcessError)
        self.assertEqual(process._pending, {})
        self.assertTrue(process._write_queue.empty())

    def test_frame_reservation_cannot_cross_worker_generation(self):
        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        self._mark_transport_alive(process)
        process._generation = 1
        creating = threading.Event()
        release = threading.Event()
        closed = threading.Event()
        slot = mock.Mock()
        slot.descriptor.to_wire.return_value = {
            "name": "test-slot",
            "generation": 1,
            "capacity": 12,
        }
        slot.close.side_effect = closed.set
        result = []

        def blocked_create(*, capacity, generation):
            del capacity, generation
            creating.set()
            release.wait(5)
            return slot

        def request():
            try:
                result.append(
                    process.request_frame(
                        epoch=7,
                        index=0,
                        surface="final",
                        viewport=(2, 2),
                        zoom_factor=1.0,
                        pan=(0.5, 0.5),
                    )
                )
            except BaseException as error:
                result.append(error)

        with mock.patch(
            "core.vs_runtime.worker_process.FrameSlot.create",
            side_effect=blocked_create,
        ):
            thread = threading.Thread(target=request)
            thread.start()
            self.assertTrue(creating.wait(2))
            with process._state_lock:
                process._generation = 2
                process._frame_reservations = 0
                process._write_queue = queue.Queue()
                process._exit_event = threading.Event()
                process._process = mock.Mock()
                process._process.poll.return_value = None
            release.set()
            thread.join(5)

        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], WorkerProcessError)
        self.assertTrue(closed.is_set())
        self.assertEqual(process._frame_reservations, 0)
        self.assertEqual(process._pending, {})
        self.assertTrue(process._write_queue.empty())

    def test_invalid_frame_identity_is_rejected_before_slot_allocation(self):
        invalid = (
            {"epoch": True},
            {"epoch": 0},
            {"index": True},
            {"index": -1},
            {"surface": "unknown"},
        )
        for override in invalid:
            with self.subTest(override=override):
                process = WorkerProcess(
                    command=[
                        str(Path(sys.executable).resolve()),
                        "-B",
                        "-c",
                        "pass",
                    ]
                )
                self._mark_transport_alive(process)
                fields = {
                    "epoch": 7,
                    "index": 0,
                    "surface": "final",
                    "viewport": (2, 2),
                    "zoom_factor": 1.0,
                    "pan": (0.5, 0.5),
                }
                fields.update(override)

                with (
                    mock.patch(
                        "core.vs_runtime.worker_process.FrameSlot.create"
                    ) as create_slot,
                    self.assertRaises(ValueError),
                ):
                    process.request_frame(**fields)

                create_slot.assert_not_called()
                self.assertEqual(process._pending, {})
                self.assertEqual(process._frame_reservations, 0)
                self.assertTrue(process._write_queue.empty())
                self.assertTrue(process.alive)

    def test_coalesced_auto_frame_publishes_submission_identity(self):
        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        self._mark_transport_alive(process)
        process.MAX_INFLIGHT_FRAMES = 1
        slots = []
        for generation, name in ((1, "slot-one"), (2, "slot-two")):
            slot = mock.Mock()
            slot.descriptor = SimpleNamespace(
                name=name,
                generation=generation,
                capacity=12,
                to_wire=lambda n=name, g=generation: {
                    "name": n,
                    "generation": g,
                    "capacity": 12,
                },
            )
            slot.read_bgr.return_value = object()
            slots.append(slot)
        events = []
        process.add_listener(events.append)

        with mock.patch(
            "core.vs_runtime.worker_process.FrameSlot.create",
            side_effect=slots,
        ):
            first = process.request_frame(
                epoch=7,
                index=0,
                surface="final",
                viewport=(2, 2),
                zoom_factor=1.0,
                pan=(0.5, 0.5),
            )
            self.assertIsNone(
                process.request_frame(
                    epoch=7,
                    index=1,
                    surface="final",
                    viewport=(2, 2),
                    zoom_factor=1.0,
                    pan=(0.5, 0.5),
                    coalesce=True,
                )
            )
            process._handle_message(
                {
                    "type": "frame_ready",
                    "request_id": first,
                    "epoch": 7,
                    "index": 0,
                    "surface": "final",
                    "slot_name": "slot-one",
                    "slot_generation": 1,
                    "width": 2,
                    "height": 2,
                    "byte_count": 12,
                },
                process.generation,
            )

        submitted = [
            event for event in events if event["type"] == "frame_submitted"
        ]
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0]["epoch"], 7)
        self.assertEqual(submitted[0]["index"], 1)
        self.assertEqual(submitted[0]["generation"], process.generation)
        self.assertIn(submitted[0]["request_id"], process._pending)

    def test_new_frame_during_terminal_callback_drops_older_coalesced_frame(self):
        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        self._mark_transport_alive(process)
        process.MAX_INFLIGHT_FRAMES = 1
        slots = []
        for generation, name in ((1, "slot-one"), (2, "slot-two")):
            slot = mock.Mock()
            slot.descriptor = SimpleNamespace(
                name=name,
                generation=generation,
                capacity=12,
                to_wire=lambda n=name, g=generation: {
                    "name": n,
                    "generation": g,
                    "capacity": 12,
                },
            )
            slot.read_bgr.return_value = object()
            slots.append(slot)
        fresh_ids = []

        def submit_fresh_frame(event):
            if event["type"] == "frame_ready" and event["index"] == 0:
                fresh_ids.append(
                    process.request_frame(
                        epoch=7,
                        index=2,
                        surface="final",
                        viewport=(2, 2),
                        zoom_factor=1.0,
                        pan=(0.5, 0.5),
                    )
                )

        process.add_listener(submit_fresh_frame)
        with mock.patch(
            "core.vs_runtime.worker_process.FrameSlot.create",
            side_effect=slots,
        ):
            first = process.request_frame(
                epoch=7,
                index=0,
                surface="final",
                viewport=(2, 2),
                zoom_factor=1.0,
                pan=(0.5, 0.5),
            )
            self.assertIsNone(
                process.request_frame(
                    epoch=7,
                    index=1,
                    surface="final",
                    viewport=(2, 2),
                    zoom_factor=1.0,
                    pan=(0.5, 0.5),
                    coalesce=True,
                )
            )
            process._handle_message(
                {
                    "type": "frame_ready",
                    "request_id": first,
                    "epoch": 7,
                    "index": 0,
                    "surface": "final",
                    "slot_name": "slot-one",
                    "slot_generation": 1,
                    "width": 2,
                    "height": 2,
                    "byte_count": 12,
                },
                process.generation,
            )

        self.assertEqual(len(fresh_ids), 1)
        self.assertIsNotNone(fresh_ids[0])
        self.assertIsNone(process._coalesced_frame)
        self.assertEqual(
            [pending.index for pending in process._pending.values()], [2]
        )

    def test_cancel_during_terminal_callback_invalidates_extracted_coalesced_frame(self):
        process = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        self._mark_transport_alive(process)
        process.MAX_INFLIGHT_FRAMES = 1
        slots = []
        for generation, name in ((1, "slot-one"), (2, "slot-two")):
            slot = mock.Mock()
            slot.descriptor = SimpleNamespace(
                name=name,
                generation=generation,
                capacity=12,
                to_wire=lambda n=name, g=generation: {
                    "name": n,
                    "generation": g,
                    "capacity": 12,
                },
            )
            slot.read_bgr.return_value = object()
            slots.append(slot)
        events = []

        def cancel_from_terminal(event):
            events.append(event)
            if event["type"] == "frame_ready":
                process.cancel_epoch(7)

        process.add_listener(cancel_from_terminal)
        with mock.patch(
            "core.vs_runtime.worker_process.FrameSlot.create",
            side_effect=slots,
        ):
            first = process.request_frame(
                epoch=7,
                index=0,
                surface="final",
                viewport=(2, 2),
                zoom_factor=1.0,
                pan=(0.5, 0.5),
            )
            self.assertIsNone(
                process.request_frame(
                    epoch=7,
                    index=1,
                    surface="final",
                    viewport=(2, 2),
                    zoom_factor=1.0,
                    pan=(0.5, 0.5),
                    coalesce=True,
                )
            )
            process._handle_message(
                {
                    "type": "frame_ready",
                    "request_id": first,
                    "epoch": 7,
                    "index": 0,
                    "surface": "final",
                    "slot_name": "slot-one",
                    "slot_generation": 1,
                    "width": 2,
                    "height": 2,
                    "byte_count": 12,
                },
                process.generation,
            )

        self.assertEqual(process.pending_frame_count(), 0)
        self.assertFalse(
            any(event["type"] == "frame_submitted" for event in events)
        )

    def test_sync_frame_discarded_raises_stable_transport_error(self):
        client = SyncVSWorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        response = {
            "type": "frame_discarded",
            "request_id": 9,
            "epoch": 7,
            "index": 2,
            "surface": "final",
            "reason": "cancelled",
        }

        with (
            mock.patch.object(
                client.transport, "request_frame", return_value=9
            ),
            mock.patch.object(client, "_wait_for", return_value=response),
            self.assertRaises(WorkerProcessError) as raised,
        ):
            client.request_frame(
                epoch=7,
                index=2,
                surface="final",
                viewport=(2, 2),
                zoom_factor=1.0,
                pan=(0.5, 0.5),
            )

        self.assertEqual(type(raised.exception).__name__, "FrameDiscardedError")
        self.assertEqual(raised.exception.response, response)
        self.assertEqual(raised.exception.reason, "cancelled")


class RetirementFailureDetectionTests(unittest.TestCase):
    def test_malformed_error_metadata_is_treated_as_non_fatal(self):
        from core.vs_runtime.worker_main import _has_retirement_failure

        class HostileCodeError(RuntimeError):
            @property
            def code(self):
                raise RuntimeError("poison code getter")

        class HostileNote:
            def __str__(self):
                raise RuntimeError("poison note string")

        notes_none = RuntimeError("ordinary notes-none failure")
        notes_none.__notes__ = None
        hostile_note = RuntimeError("ordinary hostile-note failure")
        hostile_note.__notes__ = [HostileNote()]

        for label, error in (
            ("notes_none", notes_none),
            ("hostile_code", HostileCodeError("ordinary code failure")),
            ("hostile_note", hostile_note),
        ):
            with self.subTest(label=label):
                try:
                    detected = _has_retirement_failure(error)
                except BaseException as raised:
                    self.fail(
                        "退休失败扫描器不应反抛恶意异常元数据："
                        f"{type(raised).__name__}"
                    )
                self.assertFalse(detected)

    def test_stable_retirement_markers_remain_fatal_through_error_chain(self):
        from core.vs_runtime.worker_main import _has_retirement_failure

        direct = RuntimeError("direct")
        direct.code = "executor.retirement_failed"
        noted = RuntimeError("noted")
        noted.add_note("cleanup [executor.retirement_failed]")
        outer = RuntimeError("outer")
        outer.__cause__ = noted

        self.assertTrue(_has_retirement_failure(direct))
        self.assertTrue(_has_retirement_failure(outer))


class WorkerServerFrameTests(unittest.TestCase):
    def test_load_binding_keeps_the_canonical_user_script_path(self):
        """M5/C2: worker 与 VSPipe 必须以同一 ``__file__`` 执行用户脚本。

        job 仍需是 generation staging 中的只读副本；脚本本身不能复制到
        worker 私有根，否则相对资源和 ``__file__`` 会与 VSPipe 分叉。
        """
        from core.vs_runtime.session import (
            GenerationStagingRoot,
            ScriptBundleSnapshot,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            script = root / "pipeline.vpy"
            helper = root / "relative_resource.txt"
            job = root / "job.json"
            script.write_text("# canonical script", encoding="utf-8")
            helper.write_text("same script root", encoding="utf-8")
            job.write_text("{}", encoding="utf-8")
            staging = GenerationStagingRoot.create()
            staging.initialize_marker()
            self.addCleanup(staging.close)

            binding = ScriptBundleSnapshot.create(script, job, staging)

            self.assertEqual(binding.script_path, script.resolve())
            self.assertEqual(
                binding.script_path.with_name("relative_resource.txt").read_text(
                    encoding="utf-8"
                ),
                "same script root",
            )
            self.assertNotEqual(binding.job_path, job.resolve())

    def _server(self, writer):
        from core.vs_runtime.worker_main import WorkerServer

        server = object.__new__(WorkerServer)
        server.writer = writer
        server._condition = threading.Condition(threading.RLock())
        server._cancelled_epochs = set()
        server._loaded = SimpleNamespace(epoch=7)
        server._frames = {}
        return server

    @staticmethod
    def _context(slot):
        from core.vs_runtime.worker_main import _FrameRequest

        return _FrameRequest(
            request_id=11,
            epoch=7,
            index=0,
            surface="final",
            slot=slot,
            display_clip=None,
        )

    def test_load_rejects_canonical_script_change_during_retirement_wait(self):
        from core.vs_runtime.worker_main import WorkerServer

        messages = []

        class Writer:
            def send(self, message):
                messages.append(message)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            script = root / "pipeline.vpy"
            helper = root / "helper.py"
            job = root / "job.json"
            script.write_text(
                _valid_script(compatible=False, extra="# SAFE_SCRIPT\n"),
                encoding="utf-8",
            )
            helper.write_text("VALUE = 'SAFE_HELPER'\n", encoding="utf-8")
            _write_job(job, epoch=7)
            session = _session(script, job, epoch=7)
            generation_staging = GenerationStagingRoot.create()
            generation_staging.initialize_marker()
            self.addCleanup(generation_staging.close)
            server = WorkerServer(
                writer=Writer(),
                app_dir=ROOT,
                self_test=False,
                generation_staging=generation_staging,
            )
            original_retire = server._retire_current

            def mutate_source_during_retirement(*args, **kwargs):
                script.write_text(
                    _valid_script(
                        compatible=False,
                        extra="# CHANGED_SCRIPT\n",
                    ),
                    encoding="utf-8",
                )
                helper.write_text(
                    "VALUE = 'CHANGED_HELPER'\n", encoding="utf-8"
                )
                changed_job = json.loads(job.read_text(encoding="utf-8"))
                changed_job["mutation"] = "CHANGED_JOB"
                job.write_text(
                    json.dumps(changed_job, ensure_ascii=False),
                    encoding="utf-8",
                )
                return original_retire(*args, **kwargs)

            server._retire_current = mutate_source_during_retirement
            consumed = {}
            graph = mock.Mock()

            def execute_snapshot(**kwargs):
                snapshot_script = Path(kwargs["script_path"])
                snapshot_job = Path(kwargs["job_path"])
                consumed.update(
                    script_path=snapshot_script,
                    script=snapshot_script.read_text(encoding="utf-8"),
                    helper=(snapshot_script.parent / "helper.py").read_text(
                        encoding="utf-8"
                    ),
                    job=json.loads(snapshot_job.read_text(encoding="utf-8")),
                )
                return graph

            clip = SimpleNamespace(
                width=384,
                height=640,
                num_frames=3,
                fps_num=30_000,
                fps_den=1_001,
                format=SimpleNamespace(name="YUV420P8"),
            )
            outputs = SimpleNamespace(guarded_clip=clip, editor_clip=None)
            with (
                mock.patch.object(
                    server,
                    "_ensure_vs",
                    return_value=SimpleNamespace(core=SimpleNamespace()),
                ),
                mock.patch(
                    "resources.vapoursynth.python.assetmaker_vs.contract.verify_required_callables"
                ),
                mock.patch(
                    "resources.vapoursynth.python.assetmaker_vs.executor.execute_user_script",
                    side_effect=execute_snapshot,
                ),
                mock.patch(
                    "resources.vapoursynth.python.assetmaker_vs.contract.validate_outputs",
                    return_value=outputs,
                ),
            ):
                server._handle_load(session.to_load_message(1))

            # M5/C2 的资源策略是 canonical script + bundle hash，而非复制
            # 代码树：退休期间任何脚本/helper 改写都必须在执行用户代码前
            # 拒绝，不能悄悄让 preview/VSPipe 各跑一份不同的 __file__。
            self.assertEqual(consumed, {})
            self.assertEqual(messages[-1]["type"], "request_error")
            self.assertIn("bundle", messages[-1]["message"])

    def test_early_contract_helper_import_failure_closes_load_snapshot(self):
        from core.vs_runtime.session import ScriptBundleSnapshot
        from core.vs_runtime.worker_main import WorkerServer

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            script = root / "pipeline.vpy"
            job = root / "job.json"
            script.write_text(_valid_script(compatible=False), encoding="utf-8")
            _write_job(job, epoch=7)
            session = _session(script, job, epoch=7)
            generation_staging = GenerationStagingRoot.create()
            generation_staging.initialize_marker()
            self.addCleanup(generation_staging.close)
            server = WorkerServer(
                writer=mock.Mock(),
                app_dir=ROOT,
                self_test=False,
                generation_staging=generation_staging,
            )
            created = []
            real_create = ScriptBundleSnapshot.create

            def capture_snapshot(*args, **kwargs):
                snapshot = real_create(*args, **kwargs)
                created.append(snapshot)
                return snapshot

            real_import = builtins.__import__

            def fail_contract_import(name, *args, **kwargs):
                if name == (
                    "resources.vapoursynth.python.assetmaker_vs.contract"
                ):
                    raise ImportError("contract helper import failed")
                return real_import(name, *args, **kwargs)

            try:
                with (
                    mock.patch.object(
                        ScriptBundleSnapshot,
                        "create",
                        side_effect=capture_snapshot,
                    ),
                    mock.patch.object(server, "_assert_runtime_unchanged"),
                    mock.patch.object(
                        server,
                        "_ensure_vs",
                        return_value=SimpleNamespace(core=SimpleNamespace()),
                    ),
                    mock.patch.object(
                        builtins,
                        "__import__",
                        side_effect=fail_contract_import,
                    ),
                    self.assertRaisesRegex(
                        ImportError, "contract helper import failed"
                    ),
                ):
                    server._handle_load(session.to_load_message(1))

                self.assertEqual(len(created), 1)
                self.assertEqual(
                    created[0].root_path.parent,
                    generation_staging.root_path,
                )
                self.assertFalse(created[0].root_path.exists())
            finally:
                for snapshot in created:
                    snapshot.close()

    def test_runtime_change_since_worker_start_requires_fresh_process(self):
        from core.vs_runtime.worker_main import WorkerServer

        server = object.__new__(WorkerServer)
        server.app_dir = ROOT
        server.runtime = load_vs_runtime()
        server.runtime_fingerprint = "a" * 64
        server.generation_staging = GenerationStagingRoot.create()
        server.generation_staging.initialize_marker()
        self.addCleanup(server.generation_staging.close)
        with tempfile.TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "pipeline.vpy"
            job = Path(temp_dir) / "job.json"
            script.write_text("# test\n", encoding="utf-8")
            job.write_text("{}", encoding="utf-8")
            message = {
                "type": "load",
                "request_id": 1,
                "api_version": 1,
                "track": "loop",
                "epoch": 7,
                "script_path": str(script.resolve()),
                "job_path": str(job.resolve()),
                "job_sha256": hashlib.sha256(job.read_bytes()).hexdigest(),
                "bundle_hash": "c" * 64,
                "runtime_fingerprint": "b" * 64,
                "mode": "raw",
            }
            with (
                mock.patch(
                    "core.vs_runtime.session.compute_script_bundle_hash",
                    return_value="c" * 64,
                ),
                mock.patch(
                    "core.vs_runtime.vs_loader.compute_runtime_fingerprint",
                    return_value="b" * 64,
                ),
                mock.patch(
                    "resources.vapoursynth.python.assetmaker_vs.job_api.load_job",
                    return_value={
                        "api_version": 1,
                        "track": "loop",
                        "epoch": 7,
                    },
                ) as load_job,
                mock.patch(
                    "resources.vapoursynth.python.assetmaker_vs.script_header.parse_script_header",
                    return_value={"mode": "raw"},
                ),
                mock.patch(
                    "resources.vapoursynth.python.assetmaker_vs.script_header.validate_invocation"
                ),
                self.assertRaises(ProtocolError) as raised,
            ):
                server._prepare_load(message)

        self.assertEqual(raised.exception.code, "worker.runtime_changed")
        load_job.assert_not_called()

    def test_runtime_change_while_importing_helpers_prevents_job_read(self):
        from core.vs_runtime.worker_main import WorkerServer

        server = object.__new__(WorkerServer)
        server.app_dir = ROOT
        server.runtime = load_vs_runtime()
        server.runtime_fingerprint = "a" * 64
        server.generation_staging = GenerationStagingRoot.create()
        server.generation_staging.initialize_marker()
        self.addCleanup(server.generation_staging.close)
        with tempfile.TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "pipeline.vpy"
            job = Path(temp_dir) / "job.json"
            script.write_text("# test\n", encoding="utf-8")
            job.write_text("{}", encoding="utf-8")
            message = {
                "type": "load",
                "request_id": 1,
                "api_version": 1,
                "track": "loop",
                "epoch": 7,
                "script_path": str(script.resolve()),
                "job_path": str(job.resolve()),
                "job_sha256": hashlib.sha256(job.read_bytes()).hexdigest(),
                "bundle_hash": "c" * 64,
                "runtime_fingerprint": "a" * 64,
                "mode": "raw",
            }
            with (
                mock.patch(
                    "core.vs_runtime.session.compute_script_bundle_hash",
                    return_value="c" * 64,
                ),
                mock.patch(
                    "core.vs_runtime.vs_loader.compute_runtime_fingerprint",
                    side_effect=("a" * 64, "b" * 64),
                ) as fingerprint,
                mock.patch(
                    "resources.vapoursynth.python.assetmaker_vs.job_api.load_job"
                ) as load_job,
                mock.patch(
                    "resources.vapoursynth.python.assetmaker_vs.script_header.parse_script_header",
                    return_value={"mode": "raw"},
                ),
                mock.patch(
                    "resources.vapoursynth.python.assetmaker_vs.script_header.validate_invocation"
                ),
                self.assertRaises(ProtocolError) as raised,
            ):
                server._prepare_load(message)

        self.assertEqual(raised.exception.code, "worker.runtime_changed")
        self.assertEqual(fingerprint.call_count, 2)
        load_job.assert_not_called()

    def test_runtime_change_while_loading_vs_prevents_user_execution(self):
        from core.vs_runtime.worker_main import WorkerServer

        server = object.__new__(WorkerServer)
        server.writer = mock.Mock()
        server.app_dir = ROOT
        server.runtime = load_vs_runtime()
        server.runtime_fingerprint = "a" * 64
        snapshot = mock.Mock()
        server._prepare_load = mock.Mock(
            return_value=(
                3,
                {"epoch": 7},
                {"requires": []},
                ROOT / "pipeline.vpy",
                str(ROOT / "job.json"),
                snapshot,
            )
        )
        server._retire_current = mock.Mock()
        server._ensure_vs = mock.Mock(
            return_value=SimpleNamespace(core=SimpleNamespace())
        )
        server._send_error = mock.Mock()
        message = {
            "request_id": 3,
            "epoch": 7,
            "api_version": 1,
            "mode": "raw",
            "bundle_hash": "c" * 64,
            "runtime_fingerprint": "a" * 64,
        }
        with (
            mock.patch(
                "core.vs_runtime.session.compute_script_bundle_hash",
                return_value="c" * 64,
            ),
            mock.patch(
                "core.vs_runtime.vs_loader.compute_runtime_fingerprint",
                return_value="b" * 64,
            ),
            mock.patch(
                "resources.vapoursynth.python.assetmaker_vs.contract.verify_required_callables"
            ) as verify_required_callables,
            mock.patch(
                "resources.vapoursynth.python.assetmaker_vs.executor.execute_user_script"
            ) as execute_user_script,
            self.assertRaises(BaseException) as raised,
        ):
            server._handle_load(message)

        self.assertEqual(type(raised.exception).__name__, "_FatalWorkerExit")
        self.assertEqual(raised.exception.exit_code, 73)
        server._send_error.assert_called_once()
        snapshot.close.assert_called_once_with()
        verify_required_callables.assert_not_called()
        execute_user_script.assert_not_called()

    def test_runtime_change_error_terminates_stale_worker_after_terminal(self):
        from core.vs_runtime.worker_main import WorkerServer

        server = object.__new__(WorkerServer)
        server._prepare_load = mock.Mock(
            side_effect=ProtocolError(
                "runtime changed", code="worker.runtime_changed"
            )
        )
        server._send_error = mock.Mock()

        with self.assertRaises(BaseException) as raised:
            server._handle_load({"request_id": 3, "epoch": 7})

        self.assertEqual(type(raised.exception).__name__, "_FatalWorkerExit")
        self.assertEqual(raised.exception.exit_code, 73)
        server._send_error.assert_called_once()

    def test_run_worker_snapshots_runtime_before_importing_log_helper(self):
        from core.vs_runtime import worker_main

        events = []
        server = SimpleNamespace(
            runtime_fingerprint="a" * 64,
            close_for_eof=mock.Mock(return_value=0),
        )

        def verify_runtime(_expected):
            events.append("runtime-check")

        server._assert_runtime_unchanged = verify_runtime

        def create_server(**_kwargs):
            events.append("server")
            return server

        log_writer = SimpleNamespace(flush=mock.Mock())

        def install_stdout(_writer):
            events.append("stdout")
            return log_writer

        with (
            mock.patch.object(worker_main, "WorkerServer", side_effect=create_server),
            mock.patch.object(
                worker_main,
                "_install_structured_stdout",
                side_effect=install_stdout,
            ),
        ):
            result = worker_main.run_worker(
                protocol_stream=io.BytesIO(),
                input_stream=io.BytesIO(),
                app_dir=ROOT,
                self_test=False,
                generation_staging=object(),
            )

        self.assertEqual(result, 0)
        self.assertEqual(events, ["server", "stdout", "runtime-check"])

    def test_main_defers_staging_import_until_worker_runtime_snapshot(self):
        from core.vs_runtime import worker_main

        events = []
        real_import = builtins.__import__

        def observe_import(name, *args, **kwargs):
            if name == "core.vs_runtime.session":
                events.append("session-import")
            return real_import(name, *args, **kwargs)

        def run_worker(**kwargs):
            events.append("run-worker")
            self.assertIsNone(kwargs["generation_staging"])
            return 0

        with (
            mock.patch.object(
                builtins,
                "__import__",
                side_effect=observe_import,
            ),
            mock.patch.object(
                worker_main,
                "run_worker",
                side_effect=run_worker,
            ),
        ):
            result = worker_main.main(
                protocol_stream=io.BytesIO(),
                input_stream=io.BytesIO(),
                app_dir=ROOT,
                self_test=False,
            )

        self.assertEqual(result, 0)
        self.assertEqual(events, ["run-worker"])

    def test_cancel_ack_cannot_overtake_committing_frame_terminal(self):
        write_started = threading.Event()
        release_write = threading.Event()
        cancel_ack = threading.Event()
        messages = []

        class Writer:
            def send(self, message):
                encode_message(message)
                messages.append(message)
                if message.get("operation") == "cancel_epoch":
                    cancel_ack.set()

        descriptor = SimpleNamespace(name="frame-slot", generation=3)
        slot = mock.Mock()
        slot.descriptor = descriptor

        def blocked_write(_frame):
            write_started.set()
            release_write.wait(5)
            return 12

        slot.write_vs_rgb.side_effect = blocked_write
        frame = mock.Mock(width=2, height=2)
        future = mock.Mock()
        future.result.return_value = frame
        server = self._server(Writer())
        context = self._context(slot)
        server._frames[context.request_id] = context

        frame_thread = threading.Thread(
            target=server._finish_frame, args=(context, future)
        )
        frame_thread.start()
        self.assertTrue(write_started.wait(2))
        cancel_thread = threading.Thread(
            target=server.handle,
            args=({"type": "cancel_epoch", "request_id": 12, "epoch": 7},),
        )
        cancel_thread.start()
        ack_before_release = cancel_ack.wait(0.2)
        release_write.set()
        frame_thread.join(5)
        cancel_thread.join(5)

        self.assertFalse(ack_before_release)
        self.assertEqual(
            [(item["type"], item.get("operation")) for item in messages],
            [("frame_ready", None), ("ready", "cancel_epoch")],
        )

    def test_frame_after_cancel_ack_is_discarded_without_starting_future(self):
        writer = mock.Mock()
        server = self._server(writer)
        server._cancelled_epochs.add(7)
        server._loaded = SimpleNamespace(
            epoch=7,
            outputs=SimpleNamespace(
                guarded_clip=object(), editor_clip=None
            ),
        )
        message = {
            "type": "request_frame",
            "request_id": 19,
            "epoch": 7,
            "index": 0,
            "surface": "final",
            "slot": {
                "name": "frame-slot",
                "generation": 4,
                "capacity": 12,
            },
            "display": {
                "viewport": [2, 2],
                "zoom_factor": 1.0,
                "pan": [0.5, 0.5],
            },
        }

        with (
            mock.patch(
                "core.vs_runtime.shared_frame.FrameSlot.open"
            ) as open_slot,
            mock.patch(
                "resources.vapoursynth.python.assetmaker_vs.display.to_display_clip"
            ) as to_display,
        ):
            server._handle_frame(message)

        open_slot.assert_not_called()
        to_display.assert_not_called()
        sent = writer.send.call_args.args[0]
        self.assertEqual(sent["type"], "frame_discarded")
        self.assertEqual(sent["request_id"], 19)
        self.assertEqual(sent["epoch"], 7)
        self.assertEqual(sent["slot_name"], "frame-slot")
        self.assertEqual(sent["slot_generation"], 4)

    def test_invalid_surface_terminal_keeps_slot_identity_for_host_release(self):
        messages = []

        class Writer:
            def send(self, message):
                encode_message(message)
                messages.append(message)

        host = WorkerProcess(
            command=[str(Path(sys.executable).resolve()), "-B", "-c", "pass"]
        )
        fake_process = mock.Mock()
        fake_process.poll.return_value = None
        host._process = fake_process
        host._exit_event.clear()
        slot = mock.Mock()
        slot.descriptor = SimpleNamespace(
            name="host-frame-slot",
            generation=41,
            capacity=12,
            to_wire=lambda: {
                "name": "host-frame-slot",
                "generation": 41,
                "capacity": 12,
            },
        )
        with mock.patch(
            "core.vs_runtime.worker_process.FrameSlot.create",
            return_value=slot,
        ):
            request_id = host.request_frame(
                epoch=7,
                index=0,
                surface="final",
                viewport=(2, 2),
                zoom_factor=1.0,
                pan=(0.5, 0.5),
            )
        _generation, encoded = host._write_queue.get_nowait()
        request = MessageDecoder().feed(encoded)[0]
        request["surface"] = "invalid-surface"

        server = self._server(Writer())
        server._handle_frame(request)

        self.assertEqual(len(messages), 1)
        response = messages[0]
        self.assertEqual(response["type"], "request_error")
        self.assertEqual(response["request_id"], request_id)
        self.assertEqual(response["epoch"], 7)
        self.assertEqual(response["slot_name"], "host-frame-slot")
        self.assertEqual(response["slot_generation"], 41)

        host._handle_message(response, host.generation)

        slot.close.assert_called_once_with()
        self.assertNotIn(request_id, host._pending)

    def test_invalid_unicode_callback_error_still_sends_one_terminal(self):
        from core.vs_runtime.worker_main import ProtocolWriter

        stream = io.BytesIO()
        writer = ProtocolWriter(stream)
        server = self._server(writer)
        descriptor = SimpleNamespace(name="frame-slot", generation=4)
        slot = mock.Mock()
        slot.descriptor = descriptor
        context = self._context(slot)
        server._frames[context.request_id] = context
        future = mock.Mock()
        future.result.side_effect = RuntimeError("\ud800")

        server._finish_frame(context, future)

        messages = MessageDecoder().feed(stream.getvalue())
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["type"], "request_error")
        self.assertEqual(messages[0]["request_id"], context.request_id)
        self.assertEqual(messages[0]["epoch"], context.epoch)
        self.assertEqual(messages[0]["slot_name"], descriptor.name)
        self.assertEqual(messages[0]["slot_generation"], descriptor.generation)
        encode_message(messages[0])
        slot.close.assert_called_once_with()
        self.assertEqual(server._frames, {})

    def test_cancelled_failing_future_is_discarded_after_cancel_ack(self):
        future_entered = threading.Event()
        release_future = threading.Event()
        messages = []

        class Writer:
            def send(self, message):
                encode_message(message)
                messages.append(message)

        descriptor = SimpleNamespace(name="frame-slot", generation=5)
        slot = mock.Mock()
        slot.descriptor = descriptor
        context = self._context(slot)
        server = self._server(Writer())
        server._frames[context.request_id] = context
        future = mock.Mock()

        def failing_result():
            future_entered.set()
            release_future.wait(5)
            raise RuntimeError("late callback failure")

        future.result.side_effect = failing_result
        frame_thread = threading.Thread(
            target=server._finish_frame, args=(context, future)
        )
        frame_thread.start()
        self.assertTrue(future_entered.wait(2))

        server.handle(
            {"type": "cancel_epoch", "request_id": 12, "epoch": 7}
        )
        release_future.set()
        frame_thread.join(5)

        self.assertEqual(
            [(item["type"], item.get("operation")) for item in messages],
            [("ready", "cancel_epoch"), ("frame_discarded", None)],
        )

    def test_error_encoding_failure_uses_fixed_small_fallback(self):
        from core.vs_runtime.worker_main import WorkerServer

        messages = []

        class FailFirstWriter:
            def send(self, message):
                if not messages:
                    messages.append(None)
                    raise ProtocolError("bad payload", code="protocol.encode_failed")
                encode_message(message)
                messages.append(message)

        server = object.__new__(WorkerServer)
        server.writer = FailFirstWriter()
        server._send_error(
            "script_error",
            19,
            RuntimeError("bad"),
            epoch=7,
        )

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[1]["type"], "request_error")
        self.assertEqual(messages[1]["code"], "worker.error_encoding")

    def test_hostile_exception_string_still_gets_request_terminal(self):
        from core.vs_runtime.worker_main import ProtocolWriter

        class HostileError(RuntimeError):
            def __str__(self):
                raise RuntimeError("broken __str__")

        stream = io.BytesIO()
        server = self._server(ProtocolWriter(stream))
        descriptor = SimpleNamespace(name="frame-slot", generation=6)
        slot = mock.Mock()
        slot.descriptor = descriptor
        context = self._context(slot)
        server._frames[context.request_id] = context
        future = mock.Mock()
        future.result.side_effect = HostileError()

        server._finish_frame(context, future)

        messages = MessageDecoder().feed(stream.getvalue())
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["type"], "request_error")
        self.assertEqual(messages[0]["message"], "HostileError")

    def test_frame_terminal_pipe_failure_exits_without_second_response(self):
        from core.vs_runtime.worker_main import WorkerServer

        writer = mock.Mock()
        writer.send.side_effect = BrokenPipeError("closed")
        server = self._server(writer)
        descriptor = SimpleNamespace(name="frame-slot", generation=7)
        slot = mock.Mock()
        slot.descriptor = descriptor
        slot.write_vs_rgb.return_value = 12
        context = self._context(slot)
        server._frames[context.request_id] = context
        frame = mock.Mock(width=2, height=2)
        future = mock.Mock()
        future.result.return_value = frame

        with mock.patch(
            "core.vs_runtime.worker_main.os._exit",
            side_effect=SystemExit(72),
        ) as fatal_exit:
            with self.assertRaises(SystemExit):
                server._finish_frame(context, future)

        fatal_exit.assert_called_once_with(72)
        writer.send.assert_called_once()


class WorkerProcessLifecycleTests(unittest.TestCase):
    def test_fresh_host_self_test_never_imports_vapoursynth(self):
        code = (
            "import sys; "
            "assert 'vapoursynth' not in sys.modules; "
            "assert 'core.vs_engine' not in sys.modules; "
            "from pathlib import Path; "
            "from core.vs_runtime.worker_process import SyncVSWorkerProcess; "
            f"c=SyncVSWorkerProcess(app_dir=Path({str(ROOT)!r}),self_test=True); "
            "c.start(timeout_ms=10000); c.shutdown(timeout_ms=10000); "
            "assert 'vapoursynth' not in sys.modules; "
            "assert 'core.vs_engine' not in sys.modules"
        )

        result = subprocess.run(
            [str(Path(sys.executable).resolve()), "-B", "-c", code],
            cwd=ROOT,
            capture_output=True,
            timeout=20,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_sync_shutdown_timeout_still_terminates_stuck_child(self):
        child = (
            "import json,struct,sys,time; "
            "h=sys.stdin.buffer.read(4); n=struct.unpack('>I',h)[0]; "
            "m=json.loads(sys.stdin.buffer.read(n)); "
            "from core.vs_runtime.protocol import encode_message; "
            "sys.stdout.buffer.write(encode_message({"
            "'type':'ready','request_id':m['request_id'],'api_version':1,"
            "'operation':'hello'})); sys.stdout.buffer.flush(); time.sleep(60)"
        )
        client = SyncVSWorkerProcess(
            app_dir=ROOT,
            command=[str(Path(sys.executable).resolve()), "-B", "-c", child],
        )
        self.addCleanup(client.close)
        client.start(timeout_ms=5_000)

        with self.assertRaises(TimeoutError):
            client.shutdown(timeout_ms=100)

        self.assertFalse(client.transport.alive)
        self.assertIsNotNone(client.transport.wait(timeout_ms=5_000))


class RealVSWorkerLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        missing = [
            path
            for path in (
                TOOLCHAIN.vspipe_path,
                TOOLCHAIN.x264_path,
                TOOLCHAIN.muxer_path,
                str(ROOT / "tools" / "media" / "vapoursynth.pyd"),
            )
            if not path or not Path(path).is_file()
        ]
        if missing:
            raise AssertionError(
                "M3 真实 worker 测试要求完整 media toolchain: "
                + ", ".join(str(path) for path in missing)
            )

    def setUp(self):
        self.assertNotIn("vapoursynth", sys.modules)
        self.assertNotIn("core.vs_engine", sys.modules)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name).resolve() / "素材" / "黍"
        self.root.mkdir(parents=True)
        self.job = self.root / "job.json"
        _write_job(self.job)
        self.appdata = self.root / "appdata"
        self.runtime = load_vs_runtime(
            ROOT / "config" / "vs_runtime.json",
            self.appdata
            / "ArknightsPassMaker"
            / "vapoursynth"
            / "vs_runtime.user.json",
        )
        self.client = SyncVSWorkerProcess(
            app_dir=ROOT,
            env={**os.environ, "APPDATA": str(self.appdata)},
        )
        self.addCleanup(self.client.close)

    def _write_script(self, name: str, body: str) -> Path:
        directory = self.root / name
        directory.mkdir()
        script = directory / "pipeline.vpy"
        script.write_text(body, encoding="utf-8")
        return script

    def _session(self, script: Path, *, epoch: int = 3) -> RenderSession:
        return _session(script, self.job, epoch=epoch, runtime=self.runtime)

    def test_real_start_load_metadata_frames_digest_unload_shutdown(self):
        script = self._write_script(
            "正常脚本",
            _valid_script(
                extra=(
                    "print('脚本加载日志')\n"
                    "def report(n, f):\n"
                    "    print('延迟回调日志')\n"
                    "    return f\n"
                    "base = core.std.ModifyFrame(\n"
                    "    clip=base, clips=base, selector=report\n"
                    ")\n"
                )
            ),
        )
        session = self._session(script)

        ready = self.client.start(timeout_ms=15_000)
        metadata = self.client.load(session, timeout_ms=20_000)
        final = self.client.request_frame(
            epoch=3,
            index=1,
            surface="final",
            viewport=(384, 640),
            zoom_factor=1.0,
            pan=(0.5, 0.5),
            timeout_ms=15_000,
        )
        editor = self.client.request_frame(
            epoch=3,
            index=0,
            surface="editor",
            viewport=(192, 320),
            zoom_factor=1.0,
            pan=(0.5, 0.5),
            timeout_ms=15_000,
        )
        padded = self.client.request_frame(
            epoch=3,
            index=0,
            surface="final",
            viewport=(360, 640),
            zoom_factor=1.0,
            pan=(0.5, 0.5),
            timeout_ms=15_000,
        )
        digests = self.client.request_plane_digest(
            epoch=3, index=1, timeout_ms=15_000
        )

        self.assertEqual(ready["operation"], "hello")
        self.assertEqual(metadata.epoch, 3)
        self.assertEqual(metadata.mode, "compatible")
        self.assertEqual(metadata.output0.pixel_format, "YUV420P8")
        self.assertEqual(metadata.output0.range, "limited")
        self.assertIsNotNone(metadata.editor)
        self.assertEqual(final.shape, (640, 384, 3))
        self.assertEqual(editor.shape, (320, 192, 3))
        self.assertEqual(padded.shape, (600, 360, 3))
        self.assertTrue(final.flags["OWNDATA"])
        self.assertGreater(int(final[0, 0, 2]), int(final[0, 0, 0]))
        self.assertEqual(set(digests), {"Y", "U", "V"})
        self.assertTrue(all(len(value) == 64 for value in digests.values()))
        self.assertTrue(any("脚本加载日志" in text for _, text in self.client.logs))
        self.assertTrue(any("延迟回调日志" in text for _, text in self.client.logs))

        self.client.unload(timeout_ms=10_000)
        response = self.client.shutdown(timeout_ms=10_000)
        self.assertEqual(response["operation"], "shutdown")
        self.assertEqual(self.client.wait(timeout_ms=10_000), 0)
        self.assertNotIn("vapoursynth", sys.modules)
        self.assertNotIn("core.vs_engine", sys.modules)

    def test_script_and_contract_errors_do_not_replace_worker(self):
        bad_script = self._write_script(
            "脚本错误",
            "# assetmaker-api: 1\n"
            "# assetmaker-mode: raw\n"
            "# assetmaker-capabilities: source\n"
            "# assetmaker-requires:\n"
            "# assetmaker-editor-output: 0\n\n"
            "raise RuntimeError('用户脚本失败')\n",
        )
        bad_contract = self._write_script(
            "契约错误",
            _valid_script(compatible=False).replace(
                "width=384, height=640", "width=382, height=640"
            ),
        )
        valid = self._write_script("恢复脚本", _valid_script(compatible=False))

        self.client.start(timeout_ms=15_000)
        pid = self.client.pid
        with self.assertRaises(WorkerRequestError) as script_error:
            self.client.load(self._session(bad_script), timeout_ms=15_000)
        self.assertEqual(script_error.exception.response["type"], "script_error")
        self.assertEqual(self.client.pid, pid)
        self.assertTrue(self.client.transport.alive)

        with self.assertRaises(WorkerRequestError) as contract_error:
            self.client.load(self._session(bad_contract), timeout_ms=15_000)
        self.assertEqual(
            contract_error.exception.response["type"], "contract_error"
        )
        self.assertEqual(self.client.pid, pid)
        self.assertTrue(self.client.transport.alive)

        metadata = self.client.load(self._session(valid), timeout_ms=15_000)
        self.assertEqual(metadata.epoch, 3)
        self.assertEqual(self.client.pid, pid)

    def test_sentinel_callback_errors_close_graph_and_keep_same_worker(self):
        valid = self._write_script(
            "哨兵恢复脚本", _valid_script(compatible=False)
        )
        bad_scripts = []
        for target in (0, 1, 2):
            callback = (
                "def fail_sentinel(n, f):\n"
                f"    if n == {target}:\n"
                f"        raise RuntimeError('sentinel-{target}')\n"
                "    return f\n"
                "base = core.std.ModifyFrame(\n"
                "    clip=base, clips=base, selector=fail_sentinel\n"
                ")\n"
            )
            bad_scripts.append(
                self._write_script(
                    f"哨兵错误{target}",
                    _valid_script(compatible=False, extra=callback),
                )
            )

        self.client.start(timeout_ms=15_000)
        pid = self.client.pid
        for target, script in enumerate(bad_scripts):
            with self.subTest(target=target):
                with self.assertRaises(WorkerRequestError) as raised:
                    self.client.load(self._session(script), timeout_ms=20_000)
                self.assertEqual(
                    raised.exception.response["type"], "script_error"
                )
                self.assertIn(f"sentinel-{target}", str(raised.exception))
                self.assertTrue(self.client.transport.alive)
                self.assertEqual(self.client.pid, pid)

                metadata = self.client.load(
                    self._session(valid), timeout_ms=20_000
                )
                self.assertEqual(metadata.epoch, 3)
                self.assertEqual(self.client.pid, pid)

    def test_late_output_guard_failure_is_a_frame_request_terminal(self):
        payload = json.loads(self.job.read_text(encoding="utf-8"))
        payload["timeline"]["end_frame"] = 5
        self.job.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        drift = (
            "def drift_on_second_frame(n, f):\n"
            "    if n != 1:\n"
            "        return f\n"
            "    changed = f.copy()\n"
            "    changed.props['_Matrix'] = 1\n"
            "    return changed\n"
            "base = core.std.ModifyFrame(\n"
            "    clip=base, clips=base, selector=drift_on_second_frame\n"
            ")\n"
        )
        script = self._write_script(
            "逐帧漂移",
            _valid_script(compatible=False, extra=drift).replace(
                "length=3", "length=5"
            ),
        )
        self.client.start(timeout_ms=15_000)
        self.client.load(self._session(script), timeout_ms=20_000)

        with self.assertRaises(WorkerRequestError) as raised:
            self.client.request_frame(
                epoch=3,
                index=1,
                surface="final",
                viewport=(384, 640),
                zoom_factor=1.0,
                pan=(0.5, 0.5),
                timeout_ms=15_000,
            )

        self.assertEqual(raised.exception.response["type"], "request_error")
        self.assertEqual(raised.exception.code, "contract.matrix")
        self.assertTrue(self.client.transport.alive)

    def test_identity_mismatch_is_rejected_before_user_code(self):
        marker = self.root / "executed.marker"
        script = self._write_script(
            "身份竞态",
            _valid_script(
                compatible=False,
                extra=f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
            ),
        )
        self.client.start(timeout_ms=15_000)

        with self.assertRaises(WorkerRequestError) as raised:
            self.client.load(
                self._session(script, epoch=4), timeout_ms=15_000
            )

        self.assertEqual(raised.exception.response["type"], "request_error")
        self.assertFalse(marker.exists())
        self.assertTrue(self.client.transport.alive)

    def test_runtime_snapshot_a_survives_restart_then_explicit_b_reload(self):
        """真实 child 仅在显式重载边界切换 A/B runtime。"""
        from core.vs_runtime.snapshot import RuntimeSnapshot

        runtime_a = VSRuntimeConfig(core=CoreConfig(num_threads=1))
        runtime_b = VSRuntimeConfig(core=CoreConfig(num_threads=2))
        snapshot_a = RuntimeSnapshot(
            str(ROOT), runtime_a, compute_runtime_fingerprint(ROOT, runtime_a)
        )
        snapshot_b = RuntimeSnapshot(
            str(ROOT), runtime_b, compute_runtime_fingerprint(ROOT, runtime_b)
        )
        script_a = self._write_script(
            "快照A",
            _valid_script(
                compatible=False,
                extra="assert core.num_threads == 1, core.num_threads\n",
            ),
        )
        crash = self._write_script(
            "快照A异常",
            "# assetmaker-api: 1\n# assetmaker-mode: raw\n"
            "# assetmaker-capabilities: source\n# assetmaker-requires:\n"
            "# assetmaker-editor-output: 0\n\nimport os\nos._exit(23)\n",
        )
        script_b = self._write_script(
            "快照B",
            _valid_script(
                compatible=False,
                extra="assert core.num_threads == 2, core.num_threads\n",
            ),
        )
        environment_a = snapshot_a.worker_environment(
            {**os.environ, "APPDATA": str(self.appdata)}
        )
        client_a = SyncVSWorkerProcess(app_dir=ROOT, env=environment_a)
        self.addCleanup(client_a.close)
        client_a.start(timeout_ms=15_000)
        client_a.load(_session(script_a, self.job, runtime=runtime_a), timeout_ms=20_000)
        with self.assertRaises(WorkerCrashedError):
            client_a.load(
                _session(crash, self.job, runtime=runtime_a),
                timeout_ms=15_000,
            )
        client_a.terminate_and_restart(timeout_ms=15_000)
        metadata_a = client_a.load(
            _session(script_a, self.job, runtime=runtime_a),
            timeout_ms=20_000,
        )
        self.assertEqual(metadata_a.epoch, 3)
        self.assertEqual(client_a.transport.env["ASSETMAKER_VS_RUNTIME_FINGERPRINT"], snapshot_a.fingerprint)
        client_a.close()

        client_b = SyncVSWorkerProcess(
            app_dir=ROOT,
            env=snapshot_b.worker_environment({**os.environ, "APPDATA": str(self.appdata)}),
        )
        self.addCleanup(client_b.close)
        client_b.start(timeout_ms=15_000)
        metadata_b = client_b.load(
            _session(script_b, self.job, runtime=runtime_b), timeout_ms=20_000
        )
        self.assertEqual(metadata_b.epoch, 3)
        self.assertEqual(client_b.transport.env["ASSETMAKER_VS_RUNTIME_FINGERPRINT"], snapshot_b.fingerprint)

    def test_runtime_snapshot_tampered_asset_is_rejected_before_hello(self):
        """child 自行复核快照后的受指纹资产，不能只信父进程 JSON。"""
        from core.vs_runtime.snapshot import RuntimeSnapshot

        with tempfile.TemporaryDirectory() as temporary:
            module_dir = Path(temporary) / "runtime-module"
            module_dir.mkdir()
            module = module_dir / "guarded.py"
            module.write_text("VALUE = 'A'\n", encoding="utf-8")
            runtime = VSRuntimeConfig(
                plugins=PluginConfig(python_module_dirs=(str(module_dir),))
            )
            snapshot = RuntimeSnapshot(
                str(ROOT), runtime, compute_runtime_fingerprint(ROOT, runtime)
            )
            module.write_text("VALUE = 'B'\n", encoding="utf-8")
            client = SyncVSWorkerProcess(
                app_dir=ROOT,
                env=snapshot.worker_environment({**os.environ, "APPDATA": str(self.appdata)}),
            )
            self.addCleanup(client.close)
            with self.assertRaises(WorkerCrashedError) as raised:
                client.start(timeout_ms=15_000)

        self.assertIn("runtime", str(raised.exception))

    def test_disk_runtime_reload_keeps_a_on_restart_then_switches_widget_to_b(self):
        """磁盘 A→B 必须在 widget/client/session/真实 child 同步生效。"""
        from config.vs_runtime import default_vs_runtime_user_path
        from core.vs_runtime.snapshot import RuntimeSnapshot
        from gui.widgets.video_preview import PreviewRenderContext, VideoPreviewWidget
        from tests.qt_harness import ensure_app

        ensure_app()
        appdata_patch = mock.patch.dict(
            os.environ, {"APPDATA": str(self.appdata)}
        )
        appdata_patch.start()
        self.addCleanup(appdata_patch.stop)
        override = default_vs_runtime_user_path()
        self.assertEqual(
            override,
            self.appdata
            / "ArknightsPassMaker"
            / "vapoursynth"
            / "vs_runtime.user.json",
        )
        override.parent.mkdir(parents=True)

        def write_runtime(*, threads: int, cache: int, frame_timeout: int) -> None:
            override.write_text(
                json.dumps(
                    {
                        "core": {
                            "num_threads": threads,
                            "max_cache_size_mb": cache,
                        },
                        "worker": {
                            "startup_timeout_ms": 15_000,
                            "frame_timeout_ms": frame_timeout,
                            "shutdown_timeout_ms": 3_000,
                        },
                    }
                ),
                encoding="utf-8",
            )

        def selection_for(script: Path) -> ScriptSelection:
            header = parse_script_header(script)
            return ScriptSelection.from_header(
                script, header, compute_script_bundle_hash(script)
            )

        def wait_for(predicate, message: str) -> None:
            from PyQt6.QtCore import QCoreApplication

            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                QCoreApplication.processEvents()
                if predicate():
                    return
                time.sleep(0.01)
            self.fail(message)

        write_runtime(threads=1, cache=101, frame_timeout=10_001)
        snapshot_a = RuntimeSnapshot.resolve(ROOT)
        script_a = self._write_script(
            "磁盘快照A",
            _valid_script(
                compatible=False,
                extra=(
                    "assert core.num_threads == 1, core.num_threads\n"
                    "assert core.max_cache_size == 101, core.max_cache_size\n"
                ),
            ),
        )
        crash = self._write_script(
            "磁盘快照A崩溃",
            "# assetmaker-api: 1\n# assetmaker-mode: raw\n"
            "# assetmaker-capabilities: source\n# assetmaker-requires:\n"
            "# assetmaker-editor-output: 0\n\nimport os\nos._exit(23)\n",
        )
        script_b = self._write_script(
            "磁盘快照B",
            _valid_script(
                compatible=False,
                extra=(
                    "assert core.num_threads == 2, core.num_threads\n"
                    "assert core.max_cache_size == 202, core.max_cache_size\n"
                ),
            ),
        )
        media = self.root / "source.mp4"
        media.touch()
        widget = VideoPreviewWidget()
        self.addCleanup(lambda: widget.clear(sync_shutdown=True))
        loaded = []
        failures = []
        widget.video_loaded.connect(lambda *_: loaded.append(True))
        widget.load_failed.connect(failures.append)
        widget.set_render_context(
            PreviewRenderContext(
                project_root=str(self.root),
                track="loop",
                selection=selection_for(script_a),
                cache_dir=str(self.root / "widget-cache-a"),
                runtime_snapshot=snapshot_a,
            )
        )
        self.assertTrue(widget.load_video(str(media)))
        wait_for(lambda: bool(loaded) or bool(failures), "A worker 未返回 metadata")
        self.assertEqual(failures, [])
        client_a = widget._worker_client
        self.assertIsNotNone(client_a)
        self.assertEqual(client_a.worker_config.frame_timeout_ms, 10_001)
        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline:
            from PyQt6.QtCore import QCoreApplication

            QCoreApplication.processEvents()
            time.sleep(0.01)
        widget._job_debounce.stop()
        session_a = widget.current_render_session()
        self.assertIsNotNone(session_a)
        self.assertGreater(session_a.epoch, 1)
        self.assertEqual(session_a.runtime_fingerprint, snapshot_a.fingerprint)

        # B 已在真实默认用户覆盖路径落盘；此后 restart 绝不可重新 resolve。
        write_runtime(threads=2, cache=202, frame_timeout=20_002)

        crash_job = self.root / "crash-job.json"
        _write_job(crash_job, epoch=99)
        crash_session = _session(
            crash, crash_job, epoch=99, runtime=snapshot_a.runtime
        )
        crashed = []
        client_a.worker_crashed.connect(crashed.append)
        client_a.load(crash_session)
        wait_for(lambda: bool(crashed), "A child 崩溃未到达 production client")
        wait_for(
            lambda: (
                not client_a.transport.alive
                and not client_a.transport.settling
            ),
            "A child 崩溃后 transport 未完成退休",
        )
        widget.restart_rendering()
        wait_for(
            lambda: (
                widget._worker_ready_for_frames
                and widget.current_render_session() is not None
                and widget.current_render_session().runtime_fingerprint
                == snapshot_a.fingerprint
            ),
            "A child 重启后未重放原 session",
        )
        self.assertEqual(
            widget.current_render_session(), session_a
        )
        self.assertEqual(client_a.worker_config.frame_timeout_ms, 10_001)

        snapshot_b = RuntimeSnapshot.resolve(ROOT)
        self.assertNotEqual(snapshot_a.fingerprint, snapshot_b.fingerprint)
        loaded.clear()
        widget.set_render_context(
            PreviewRenderContext(
                project_root=str(self.root),
                track="loop",
                selection=selection_for(script_b),
                cache_dir=str(self.root / "widget-cache-b"),
                runtime_snapshot=snapshot_b,
            )
        )
        self.assertTrue(widget.load_video(str(media)))
        wait_for(lambda: bool(loaded) or bool(failures), "B worker 未返回 metadata")
        self.assertEqual(failures, [])
        session_b = widget.current_render_session()
        client_b = widget._worker_client
        self.assertIsNot(client_a, client_b)
        self.assertEqual(session_b.runtime_fingerprint, snapshot_b.fingerprint)
        self.assertEqual(client_b.worker_config.frame_timeout_ms, 20_002)

    def test_os_exit_23_only_kills_child_and_same_transport_restarts(self):
        root_record = self.root / "crash-generation-root.txt"
        crash = self._write_script(
            "崩溃脚本",
            "# assetmaker-api: 1\n"
            "# assetmaker-mode: raw\n"
            "# assetmaker-capabilities: source\n"
            "# assetmaker-requires:\n"
            "# assetmaker-editor-output: 0\n\n"
            "import os\n"
            "from pathlib import Path\n"
            f"Path({str(root_record)!r}).write_text("
            f"os.environ.get({GENERATION_STAGING_ROOT_ENV!r}, ''), encoding='utf-8')\n"
            "os._exit(23)\n",
        )
        valid = self._write_script("重启脚本", _valid_script(compatible=False))
        self.client.start(timeout_ms=15_000)
        generation = self.client.generation

        with self.assertRaises(WorkerCrashedError) as raised:
            self.client.load(self._session(crash), timeout_ms=15_000)

        self.assertEqual(raised.exception.exit_code, 23)
        self.assertNotIn("vapoursynth", sys.modules)
        self.assertNotIn("core.vs_engine", sys.modules)
        self.assertTrue(root_record.is_file())
        crashed_root_text = root_record.read_text(encoding="utf-8")
        self.assertTrue(crashed_root_text)
        crashed_root = Path(crashed_root_text)
        self.assertFalse(crashed_root.exists())
        self.client.terminate_and_restart(timeout_ms=15_000)
        self.assertGreater(self.client.generation, generation)
        metadata = self.client.load(self._session(valid), timeout_ms=15_000)
        self.assertEqual(metadata.epoch, 3)

    def test_cancel_keeps_three_slots_until_late_callbacks_finish(self):
        unblock = self.root / "unblock.marker"
        arm = self.root / "arm.marker"
        script = self._write_script(
            "延迟回调",
            _valid_script(
                compatible=False,
                extra=(
                    "import time\n"
                    "from pathlib import Path\n"
                    f"UNBLOCK = Path({str(unblock)!r})\n"
                    f"ARM = Path({str(arm)!r})\n"
                    "def wait_for_test(n, f):\n"
                    "    deadline = time.monotonic() + 20\n"
                    "    if ARM.exists():\n"
                    "        while not UNBLOCK.exists() and time.monotonic() < deadline:\n"
                    "            time.sleep(0.01)\n"
                    "    return f\n"
                    "base = core.std.ModifyFrame(\n"
                    "    clip=base, clips=base, selector=wait_for_test\n"
                    ")\n"
                ),
            ),
        )
        self.client.start(timeout_ms=15_000)
        self.client.load(self._session(script), timeout_ms=15_000)
        arm.touch()
        request_ids = [
            self.client.transport.request_frame(
                epoch=3,
                index=index,
                surface="final",
                viewport=(384, 640),
                zoom_factor=1.0,
                pan=(0.5, 0.5),
            )
            for index in range(3)
        ]
        generations = {
            self.client.transport._pending[request_id].slot.descriptor.generation
            for request_id in request_ids
        }
        self.assertEqual(len(generations), 3)
        self.assertIsNone(
            self.client.transport.request_frame(
                epoch=3,
                index=2,
                surface="final",
                viewport=(384, 640),
                zoom_factor=1.0,
                pan=(0.5, 0.5),
                coalesce=True,
            )
        )
        self.assertEqual(self.client.transport.pending_frame_count(), 3)
        self.client.cancel_epoch(3, timeout_ms=10_000)
        self.assertEqual(self.client.transport.pending_frame_count(), 3)
        self.assertIsNone(self.client.transport._coalesced_frame)

        unblock.touch()
        terminals = [
            self.client._wait_for(request_id, 15_000)["type"]
            for request_id in request_ids
        ]
        self.assertEqual(terminals, ["frame_discarded"] * 3)
        self.assertEqual(self.client.transport.pending_frame_count(), 0)

    def test_none_exception_notes_keep_same_worker_available_after_script_error(self):
        invalid = self._write_script(
            "异常 notes 为空",
            (
                "# assetmaker-api: 1\n"
                "# assetmaker-mode: raw\n"
                "# assetmaker-capabilities: source\n"
                "# assetmaker-requires:\n"
                "# assetmaker-editor-output: 0\n\n"
                "error = RuntimeError('ordinary script failure')\n"
                "error.__notes__ = None\n"
                "raise error\n"
            ),
        )
        valid = self._write_script(
            "异常后恢复", _valid_script(compatible=False)
        )
        self.client.start(timeout_ms=15_000)
        pid = self.client.pid

        with self.assertRaises(WorkerRequestError) as raised:
            self.client.load(self._session(invalid), timeout_ms=15_000)

        self.assertEqual(raised.exception.response["type"], "script_error")
        try:
            metadata = self.client.load(self._session(valid), timeout_ms=15_000)
        except Exception as error:
            self.fail(f"普通 script_error 后同一 worker 应可继续 load：{error!r}")
        self.assertEqual(metadata.epoch, 3)
        self.assertEqual(self.client.pid, pid)
        self.assertTrue(self.client.transport.alive)

    def test_retirement_failure_from_close_kills_worker_before_reload(self):
        poison = (
            "import sys, types\n"
            "class Poison(types.ModuleType):\n"
            "    def __delattr__(self, name):\n"
            "        raise RuntimeError('refuse retirement')\n"
            "parent = Poison('assetmaker_poison')\n"
            "parent.__file__ = str(__import__('pathlib').Path(__file__).parent.parent / 'external.py')\n"
            "child = types.ModuleType('assetmaker_poison.child')\n"
            "child.__file__ = __file__\n"
            "parent.child = child\n"
            "sys.modules[parent.__name__] = parent\n"
            "sys.modules[child.__name__] = child\n"
        )
        script = self._write_script(
            "退休失败", _valid_script(compatible=False, extra=poison)
        )
        valid = self._write_script("退休后重建", _valid_script(compatible=False))
        self.client.start(timeout_ms=15_000)
        self.client.load(self._session(script), timeout_ms=15_000)
        generation = self.client.generation

        with self.assertRaises(WorkerRequestError) as raised:
            self.client.unload(timeout_ms=15_000)
        self.assertEqual(raised.exception.code, "executor.retirement_failed")
        self.assertNotEqual(self.client.wait(timeout_ms=10_000), 0)

        self.client.terminate_and_restart(timeout_ms=15_000)
        self.assertGreater(self.client.generation, generation)
        self.client.load(self._session(valid), timeout_ms=15_000)

    def test_script_failure_cleanup_retirement_note_also_kills_worker(self):
        poison_then_raise = (
            "# assetmaker-api: 1\n"
            "# assetmaker-mode: raw\n"
            "# assetmaker-capabilities: source\n"
            "# assetmaker-requires:\n"
            "# assetmaker-editor-output: 0\n\n"
            "import sys, types\n"
            "class Poison(types.ModuleType):\n"
            "    def __delattr__(self, name):\n"
            "        raise RuntimeError('refuse retirement')\n"
            "parent = Poison('assetmaker_cleanup_poison')\n"
            "parent.__file__ = str(__import__('pathlib').Path(__file__).parent.parent / 'external.py')\n"
            "child = types.ModuleType('assetmaker_cleanup_poison.child')\n"
            "child.__file__ = __file__\n"
            "parent.child = child\n"
            "sys.modules[parent.__name__] = parent\n"
            "sys.modules[child.__name__] = child\n"
            "raise RuntimeError('primary script failure')\n"
        )
        script = self._write_script("清理失败", poison_then_raise)
        self.client.start(timeout_ms=15_000)
        generation = self.client.generation

        with self.assertRaises(WorkerRequestError) as raised:
            self.client.load(self._session(script), timeout_ms=15_000)

        self.assertEqual(raised.exception.response["type"], "script_error")
        self.assertIn("executor.retirement_failed", raised.exception.response["traceback"])
        self.assertNotEqual(self.client.wait(timeout_ms=10_000), 0)
        self.client.terminate_and_restart(timeout_ms=15_000)
        self.assertGreater(self.client.generation, generation)
        self.assertNotIn("vapoursynth", sys.modules)
        self.assertNotIn("core.vs_engine", sys.modules)

    def test_script_failure_environment_restore_error_also_kills_worker(self):
        poison_then_raise = (
            "# assetmaker-api: 1\n"
            "# assetmaker-mode: raw\n"
            "# assetmaker-capabilities: source\n"
            "# assetmaker-requires:\n"
            "# assetmaker-editor-output: 0\n\n"
            "import sys\n"
            "sys.path = object()\n"
            "raise RuntimeError('primary script failure')\n"
        )
        script = self._write_script("环境恢复失败", poison_then_raise)
        self.client.start(timeout_ms=15_000)

        with self.assertRaises(WorkerRequestError) as raised:
            self.client.load(self._session(script), timeout_ms=15_000)

        self.assertEqual(raised.exception.response["type"], "script_error")
        self.assertIn("脚本清理阶段另有异常", raised.exception.response["traceback"])
        self.assertNotEqual(self.client.wait(timeout_ms=3_000), 0)

    def test_plane_digest_ignores_real_r73_stride_padding(self):
        payload = json.loads(self.job.read_text(encoding="utf-8"))
        payload["output"].update(
            {
                "profile": "720x1080",
                "display_width": 720,
                "display_height": 1080,
                "coded_width": 720,
                "coded_height": 1080,
            }
        )
        self.job.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        script = self._write_script(
            "R73步长",
            _valid_script(compatible=False).replace(
                "width=384, height=640", "width=720, height=1080"
            ),
        )
        self.client.start(timeout_ms=15_000)
        self.client.load(self._session(script), timeout_ms=20_000)

        digests = self.client.request_plane_digest(
            epoch=3, index=0, timeout_ms=15_000
        )

        expected = {
            "Y": hashlib.sha256(bytes([81]) * (720 * 1080)).hexdigest(),
            "U": hashlib.sha256(bytes([90]) * (360 * 540)).hexdigest(),
            "V": hashlib.sha256(bytes([240]) * (360 * 540)).hexdigest(),
        }
        self.assertEqual(digests, expected)

    def test_drain_timeout_exits_without_closing_live_graph(self):
        override = (
            self.appdata
            / "ArknightsPassMaker"
            / "vapoursynth"
            / "vs_runtime.user.json"
        )
        override.parent.mkdir(parents=True)
        override.write_text(
            json.dumps({"worker": {"shutdown_timeout_ms": 200}}),
            encoding="utf-8",
        )
        self.runtime = load_vs_runtime(
            ROOT / "config" / "vs_runtime.json", override
        )
        arm = self.root / "drain-arm.txt"
        entered = self.root / "drain-entered.txt"
        retired = self.root / "retirement-attempted.txt"
        root_record = self.root / "drain-generation-root.txt"
        extra = (
            "import os, sys, time, types\n"
            "from pathlib import Path\n"
            f"ARM = Path({str(arm)!r})\n"
            f"ENTERED = Path({str(entered)!r})\n"
            f"RETIRED = Path({str(retired)!r})\n"
            f"Path({str(root_record)!r}).write_text("
            f"os.environ.get({GENERATION_STAGING_ROOT_ENV!r}, ''), encoding='utf-8')\n"
            "class ObserveRetirement(types.ModuleType):\n"
            "    def __delattr__(self, name):\n"
            "        RETIRED.write_text(name, encoding='utf-8')\n"
            "        return super().__delattr__(name)\n"
            "parent = ObserveRetirement('assetmaker_drain_parent')\n"
            "parent.__file__ = str(Path(__file__).parent.parent / 'external.py')\n"
            "child = types.ModuleType('assetmaker_drain_parent.child')\n"
            "child.__file__ = __file__\n"
            "parent.child = child\n"
            "sys.modules[parent.__name__] = parent\n"
            "sys.modules[child.__name__] = child\n"
            "def block_after_load(n, f):\n"
            "    if ARM.exists():\n"
            "        ENTERED.write_text('entered', encoding='utf-8')\n"
            "        while True:\n"
            "            time.sleep(1)\n"
            "    return f\n"
            "base = core.std.ModifyFrame(\n"
            "    clip=base, clips=base, selector=block_after_load\n"
            ")\n"
        )
        script = self._write_script(
            "排空超时", _valid_script(compatible=False, extra=extra)
        )
        self.client.start(timeout_ms=15_000)
        self.client.load(self._session(script), timeout_ms=20_000)
        arm.touch()
        frame_request = self.client.transport.request_frame(
            epoch=3,
            index=0,
            surface="final",
            viewport=(384, 640),
            zoom_factor=1.0,
            pan=(0.5, 0.5),
        )
        assert frame_request is not None
        descriptor = self.client.transport._pending[frame_request].slot.descriptor
        deadline = time.monotonic() + 10
        while not entered.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(entered.exists(), "真实 VS callback 未进入阻塞点")

        unload = self.client.transport.send_request({"type": "unload"})
        with self.assertRaises(WorkerRequestError) as raised:
            self.client._wait_for(unload, 10_000)

        self.assertEqual(raised.exception.code, "worker.drain_timeout")
        self.assertEqual(self.client.wait(timeout_ms=10_000), 71)
        self.assertFalse(retired.exists())
        self.assertTrue(root_record.is_file())
        drained_root_text = root_record.read_text(encoding="utf-8")
        self.assertTrue(drained_root_text)
        drained_root = Path(drained_root_text)
        self.assertFalse(drained_root.exists())
        from core.vs_runtime.shared_frame import FrameSlot

        with self.assertRaises(FileNotFoundError):
            FrameSlot.open(descriptor)


if __name__ == "__main__":
    unittest.main()
