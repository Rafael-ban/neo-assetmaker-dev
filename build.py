import os
import sys
import subprocess
import argparse

PROJECT_NAME = "ArknightsPassMaker"
MAIN_SCRIPT = "main.py"
ICON_FILE = "resources/icons/favicon.ico"


def parse_args():
    parser = argparse.ArgumentParser(
        description=f"{PROJECT_NAME} - Nuitka Build Tool"
    )
    parser.add_argument(
        '--with-ocr',
        action='store_true',
        help='Include OCR support (easyocr/torch/scipy). Slower build, larger executable.'
    )
    return parser.parse_args()


def get_nuitka_args(with_ocr: bool):
    """Generate Nuitka arguments based on build mode."""
    output_name = f"{PROJECT_NAME}-OCR.exe" if with_ocr else f"{PROJECT_NAME}.exe"

    args = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--onefile",
        f"--output-filename={output_name}",
        "--windows-console-mode=disable",
        "--enable-plugin=pyqt6",
        "--include-package=config",
        "--include-package=core",
        "--include-package=gui",
        "--include-package=utils",
        "--include-data-dir=resources=resources",
        "--assume-yes-for-downloads",
        "--remove-output",
        "--output-dir=dist",
        # Memory optimization
        "--jobs=1",
        "--lto=no",
    ]

    if with_ocr:
        # OCR version: include torch but exclude unnecessary modules
        args.extend([
            "--module-parameter=torch-disable-jit=yes",
            # Exclude test modules to reduce size
            "--nofollow-import-to=torch.testing",
            "--nofollow-import-to=torch.utils.tensorboard",
            "--nofollow-import-to=torch.utils.benchmark",
            "--nofollow-import-to=torch.distributed",
            "--nofollow-import-to=torchvision",
            "--nofollow-import-to=torchaudio",
            "--nofollow-import-to=sympy",
            "--nofollow-import-to=sympy.*",
            "--nofollow-import-to=scipy.integrate",
            "--nofollow-import-to=scipy.optimize",
            "--nofollow-import-to=scipy.stats",
        ])
    else:
        # Standard version: exclude all OCR dependencies
        args.extend([
            # Exclude OCR and heavy dependencies
            "--nofollow-import-to=easyocr",
            "--nofollow-import-to=easyocr.*",
            "--nofollow-import-to=torch",
            "--nofollow-import-to=torch.*",
            "--nofollow-import-to=torchvision",
            "--nofollow-import-to=torchvision.*",
            "--nofollow-import-to=torchaudio",
            "--nofollow-import-to=torchaudio.*",
            "--nofollow-import-to=scipy",
            "--nofollow-import-to=scipy.*",
            "--nofollow-import-to=sympy",
            "--nofollow-import-to=sympy.*",
        ])

    # Common exclusions for both versions
    args.extend([
        "--nofollow-import-to=pytest",
        "--nofollow-import-to=unittest",
        "--nofollow-import-to=IPython",
        "--nofollow-import-to=notebook",
        "--nofollow-import-to=jupyter",
    ])

    args.append(MAIN_SCRIPT)
    return args, output_name


def check_requirements(nuitka_args: list):
    print("Checking build environment...")

    try:
        result = subprocess.run(
            [sys.executable, "-m", "nuitka", "--version"],
            capture_output=True, text=True
        )
        print(f"Nuitka version: {result.stdout.strip()}")
    except Exception:
        print("Error: Nuitka not installed, run: pip install nuitka")
        return False

    if not os.path.exists(MAIN_SCRIPT):
        print(f"Error: Main script {MAIN_SCRIPT} not found")
        return False

    if os.path.exists(ICON_FILE):
        print(f"Icon file: {ICON_FILE} found")
        nuitka_args.insert(-1, f"--windows-icon-from-ico={ICON_FILE}")
    else:
        print(f"Warning: Icon file {ICON_FILE} not found")

    if os.path.exists("ffmpeg.exe"):
        print("ffmpeg.exe: found")
        nuitka_args.insert(-1, "--include-data-files=ffmpeg.exe=ffmpeg.exe")
    else:
        print("Warning: ffmpeg.exe not found, video export may not work")

    return True


def build(nuitka_args: list, output_name: str):
    print("\n" + "=" * 50)
    print("Starting Nuitka build...")
    print("=" * 50 + "\n")

    print("Command:")
    print(" ".join(nuitka_args))
    print()

    result = subprocess.run(nuitka_args)

    if result.returncode == 0:
        print("\n" + "=" * 50)
        print("Build successful!")
        print(f"Output: dist/{output_name}")
        print("=" * 50)
        return True
    else:
        print("\n" + "=" * 50)
        print("Build failed!")
        print("=" * 50)
        return False


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    args = parse_args()

    build_mode = "OCR" if args.with_ocr else "Standard"
    nuitka_args, output_name = get_nuitka_args(args.with_ocr)

    print("=" * 50)
    print(f"  {PROJECT_NAME} - Nuitka Build Tool")
    print(f"  Build Mode: {build_mode}")
    print("=" * 50)

    if args.with_ocr:
        print("\nOCR mode: Including easyocr/torch/scipy (slower build)")
    else:
        print("\nStandard mode: Excluding OCR dependencies (faster build)")

    if not check_requirements(nuitka_args):
        sys.exit(1)

    if build(nuitka_args, output_name):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
