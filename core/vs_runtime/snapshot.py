"""F3-P1 的不可变运行配置快照。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from config.vs_runtime import (
    VSRuntimeConfig,
    default_vs_runtime_user_path,
    load_vs_runtime,
)
from core.vs_runtime.vs_loader import compute_runtime_fingerprint
from resources.vapoursynth.python.assetmaker_vs.runtime_fingerprint import (
    PYTHON_DIRS_ENV,
    RUNTIME_APP_DIR_ENV,
    RUNTIME_CONFIG_ENV,
    RUNTIME_FINGERPRINT_ENV,
    RUNTIME_MEDIA_ROOT_ENV,
    canonical_runtime_json_bytes,
    verify_runtime_from_env,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SNAPSHOT_ENV_KEYS = frozenset({
    RUNTIME_APP_DIR_ENV, RUNTIME_CONFIG_ENV, RUNTIME_FINGERPRINT_ENV,
    RUNTIME_MEDIA_ROOT_ENV, PYTHON_DIRS_ENV, "VAPOURSYNTH_EXTRA_PLUGIN_PATH",
})


@dataclass(frozen=True)
class RuntimeSnapshot:
    """一次显式配置应用所使用的完整 worker runtime 身份。"""

    app_dir: str
    runtime: VSRuntimeConfig
    fingerprint: str

    def __post_init__(self) -> None:
        root = Path(self.app_dir)
        if not root.is_absolute():
            raise ValueError("app_dir 必须是绝对路径")
        if not isinstance(self.runtime, VSRuntimeConfig):
            raise TypeError("runtime 必须是 VSRuntimeConfig")
        if not isinstance(self.fingerprint, str) or not _SHA256_RE.fullmatch(
            self.fingerprint
        ):
            raise ValueError("fingerprint 必须是小写 SHA-256")
        object.__setattr__(self, "app_dir", str(root.resolve()))
        # dataclass 构造本身不校验 schema，入口必须走现有严格模型。
        object.__setattr__(
            self, "runtime", VSRuntimeConfig.from_dict(self.runtime.to_dict())
        )

    @classmethod
    def from_environment(
        cls, app_dir: str | os.PathLike[str], environment: Mapping[str, str]
    ) -> "RuntimeSnapshot":
        """child 重新校验完整环境、schema 和实际执行资产，绝不重读配置。"""
        missing = SNAPSHOT_ENV_KEYS.difference(environment)
        if missing:
            raise ValueError(f"runtime 环境缺失: {', '.join(sorted(missing))}")
        supplied_root = Path(environment[RUNTIME_APP_DIR_ENV])
        if not supplied_root.is_absolute() or supplied_root.resolve() != Path(app_dir).resolve():
            raise ValueError("runtime app_dir 与 worker 执行目录冲突")
        runtime = VSRuntimeConfig.from_dict(json.loads(environment[RUNTIME_CONFIG_ENV]))
        verified = verify_runtime_from_env(environment)
        return cls(str(supplied_root), runtime, verified.fingerprint)

    @classmethod
    def resolve(
        cls,
        app_dir: str | os.PathLike[str],
        *,
        user_path: str | os.PathLike[str] | None = None,
    ) -> "RuntimeSnapshot":
        """在显式边界读取分发配置及现有用户覆盖并计算资产身份。"""
        root = Path(app_dir).resolve()
        runtime = load_vs_runtime(
            root / "config" / "vs_runtime.json",
            default_vs_runtime_user_path() if user_path is None else user_path,
        )
        return cls(
            app_dir=str(root),
            runtime=runtime,
            fingerprint=compute_runtime_fingerprint(root, runtime),
        )

    def worker_environment(
        self, base_environment: Mapping[str, str] | None = None
    ) -> dict[str, str]:
        """构建 worker 唯一可接受的完整 runtime 环境。"""
        env = dict(os.environ if base_environment is None else base_environment)
        python_dirs = [
            str(Path(path)) for path in self.runtime.plugins.python_module_dirs
        ]
        # R79 不能从多目录环境变量可靠加载；worker 在 import 后共享显式策略。
        env["VAPOURSYNTH_EXTRA_PLUGIN_PATH"] = ""
        env[PYTHON_DIRS_ENV] = json.dumps(
            python_dirs, ensure_ascii=False, separators=(",", ":")
        )
        env[RUNTIME_APP_DIR_ENV] = self.app_dir
        env[RUNTIME_CONFIG_ENV] = canonical_runtime_json_bytes(
            self.runtime.to_dict()
        ).decode("utf-8")
        env[RUNTIME_FINGERPRINT_ENV] = self.fingerprint
        env[RUNTIME_MEDIA_ROOT_ENV] = str(Path(self.app_dir) / "tools" / "media")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env


__all__ = ["RuntimeSnapshot"]
