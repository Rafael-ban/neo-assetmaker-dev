"""
明日方舟通行证素材工具箱 - cx_Freeze + Inno Setup 打包工具
"""

import os
import sys
import subprocess
import argparse
import shutil
import stat
import urllib.request
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

sys.setrecursionlimit(10000)

PROJECT_NAME = "ArknightsPassMaker"
MAIN_SCRIPT = "main.py"
ICON_FILE = "resources/icons/favicon.ico"
OBFUSCATION_DIR = os.path.join(".tmp", "pyarmor-src")
# Keep the Qt GUI package in plain source for now: it has legacy encoding and
# dynamic Qt/plugin patterns that are fragile under source-level obfuscation.
OBFUSCATABLE_ENTRIES = ["main.py", "core", "config", "utils", "_mext"]
MEDIA_TOOL_DIR = os.path.join("tools", "media")
MEDIA_TOOL_SOURCE_DIRS = ("", MEDIA_TOOL_DIR)
PYCACHE_SOURCE_ROOTS = (
    Path("core"),
    Path("config"),
    Path("gui"),
    Path("utils"),
    Path("_mext"),
    Path("tests"),
    Path("resources/vapoursynth/python"),
)
MEDIA_TOOL_CANDIDATES = [
    ("VSPipe.exe", os.path.join(MEDIA_TOOL_DIR, "VSPipe.exe")),
    ("x264-7mod.exe", os.path.join(MEDIA_TOOL_DIR, "x264-7mod.exe")),
    ("mp4box.exe", os.path.join(MEDIA_TOOL_DIR, "mp4box.exe")),
    ("lsmash-muxer.exe", os.path.join(MEDIA_TOOL_DIR, "lsmash-muxer.exe")),
    ("muxer.exe", os.path.join(MEDIA_TOOL_DIR, "muxer.exe")),
]
VS_CONTRACT_FILES = (
    "config/vs_runtime.json",
    "schemas/vs_runtime.schema.json",
    "schemas/vs_job.schema.json",
)
VS_WORKER_SUPPORT_FILES = (
    "resources/vapoursynth/assetmaker_runner.vpy",
    "resources/vapoursynth/default_pipeline.vpy",
    "resources/vapoursynth/python/assetmaker_vs/__init__.py",
    "resources/vapoursynth/python/assetmaker_vs/job_api.py",
    "resources/vapoursynth/python/assetmaker_vs/script_header.py",
    "resources/vapoursynth/python/assetmaker_vs/executor.py",
    "resources/vapoursynth/python/assetmaker_vs/contract.py",
    "resources/vapoursynth/python/assetmaker_vs/display.py",
    "resources/vapoursynth/python/assetmaker_vs/runtime_fingerprint.py",
    "resources/vapoursynth/python/assetmaker_vs/runtime_layout.py",
    "resources/vapoursynth/python/assetmaker_vs/core_resources.py",
    "resources/vapoursynth/python/assetmaker_vs/native_plugins.py",
)


def get_version() -> str:
    """从 pyproject.toml 读取版本号（单一来源）

    参考: cx_Freeze 在 _pyproject.py:7-10 使用相同的 tomllib/tomli fallback 模式
    """
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    with open(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "pyproject.toml"), "rb"
    ) as f:
        return tomllib.load(f)["project"]["version"]


VERSION = get_version()
BUILD_DIR = PROJECT_NAME
DIST_DIR = "dist"
ISS_FILE = "installer.iss"
INNO_SETUP_DIR = "tools/innosetup"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=f"{PROJECT_NAME} Build Tool")
    parser.add_argument(
        "--no-installer", action="store_true", help="Skip installer packaging"
    )
    parser.add_argument("--clean", action="store_true", help="Clean build directories")
    parser.add_argument(
        "--skip-flasher",
        action="store_true",
        help="Skip packaging epass_flasher/bin even if it exists",
    )
    parser.add_argument(
        "--obfuscate",
        action="store_true",
        help="Obfuscate first-party Python sources with PyArmor before packaging",
    )
    parser.add_argument(
        "--no-portable",
        action="store_true",
        help="Skip the portable ZIP archive after cx_Freeze finishes",
    )
    return parser.parse_args(argv)


def get_site_packages():
    """获取 site-packages 路径（跨平台）

    参考: https://docs.python.org/3/library/sysconfig.html#sysconfig.get_path
    """
    import sysconfig

    return sysconfig.get_path("purelib")


def download_inno_setup():
    """下载 Inno Setup 便携版"""
    iscc_path = os.path.join(INNO_SETUP_DIR, "ISCC.exe")
    if os.path.exists(iscc_path):
        return iscc_path

    print("Downloading Inno Setup...")
    os.makedirs(INNO_SETUP_DIR, exist_ok=True)

    url = "https://github.com/jrsoftware/issrc/releases/download/is-6_7_1/innosetup-6.7.1.exe"
    installer_path = os.path.join(INNO_SETUP_DIR, "innosetup.exe")

    try:
        urllib.request.urlretrieve(url, installer_path)
        print("Installing Inno Setup (silent)...")
        subprocess.run(
            [
                installer_path,
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                f"/DIR={os.path.abspath(INNO_SETUP_DIR)}",
            ],
            check=True,
            capture_output=True,
        )
        os.remove(installer_path)

        if os.path.exists(iscc_path):
            print(f"Inno Setup installed: {iscc_path}")
            return iscc_path
    except Exception as e:
        print(f"Failed to download Inno Setup: {e}")

    return None


def find_inno_setup():
    """查找 Inno Setup"""
    local_iscc = os.path.join(INNO_SETUP_DIR, "ISCC.exe")
    if os.path.exists(local_iscc):
        return local_iscc

    paths = [
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ]
    for path in paths:
        if os.path.exists(path):
            return path
    return None


def _lstat_without_reparse(path):
    try:
        result = path.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(result.st_mode):
        return None
    try:
        attributes = result.st_file_attributes
    except (AttributeError, OSError):
        return None
    if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return None
    return result


def _cache_tree_is_safe(cache_path):
    pending = [cache_path]
    while pending:
        path = pending.pop()
        result = _lstat_without_reparse(path)
        if result is None:
            return False
        if not stat.S_ISDIR(result.st_mode):
            continue
        try:
            with os.scandir(path) as entries:
                pending.extend(Path(entry.path) for entry in entries)
        except OSError:
            return False
    return True


def _clean_pycache(base_dir="."):
    """仅清理明确项目源码目录内的安全 ``__pycache__`` 树。"""
    base_path = Path(base_dir)
    base_stat = _lstat_without_reparse(base_path)
    if base_stat is None or not stat.S_ISDIR(base_stat.st_mode):
        return

    root_cache = base_path / "__pycache__"
    if _cache_tree_is_safe(root_cache):
        shutil.rmtree(root_cache)
        print(f"  Cleared: {root_cache}")

    for source_root in PYCACHE_SOURCE_ROOTS:
        current = base_path
        for part in source_root.parts:
            current /= part
            current_stat = _lstat_without_reparse(current)
            if current_stat is None or not stat.S_ISDIR(current_stat.st_mode):
                break
        else:
            pending = [current]
            while pending:
                directory = pending.pop()
                try:
                    with os.scandir(directory) as entries:
                        children = [Path(entry.path) for entry in entries]
                except OSError:
                    continue
                for child in children:
                    child_stat = _lstat_without_reparse(child)
                    if child_stat is None or not stat.S_ISDIR(child_stat.st_mode):
                        continue
                    if child.name == "__pycache__":
                        if _cache_tree_is_safe(child):
                            shutil.rmtree(child)
                            print(f"  Cleared: {child}")
                        continue
                    pending.append(child)


def _diagnose_build_env():
    """打印构建环境诊断信息，便于 CI 调试"""
    import importlib.util
    import sysconfig as _sc

    print("\n--- Build Environment ---")
    print(f"  Python: {sys.version}")
    print(f"  Platform: {sys.platform}")
    print(f"  Executable: {sys.executable}")
    print(f"  purelib: {_sc.get_path('purelib')}")
    print(f"  platlib: {_sc.get_path('platlib')}")
    print(f"  sys.path ({len(sys.path)} entries):")
    for i, p in enumerate(sys.path):
        print(f"    [{i}] {p}")
    # 关键包定位
    for pkg in ("OpenGL", "cv2", "PyQt6", "cx_Freeze", "numpy"):
        spec = importlib.util.find_spec(pkg)
        status = spec.origin if spec else "NOT FOUND"
        print(f"  {pkg}: {status}")
    print("--- End ---\n")


def _verify_modules(search_paths, project_root):
    """统一验证所有关键模块可被 PathFinder 发现

    cx_Freeze finder.py:382-383 使用 importlib.machinery.PathFinder.find_spec(name, path)
    而非 importlib.util.find_spec（后者使用 sys.meta_path hooks，结果可能不同）。
    本函数同时检查本地项目模块和第三方包，并在 PathFinder 失败时使用
    importlib.util.find_spec 作为 fallback 定位包路径。
    """
    import importlib.machinery
    import importlib.util as _ilu

    # ── 本地项目模块检查 ──
    gui_path = os.path.join(project_root, "gui")
    local_modules = [
        ("gui", [project_root]),
        ("gui.main_window", [gui_path]),
        ("core", [project_root]),
        ("config", [project_root]),
    ]
    for mod_name, mod_path in local_modules:
        importlib.machinery.PathFinder.invalidate_caches()
        spec = importlib.machinery.PathFinder.find_spec(mod_name, mod_path)
        if spec is None:
            print(f"  FATAL: PathFinder cannot find {mod_name} in {mod_path}")
            print(f"  Directory listing: {os.listdir(mod_path[0])}")
            return False
        print(f"  PathFinder check: {mod_name} -> {spec.origin}")

    # ── 第三方包检查（含 fallback 路径发现）──
    # importlib.util.find_spec 使用完整的 sys.meta_path（包括 uv 的自定义 finder）
    # PathFinder.find_spec 只搜索给定的 path 列表
    pip_names = {"OpenGL": "PyOpenGL", "cv2": "opencv-python"}
    critical_packages = ["OpenGL", "cv2", "PyQt6", "requests"]
    missing = []

    for pkg_name in critical_packages:
        importlib.machinery.PathFinder.invalidate_caches()
        pf_spec = importlib.machinery.PathFinder.find_spec(pkg_name, search_paths)
        if pf_spec:
            print(f"  PathFinder check: {pkg_name} -> {pf_spec.origin}")
            continue

        # fallback: 使用 importlib.util.find_spec 定位并添加路径
        util_spec = _ilu.find_spec(pkg_name)
        if util_spec is None:
            pip_name = pip_names.get(pkg_name, pkg_name)
            print(f"  FATAL: {pkg_name} is NOT INSTALLED")
            print(f"         Run: uv pip install {pip_name}")
            missing.append(pkg_name)
            continue

        # 从 util_spec 提取父目录添加到搜索路径
        parent = None
        if util_spec.submodule_search_locations:
            parent = os.path.dirname(util_spec.submodule_search_locations[0])
        elif util_spec.origin:
            parent = os.path.dirname(os.path.dirname(util_spec.origin))
        if parent and parent not in search_paths:
            search_paths.insert(1, parent)
            print(f"  Fallback: added {parent} for {pkg_name}")
        print(
            f"  WARNING: PathFinder cannot find {pkg_name}, "
            f"but importlib.util found it at {util_spec.origin}"
        )

    if missing:
        print(f"\n  Cannot proceed without: {', '.join(missing)}")
        print(f"  This usually means 'uv sync' did not install these packages.")
        return False

    return True


def check_requirements():
    """检查构建环境"""
    print("Checking build environment...")

    try:
        import cx_Freeze

        print(f"  cx_Freeze: {cx_Freeze.__version__}")
    except ImportError:
        print("Error: cx_Freeze not installed")
        return False

    if not os.path.exists(MAIN_SCRIPT):
        print(f"Error: {MAIN_SCRIPT} not found")
        return False

    iscc = find_inno_setup()
    if iscc:
        print(f"  Inno Setup: found")
    else:
        print("  Inno Setup: not found (will download)")

    return True


def clean_build():
    """清理构建目录"""
    print("Cleaning...")
    for d in [BUILD_DIR, DIST_DIR]:
        if os.path.exists(d):
            shutil.rmtree(d)
            print(f"  Removed: {d}")
    if os.path.exists(OBFUSCATION_DIR):
        shutil.rmtree(OBFUSCATION_DIR)
        print(f"  Removed: {OBFUSCATION_DIR}")

    print("Cleaning __pycache__ directories...")
    _clean_pycache()


def _find_pyarmor_executable():
    """Return a PyArmor executable path if it is available."""
    candidates = [
        shutil.which("pyarmor"),
        os.path.join(os.path.dirname(sys.executable), "pyarmor.exe"),
        os.path.join(os.path.dirname(sys.executable), "pyarmor"),
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def prepare_obfuscated_source():
    """Generate a conservative PyArmor-obfuscated source tree."""
    pyarmor = _find_pyarmor_executable()
    if not pyarmor:
        print("Error: PyArmor not found. Install it before using --obfuscate.")
        return None

    source_root = os.path.abspath(OBFUSCATION_DIR)
    if os.path.exists(source_root):
        shutil.rmtree(source_root)
    os.makedirs(source_root, exist_ok=True)

    command = [
        pyarmor,
        "gen",
        "-r",
        "-O",
        source_root,
        *OBFUSCATABLE_ENTRIES,
    ]
    print("Running PyArmor obfuscation...")
    print("  " + " ".join(command))
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"PyArmor failed: {exc}")
        return None

    missing_entries = [
        entry
        for entry in OBFUSCATABLE_ENTRIES
        if not os.path.exists(os.path.join(source_root, entry))
    ]
    if missing_entries:
        print("PyArmor output is incomplete. Missing:")
        for entry in missing_entries:
            print(f"  {entry}")
        return None

    if not _pyarmor_runtime_packages(source_root):
        print("PyArmor output missing runtime package: pyarmor_runtime_*")
        return None

    return source_root


def _pyarmor_runtime_packages(source_root):
    """Return PyArmor runtime package names and paths in a source tree."""
    if not source_root or not os.path.isdir(source_root):
        return []
    result = []
    for name in sorted(os.listdir(source_root)):
        path = os.path.join(source_root, name)
        if name.startswith("pyarmor_runtime") and os.path.isdir(path):
            result.append((name, path))
    return result


def _collect_media_tool_include_files():
    """Return optional media tools and their sibling runtime files."""
    include_files = []
    if os.path.isdir(MEDIA_TOOL_DIR):
        for root, dirs, files in os.walk(MEDIA_TOOL_DIR):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for filename in files:
                if filename.endswith((".pyc", ".pyo")):
                    continue
                source_path = os.path.join(root, filename)
                target_path = os.path.relpath(source_path, ".")
                include_files.append((source_path, target_path))
        if include_files:
            print(f"  Including media tool runtime tree: {MEDIA_TOOL_DIR}")
            return include_files

    for filename, target_path in MEDIA_TOOL_CANDIDATES:
        for source_dir in MEDIA_TOOL_SOURCE_DIRS:
            source_path = os.path.join(source_dir, filename) if source_dir else filename
            if os.path.exists(source_path):
                include_files.append((source_path, target_path))
                print(f"  Including media tool: {source_path} -> {target_path}")
                break
    return include_files


def _collect_vs_contract_include_files(project_root):
    """Return required VS contract files or fail before freezing starts."""
    root = os.path.abspath(os.fspath(project_root))
    include_files = [
        (os.path.join(root, *relative.split("/")), relative)
        for relative in VS_CONTRACT_FILES
    ]
    missing = [source for source, _ in include_files if not os.path.isfile(source)]
    if missing:
        raise FileNotFoundError(
            "Required VS contract files missing: " + ", ".join(missing)
        )
    return include_files


def _collect_vs_worker_support_files(project_root):
    """验证 worker/runner 共用的 portable helper 与内置脚本。"""
    root = os.path.abspath(os.fspath(project_root))
    entries = [
        (os.path.join(root, *relative.split("/")), relative)
        for relative in VS_WORKER_SUPPORT_FILES
    ]
    missing = [source for source, _ in entries if not os.path.isfile(source)]
    if missing:
        raise FileNotFoundError(
            "Required VS worker support files missing: " + ", ".join(missing)
        )
    return entries


def _executable_specs(source_root, project_root, gui_base):
    """返回可单测的 GUI/worker cx_Freeze executable 参数。"""
    source = os.path.abspath(os.fspath(source_root))
    project = os.path.abspath(os.fspath(project_root))
    gui = {
        "script": os.path.join(source, MAIN_SCRIPT),
        "base": gui_base,
        "target_name": f"{PROJECT_NAME}.exe",
    }
    if os.path.exists(ICON_FILE):
        gui["icon"] = ICON_FILE
    return [
        gui,
        {
            "script": os.path.join(project, "vs_worker.py"),
            "base": None,
            "target_name": "vs_worker.exe",
        },
    ]


def run_cxfreeze(skip_flasher=False, source_root=None):
    """执行 cx_Freeze 打包"""

    # 确保项目根目录在 Python 路径中（防御性措施，正常应通过 uv sync --group dev 的 editable install 实现）
    project_root = os.path.dirname(os.path.abspath(__file__))
    source_root = os.path.abspath(source_root or project_root)
    try:
        vs_contract_include_files = _collect_vs_contract_include_files(
            project_root
        )
        vs_worker_support_files = _collect_vs_worker_support_files(project_root)
    except FileNotFoundError as exc:
        print(f"  FATAL: {exc}")
        return False
    for path in (project_root, source_root):
        if path in sys.path:
            sys.path.remove(path)
    sys.path.insert(0, source_root)
    if project_root != source_root:
        sys.path.insert(1, project_root)
    print(f"Project root: {project_root}")
    print(f"Source root: {source_root}")

    # 输出构建环境诊断信息
    _diagnose_build_env()

    # ── 构建 cx_Freeze 模块搜索路径 ──
    # cx_Freeze 只使用 PathFinder（finder.py:382-383），不使用 sys.meta_path。
    # 必须显式包含 site-packages，否则 uv/conda 等环境中第三方包可能找不到。
    import sysconfig as _sc

    search_paths = [source_root] + list(sys.path)
    for extra in [_sc.get_path("purelib"), _sc.get_path("platlib")]:
        if extra and os.path.isdir(extra) and extra not in search_paths:
            search_paths.insert(1, extra)
            print(f"  Added site-packages to search path: {extra}")

    # 统一验证本地模块和第三方包
    if not _verify_modules(search_paths, project_root):
        return False

    # 预编译检查：使用与 cx_Freeze 相同的 optimize 级别（finder.py:446-448）
    main_window_path = os.path.join(project_root, "gui", "main_window.py")
    try:
        with open(main_window_path, "rb") as f:
            compile(f.read(), main_window_path, "exec", optimize=2)
        print(f"  Compile check: gui/main_window.py (optimize=2) OK")
    except SyntaxError as e:
        print(f"  FATAL: gui/main_window.py compile error: {e}")
        return False

    # 清理 __pycache__，确保使用最新源代码编译
    print("Clearing __pycache__ before build...")
    _clean_pycache()

    os.environ["QT_API"] = "pyqt6"

    from cx_Freeze import setup, Executable

    site_packages = get_site_packages()

    packages = [
        "PyQt6",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        "PyQt6.QtOpenGLWidgets",
        "PyQt6.QtOpenGL",
        "qfluentwidgets",
        # PyOpenGL 不放在 packages 中 — packages 会触发 cx_Freeze 的
        # _import_all_sub_modules() 递归扫描整个 OpenGL/ 目录(2800+ 文件),
        # 任何子模块加载失败都会导致 ImportError 中止构建。
        # 改为在 includes 中精确指定入口点和动态加载的模块。
        "cv2",
        "PIL",
        "numpy",
        "jsonschema",
        "thefuzz",
        "logging",
        "json",
        "uuid",
        "dataclasses",
        "httpx",
        "httpcore",
        "httpx._transports",
        "requests",
        "urllib3",
        "certifi",
        "charset_normalizer",
        "idna",
        "keyring",
        "keyring.backends",
        "platformdirs",
        "usb",
        "fido2",
        "fido2.hid",
        "fido2.client",
        "fido2.webauthn",
        # 本地项目包 — 使用 packages 让 cx_Freeze 通过 _import_all_sub_modules 自动发现所有子模块
        "gui",
        "core",
        "config",
        "utils",
        "_mext",
    ]
    pyarmor_runtimes = _pyarmor_runtime_packages(source_root)
    for runtime_name, _runtime_path in pyarmor_runtimes:
        if runtime_name not in packages:
            packages.append(runtime_name)

    includes = [
        # ── PyOpenGL: 精确包含，避免 packages 递归发现导致构建失败 ──
        # 入口点 — cx_Freeze 自动跟踪 GL/__init__.py 中的 star-import 链
        # (GL.VERSION.GL_1_1~GL_4_6, GL.pointers, GL.images, GL.exceptional,
        #  GL.glget, GL.vboimplementation, raw.GL.VERSION.* 等所有静态依赖)
        "OpenGL",
        "OpenGL.GL",
        # 平台模块 — PlatformPlugin 使用 importByName() 动态加载，
        # cx_Freeze 静态分析无法跟踪 __import__ 中的字符串参数
        "OpenGL.platform.win32",
        "OpenGL.platform.ctypesloader",
        "OpenGL.platform.baseplatform",
        "OpenGL._configflags",
        "OpenGL.plugins",
        # 数组格式处理器 — FormatHandler 插件使用 __import__ 动态加载
        "OpenGL.arrays.numpymodule",
        "OpenGL.arrays.ctypesarrays",
        "OpenGL.arrays.ctypesparameters",
        "OpenGL.arrays.ctypespointers",
        "OpenGL.arrays.lists",
        "OpenGL.arrays.nones",
        "OpenGL.arrays.numbers",
        "OpenGL.arrays.strings",
        "OpenGL.arrays.buffers",
        "OpenGL.arrays.arraydatatype",
        "OpenGL.arrays.formathandler",
        "OpenGL.converters",
        # raw GL 绑定
        "OpenGL.raw.GL",
    ]

    excludes = [
        "tkinter",
        "unittest",
        "test",
        "tests",
        "pytest",
        "IPython",
        "notebook",
        "jupyter",
        "torch.testing",
        "torch.utils.tensorboard",
        "torch.utils.benchmark",
        "torch.distributed",
        "torchvision",
        "torchaudio",
        "scipy.spatial.cKDTree",
        "sympy",
        "PySide6",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        # OpenGL: 排除非 Windows 平台模块（finder.py:230 会跳过 excludes 中的模块）
        "OpenGL.platform.glx",
        "OpenGL.platform.darwin",
        "OpenGL.platform.egl",
        "OpenGL.platform.osmesa",
        "OpenGL.platform.entrypoint31",
        # OpenGL: 排除不需要的子包（减小体积，防止间接引用报错）
        "OpenGL.GLES1",
        "OpenGL.GLES2",
        "OpenGL.GLES3",
        "OpenGL.GLU",
        "OpenGL.GLUT",
        "OpenGL.GLE",
        "OpenGL.EGL",
        "OpenGL.GLX",
        "OpenGL.WGL",
        "OpenGL.AGL",
        "OpenGL.Tk",
    ]

    include_files = [
        ("resources", "resources"),
        (
            "resources/class_icons",
            "class_icons",
        ),  # 运行时通过 class_icons/ 相对路径访问
    ]

    # M1 同时分发严格 runtime/job contract 和供一次性迁移读取的旧配置。
    include_files.extend(vs_contract_include_files)
    for source_path, contract_path in vs_contract_include_files:
        print(f"  Including VS contract: {source_path} -> {contract_path}")
    for source_path, support_path in vs_worker_support_files:
        print(f"  Verified VS worker support: {source_path} -> {support_path}")
    for runtime_name, runtime_path in pyarmor_runtimes:
        include_files.append((runtime_path, runtime_name))
        print(f"  Including PyArmor runtime: {runtime_path}")
    include_files.extend(_collect_media_tool_include_files())

    # 添加 Rust 模拟器
    simulator_exe = os.path.join(
        "simulator", "target", "release", "arknights_pass_simulator.exe"
    )
    if os.path.exists(simulator_exe):
        # 创建目标目录结构
        target_path = os.path.join("simulator", "arknights_pass_simulator.exe")
        include_files.append((simulator_exe, target_path))
        print(f"  Including simulator: {simulator_exe}")

    # flasher_dialog 运行时会直接调用 epass_flasher/bin 下的工具。
    # 缺失时不再中止构建，保留对话框功能，由运行时自行提示或引导下载。
    flasher_bin_dir = os.path.join("epass_flasher", "bin")
    if os.path.exists(flasher_bin_dir) and not skip_flasher:
        include_files.append((flasher_bin_dir, os.path.join("epass_flasher", "bin")))
        print(f"  Including flasher bin dir: {flasher_bin_dir}")
    elif os.path.exists(flasher_bin_dir):
        print("  Warning: epass_flasher/bin/ exists but packaging was skipped")
    else:
        print("  Warning: epass_flasher/bin/ not found; flasher dialog will require runtime setup")

    pyqt6_plugins = os.path.join(site_packages, "PyQt6", "Qt6", "plugins")
    if os.path.exists(pyqt6_plugins):
        for plugin in ["platforms", "imageformats", "styles"]:
            plugin_path = os.path.join(pyqt6_plugins, plugin)
            if os.path.exists(plugin_path):
                include_files.append((plugin_path, f"lib/PyQt6/Qt6/plugins/{plugin}"))

    # libusb DLL（fido2 运行时依赖）
    libusb_dll = os.path.join(site_packages, "fido2", "libusb-1.0.dll")
    if os.path.exists(libusb_dll):
        include_files.append((libusb_dll, "libusb-1.0.dll"))
        print(f"  Including libusb: {libusb_dll}")

    build_options = {
        "packages": packages,
        "includes": includes,
        "excludes": excludes,
        "include_files": include_files,
        "optimize": 2,
        "build_exe": BUILD_DIR,
        "path": search_paths,
    }

    # Windows 上使用 "gui" base 避免出现控制台窗口（cx_Freeze 7.0+ 用 "gui" 替代了旧的 "Win32GUI"）
    base = "gui" if sys.platform == "win32" else None

    print(f"\n  Version: {VERSION}")
    print(f"  Packages ({len(packages)}): {', '.join(packages)}")
    print(f"  Include files ({len(include_files)}):")
    for src, dst in include_files:
        print(f"    {src} -> {dst}")

    # 使用 script_args 替代 sys.argv hack（参考 cx_Freeze cli.py:251 使用相同方式）
    try:
        setup(
            name=PROJECT_NAME,
            version=VERSION,
            description="Arknights Pass Material Maker",
            options={"build_exe": build_options},
            executables=[
                Executable(**spec)
                for spec in _executable_specs(source_root, project_root, base)
            ],
            script_args=["build"],
        )
        license_file = os.path.join(BUILD_DIR, "frozen_application_license.txt")
        if os.path.exists(license_file):
            os.remove(license_file)
        return True
    except Exception as e:
        import traceback

        print(f"\nBuild failed: {e}")
        traceback.print_exc()
        print(f"\nSearch paths ({len(search_paths)}):")
        for i, p in enumerate(search_paths):
            print(f"  [{i}] {p}")
        return False


def generate_install_manifest():
    """生成构建产物清单，用于调试和升级清理验证

    清单文件随 cx_Freeze 输出一起被 [Files] 的递归复制规则打入安装包，
    安装后可在安装目录中查看，便于排查升级清理遗漏。
    """
    manifest_path = os.path.join(BUILD_DIR, "install_manifest.txt")
    entries = []
    for root, dirs, files in os.walk(BUILD_DIR):
        rel_root = os.path.relpath(root, BUILD_DIR)
        if rel_root != '.':
            entries.append(f"D {rel_root}")
        for f in files:
            rel_path = os.path.join(rel_root, f) if rel_root != '.' else f
            entries.append(f"F {rel_path}")
    entries.sort()
    with open(manifest_path, 'w', encoding='utf-8') as mf:
        mf.write(f"# ArknightsPassMaker Build Manifest\n")
        mf.write(f"# Version: {VERSION}\n")
        mf.write(f"# Entries: {len(entries)}\n\n")
        for entry in entries:
            mf.write(entry + '\n')
    print(f"  Generated manifest: {manifest_path} ({len(entries)} entries)")


def portable_archive_name(version: str = VERSION) -> str:
    """Return the stable filename used for the Windows portable package."""
    return f"{PROJECT_NAME}-v{version}-windows-portable.zip"


def create_portable_archive(
    build_dir=BUILD_DIR,
    dist_dir=DIST_DIR,
    version: str = VERSION,
) -> Path:
    """Archive the complete cx_Freeze tree as a directly runnable ZIP package.

    The archive retains the top-level build directory.  Extracting it therefore
    never scatters DLLs and resources into the caller's current directory.
    A temporary sibling is atomically replaced only after ZipFile closes.
    """
    source_root = Path(build_dir)
    if not source_root.is_dir():
        raise FileNotFoundError(f"Portable build directory not found: {source_root}")

    output_dir = Path(dist_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / portable_archive_name(version)
    temporary_path = archive_path.with_suffix(".zip.tmp")
    temporary_path.unlink(missing_ok=True)

    try:
        with ZipFile(
            temporary_path,
            mode="w",
            compression=ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            for source_path in sorted(source_root.rglob("*")):
                if source_path.is_file():
                    archive.write(
                        source_path,
                        source_path.relative_to(source_root.parent).as_posix(),
                    )
        temporary_path.replace(archive_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    size_mb = archive_path.stat().st_size / 1024 / 1024
    print(f"  Created portable archive: {archive_path} ({size_mb:.2f} MB)")
    return archive_path


def create_installer():
    """创建安装包"""
    print("\n" + "=" * 50)
    print("Creating installer...")
    print("=" * 50)

    iscc = find_inno_setup()
    if not iscc:
        iscc = download_inno_setup()
    if not iscc:
        print("Error: Inno Setup not available")
        return False

    if not os.path.exists(ISS_FILE):
        print(f"Error: {ISS_FILE} not found")
        return False

    os.makedirs(DIST_DIR, exist_ok=True)

    try:
        result = subprocess.run(
            [iscc, f"/DMyAppVersion={VERSION}", ISS_FILE],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"Inno Setup failed: {result.stderr}")
            return False

        print(result.stdout)
        for f in os.listdir(DIST_DIR):
            if f.endswith(".exe"):
                path = os.path.join(DIST_DIR, f)
                size = os.path.getsize(path) / 1024 / 1024
                print(f"\nCreated: {path} ({size:.2f} MB)")
        return True
    except Exception as e:
        print(f"Error: {e}")
        return False


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    args = parse_args()

    print("=" * 50)
    print(f"  {PROJECT_NAME} Build Tool v{VERSION}")
    print("=" * 50)

    if not check_requirements():
        sys.exit(1)

    if args.clean:
        clean_build()

    source_root = None
    if args.obfuscate:
        source_root = prepare_obfuscated_source()
        if not source_root:
            sys.exit(1)

    print("\n" + "=" * 50)
    print("Running cx_Freeze...")
    print("=" * 50)

    if not run_cxfreeze(skip_flasher=args.skip_flasher, source_root=source_root):
        sys.exit(1)

    print(f"\ncx_Freeze done: {BUILD_DIR}/")
    generate_install_manifest()

    if args.no_portable:
        print("Portable archive skipped")
    else:
        try:
            create_portable_archive()
        except OSError as exc:
            print(f"Portable archive creation failed: {exc}")
            sys.exit(1)

    if not args.no_installer:
        if create_installer():
            print("\n" + "=" * 50)
            print("Build completed!")
            print("=" * 50)
        else:
            print("\nInstaller creation failed")
            sys.exit(1)


if __name__ == "__main__":
    main()
