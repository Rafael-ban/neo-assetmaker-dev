"""固定 VSPipe 启动器：加载用户脚本并注册通过契约的 output 0。"""

import hashlib
import os
import sys


# 必须先于任何 assetmaker_vs import：clean runtime 不写解释器派生缓存，避免
# 自身缓存成为 fingerprint 输入。worker 已以 -B 启动；这里显式保持同一约束。
sys.dont_write_bytecode = True


# Python 输出永远走 stderr；VSPipe 的 C 层 Y4M stdout 不受此替换影响。
sys.stdout = sys.stderr

from pathlib import Path

import vapoursynth as vs


_root = Path(__file__).resolve().parent
_helper_root = str(_root / "python")
if _helper_root not in sys.path:
    sys.path.insert(0, _helper_root)

from assetmaker_vs.contract import (  # noqa: E402
    validate_outputs,
    verify_required_callables,
)
from assetmaker_vs.executor import execute_user_script  # noqa: E402
from assetmaker_vs.job_api import (  # noqa: E402
    load_job,
    runtime_python_dirs_from_env,
)
from assetmaker_vs.runtime_fingerprint import (  # noqa: E402
    RUNTIME_APP_DIR_ENV,
    verify_runtime_from_env,
)
from assetmaker_vs.core_resources import (  # noqa: E402
    CoreResourceBaseline,
    restore_core_resources,
)
from assetmaker_vs.native_plugins import configure_native_plugins, verify_native_plugin_requirements  # noqa: E402
from assetmaker_vs.script_header import (  # noqa: E402
    parse_script_header,
    validate_invocation,
)


def _verify_job_content(stage):
    """VSPipe 只执行导出会话冻结的 job 字节，绝不按路径盲读。"""
    try:
        expected = expected_job_sha256
    except NameError as exc:
        raise RuntimeError("缺少 expected_job_sha256 VSPipe 参数") from exc
    if type(expected) is not str or len(expected) != 64 or any(
        character not in "0123456789abcdef" for character in expected
    ):
        raise RuntimeError("expected_job_sha256 必须是小写 SHA-256")
    try:
        actual = hashlib.sha256(Path(assetmaker_job).read_bytes()).hexdigest()
    except OSError as exc:
        raise RuntimeError(f"{stage}：冻结 job 无法读取") from exc
    if actual != expected:
        raise RuntimeError(f"{stage}：冻结 job 内容 hash 已变化")


# 在用户脚本和滤镜图执行前，复核 VSPipe 实际继承的 runtime 身份，并按与
# worker loader 相同的语义应用非零 core 限制；0 保留 VapourSynth 默认值。
verified_runtime = verify_runtime_from_env()
core_config = verified_runtime.runtime["core"]
core_baseline = CoreResourceBaseline.capture(vs.core)
restore_core_resources(vs.core, core_config, core_baseline)
native_plugin_state = configure_native_plugins(
    vs.core,
    verified_runtime.runtime["plugins"]["native_plugin_dirs"],
    builtin_plugin_dirs=(
        Path(os.environ[RUNTIME_APP_DIR_ENV]) / "tools" / "media" / "vs-plugins",
        Path(os.environ[RUNTIME_APP_DIR_ENV]) / "tools" / "media" / "vs-coreplugins",
    ),
)
header = parse_script_header(assetmaker_script)
_verify_job_content("读取 job 前")
job = load_job(assetmaker_job, for_export=True)
_verify_job_content("读取 job 后")
validate_invocation(
    header,
    api_version=assetmaker_api,
    mode=assetmaker_mode,
)
verify_native_plugin_requirements(vs.core, native_plugin_state, header["requires"])
verify_required_callables(vs.core, header["requires"])
_verify_job_content("执行用户脚本前")
graph = execute_user_script(
    script_path=assetmaker_script,
    job_path=assetmaker_job,
    api_version=assetmaker_api,
    mode=assetmaker_mode,
    python_module_dirs=runtime_python_dirs_from_env(),
)
_verify_job_content("执行用户脚本后")
validated = validate_outputs(vs, job, header)
validated.guarded_clip.set_output(0)
