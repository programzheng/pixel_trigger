"""
Build script: 將 pixel_trigger 編譯成 Windows 單一執行檔

用法：
    uv run python build.py
"""

import subprocess
import sys

NUITKA_ARGS = [
    sys.executable, "-m", "nuitka",
    # 輸出模式
    "--onefile",
    "--windows-disable-console",
    # 輸出名稱
    "--output-filename=pixel_trigger.exe",
    "--output-dir=dist",
    # Plugins
    "--enable-plugin=tk-inter",
    "--enable-plugin=numpy",
    "--enable-plugin=torch",   # easyocr 依賴 PyTorch
    # 包含套件（Nuitka 靜態分析可能遺漏的）
    "--include-package=cv2",
    "--include-package=easyocr",
    "--include-package=PIL",
    "--include-package=mss",
    "--include-package=keyboard",
    "--include-package=pyautogui",
    # 包含資料目錄
    "--include-data-dir=templates=templates",
    "--include-data-dir=profiles=profiles",
    "--include-data-dir=models=models",  # easyocr 模型（需先執行 prepare_models.py）
    # Windows 版本資訊（可選）
    "--windows-product-name=PixelTrigger",
    "--windows-file-version=0.2.0.0",
    # 進入點
    "main.py",
]

if __name__ == "__main__":
    print("開始編譯 pixel_trigger...")
    result = subprocess.run(NUITKA_ARGS)
    if result.returncode == 0:
        print("\n✓ 編譯完成：dist/pixel_trigger.exe")
    else:
        print("\n✗ 編譯失敗")
        sys.exit(result.returncode)
