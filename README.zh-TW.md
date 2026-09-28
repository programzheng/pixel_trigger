# Pixel Trigger

[English](README.md) | 繁體中文

Pixel Trigger 是一款 Windows 桌面自動化工具，能根據畫面上的視覺變化執行操作。
它透過 Tkinter 圖形介面，整合圖片比對、OCR 與色彩追蹤，並可設定滑鼠、
鍵盤及提示音動作。

## 功能特色

- 使用 OpenCV 進行多尺度圖片比對。
- 使用 EasyOCR 驗證繁體中文與英文文字。
- 在指定區域內追蹤色彩標記與色帶。
- 觸發點擊、按鍵、長按、連續輸入或提示音。
- 將擷取範圍限制在指定螢幕區域或應用程式視窗。
- 以 JSON 儲存可重複使用的自動化設定檔。
- 將辨識到的數值變化記錄至每日 CSV 帳本。
- 透過日誌、預覽與滑鼠穿透的除錯覆蓋層檢查辨識結果。
- 將應用程式與 OCR 模型封裝為可離線使用的 Windows 程式。

## 系統需求

- Windows 10 或更新版本
- Python 3.12 或更新版本
- [uv](https://docs.astral.sh/uv/)

全域快捷鍵、視窗鎖定與輸入自動化功能依賴 Windows API。

## 開始使用

```powershell
git clone https://github.com/programzheng/pixel_trigger.git
cd pixel_trigger
uv sync --dev
uv run python main.py
```

EasyOCR 會在第一次使用時下載語言模型。若要在建立離線版本前準備本機模型，請執行：

```powershell
uv run python prepare_models.py
```

## 使用方式

1. 建立或選擇設定檔。
2. 選擇畫面擷取區域或目標視窗。
3. 新增組合，並設定觸發圖片、OCR 條件或色彩規則。
4. 選擇執行動作，調整信心門檻與時間設定。
5. 使用已設定的全域快捷鍵啟動或停止偵測。

設定檔與擷取的範本圖片分別儲存在 `profiles/` 與 `templates/`，這兩個目錄
都不會納入版本控制。

## 測試

本專案使用 Python 標準函式庫的 `unittest` 框架：

```powershell
uv run python -m unittest discover
```

部分圖形介面與 Windows 整合行為仍應進行人工驗證。

## 建置

建立預設的 Nuitka 單檔執行程式：

```powershell
uv run python prepare_models.py
uv run python build.py
```

執行檔會輸出至 `dist/pixel_trigger.exe`。亦可使用 `--standalone` 或
`--pyinstaller` 選擇其他建置模式。

## 專案結構

| 路徑 | 用途 |
| --- | --- |
| `gui.py` | Tkinter 介面、編輯器、區域選取工具與除錯覆蓋層 |
| `automation.py` | 偵測迴圈、觸發狀態與動作排程 |
| `screen_capture.py` | 螢幕擷取、範本比對、OCR 與色彩偵測 |
| `input_controller.py` | 可中斷的滑鼠、鍵盤與提示音操作 |
| `models.py` | 具型別的設定檔、組合、動作與觸發條件模型 |
| `profile_manager.py` | JSON 設定檔持久化 |
| `calc.py` | 背景數值辨識與 CSV 帳本處理 |
| `window_target.py` | Windows 視窗搜尋與客戶區域鎖定 |
| `test_*.py` | 單元及整合導向的回歸測試 |

## 參與貢獻

開發指令、測試要求及 Pull Request 規範請參閱 [AGENTS.md](AGENTS.md)。

請只在獲得授權的應用程式與環境中使用自動化功能。
