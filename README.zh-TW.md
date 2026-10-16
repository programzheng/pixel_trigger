# Pixel Trigger

[English](README.md) | 繁體中文

Pixel Trigger 是一款 Windows 桌面自動化工具，能根據畫面上的視覺變化執行操作。
它透過 Tkinter 圖形介面，整合圖片比對、OCR 與色彩追蹤，並可設定滑鼠、
鍵盤及提示音動作。

## 功能特色

- 使用 OpenCV 進行多尺度圖片比對。
- 使用 Tesseract OCR 驗證繁體中文與英文文字。
- 在指定區域內追蹤色彩標記與色帶。
- 觸發點擊、按鍵、長按、連續輸入或提示音。
- 將擷取範圍限制在指定螢幕區域或應用程式視窗。
- 以 JSON 儲存可重複使用的自動化設定檔。
- 將辨識到的數值變化記錄至每日 CSV 帳本。
- 可為每個 profile 建立獨立統計，使用 OCR 餘額或手動收入、支出紀錄。
- 透過日誌、預覽與滑鼠穿透的除錯覆蓋層檢查辨識結果。
- 將應用程式與 OCR 模型封裝為可離線使用的 Windows 程式。

## 系統需求

- Windows 10 或更新版本
- Python 3.12 或更新版本
- [uv](https://docs.astral.sh/uv/)

全域快捷鍵、視窗鎖定與輸入自動化功能依賴 Windows API。

### 建議硬體

近年款的 4 核心 CPU 搭配 4 GB 記憶體，在 Windows 與目標程式啟動後仍有足夠
可用記憶體時，可以嘗試基本偵測。一般使用建議 8 GB 記憶體與 SSD；若其他程式
大量使用記憶體，可考慮 16 GB。無須獨立顯示卡。以上是使用建議，並非經實測
驗證的最低規格。

偵測速度主要受 CPU 負載、擷取範圍、啟用的規則數量及 OCR 使用情況影響。
預設每輪擷取後會等待 0.1 秒；若圖片比對或 OCR 較慢，實際偵測頻率會降低。
建議先縮小擷取範圍，再考慮縮短掃描間隔。若記憶體不足而讓 Windows 開始
使用交換檔，偵測也可能變慢。

封裝後的執行檔不需要另外安裝 Python；從原始碼執行則需要上述 Python 與
uv。文字驗證另需 Tesseract 及 `eng`、`chi_tra` 語言資料。

## 開始使用

```powershell
git clone https://github.com/programzheng/pixel_trigger.git
cd pixel_trigger
uv sync --dev
uv run python main.py
```

文字驗證需要 Tesseract 與 `eng`、`chi_tra` 語言資料。開發模式會偵測 PATH 或預設 Windows 安裝；建立可離線使用的版本前請執行：

```powershell
uv run python prepare_tesseract.py
```

## 使用方式

1. 建立或選擇設定檔。
2. 選擇畫面擷取區域或目標視窗。
3. 新增組合，並設定觸發圖片、OCR 條件或色彩規則。
4. 選擇執行動作，調整信心門檻與時間設定。
5. 使用已設定的全域快捷鍵啟動或停止偵測。

在「統計」中可建立純手動統計，或選擇背景 OCR Combo 作為來源，並設定收入、
支出、淨額、紀錄次數或最新餘額等規則與期間。新紀錄儲存在 `statistics/`；
既有 `calc/` CSV 會原樣保留，但不計入新統計。

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
uv run python prepare_tesseract.py
uv run python build.py
```

執行檔會輸出至 `dist/pixel_trigger.exe`。亦可使用 `--standalone` 或
`--pyinstaller` 選擇其他建置模式。建置也會在執行檔旁產生
`THIRD_PARTY_NOTICES.txt`，並可從「關於／第三方授權」視窗查看；
授權檔產生或依賴稽核失敗時請勿散布。

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
| `stat_tracking.py` | 統計計算與手動 CSV 紀錄 |
| `statistics_ui.py` | 獨立統計視窗與編輯器 |
| `window_target.py` | Windows 視窗搜尋與客戶區域鎖定 |
| `test_*.py` | 單元及整合導向的回歸測試 |

## 參與貢獻

開發指令、測試要求及 Pull Request 規範請參閱 [AGENTS.md](AGENTS.md)。

請只在獲得授權的應用程式與環境中使用自動化功能。
