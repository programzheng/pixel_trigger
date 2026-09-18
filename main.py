"""PixelTrigger — 通用圖像觸發自動化工具"""

from gui import App
import ctypes
ctypes.windll.shcore.SetProcessDpiAwareness(2)


def main():
    app = App()

    app._register_hotkeys()

    app.root.protocol("WM_DELETE_WINDOW", app.on_close)

    print(f"pixel_trigger 已啟動！主動偵測：{app.profile.hotkey}，背景偵測：{app.profile.background_hotkey}")
    app.run()

    app._unregister_hotkeys()


if __name__ == "__main__":
    main()
