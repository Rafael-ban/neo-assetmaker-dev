"""F3-P1：运行配置与执行资产必须在一个不可变边界冻结。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


class RuntimeSnapshotTests(unittest.TestCase):
    def test_resolve_uses_selected_app_distribution_and_existing_user_override(self):
        """改掉 shipped/user 任一输入，下一次显式 resolve 才得到新快照。"""
        from core.vs_runtime.snapshot import RuntimeSnapshot

        with tempfile.TemporaryDirectory() as temporary:
            override = Path(temporary) / "vs_runtime.user.json"
            override.write_text(
                json.dumps(
                    {
                        "worker": {
                            "startup_timeout_ms": 1234,
                            "frame_timeout_ms": 2345,
                            "shutdown_timeout_ms": 3456,
                        },
                        "core": {"num_threads": 1},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            first = RuntimeSnapshot.resolve(ROOT, user_path=override)
            override.write_text(
                json.dumps({"worker": {"frame_timeout_ms": 6789}}),
                encoding="utf-8",
            )
            second = RuntimeSnapshot.resolve(ROOT, user_path=override)

        self.assertEqual(first.app_dir, str(ROOT.resolve()))
        self.assertEqual(first.runtime.worker.frame_timeout_ms, 2345)
        self.assertEqual(first.runtime.core.num_threads, 1)
        self.assertNotEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(second.runtime.worker.frame_timeout_ms, 6789)

    def test_worker_environment_is_complete_and_rejects_tampered_identity(self):
        """worker 只能接收完整 canonical runtime，而不是父进程的一段字符串。"""
        from core.vs_runtime.snapshot import RuntimeSnapshot
        from resources.vapoursynth.python.assetmaker_vs.runtime_fingerprint import (
            RuntimeFingerprintError,
            verify_runtime_from_env,
        )

        snapshot = RuntimeSnapshot.resolve(ROOT)
        environment = snapshot.worker_environment({"APPDATA": "snapshot-test"})
        verified = verify_runtime_from_env(environment)
        self.assertEqual(verified.fingerprint, snapshot.fingerprint)
        self.assertEqual(
            verified.runtime["worker"]["frame_timeout_ms"],
            snapshot.runtime.worker.frame_timeout_ms,
        )

        altered = dict(environment)
        runtime = json.loads(altered["ASSETMAKER_VS_RUNTIME_CONFIG_JSON"])
        runtime["worker"]["frame_timeout_ms"] += 1
        altered["ASSETMAKER_VS_RUNTIME_CONFIG_JSON"] = json.dumps(
            runtime, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        with self.assertRaisesRegex(RuntimeFingerprintError, "fingerprint_mismatch"):
            verify_runtime_from_env(altered)

    def test_direct_worker_process_derives_complete_environment_from_snapshot(self):
        """兼容的直接 WorkerProcess 调用也必须传入完整而非部分 runtime。"""
        from core.vs_runtime.snapshot import RuntimeSnapshot
        from core.vs_runtime.worker_process import WorkerProcess

        snapshot = RuntimeSnapshot.resolve(ROOT)
        process = WorkerProcess(
            app_dir=ROOT,
            command=["C:/worker.exe"],
            env=snapshot.worker_environment({"APPDATA": "worker-test"}),
        )

        self.assertEqual(
            process.env["ASSETMAKER_VS_RUNTIME_FINGERPRINT"],
            snapshot.fingerprint,
        )
        self.assertEqual(
            process.env["ASSETMAKER_VS_RUNTIME_CONFIG_JSON"],
            snapshot.worker_environment()["ASSETMAKER_VS_RUNTIME_CONFIG_JSON"],
        )

    def test_worker_server_rejects_missing_snapshot_environment_before_hello(self):
        """绕开 WorkerProcess 直接启动 child 也不能退回从磁盘重读配置。"""
        from core.vs_runtime.protocol import ProtocolError
        from core.vs_runtime.worker_main import ProtocolWriter, WorkerServer

        with mock.patch.dict(
            "os.environ", {"APPDATA": str(ROOT),
                           "ASSETMAKER_VS_RUNTIME_CONFIG_JSON": "{}"}, clear=True
        ):
            with self.assertRaisesRegex(ProtocolError, "runtime") as raised:
                WorkerServer(
                    writer=ProtocolWriter(mock.Mock()),
                    app_dir=ROOT,
                    self_test=True,
                    generation_staging=mock.Mock(),
                )

        self.assertEqual(raised.exception.code, "worker.runtime_environment")


if __name__ == "__main__":
    unittest.main()
