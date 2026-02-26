"""PixelTrigger — 通用圖像觸發自動化工具"""

from gui import App
import keyboard
import ctypes
ctypes.windll.shcore.SetProcessDpiAwareness(2)


def main():
    app = App()

    # keyboard 的回呼在內部執行緒執行，不能直接呼叫 tkinter
    # 透過 queue 讓 main thread 處理
    keyboard.add_hotkey(
        app.profile.hotkey,
        lambda: app._msg_queue.append(("hotkey",)),
    )

    app.root.protocol("WM_DELETE_WINDOW", app.on_close)

    print(f"pixel_trigger 已啟動！按 {app.profile.hotkey} 啟動/停止")
    app.run()

    keyboard.unhook_all()


if __name__ == "__main__":
    main()
