"""Windows 視窗清單與內容區域，不需要額外依賴。"""

import ctypes
from ctypes import wintypes


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_enum_callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
for name, args, result in (
    ("EnumWindows", [_enum_callback, wintypes.LPARAM], wintypes.BOOL),
    ("IsWindow", [wintypes.HWND], wintypes.BOOL),
    ("IsWindowVisible", [wintypes.HWND], wintypes.BOOL),
    ("IsIconic", [wintypes.HWND], wintypes.BOOL),
    ("GetWindowTextLengthW", [wintypes.HWND], ctypes.c_int),
    ("GetWindowTextW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
    ("GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
    ("GetClientRect", [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
    ("ClientToScreen", [wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
):
    function = getattr(_user32, name)
    function.argtypes = args
    function.restype = result


def list_windows() -> list[tuple[int, str, int]]:
    windows = []

    @_enum_callback
    def collect(hwnd, _):
        if _user32.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(_user32.GetWindowTextLengthW(hwnd) + 1)
            _user32.GetWindowTextW(hwnd, title, len(title))
            if title.value:
                pid = wintypes.DWORD()
                _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                windows.append((hwnd, title.value, pid.value))
        return True

    if not _user32.EnumWindows(collect, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return sorted(windows, key=lambda item: (item[1].casefold(), item[2], item[0]))


def get_window_region(hwnd: int, pid: int) -> tuple[int, int, int, int] | None:
    """每次重新取得內容區域；失效／最小化時不退回全螢幕。"""
    # ponytail: 以螢幕範圍擷取，視窗需可見；需要遮擋擷取時改用 Windows.Graphics.Capture。
    actual_pid = wintypes.DWORD()
    if (not _user32.IsWindow(hwnd) or not _user32.IsWindowVisible(hwnd)
            or _user32.IsIconic(hwnd)):
        return None
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(actual_pid))
    if actual_pid.value != pid:
        return None
    rect, origin = wintypes.RECT(), wintypes.POINT()
    if (not _user32.GetClientRect(hwnd, ctypes.byref(rect))
            or not _user32.ClientToScreen(hwnd, ctypes.byref(origin))):
        return None
    width, height = rect.right - rect.left, rect.bottom - rect.top
    return (origin.x, origin.y, width, height) if min(width, height) > 0 else None
