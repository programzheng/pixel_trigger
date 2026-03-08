"""
Build 前置作業：將 easyocr 模型複製到專案 models/ 目錄，供 Nuitka 打包使用。

用法：
    uv run python prepare_models.py

會自動觸發 easyocr 下載模型（若尚未下載），再複製到 ./models/
"""

import os
import shutil
import sys


LANGS = ["ch_tra", "en"]
SRC_DIR = os.path.join(os.path.expanduser("~"), ".EasyOCR", "model")
DST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")


def download_models():
    print("初始化 easyocr，若模型尚未下載會自動下載（約 300MB）...")
    import easyocr
    easyocr.Reader(LANGS, gpu=False, verbose=True)
    print(f"模型已存於：{SRC_DIR}")


def copy_models():
    if not os.path.isdir(SRC_DIR):
        print(f"找不到模型目錄：{SRC_DIR}")
        sys.exit(1)

    os.makedirs(DST_DIR, exist_ok=True)
    copied = []
    for fname in os.listdir(SRC_DIR):
        src = os.path.join(SRC_DIR, fname)
        dst = os.path.join(DST_DIR, fname)
        shutil.copy2(src, dst)
        copied.append(fname)

    print(f"\n已複製 {len(copied)} 個模型檔案到 {DST_DIR}：")
    for f in copied:
        print(f"  {f}")


if __name__ == "__main__":
    download_models()
    copy_models()
    print("\n完成！可以執行 uv run python build.py 進行打包。")
