"""仅测试：在真实 worker 退休边界发出确定性文件握手。"""

from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path


protocol_owner = sys.stdout
protocol_stream = protocol_owner.buffer
sys.stdout = sys.stderr
sys.dont_write_bytecode = True
app_dir = Path(os.environ["TASK3B_APP_DIR"]).resolve()
sys.path.insert(0, str(app_dir))

from core.vs_runtime.worker_main import WorkerServer, main  # noqa: E402
from core.vs_runtime import vs_loader  # noqa: E402


_active_load_epoch: int | None = None


def _marker(name: str) -> Path:
    return Path(os.environ[name])


def _record_event(event: str, *, epoch: int | None = None, **values) -> None:
    payload = {"event": event, "epoch": epoch, **values}
    with _marker("TASK3B_EVENT_LOG").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _install_retirement_observer() -> None:
    target_epoch = int(os.environ["TASK3B_TARGET_EPOCH"])
    original_init = WorkerServer.__init__

    def observed_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        _marker("TASK3B_HARNESS_READY").write_text("ready", encoding="utf-8")
        _record_event(
            "harness_ready",
            loader_file=str(Path(vs_loader.__file__).resolve()),
        )
        original_handle = self.handle

        def observed_handle(message):
            global _active_load_epoch

            previous_epoch = _active_load_epoch
            if message.get("type") == "load":
                _active_load_epoch = message.get("epoch")
            if (
                message.get("type") == "load"
                and message.get("epoch") != target_epoch
            ):
                _marker("TASK3B_LOAD_RECEIVED").write_text(
                    "received", encoding="utf-8"
                )
                _record_event("load_received", epoch=message.get("epoch"))
            try:
                return original_handle(message)
            finally:
                _active_load_epoch = previous_epoch

        self.handle = observed_handle
        original_wait = self._condition.wait

        def observed_wait(timeout=None):
            loaded = self._loaded
            if (
                loaded is not None
                and loaded.epoch == target_epoch
                and any(
                    frame.epoch == target_epoch
                    for frame in self._frames.values()
                )
            ):
                _marker("TASK3B_WAIT_ENTERED").write_text(
                    "waiting", encoding="utf-8"
                )
                _record_event("retire_wait_entered", epoch=target_epoch)
            return original_wait(timeout)

        self._condition.wait = observed_wait
        original_retire = self._retire_current

        def observed_retire(_self, *retire_args, **retire_kwargs):
            loaded = self._loaded
            if loaded is not None and loaded.epoch == target_epoch:
                _marker("TASK3B_RETIRE_CALLED").write_text(
                    "called", encoding="utf-8"
                )
                _record_event("retire_called", epoch=target_epoch)
                if os.environ.get("TASK3B_FORCE_EARLY_RESTORE") == "1":
                    vs_loader.restore_vapoursynth_resources(
                        self._vs, self.runtime
                    )
                    _record_event(
                        "forced_early_restore", epoch=_active_load_epoch
                    )
            result = original_retire(*retire_args, **retire_kwargs)
            if loaded is not None and loaded.epoch == target_epoch:
                _marker("TASK3B_RETIRE_DONE").write_text(
                    "retired", encoding="utf-8"
                )
                _record_event("retire_done", epoch=target_epoch)
            return result

        self._retire_current = types.MethodType(observed_retire, self)

    WorkerServer.__init__ = observed_init


def _install_restore_observer() -> None:
    original_restore = vs_loader.restore_vapoursynth_resources

    def observed_restore(module, runtime):
        before = [module.core.num_threads, module.core.max_cache_size]
        result = original_restore(module, runtime)
        after = [module.core.num_threads, module.core.max_cache_size]
        epoch = _active_load_epoch
        payload = {
            "event": "restore",
            "epoch": epoch,
            "before": before,
            "after": after,
            "module_file": getattr(module, "__file__", None),
        }
        with _marker("TASK3B_RESTORE_OBSERVED").open(
            "a", encoding="utf-8"
        ) as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        _record_event("restore", epoch=epoch, before=before, after=after)
        return result

    vs_loader.restore_vapoursynth_resources = observed_restore


_install_retirement_observer()
_install_restore_observer()
result = main(protocol_stream=protocol_stream, app_dir=os.environ["TASK3B_APP_DIR"])
del protocol_owner
if result:
    os._exit(result)
