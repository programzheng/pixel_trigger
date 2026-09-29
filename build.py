"""
Build script: 將 pixel_trigger 編譯成 Windows 單一執行檔或資料夾版

用法：
    uv run python build.py
    uv run python build.py --standalone
    uv run python build.py --pyinstaller
"""

import argparse
import importlib.metadata as metadata
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
TESSERACT_DIR = PROJECT_DIR / "vendor" / "tesseract"
NOTICES_FILE = PROJECT_DIR / "THIRD_PARTY_NOTICES.txt"
GENERATED_NOTICES = PROJECT_DIR / "dist" / "THIRD_PARTY_NOTICES.txt"
NUITKA_REPORT = PROJECT_DIR / "dist" / "nuitka-report.xml"
FORBIDDEN_MODULES = ("mouseinfo", "easyocr", "bidi", "torch", "torchvision")
NOTICE_DISTRIBUTIONS = (
    "opencv-python", "pyautogui", "mss", "keyboard", "numpy", "pillow",
    "ttkbootstrap", "pymsgbox", "pytweening", "pyscreeze", "pygetwindow",
    "pyrect", "pyperclip", "nuitka", "pyinstaller", "pyinstaller-hooks-contrib",
)

NUITKA_ARGS = [
    sys.executable, "-m", "nuitka",
    # 輸出模式
    "--onefile",
    "--windows-console-mode=disable",
    # 輸出名稱
    "--output-filename=pixel_trigger.exe",
    "--output-dir=dist",
    # Plugins
    "--enable-plugin=tk-inter",
    *[f"--nofollow-import-to={module}" for module in FORBIDDEN_MODULES],
    f"--report={NUITKA_REPORT}",
    # 保留動態載入的 OCR 模型和圖片格式；一般 import 由 Nuitka 追蹤。
    "--include-package=PIL",
    "--include-package=ttkbootstrap",
    "--include-package-data=ttkbootstrap",
    # 包含資料目錄
    "--include-data-dir=templates=templates",
    "--include-data-dir=profiles=profiles",
    f"--include-data-dir={TESSERACT_DIR}=tesseract",
    f"--include-data-files={GENERATED_NOTICES}=THIRD_PARTY_NOTICES.txt",
    "--include-data-dir=assets=assets",
    # Windows 版本資訊（可選）
    "--windows-product-name=PixelTrigger",
    "--windows-file-version=0.2.0.0",
    # 進入點
    "main.py",
]

PYINSTALLER_ARGS = [
    sys.executable, "-m", "PyInstaller",
    "--onedir", "--windowed", "--noconfirm", "--noupx",
    # 資料與 exe 放在同一層，沿用現有模型、profile 與範本路徑。
    "--contents-directory=.",
    "--name=pixel_trigger",
    "--distpath=dist/pyinstaller",
    "--workpath=dist/pyinstaller-build",
    "--specpath=dist/pyinstaller-build",
    "--collect-all=ttkbootstrap",
    *[option for module in FORBIDDEN_MODULES for option in ("--exclude-module", module)],
    f"--add-data={TESSERACT_DIR}:tesseract",
    f"--add-data={GENERATED_NOTICES}:.",
    f"--add-data={PROJECT_DIR / 'assets'}:assets",
    "main.py",
]


def _validate_inputs():
    required = (
        TESSERACT_DIR / "tesseract.exe",
        TESSERACT_DIR / "manifest.json",
        TESSERACT_DIR / "tessdata" / "eng.traineddata",
        TESSERACT_DIR / "tessdata" / "chi_tra.traineddata",
        NOTICES_FILE,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("Missing build inputs; run prepare_tesseract.py first:\n" + "\n".join(missing))


def _generate_notices():
    sections = [NOTICES_FILE.read_text(encoding="utf-8")]
    for name in NOTICE_DISTRIBUTIONS:
        distribution = metadata.distribution(name)
        sections.append(f"\n\n===== {distribution.metadata['Name']} {distribution.version} =====\n")
        license_files = distribution.metadata.get_all("License-File") or []
        if license_files:
            for filename in license_files:
                candidates = [file for file in distribution.files or ()
                              if file.as_posix().endswith("/" + filename)]
                preferred = next((file for file in candidates
                                  if ".dist-info/licenses/" in file.as_posix()), None)
                path = Path(distribution.locate_file(preferred or candidates[0])) \
                    if candidates else Path(distribution.locate_file(filename))
                if path.is_file():
                    sections.append(path.read_text(encoding="utf-8", errors="replace"))
        else:
            candidates = [file for file in distribution.files or ()
                          if ".dist-info/" in file.as_posix()
                          and Path(file.name).name.lower().startswith(("license", "copying"))]
            if candidates:
                sections.append(Path(distribution.locate_file(candidates[0])).read_text(
                    encoding="utf-8", errors="replace"))
            else:
                sections.append(distribution.metadata.get(
                    "License", "License metadata unavailable."))
    for path in (
        Path(sys.base_prefix) / "LICENSE.txt",
        TESSERACT_DIR / "doc" / "LICENSE",
    ):
        if path.is_file():
            sections.extend((f"\n\n===== {path.name} ({path.parent.name}) =====\n",
                             path.read_text(encoding="utf-8", errors="replace")))
    GENERATED_NOTICES.parent.mkdir(parents=True, exist_ok=True)
    GENERATED_NOTICES.write_text("".join(sections), encoding="utf-8")


def _validate_nuitka_report():
    if not NUITKA_REPORT.is_file():
        raise SystemExit(f"Nuitka did not create its compilation report: {NUITKA_REPORT}")
    included = {
        value for element in ET.parse(NUITKA_REPORT).iter()
        for key, value in element.attrib.items() if key in ("name", "module_name")
    }
    forbidden = sorted(name for name in included if name.split(".", 1)[0] in FORBIDDEN_MODULES)
    if forbidden:
        raise SystemExit("Forbidden modules found in build: " + ", ".join(forbidden))


if __name__ == "__main__":
    _validate_inputs()
    _generate_notices()
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--standalone", action="store_true", help="Nuitka 資料夾版，省略單檔封裝")
    modes.add_argument("--pyinstaller", action="store_true", help="PyInstaller 資料夾版，保留完整 OCR")
    args = parser.parse_args()
    output = "dist/pixel_trigger.exe"
    command = NUITKA_ARGS
    if args.standalone:
        NUITKA_ARGS[NUITKA_ARGS.index("--onefile")] = "--standalone"
        NUITKA_ARGS[NUITKA_ARGS.index("--output-dir=dist")] = "--output-dir=dist/standalone"
        output = "dist/standalone/main.dist/pixel_trigger.exe"
    elif args.pyinstaller:
        command = PYINSTALLER_ARGS
        output = "dist/pyinstaller/pixel_trigger/pixel_trigger.exe"
    print("開始打包 pixel_trigger...", flush=True)
    started = time.perf_counter()
    result = subprocess.run(command)
    if result.returncode == 0:
        if not args.pyinstaller:
            _validate_nuitka_report()
        output_path = PROJECT_DIR / output
        if output_path.is_file():
            shutil.copy2(GENERATED_NOTICES, output_path.parent / GENERATED_NOTICES.name)
        print(f"\n打包完成：{output}（耗時 {time.perf_counter() - started:.0f} 秒）")
    else:
        print("\n打包失敗")
        sys.exit(result.returncode)
