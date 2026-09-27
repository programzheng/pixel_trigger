"""
Build script: 將 pixel_trigger 編譯成 Windows 單一執行檔或資料夾版

用法：
    uv run python build.py
    uv run python build.py --standalone
    uv run python build.py --pyinstaller
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent

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
    "--module-parameter=torch-disable-jit=yes",
    # 保留動態載入的 OCR 模型和圖片格式；一般 import 由 Nuitka 追蹤。
    "--include-package=easyocr",
    "--include-package=PIL",
    "--include-package=ttkbootstrap",
    "--include-package-data=ttkbootstrap",
    # 包含資料目錄
    "--include-data-dir=templates=templates",
    "--include-data-dir=profiles=profiles",
    "--include-data-dir=models=models",  # easyocr 模型（需先執行 prepare_models.py）
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
    "--collect-all=easyocr",
    "--collect-all=ttkbootstrap",
    f"--add-data={PROJECT_DIR / 'models'}:models",
    f"--add-data={PROJECT_DIR / 'assets'}:assets",
    "main.py",
]


if __name__ == "__main__":
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
        print(f"\n打包完成：{output}（耗時 {time.perf_counter() - started:.0f} 秒）")
    else:
        print("\n打包失敗")
        sys.exit(result.returncode)
