"""在全新解释器中运行会初始化真实 VapourSynth core 的测试 case。"""

from __future__ import annotations

import hashlib
import gc
import importlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import types
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# A shared virtual environment can already expose ROOT after a competing
# project.  File-entry imports must always resolve this helper's own checkout.
sys.path.insert(0, str(ROOT))

from core.vs_runtime.job import RationalFPS


HELPER_ROOT = ROOT / "resources" / "vapoursynth" / "python"
DEFAULT_PIPELINE = ROOT / "resources" / "vapoursynth" / "default_pipeline.vpy"
RUNNER = ROOT / "resources" / "vapoursynth" / "assetmaker_runner.vpy"


def _import_origin_case() -> dict[str, str]:
    """Expose imports chosen by this file-entry process for a parent test."""
    from config import vs_runtime
    from core.vs_runtime import vs_loader
    from resources.vapoursynth.python.assetmaker_vs import runtime_fingerprint

    return {
        "root": str(ROOT.resolve()),
        "config": str(Path(vs_runtime.__file__).resolve()),
        "loader": str(Path(vs_loader.__file__).resolve()),
        "portable": str(Path(runtime_fingerprint.__file__).resolve()),
    }


def _emit(payload: dict[str, object], *, exit_code: int = 0) -> None:
    # This child speaks a JSON protocol to its parent.  GitHub's Windows
    # Git-Bash worker can expose sys.__stdout__ as cp1252, which cannot encode
    # our Chinese diagnostics.  ASCII-escaped JSON is valid JSON on every text
    # stream and json.loads() in the parent restores the original Unicode.
    sys.__stdout__.write(json.dumps(payload, ensure_ascii=True) + "\n")
    sys.__stdout__.flush()
    raise SystemExit(exit_code)


def _load_vs():
    from config.vs_runtime import load_vs_runtime
    from core.vs_runtime.vs_loader import load_vapoursynth

    runtime = load_vs_runtime(
        ROOT / "config" / "vs_runtime.json",
        ROOT / "tests" / "fixtures" / ".vs_runtime.user.json",
    )
    return load_vapoursynth(ROOT, runtime)


def _executor_deferred_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    vs = _load_vs()
    from assetmaker_vs.executor import (
        execute_user_script,
        install_python_stdout,
    )

    log_lines: list[str] = []
    install_python_stdout(log_lines.append)
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir).resolve()
        modules = root / "modules"
        modules.mkdir()
        (modules / "marker.py").write_text(
            'VALUE = "marker-from-script-root"\n', encoding="utf-8"
        )
        (modules / "lazy_marker.py").write_text(
            'print("lazy module imported")\nVALUE = "lazy-marker"\n',
            encoding="utf-8",
        )
        script = root / "pipeline.vpy"
        script.write_bytes(
            (ROOT / "tests" / "fixtures" / "vs_scripts" / "prints_and_imports.vpy")
            .read_bytes()
        )
        job = root / "job.json"
        job.write_text("{}", encoding="utf-8")
        graph = execute_user_script(
            script_path=script,
            job_path=job,
            api_version="1",
            mode="raw",
        )
        active_during_render = str(modules) in sys.path
        output = vs.get_output(0)
        frame = output.clip.get_frame(0)
        frame.close()
        graph.close()
        active_after_close = str(modules) in sys.path
    sys.stdout = sys.__stdout__
    return {
        "logs": log_lines,
        "active_during_render": active_during_render,
        "active_after_close": active_after_close,
    }


def _executor_cross_root_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    vs = _load_vs()
    from assetmaker_vs import executor as helper_module
    from assetmaker_vs.executor import execute_user_script

    stdlib_json = sys.modules["json"]
    script_source = """import vapoursynth as vs
from marker import VALUE

marker_value = VALUE
base = vs.core.std.BlankClip(
    width=16,
    height=16,
    length=1,
    format=vs.GRAY8,
)

def deferred(n, f):
    import marker
    assert marker.VALUE == marker_value
    return f

vs.core.std.ModifyFrame(
    clip=base,
    clips=base,
    selector=deferred,
).set_output(0)
"""
    with tempfile.TemporaryDirectory() as temp_dir:
        roots = [Path(temp_dir).resolve() / name for name in ("A", "B")]
        for root, value in zip(roots, ("from-a", "from-b"), strict=True):
            modules = root / "modules"
            modules.mkdir(parents=True)
            (modules / "marker.py").write_text(
                f"VALUE = {value!r}\n", encoding="utf-8"
            )
            (root / "pipeline.vpy").write_text(
                script_source, encoding="utf-8"
            )
            (root / "job.json").write_text("{}", encoding="utf-8")

        first = execute_user_script(
            script_path=roots[0] / "pipeline.vpy",
            job_path=roots[0] / "job.json",
            api_version="1",
            mode="raw",
        )
        first_value = first.namespace["marker_value"]
        first_frame = vs.get_output(0).clip.get_frame(0)
        first_frame.close()
        active_module_path = str(Path(sys.modules["marker"].__file__).resolve())
        first.close()
        retired_after_close = "marker" not in sys.modules

        second = execute_user_script(
            script_path=roots[1] / "pipeline.vpy",
            job_path=roots[1] / "job.json",
            api_version="1",
            mode="raw",
        )
        second_value = second.namespace["marker_value"]
        second_module_path = str(Path(sys.modules["marker"].__file__).resolve())
        second.close()

    return {
        "first_value": first_value,
        "second_value": second_value,
        "active_module_path": active_module_path,
        "second_module_path": second_module_path,
        "retired_after_close": retired_after_close,
        "retired_second_after_close": "marker" not in sys.modules,
        "helper_preserved": sys.modules.get("assetmaker_vs.executor") is helper_module,
        "stdlib_preserved": sys.modules.get("json") is stdlib_json,
    }


def _executor_mixed_namespace_case(
    *,
    runtime_only: bool,
    fail_first: bool = False,
) -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    _load_vs()
    from assetmaker_vs import executor as helper_module
    from assetmaker_vs.executor import execute_user_script

    stdlib_json = sys.modules["json"]
    observer_name = "assetmaker_mixed_namespace_observer"
    observer = types.ModuleType(observer_name)
    sys.modules[observer_name] = observer
    module_names = (
        "shared_ns.local_piece",
        "shared_ns.external_piece",
        "shared_ns",
        "ordinary_external",
    )
    script_template = f"""from shared_ns import local_piece
import shared_ns
import shared_ns.external_piece as external_piece
import ordinary_external
import {observer_name} as observer

loaded = local_piece.VALUE
observer.parent = shared_ns
observer.external = external_piece
observer.local = local_piece
observer.ordinary = ordinary_external
{{failure}}
"""
    path_added = False
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir).resolve()
            third_party = base / "third_party"
            external_package = third_party / "shared_ns"
            external_package.mkdir(parents=True)
            (external_package / "external_piece.py").write_text(
                'VALUE = "external"\n', encoding="utf-8"
            )
            (third_party / "ordinary_external.py").write_text(
                'VALUE = "ordinary"\n', encoding="utf-8"
            )
            roots = [base / name for name in ("A", "B")]
            for root, value in zip(roots, ("A", "B"), strict=True):
                package = root / "modules" / "shared_ns"
                package.mkdir(parents=True)
                (package / "local_piece.py").write_text(
                    f"VALUE = {value!r}\n", encoding="utf-8"
                )
                failure = (
                    'raise RuntimeError("first script failed")'
                    if fail_first and value == "A"
                    else ""
                )
                (root / "pipeline.vpy").write_text(
                    script_template.format(failure=failure), encoding="utf-8"
                )
                (root / "job.json").write_text("{}", encoding="utf-8")

            python_module_dirs = (third_party,) if runtime_only else ()
            if not runtime_only:
                sys.path.insert(0, str(third_party))
                path_added = True
                importlib.import_module("shared_ns.external_piece")
                importlib.import_module("ordinary_external")

            first_error = None
            first = None
            try:
                first = execute_user_script(
                    script_path=roots[0] / "pipeline.vpy",
                    job_path=roots[0] / "job.json",
                    api_version="1",
                    mode="raw",
                    python_module_dirs=python_module_dirs,
                )
            except RuntimeError as exc:
                first_error = str(exc)
                if not fail_first:
                    raise

            parent = observer.parent
            external = observer.external
            ordinary = observer.ordinary
            first_value = observer.local.VALUE
            if first is not None:
                first.close()

            after_first = {
                "parent_same": sys.modules.get("shared_ns") is parent,
                "external_same": (
                    sys.modules.get("shared_ns.external_piece") is external
                ),
                "external_attr_same": (
                    getattr(parent, "external_piece", None) is external
                ),
                "ordinary_same": (
                    sys.modules.get("ordinary_external") is ordinary
                ),
                "local_cached": "shared_ns.local_piece" in sys.modules,
                "stale_local_attr": getattr(parent, "local_piece", None)
                is observer.local,
            }

            second = execute_user_script(
                script_path=roots[1] / "pipeline.vpy",
                job_path=roots[1] / "job.json",
                api_version="1",
                mode="raw",
                python_module_dirs=python_module_dirs,
            )
            second_value = second.namespace["loaded"]
            second_parent = observer.parent
            second_external = observer.external
            second_ordinary = observer.ordinary
            second_local_file = str(
                Path(observer.local.__file__).resolve()
            )
            second.close()

            return {
                "runtime_only": runtime_only,
                "first_error": first_error,
                "first_value": first_value,
                "after_first": after_first,
                "second_value": second_value,
                "second_local_file": second_local_file,
                "second_parent_same": second_parent is parent,
                "second_external_same": second_external is external,
                "second_external_attr_same": (
                    getattr(second_parent, "external_piece", None)
                    is external
                ),
                "second_ordinary_same": second_ordinary is ordinary,
                "after_second_local_cached": (
                    "shared_ns.local_piece" in sys.modules
                ),
                "after_second_stale_local_attr": (
                    getattr(second_parent, "local_piece", None)
                    is observer.local
                ),
                "helper_preserved": (
                    sys.modules.get("assetmaker_vs.executor") is helper_module
                ),
                "stdlib_preserved": sys.modules.get("json") is stdlib_json,
            }
    finally:
        if path_added:
            sys.path.remove(str(third_party))
        sys.modules.pop(observer_name, None)
        for name in module_names:
            sys.modules.pop(name, None)


def _executor_mixed_namespace_sys_path_case() -> dict[str, object]:
    return _executor_mixed_namespace_case(runtime_only=False)


def _executor_mixed_namespace_runtime_dirs_case() -> dict[str, object]:
    return _executor_mixed_namespace_case(runtime_only=True)


def _executor_mixed_namespace_failure_case() -> dict[str, object]:
    return _executor_mixed_namespace_case(runtime_only=True, fail_first=True)


def _install_fake_vapoursynth() -> types.ModuleType:
    """安装只覆盖 executor 生命周期所需表面的进程内假实现。"""
    fake = types.ModuleType("vapoursynth")
    outputs: dict[int, object] = {}
    lock = threading.Lock()
    fake.clear_calls = 0

    def clear_outputs() -> None:
        with lock:
            fake.clear_calls += 1
            outputs.clear()

    def get_outputs() -> dict[int, object]:
        with lock:
            return dict(outputs)

    fake.clear_outputs = clear_outputs
    fake.get_outputs = get_outputs
    sys.modules["vapoursynth"] = fake
    return fake


def _write_executor_lifecycle_roots(base: Path) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for name in ("A", "B", "C"):
        root = base / name
        root.mkdir(parents=True)
        (root / "pipeline.vpy").write_text(
            f"GRAPH_ID = {name!r}\n", encoding="utf-8"
        )
        (root / "failed.vpy").write_text(
            'raise RuntimeError("script failed")\n', encoding="utf-8"
        )
        (root / "job.json").write_text("{}", encoding="utf-8")
        roots[name] = root
    return roots


def _execute_lifecycle_graph(
    execute_user_script, root: Path, script: str = "pipeline.vpy"
):
    return execute_user_script(
        script_path=root / script,
        job_path=root / "job.json",
        api_version="1",
        mode="raw",
    )


def _executor_graph_overlap_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    fake_vs = _install_fake_vapoursynth()
    from assetmaker_vs.executor import execute_user_script

    with tempfile.TemporaryDirectory() as temp_dir:
        roots = _write_executor_lifecycle_roots(Path(temp_dir).resolve())
        first = _execute_lifecycle_graph(execute_user_script, roots["A"])
        active_path = list(sys.path)
        overlapping = None
        overlap_error: BaseException | None = None
        try:
            overlapping = _execute_lifecycle_graph(
                execute_user_script, roots["B"]
            )
        except BaseException as exc:
            overlap_error = exc
        finally:
            clear_calls_while_rejected = fake_vs.clear_calls
            active_path_unchanged = sys.path == active_path
            if overlapping is not None:
                overlapping.close()
            first.close()

        after_close = _execute_lifecycle_graph(
            execute_user_script, roots["B"]
        )
        after_close_value = after_close.namespace["GRAPH_ID"]
        after_close.close()

    return {
        "accepted_while_active": overlapping is not None,
        "error_type": (
            None if overlap_error is None else type(overlap_error).__name__
        ),
        "error_code": getattr(overlap_error, "code", None),
        "error_message": (
            None if overlap_error is None else str(overlap_error)
        ),
        "clear_calls_while_rejected": clear_calls_while_rejected,
        "active_path_unchanged": active_path_unchanged,
        "after_close_value": after_close_value,
    }


def _executor_graph_reuse_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    _install_fake_vapoursynth()
    from assetmaker_vs.executor import execute_user_script

    with tempfile.TemporaryDirectory() as temp_dir:
        roots = _write_executor_lifecycle_roots(Path(temp_dir).resolve())
        first = _execute_lifecycle_graph(execute_user_script, roots["A"])
        first.close()
        first.close()
        after_double_close = _execute_lifecycle_graph(
            execute_user_script, roots["B"]
        )
        after_double_close_value = after_double_close.namespace["GRAPH_ID"]
        after_double_close.close()

        failure_error = None
        try:
            _execute_lifecycle_graph(
                execute_user_script, roots["A"], "failed.vpy"
            )
        except RuntimeError as exc:
            failure_error = str(exc)
        after_failure = _execute_lifecycle_graph(
            execute_user_script, roots["C"]
        )
        after_failure_value = after_failure.namespace["GRAPH_ID"]
        after_failure.close()

    return {
        "after_double_close_value": after_double_close_value,
        "failure_error": failure_error,
        "after_failure_value": after_failure_value,
    }


def _executor_graph_abnormal_close_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    _install_fake_vapoursynth()
    from assetmaker_vs.executor import execute_user_script

    with tempfile.TemporaryDirectory() as temp_dir:
        roots = _write_executor_lifecycle_roots(Path(temp_dir).resolve())
        first = _execute_lifecycle_graph(execute_user_script, roots["A"])
        original_close = first.environment.close
        close_calls = 0

        def abnormal_close() -> None:
            nonlocal close_calls
            close_calls += 1
            original_close()
            raise RuntimeError("environment close failed")

        first.environment.close = abnormal_close
        first_error = None
        second_error = None
        try:
            first.close()
        except RuntimeError as exc:
            first_error = str(exc)
        try:
            first.close()
        except RuntimeError as exc:
            second_error = str(exc)

        after_error = _execute_lifecycle_graph(
            execute_user_script, roots["B"]
        )
        after_error_value = after_error.namespace["GRAPH_ID"]
        after_error.close()

    return {
        "first_error": first_error,
        "second_error": second_error,
        "close_calls": close_calls,
        "after_error_value": after_error_value,
    }


def _executor_graph_race_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    fake_vs = _install_fake_vapoursynth()
    from assetmaker_vs.executor import execute_user_script

    with tempfile.TemporaryDirectory() as temp_dir:
        roots = _write_executor_lifecycle_roots(Path(temp_dir).resolve())
        start = threading.Barrier(2)
        result_lock = threading.Lock()
        outcomes: list[dict[str, object]] = []
        graphs: list[object] = []

        def load(name: str) -> None:
            start.wait(timeout=5)
            try:
                graph = _execute_lifecycle_graph(
                    execute_user_script, roots[name]
                )
            except BaseException as exc:
                outcome = {
                    "kind": "error",
                    "type": type(exc).__name__,
                    "code": getattr(exc, "code", None),
                }
                with result_lock:
                    outcomes.append(outcome)
            else:
                with result_lock:
                    outcomes.append(
                        {
                            "kind": "graph",
                            "value": graph.namespace["GRAPH_ID"],
                        }
                    )
                    graphs.append(graph)

        threads = [
            threading.Thread(target=load, args=(name,))
            for name in ("A", "B")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        alive = [thread.is_alive() for thread in threads]
        clear_calls_during_race = fake_vs.clear_calls
        for graph in reversed(graphs):
            graph.close()

        after_race = _execute_lifecycle_graph(
            execute_user_script, roots["C"]
        )
        after_race_value = after_race.namespace["GRAPH_ID"]
        after_race.close()

    return {
        "alive": alive,
        "outcomes": outcomes,
        "clear_calls_during_race": clear_calls_during_race,
        "after_race_value": after_race_value,
    }


class _PoisonModule(types.ModuleType):
    def __getattr__(self, name: str):
        raise RuntimeError(f"poison getattr: {name}")


def _write_executor_poison_roots(
    base: Path, *, observer_name: str | None = None
) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for name in ("A", "B"):
        root = base / name
        modules = root / "modules"
        modules.mkdir(parents=True)
        (modules / "marker.py").write_text(
            f"VALUE = {name!r}\n", encoding="utf-8"
        )
        lines = ["import marker", "loaded = marker.VALUE"]
        if name == "A" and observer_name is not None:
            lines.extend(
                (
                    f"import {observer_name} as observer",
                    "observer.marker = marker",
                    "observer.install_poison()",
                    'raise RuntimeError("script failure")',
                )
            )
        (root / "pipeline.vpy").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        (root / "job.json").write_text("{}", encoding="utf-8")
        roots[name] = root
    return roots


def _executor_poison_close_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    _install_fake_vapoursynth()
    from assetmaker_vs import executor as helper_module
    from assetmaker_vs.executor import execute_user_script

    poison_name = "assetmaker_retirement_path_poison"
    stdlib_json = sys.modules["json"]
    with tempfile.TemporaryDirectory() as temp_dir:
        roots = _write_executor_poison_roots(Path(temp_dir).resolve())
        first = _execute_lifecycle_graph(execute_user_script, roots["A"])
        first_value = first.namespace["loaded"]
        first_marker = sys.modules["marker"]
        poison = _PoisonModule(poison_name)
        poison.__file__ = None
        sys.modules[poison_name] = poison
        close_error = None
        try:
            first.close()
        except BaseException as exc:
            close_error = {
                "type": type(exc).__name__,
                "message": str(exc),
            }
        poison_preserved = sys.modules.get(poison_name) is poison
        a_marker_cached = sys.modules.get("marker") is first_marker
        sys.modules.pop(poison_name, None)

        second = _execute_lifecycle_graph(execute_user_script, roots["B"])
        second_value = second.namespace["loaded"]
        second.close()

    return {
        "first_value": first_value,
        "close_error": close_error,
        "poison_preserved": poison_preserved,
        "a_marker_cached": a_marker_cached,
        "second_value": second_value,
        "second_retired": "marker" not in sys.modules,
        "helper_preserved": (
            sys.modules.get("assetmaker_vs.executor") is helper_module
        ),
        "stdlib_preserved": sys.modules.get("json") is stdlib_json,
    }


def _executor_poison_failure_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    _install_fake_vapoursynth()
    from assetmaker_vs import executor as helper_module
    from assetmaker_vs.executor import execute_user_script

    poison_name = "assetmaker_retirement_file_poison"
    observer_name = "assetmaker_retirement_poison_observer"
    observer = types.ModuleType(observer_name)

    def install_poison() -> None:
        poison = _PoisonModule(poison_name)
        observer.poison = poison
        sys.modules[poison_name] = poison

    observer.install_poison = install_poison
    sys.modules[observer_name] = observer
    stdlib_json = sys.modules["json"]
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            roots = _write_executor_poison_roots(
                Path(temp_dir).resolve(), observer_name=observer_name
            )
            execution_error = None
            try:
                _execute_lifecycle_graph(execute_user_script, roots["A"])
            except BaseException as exc:
                execution_error = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
            poison_preserved = (
                sys.modules.get(poison_name) is observer.poison
            )
            a_marker_cached = sys.modules.get("marker") is observer.marker
            sys.modules.pop(poison_name, None)

            second = _execute_lifecycle_graph(
                execute_user_script, roots["B"]
            )
            second_value = second.namespace["loaded"]
            second.close()

        return {
            "execution_error": execution_error,
            "poison_preserved": poison_preserved,
            "a_marker_cached": a_marker_cached,
            "second_value": second_value,
            "second_retired": "marker" not in sys.modules,
            "helper_preserved": (
                sys.modules.get("assetmaker_vs.executor") is helper_module
            ),
            "stdlib_preserved": sys.modules.get("json") is stdlib_json,
        }
    finally:
        sys.modules.pop(poison_name, None)
        sys.modules.pop(observer_name, None)
        sys.modules.pop("marker", None)


class _UnbindPoisonParent(types.ModuleType):
    def __init__(
        self,
        name: str,
        *,
        replacement_name: str,
        replacement: types.ModuleType,
    ) -> None:
        super().__init__(name)
        self._replacement_name = replacement_name
        self._replacement = replacement

    def __delattr__(self, name: str) -> None:
        if name == "bad_piece":
            sys.modules[self._replacement_name] = self._replacement
            types.ModuleType.__setattr__(
                self, "replace_piece", self._replacement
            )
            raise RuntimeError("parent unlink failed")
        super().__delattr__(name)


def _install_retirement_fault(root: Path, external: Path):
    parent_name = "assetmaker_unbind_parent"
    replacement_name = f"{parent_name}.replace_piece"
    replacement = types.ModuleType(replacement_name)
    replacement.__file__ = str(external / "replacement.py")
    parent = _UnbindPoisonParent(
        parent_name,
        replacement_name=replacement_name,
        replacement=replacement,
    )
    parent.__file__ = str(external / "parent.py")
    sys.modules[parent_name] = parent

    children: dict[str, types.ModuleType] = {}
    for attribute in ("bad_piece", "replace_piece", "later_piece"):
        name = f"{parent_name}.{attribute}"
        child = types.ModuleType(name)
        child.__file__ = str(root / "modules" / parent_name / f"{attribute}.py")
        setattr(parent, attribute, child)
        sys.modules[name] = child
        children[attribute] = child

    read_parent_name = "assetmaker_read_poison_parent"
    read_child_name = f"{read_parent_name}.orphan_piece"
    read_parent = _PoisonModule(read_parent_name)
    read_parent.__file__ = str(external / "read_parent.py")
    read_child = types.ModuleType(read_child_name)
    read_child.__file__ = str(
        root / "modules" / read_parent_name / "orphan_piece.py"
    )
    sys.modules[read_parent_name] = read_parent
    sys.modules[read_child_name] = read_child
    return types.SimpleNamespace(
        parent_name=parent_name,
        parent=parent,
        replacement_name=replacement_name,
        replacement=replacement,
        children=children,
        read_parent_name=read_parent_name,
        read_parent=read_parent,
        read_child_name=read_child_name,
        read_child=read_child,
    )


def _retirement_fault_state(fault) -> dict[str, object]:
    parent = fault.parent
    return {
        "parent_preserved": sys.modules.get(fault.parent_name) is parent,
        "bad_module_removed": (
            f"{fault.parent_name}.bad_piece" not in sys.modules
        ),
        "bad_attr_remains": (
            getattr(parent, "bad_piece", None) is fault.children["bad_piece"]
        ),
        "replacement_module_preserved": (
            sys.modules.get(fault.replacement_name) is fault.replacement
        ),
        "replacement_attr_preserved": (
            getattr(parent, "replace_piece", None) is fault.replacement
        ),
        "later_module_removed": (
            f"{fault.parent_name}.later_piece" not in sys.modules
        ),
        "later_attr_removed": not hasattr(parent, "later_piece"),
        "read_parent_preserved": (
            sys.modules.get(fault.read_parent_name) is fault.read_parent
        ),
        "read_child_removed": fault.read_child_name not in sys.modules,
    }


def _remove_retirement_fault(fault) -> None:
    names = (
        fault.parent_name,
        f"{fault.parent_name}.bad_piece",
        fault.replacement_name,
        f"{fault.parent_name}.later_piece",
        fault.read_parent_name,
        fault.read_child_name,
    )
    for name in names:
        sys.modules.pop(name, None)


def _executor_retirement_unbind_close_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    _install_fake_vapoursynth()
    from assetmaker_vs.executor import execute_user_script

    with tempfile.TemporaryDirectory() as temp_dir:
        base = Path(temp_dir).resolve()
        roots = _write_executor_poison_roots(base)
        first = _execute_lifecycle_graph(execute_user_script, roots["A"])
        first_marker = sys.modules["marker"]
        fault = _install_retirement_fault(roots["A"], base / "external")
        close_error = None
        try:
            first.close()
        except BaseException as exc:
            close_error = {
                "type": type(exc).__name__,
                "code": getattr(exc, "code", None),
                "message": str(exc),
            }
        state = _retirement_fault_state(fault)
        a_marker_cached = sys.modules.get("marker") is first_marker
        _remove_retirement_fault(fault)

        second = _execute_lifecycle_graph(execute_user_script, roots["B"])
        second_value = second.namespace["loaded"]
        second.close()

    return {
        "close_error": close_error,
        "state": state,
        "a_marker_cached": a_marker_cached,
        "second_value": second_value,
        "second_retired": "marker" not in sys.modules,
    }


def _executor_retirement_unbind_failure_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    _install_fake_vapoursynth()
    from assetmaker_vs.executor import execute_user_script

    observer_name = "assetmaker_retirement_unbind_observer"
    observer = types.ModuleType(observer_name)
    sys.modules[observer_name] = observer
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir).resolve()
            roots = _write_executor_poison_roots(
                base, observer_name=observer_name
            )

            def install_fault() -> None:
                observer.fault = _install_retirement_fault(
                    roots["A"], base / "external"
                )

            observer.install_poison = install_fault
            execution_error = None
            try:
                _execute_lifecycle_graph(execute_user_script, roots["A"])
            except BaseException as exc:
                execution_error = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "notes": list(getattr(exc, "__notes__", ())),
                }
            state = _retirement_fault_state(observer.fault)
            a_marker_cached = sys.modules.get("marker") is observer.marker
            _remove_retirement_fault(observer.fault)

            second = _execute_lifecycle_graph(
                execute_user_script, roots["B"]
            )
            second_value = second.namespace["loaded"]
            second.close()

        return {
            "execution_error": execution_error,
            "state": state,
            "a_marker_cached": a_marker_cached,
            "second_value": second_value,
            "second_retired": "marker" not in sys.modules,
        }
    finally:
        fault = getattr(observer, "fault", None)
        if fault is not None:
            _remove_retirement_fault(fault)
        sys.modules.pop(observer_name, None)
        sys.modules.pop("marker", None)


def _job_payload(
    *, frame_count: int = 5, output_range: str = "limited"
) -> dict[str, object]:
    return {
        "api_version": 1,
        "epoch": 1,
        "track": "loop",
        "project_root": r"D:\素材\黍",
        "source": {
            "path": r"D:\素材\黍\source.mp4",
            "kind": "video",
            "virtual_frame_count": None,
        },
        "timeline": {
            "start_frame": 0,
            "end_frame": frame_count,
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
            "range": output_range,
            "final_rotate_180": False,
        },
        "paths": {"cache_dir": r"D:\素材\黍\cache"},
    }


def _raw_header() -> dict[str, object]:
    return {
        "api_version": 1,
        "mode": "raw",
        "capabilities": ["source"],
        "requires": [],
        "editor_output": 0,
    }


def _tagged_clip(vs, *, length: int = 5):
    clip = vs.core.std.BlankClip(
        width=384,
        height=640,
        length=length,
        fpsnum=30000,
        fpsden=1001,
        format=vs.YUV420P8,
        color=[16, 128, 128],
    )
    return vs.core.std.SetFrameProps(
        clip,
        _Matrix=6,
        _Transfer=6,
        _Primaries=6,
        _ColorRange=1,
    )


def _contract_valid_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    vs = _load_vs()
    from assetmaker_vs.contract import validate_outputs

    vs.clear_outputs()
    _tagged_clip(vs).set_output(0)
    validated = validate_outputs(vs, _job_payload(), _raw_header())
    frame = validated.guarded_clip.get_frame(1)
    props = dict(frame.props)
    frame.close()
    return {
        "vui": validated.vui.to_dict(),
        "matrix": props["_Matrix"],
        "format": validated.guarded_clip.format.name,
    }


def _contract_late_drift_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    vs = _load_vs()
    from assetmaker_vs.contract import (
        decode_output_contract_error,
        validate_outputs,
    )

    base = _tagged_clip(vs)

    def drift(n, f):
        if n != 1:
            return f
        changed = f.copy()
        changed.props["_Matrix"] = 1
        return changed

    clip = vs.core.std.ModifyFrame(clip=base, clips=base, selector=drift)
    vs.clear_outputs()
    clip.set_output(0)
    validated = validate_outputs(vs, _job_payload(), _raw_header())
    try:
        validated.guarded_clip.get_frame(1)
    except BaseException as exc:
        contract_error = decode_output_contract_error(exc)
        if contract_error is not None:
            return {"error": contract_error.to_dict()}
        raise
    raise AssertionError("逐帧 guard 未拒绝第 2 帧的 matrix 漂移")


def _invalid_prop_clip(
    vs,
    *,
    prop: str,
    value: object,
    late: bool = False,
):
    base = _tagged_clip(vs)

    def invalid(n, f):
        if late and n != 1:
            return f
        changed = f.copy()
        changed.props[prop] = value
        return changed

    return vs.core.std.ModifyFrame(clip=base, clips=base, selector=invalid)


def _contract_strict_types_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    vs = _load_vs()
    from assetmaker_vs.contract import (
        OutputContractError,
        decode_output_contract_error,
        validate_outputs,
    )

    cases = (
        ("_Matrix", 6.5),
        ("_Transfer", 6.0),
        ("_Primaries", b"6"),
        ("_Range", 0.0),
        ("_ColorRange", 1.0),
    )
    results: dict[str, object] = {}
    for prop, value in cases:
        clip = _invalid_prop_clip(
            vs,
            prop=prop,
            value=value,
        )
        vs.clear_outputs()
        clip.set_output(0)
        try:
            validate_outputs(vs, _job_payload(), _raw_header())
        except OutputContractError as exc:
            decoded = decode_output_contract_error(exc)
            results[prop] = {
                "error": (decoded or exc).to_dict(),
            }
        else:
            results[prop] = {"accepted": True}
    return {"results": results}


def _physical_old_range_clip(
    vs,
    *,
    shadowed: bool,
    late: bool,
):
    base = _tagged_clip(vs)

    def invalid(n, f):
        if late and n != 1:
            return f
        changed = f.copy()
        changed.props["_ColorRange"] = 1.0
        if shadowed:
            changed.props["_Range"] = 0
        return changed

    return vs.core.std.ModifyFrame(clip=base, clips=base, selector=invalid)


def _physical_old_range_layout(
    vs,
    clip,
    *,
    frame_index: int,
    expected_keys: set[str],
) -> dict[str, object]:
    frame = clip.get_frame(frame_index)
    try:
        frame_props_type_identity = type(frame.props) is vs.FrameProps
        physical_keys = [
            key
            for key in frame.props.keys()
            if key in ("_Range", "_ColorRange")
        ]
        assert frame_props_type_identity, type(frame.props)
        assert set(physical_keys) == expected_keys, physical_keys
        new_range_type_identity = None
        new_range_is_limited = None
        if "_Range" in expected_keys:
            new_range = frame.props["_Range"]
            new_range_type_identity = type(new_range) is vs.Range
            new_range_is_limited = new_range is vs.Range.RANGE_LIMITED
            assert new_range_type_identity, type(new_range)
            assert new_range_is_limited, new_range
        return {
            "frame_props_type_identity": frame_props_type_identity,
            "physical_keys": physical_keys,
            "new_range_type_identity": new_range_type_identity,
            "new_range_is_limited": new_range_is_limited,
        }
    finally:
        frame.close()


def _contract_physical_old_range_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    vs = _load_vs()
    from assetmaker_vs.contract import (
        decode_output_contract_error,
        validate_outputs,
    )

    results: dict[str, object] = {}
    for label, shadowed, late in (
        ("old_only_sentinel", False, False),
        ("old_only_late", False, True),
        ("shadowed_sentinel", True, False),
        ("shadowed_late", True, True),
    ):
        clip = _physical_old_range_clip(
            vs,
            shadowed=shadowed,
            late=late,
        )
        expected_keys = (
            {"_Range", "_ColorRange"}
            if shadowed
            else {"_ColorRange"}
        )
        expected_actual = {
            "property": "_ColorRange",
            "read": (
                "shadowed_by__Range" if shadowed else "unavailable"
            ),
            "physical_keys": (
                ["_Range", "_ColorRange"]
                if shadowed
                else ["_ColorRange"]
            ),
        }
        layout = _physical_old_range_layout(
            vs,
            clip,
            frame_index=1 if late else 0,
            expected_keys=expected_keys,
        )
        vs.clear_outputs()
        clip.set_output(0)
        if late:
            validated = validate_outputs(
                vs,
                _job_payload(),
                _raw_header(),
            )
            try:
                validated.guarded_clip.get_frame(1)
            except BaseException as exc:
                caught = exc
            else:
                raise AssertionError(f"{label} 未在第 1 帧拒绝物理 _ColorRange")
            error_stage = "guarded_frame_1"
        else:
            try:
                validate_outputs(
                    vs,
                    _job_payload(),
                    _raw_header(),
                )
            except BaseException as exc:
                caught = exc
            else:
                raise AssertionError(
                    f"{label} 未在 validate_outputs 拒绝物理 _ColorRange"
                )
            error_stage = "validate_outputs"
        decoded = decode_output_contract_error(caught)
        if decoded is None:
            raise caught
        error = decoded.to_dict()
        assert error["code"] == "contract.range", error
        assert error["field"] == "range", error
        assert error["expected"] == ["limited", "full"], error
        assert error["actual"] == expected_actual, error
        results[label] = {
            "layout": layout,
            "error_stage": error_stage,
            "error": error,
        }
    return {"results": results}


def _contract_bytes_case(*, late: bool) -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    vs = _load_vs()
    from assetmaker_vs.contract import (
        decode_output_contract_error,
        validate_outputs,
    )

    clip = _invalid_prop_clip(
        vs,
        prop="_Matrix",
        value=b"not-an-int",
        late=late,
    )
    vs.clear_outputs()
    clip.set_output(0)
    try:
        validated = validate_outputs(vs, _job_payload(), _raw_header())
        if late:
            validated.guarded_clip.get_frame(1)
    except BaseException as exc:
        decoded = decode_output_contract_error(exc)
        if decoded is None:
            return {
                "exception": type(exc).__name__,
                "message": str(exc),
            }
        return {"error": decoded.to_dict()}
    return {"accepted": True}


def _contract_bytes_sentinel_case() -> dict[str, object]:
    return _contract_bytes_case(late=False)


def _contract_bytes_late_case() -> dict[str, object]:
    return _contract_bytes_case(late=True)


def _describe_prop(value: object) -> dict[str, object]:
    value_type = type(value)
    description: dict[str, object] = {
        "repr": repr(value),
        "type": value_type.__name__,
        "module": value_type.__module__,
    }
    name = getattr(value, "name", None)
    enum_value = getattr(value, "value", None)
    if type(name) is str:
        description["name"] = name
    if type(enum_value) is int:
        description["value"] = enum_value
    return description


def _range_probe_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    vs = _load_vs()
    from assetmaker_vs.contract import validate_outputs

    binding = sys.modules["vapoursynth.vapoursynth"]
    base = vs.core.std.BlankClip(
        width=384,
        height=640,
        length=5,
        fpsnum=30000,
        fpsden=1001,
        format=vs.YUV420P8,
        color=[16, 128, 128],
    )
    base = vs.core.std.SetFrameProps(
        base,
        _Matrix=6,
        _Transfer=6,
        _Primaries=6,
    )
    payload: dict[str, object] = {
        "runtime": {
            "package": str(Path(vs.__file__).resolve()),
            "binding": str(Path(binding.__file__).resolve()),
            "version": repr(vs.__version__),
            "api": repr(vs.__api_version__),
            "core_version": str(vs.core.core_version),
            "range_type": {
                "name": vs.Range.__name__,
                "module": vs.Range.__module__,
            },
            "binding_range_is_package_range": binding.Range is vs.Range,
            "color_range_is_range": vs.ColorRange is vs.Range,
        },
        "cases": {},
    }
    cases = (
        ("new_limited", "_Range", 0, "limited", "RANGE_LIMITED", 0, "tv"),
        ("new_full", "_Range", 1, "full", "RANGE_FULL", 1, "pc"),
        ("old_full", "_ColorRange", 0, "full", "RANGE_FULL", 1, "pc"),
        (
            "old_limited",
            "_ColorRange",
            1,
            "limited",
            "RANGE_LIMITED",
            0,
            "tv",
        ),
    )
    for label, prop, code, job_range, enum_name, enum_value, vui_range in cases:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            clip = vs.core.std.SetFrameProps(base, **{prop: code})
            frame = clip.get_frame(0)
            try:
                contains = {
                    key: key in frame.props
                    for key in ("_Range", "_ColorRange")
                }
                raw_reads = {
                    key: frame.props[key]
                    for key in ("_Range", "_ColorRange")
                }
                raw_physical = dict(frame.props)
                raw_colour = {
                    key: frame.props[key]
                    for key in ("_Matrix", "_Transfer", "_Primaries")
                }
                range_type_identity = {
                    key: type(value) is vs.Range
                    for key, value in raw_reads.items()
                }
                physical_range_type_identity = (
                    type(raw_physical["_Range"]) is vs.Range
                )
                colour_type_identity = {
                    key: type(value) is int
                    for key, value in raw_colour.items()
                }
                assert all(range_type_identity.values()), range_type_identity
                assert physical_range_type_identity
                assert all(colour_type_identity.values()), colour_type_identity
                reads = {
                    key: _describe_prop(value)
                    for key, value in raw_reads.items()
                }
                physical_range = {
                    key: _describe_prop(value)
                    for key, value in raw_physical.items()
                    if key in ("_Range", "_ColorRange")
                }
                colour_types = {
                    key: _describe_prop(value)
                    for key, value in raw_colour.items()
                }
            finally:
                frame.close()
        for value in reads.values():
            assert value["type"] == vs.Range.__name__, value
            assert value["module"] == vs.Range.__module__, value
            assert value["name"] == enum_name, value
            assert value["value"] == enum_value, value
        assert contains == {"_Range": True, "_ColorRange": True}, contains
        assert list(physical_range) == ["_Range"], physical_range
        assert physical_range["_Range"]["name"] == enum_name, physical_range
        assert all(value["type"] == "int" for value in colour_types.values())

        vs.clear_outputs()
        clip.set_output(0)
        validated = validate_outputs(
            vs,
            _job_payload(output_range=job_range),
            _raw_header(),
        )
        guarded = validated.guarded_clip.get_frame(1)
        guarded.close()
        assert validated.vui.range_ == vui_range, validated.vui
        payload["cases"][label] = {
            "write": {"prop": prop, "code": code},
            "job_range": job_range,
            "contains": contains,
            "keys": [
                key
                for key in raw_physical
                if key in ("_Range", "_ColorRange")
            ],
            "dict": physical_range,
            "reads": reads,
            "range_type_identity": range_type_identity,
            "physical_range_type_identity": physical_range_type_identity,
            "colour_types": colour_types,
            "colour_type_identity": colour_type_identity,
            "vui_range": validated.vui.range_,
            "warnings": [
                {
                    "category": item.category.__name__,
                    "message": str(item.message),
                }
                for item in caught
            ],
        }
    return payload


def _contract_range_late_drift_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    vs = _load_vs()
    from assetmaker_vs.contract import (
        decode_output_contract_error,
        validate_outputs,
    )

    base = _tagged_clip(vs)

    def drift(n, f):
        if n != 1:
            return f
        changed = f.copy()
        changed.props["_Range"] = 1
        return changed

    clip = vs.core.std.ModifyFrame(clip=base, clips=base, selector=drift)
    vs.clear_outputs()
    clip.set_output(0)
    validated = validate_outputs(vs, _job_payload(), _raw_header())
    try:
        validated.guarded_clip.get_frame(1)
    except BaseException as exc:
        contract_error = decode_output_contract_error(exc)
        if contract_error is not None:
            return {"error": contract_error.to_dict()}
        raise
    raise AssertionError("逐帧 guard 未拒绝第 2 帧的 Range 漂移")


def _display_geometry_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    vs = _load_vs()
    from assetmaker_vs.display import to_display_clip

    clip = vs.core.std.BlankClip(
        width=1920,
        height=1080,
        length=1,
        format=vs.YUV420P8,
        color=[16, 128, 128],
    )
    clip = vs.core.std.SetFrameProps(
        clip,
        _Matrix=6,
        _Transfer=6,
        _Primaries=6,
        _ColorRange=1,
    )
    dimensions: dict[str, list[int]] = {}
    for name, zoom in (
        ("one_percent", 0.01),
        ("fit", 1.0),
        ("two_x", 2.0),
        ("hundred_x", 100.0),
    ):
        out = to_display_clip(
            clip,
            viewport=(480, 270),
            zoom_factor=zoom,
            pan=(0.5, 0.5),
        )
        frame = out.get_frame(0)
        frame.close()
        dimensions[name] = [out.width, out.height]
    large = vs.core.std.BlankClip(
        width=3840,
        height=2160,
        length=1,
        format=vs.RGB24,
    )
    capped = to_display_clip(
        large,
        viewport=(321, 181),
        zoom_factor=100.0,
        pan=(1.0, 1.0),
    )
    frame = capped.get_frame(0)
    frame.close()
    dimensions["large_capped"] = [capped.width, capped.height]
    return dimensions


def _display_center_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    vs = _load_vs()
    from assetmaker_vs.display import to_display_clip

    black = vs.core.std.BlankClip(
        width=1,
        height=1,
        length=1,
        format=vs.RGB24,
        color=[0, 0, 0],
    )
    red = vs.core.std.BlankClip(
        width=1,
        height=1,
        length=1,
        format=vs.RGB24,
        color=[255, 0, 0],
    )
    black_row = vs.core.std.StackHorizontal([black] * 5)
    center_row = vs.core.std.StackHorizontal([black, black, red, black, black])
    source = vs.core.std.StackVertical([black_row, center_row, black_row])
    out = to_display_clip(
        source,
        viewport=(5, 3),
        zoom_factor=100.0,
        pan=(0.5, 0.5),
    )
    frame = out.get_frame(0)
    red_plane = bytes(frame[0])
    green_plane = bytes(frame[1])
    blue_plane = bytes(frame[2])
    frame.close()
    odd = vs.core.std.BlankClip(
        width=7,
        height=5,
        length=1,
        format=vs.RGB24,
    )
    odd_out = to_display_clip(
        odd,
        viewport=(5, 3),
        zoom_factor=2.0,
        pan=(0.5, 0.5),
    )
    one_pixel = to_display_clip(
        odd,
        viewport=(5, 3),
        zoom_factor=100.0,
        pan=(0.5, 0.5),
    )
    one_pixel.get_frame(0).close()
    return {
        "dimensions": [out.width, out.height],
        "all_red": all(value == 255 for value in red_plane),
        "green_zero": all(value == 0 for value in green_plane),
        "blue_zero": all(value == 0 for value in blue_plane),
        "odd_dimensions": [odd_out.width, odd_out.height],
        "one_pixel_dimensions": [one_pixel.width, one_pixel.height],
    }


def _output_payload(profile: str) -> dict[str, object]:
    if profile == "360x640":
        geometry = (360, 640, 384, 640)
    elif profile == "720x1080":
        geometry = (720, 1080, 720, 1080)
    else:
        raise ValueError(profile)
    display_width, display_height, coded_width, coded_height = geometry
    return {
        "profile": profile,
        "display_width": display_width,
        "display_height": display_height,
        "coded_width": coded_width,
        "coded_height": coded_height,
        "pixel_format": "YUV420P8",
        "matrix": "170m",
        "transfer": "170m",
        "primaries": "170m",
        "range": "limited",
        "final_rotate_180": False,
    }


def _write_default_job(
    path: Path,
    *,
    source: Path,
    kind: str,
    virtual_frame_count: int | None,
    start_frame: int,
    end_frame: int | None,
    fps: tuple[int, int] | None,
    rotation: int,
    crop: tuple[int, int, int, int],
    profile: str,
    epoch: int,
) -> None:
    root = path.parent.resolve()
    crop_x, crop_y, crop_width, crop_height = crop
    payload = {
        "api_version": 1,
        "epoch": epoch,
        "track": "loop",
        "project_root": str(root),
        "source": {
            "path": str(source.resolve()),
            "kind": kind,
            "virtual_frame_count": virtual_frame_count,
        },
        "timeline": {
            "start_frame": start_frame,
            "end_frame": end_frame,
            "fps": (
                None
                if fps is None
                else {"numerator": fps[0], "denominator": fps[1]}
            ),
        },
        "transform": {
            "rotation": rotation,
            "crop": {
                "coordinate_space": "post_rotation_source_pixels",
                "x": crop_x,
                "y": crop_y,
                "width": crop_width,
                "height": crop_height,
            },
        },
        "output": _output_payload(profile),
        "paths": {"cache_dir": str((root / "cache").resolve())},
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _make_png(path: Path, *, width: int = 8, height: int = 6) -> None:
    from PIL import Image

    image = Image.new("RGB", (width, height), (220, 40, 20))
    image.putpixel((width // 2, height // 2), (10, 240, 30))
    image.save(path)


def _make_source_mp4(
    path: Path,
    *,
    width: int = 12,
    height: int = 8,
    length: int = 8,
) -> None:
    script = path.with_suffix(".source.vpy")
    script.write_text(
        "\n".join(
            [
                "import vapoursynth as vs",
                "core = vs.core",
                "clip = core.std.BlankClip(",
                f"    width={width}, height={height}, length={length},",
                "    fpsnum=30000, fpsden=1001, format=vs.RGB24,",
                "    color=[220, 40, 20],",
                ")",
                "clip = core.resize.Bicubic(",
                "    clip, format=vs.YUV420P8, matrix_s='170m', range_s='limited'",
                ")",
                "clip.set_output()",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _encode_source_script(
        script,
        path,
        colormatrix="smpte170m",
        colorprim="smpte170m",
        transfer="smpte170m",
    )


def _execute_default(job_path: Path, *, for_export: bool):
    sys.path.insert(0, str(HELPER_ROOT))
    vs = _load_vs()
    from assetmaker_vs.contract import (
        validate_outputs,
        verify_required_callables,
    )
    from assetmaker_vs.executor import execute_user_script
    from assetmaker_vs.job_api import load_job
    from assetmaker_vs.script_header import (
        parse_script_header,
        validate_invocation,
    )

    header = parse_script_header(DEFAULT_PIPELINE)
    job = load_job(job_path, for_export=for_export)
    validate_invocation(header, api_version="1", mode="compatible")
    verify_required_callables(vs.core, header["requires"])
    graph = execute_user_script(
        script_path=DEFAULT_PIPELINE,
        job_path=job_path,
        api_version="1",
        mode="compatible",
    )
    validated = validate_outputs(vs, job, header)
    return vs, graph, validated


def _node_metadata(node) -> dict[str, object]:
    frame = node.get_frame(0)
    props = {
        key: int(frame.props[key])
        for key in ("_Matrix", "_Transfer", "_Primaries", "_Range")
        if key in frame.props
    }
    frame.close()
    return {
        "width": node.width,
        "height": node.height,
        "num_frames": node.num_frames,
        "fps": [node.fps_num, node.fps_den],
        "format": node.format.name,
        "props": props,
    }


def _run_default_vspipe(job: Path) -> dict[str, object]:
    from config.vs_runtime import load_vs_runtime
    from core.media_pipeline import VSPipeRenderRequest, build_vspipe_render_env
    from core.media_tools import MediaToolchain
    from core.vs_runtime.vs_loader import compute_runtime_fingerprint

    toolchain = MediaToolchain.discover(str(ROOT))
    runtime = load_vs_runtime(ROOT / "config" / "vs_runtime.json")
    request = VSPipeRenderRequest(
        runner_path=str(RUNNER),
        script_path=str(DEFAULT_PIPELINE),
        job_path=str(job),
        expected_job_sha256=hashlib.sha256(job.read_bytes()).hexdigest(),
        api_version=1,
        mode="compatible",
        app_dir=str(ROOT),
        runtime=runtime,
        runtime_fingerprint=compute_runtime_fingerprint(ROOT, runtime),
    )
    command = [
        toolchain.vspipe_path,
        "--info",
        "--arg",
        f"assetmaker_job={request.job_path}",
        "--arg",
        f"expected_job_sha256={request.expected_job_sha256}",
        "--arg",
        f"assetmaker_script={request.script_path}",
        "--arg",
        "assetmaker_api=1",
        "--arg",
        f"assetmaker_mode={request.mode}",
        request.runner_path,
        "-",
    ]
    kwargs: dict[str, object] = {
        "cwd": ROOT,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 60,
        "check": False,
        "env": build_vspipe_render_env(
            toolchain.vspipe_path,
            app_dir=request.app_dir,
            runtime=request.runtime,
            expected_fingerprint=request.runtime_fingerprint,
        ),
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(command, **kwargs)
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _default_image_case() -> dict[str, object]:
    _load_vs()
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir).resolve() / "素材" / "黍"
        root.mkdir(parents=True)
        image = root / "静态图.png"
        _make_png(image)
        job = root / "image-job.json"
        _write_default_job(
            job,
            source=image,
            kind="image",
            virtual_frame_count=9,
            start_frame=2,
            end_frame=7,
            fps=(30, 1),
            rotation=90,
            crop=(1, 1, 999, 999),
            profile="360x640",
            epoch=11,
        )
        vs, graph, validated = _execute_default(job, for_export=True)
        output0 = _node_metadata(validated.guarded_clip)
        output1 = _node_metadata(vs.get_output(1).clip)
        graph.close()
        runner = _run_default_vspipe(job)
        encoded_path = root / "runner-output.mp4"
        _encode_default_runner(job, encoded_path)
        encoded = {"size": encoded_path.stat().st_size}
        vs.clear_outputs()
        del validated, graph
        gc.collect()
    return {
        "output0": output0,
        "output1": output1,
        "runner": runner,
        "encoded": encoded,
    }


def _default_video_case() -> dict[str, object]:
    _load_vs()
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir).resolve() / "素材" / "黍"
        root.mkdir(parents=True)
        source = root / "视频.mp4"
        _make_source_mp4(source)

        bootstrap = root / "bootstrap.json"
        _write_default_job(
            bootstrap,
            source=source,
            kind="video",
            virtual_frame_count=None,
            start_frame=0,
            end_frame=None,
            fps=None,
            rotation=90,
            crop=(0, 0, 0, 0),
            profile="720x1080",
            epoch=12,
        )
        vs, bootstrap_graph, bootstrap_validated = _execute_default(
            bootstrap, for_export=False
        )
        bootstrap0 = _node_metadata(bootstrap_validated.guarded_clip)
        bootstrap1 = _node_metadata(vs.get_output(1).clip)
        bootstrap_graph.close()

        resolved = root / "resolved.json"
        _write_default_job(
            resolved,
            source=source,
            kind="video",
            virtual_frame_count=None,
            start_frame=2,
            end_frame=7,
            fps=(30000, 1001),
            rotation=90,
            crop=(1, 1, 999, 999),
            profile="720x1080",
            epoch=13,
        )
        vs, resolved_graph, resolved_validated = _execute_default(
            resolved, for_export=True
        )
        resolved0 = _node_metadata(resolved_validated.guarded_clip)
        resolved1 = _node_metadata(vs.get_output(1).clip)
        resolved_graph.close()
        vs.clear_outputs()
        del bootstrap_validated, bootstrap_graph
        del resolved_validated, resolved_graph
        gc.collect()
    return {
        "bootstrap0": bootstrap0,
        "bootstrap1": bootstrap1,
        "resolved0": resolved0,
        "resolved1": resolved1,
    }


def _encode_vspipe_output(
    vspipe_arguments: list[str],
    path: Path,
    *,
    fps: RationalFPS,
    colormatrix: str,
    colorprim: str,
    transfer: str,
    vspipe_env: dict[str, str] | None = None,
) -> None:
    from core.media_pipeline import build_mux_command, build_x264_command
    from core.media_tools import (
        MediaToolchain,
        build_media_subprocess_env,
    )
    from core.vs_runtime.output_contract import X264Vui

    toolchain = MediaToolchain.discover(str(ROOT))
    raw = path.with_suffix(".264")
    popen_kwargs: dict[str, object] = {
        "cwd": ROOT,
        "env": (
            build_media_subprocess_env(toolchain.vspipe_path)
            if vspipe_env is None
            else vspipe_env
        ),
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    vspipe = subprocess.Popen(
        [toolchain.vspipe_path, *vspipe_arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **popen_kwargs,
    )
    x264 = subprocess.Popen(
        build_x264_command(
            toolchain.x264_path,
            str(raw),
            crf=10,
            preset="ultrafast",
            vui=X264Vui(
                colormatrix=colormatrix,
                colorprim=colorprim,
                transfer=transfer,
                range_="tv",
            ),
        ),
        stdin=vspipe.stdout,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        **popen_kwargs,
    )
    if vspipe.stdout is not None:
        vspipe.stdout.close()
    x264_stderr = x264.communicate(timeout=120)[1]
    vspipe.wait(timeout=30)
    vspipe_stderr = (
        vspipe.stderr.read() if vspipe.stderr is not None else b""
    )
    if vspipe.returncode != 0 or x264.returncode != 0:
        raise RuntimeError(
            (vspipe_stderr + x264_stderr).decode("utf-8", errors="replace")
        )
    mux = subprocess.run(
        build_mux_command(
            toolchain.muxer_path,
            str(raw),
            str(path),
            fps,
        ),
        cwd=ROOT,
        capture_output=True,
        timeout=120,
        check=False,
        env=build_media_subprocess_env(toolchain.muxer_path),
        creationflags=(
            subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        ),
    )
    if mux.returncode != 0:
        raise RuntimeError(mux.stderr.decode("utf-8", errors="replace"))


def _encode_source_script(
    script: Path,
    path: Path,
    *,
    colormatrix: str,
    colorprim: str,
    transfer: str,
) -> None:
    _encode_vspipe_output(
        ["-c", "y4m", str(script), "-"],
        path,
        fps=RationalFPS(30_000, 1_001),
        colormatrix=colormatrix,
        colorprim=colorprim,
        transfer=transfer,
    )


def _encode_default_runner(job: Path, path: Path) -> None:
    from config.vs_runtime import load_vs_runtime
    from core.media_pipeline import (
        VSPipeRenderRequest,
        build_vspipe_command,
        build_vspipe_render_env,
    )
    from core.media_tools import MediaToolchain
    from core.vs_runtime.vs_loader import compute_runtime_fingerprint

    toolchain = MediaToolchain.discover(str(ROOT))
    runtime = load_vs_runtime(ROOT / "config" / "vs_runtime.json")
    request = VSPipeRenderRequest(
        runner_path=str(RUNNER),
        script_path=str(DEFAULT_PIPELINE),
        job_path=str(job),
        expected_job_sha256=hashlib.sha256(job.read_bytes()).hexdigest(),
        api_version=1,
        mode="compatible",
        app_dir=str(ROOT),
        runtime=runtime,
        runtime_fingerprint=compute_runtime_fingerprint(ROOT, runtime),
    )
    command = build_vspipe_command(toolchain.vspipe_path, request)
    _encode_vspipe_output(
        command[1:],
        path,
        fps=RationalFPS(30, 1),
        colormatrix="smpte170m",
        colorprim="smpte170m",
        transfer="smpte170m",
        vspipe_env=build_vspipe_render_env(
            toolchain.vspipe_path,
            app_dir=request.app_dir,
            runtime=request.runtime,
            expected_fingerprint=request.runtime_fingerprint,
        ),
    )


def _encode_untagged_709_source(path: Path) -> None:
    script = path.with_suffix(".vpy")
    script.write_text(
        "\n".join(
            [
                "import vapoursynth as vs",
                "core = vs.core",
                "clip = core.std.BlankClip(",
                "    width=800, height=800, length=3,",
                "    fpsnum=30000, fpsden=1001, format=vs.RGB24,",
                "    color=[220, 40, 20],",
                ")",
                "clip = core.resize.Bicubic(",
                "    clip, format=vs.YUV420P8, matrix_s='709', range_s='limited'",
                ")",
                "clip.set_output()",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _encode_source_script(
        script,
        path,
        colormatrix="undef",
        colorprim="undef",
        transfer="undef",
    )


def _frame_digest(frame) -> str:
    digest = hashlib.sha256()
    for plane in range(frame.format.num_planes):
        digest.update(bytes(frame[plane]))
    return digest.hexdigest()


def _default_p7_case() -> dict[str, object]:
    vs = _load_vs()
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir).resolve() / "素材" / "黍"
        root.mkdir(parents=True)
        source = root / "untagged-709.mp4"
        _encode_untagged_709_source(source)
        source_frame = vs.core.lsmas.LWLibavSource(str(source)).get_frame(0)
        source_matrix = int(source_frame.props.get("_Matrix", 2))
        source_frame.close()
        digests: list[str] = []
        for epoch, crop in (
            (21, (0, 0, 450, 800)),
            (22, (0, 0, 404, 718)),
        ):
            job = root / f"p7-{epoch}.json"
            _write_default_job(
                job,
                source=source,
                kind="video",
                virtual_frame_count=None,
                start_frame=0,
                end_frame=3,
                fps=(30000, 1001),
                rotation=0,
                crop=crop,
                profile="360x640",
                epoch=epoch,
            )
            _vs, graph, validated = _execute_default(job, for_export=True)
            frame = validated.guarded_clip.get_frame(0)
            digests.append(_frame_digest(frame))
            frame.close()
            graph.close()
            vs.clear_outputs()
            del validated, graph
            gc.collect()
    return {
        "source_matrix": source_matrix,
        "digests": digests,
        "equal": digests[0] == digests[1],
    }


def _encoded_vui_case() -> dict[str, int]:
    if len(sys.argv) != 3:
        raise ValueError("encoded_vui requires an MP4 path")
    clip = _load_vs().core.lsmas.LWLibavSource(str(Path(sys.argv[2]).resolve()))
    frame = clip.get_frame(0)
    try:
        return {
            name: int(frame.props[name])
            for name in ("_Matrix", "_Transfer", "_Primaries", "_Range")
        }
    finally:
        frame.close()


CASES = {
    "contract_bytes_late": _contract_bytes_late_case,
    "contract_bytes_sentinel": _contract_bytes_sentinel_case,
    "contract_late_drift": _contract_late_drift_case,
    "contract_physical_old_range": _contract_physical_old_range_case,
    "contract_range_late_drift": _contract_range_late_drift_case,
    "contract_strict_types": _contract_strict_types_case,
    "contract_valid": _contract_valid_case,
    "display_center": _display_center_case,
    "display_geometry": _display_geometry_case,
    "default_image": _default_image_case,
    "default_p7": _default_p7_case,
    "default_video": _default_video_case,
    "encoded_vui": _encoded_vui_case,
    "executor_deferred": _executor_deferred_case,
    "executor_cross_root": _executor_cross_root_case,
    "executor_mixed_namespace_failure": _executor_mixed_namespace_failure_case,
    "executor_mixed_namespace_runtime_dirs": (
        _executor_mixed_namespace_runtime_dirs_case
    ),
    "executor_mixed_namespace_sys_path": (
        _executor_mixed_namespace_sys_path_case
    ),
    "executor_graph_abnormal_close": _executor_graph_abnormal_close_case,
    "executor_graph_overlap": _executor_graph_overlap_case,
    "executor_graph_race": _executor_graph_race_case,
    "executor_graph_reuse": _executor_graph_reuse_case,
    "executor_poison_close": _executor_poison_close_case,
    "executor_poison_failure": _executor_poison_failure_case,
    "executor_retirement_unbind_close": (
        _executor_retirement_unbind_close_case
    ),
    "executor_retirement_unbind_failure": (
        _executor_retirement_unbind_failure_case
    ),
    "import_origin": _import_origin_case,
    "range_probe": _range_probe_case,
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in CASES:
        _emit({"error": "unknown case"}, exit_code=2)
    try:
        payload = CASES[sys.argv[1]]()
    except BaseException as exc:
        sys.stdout = sys.__stdout__
        _emit(
            {
                "error": type(exc).__name__,
                "message": str(exc),
            },
            exit_code=1,
        )
    _emit(payload)


if __name__ == "__main__":
    main()
