import threading
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import numpy as np

from automation import HookEngine
from gui import App
from models import Combo, Profile
from window_target import get_window_region, list_windows


class WindowTargetTest(unittest.TestCase):
    def test_real_window_tracks_geometry_and_rejects_hidden_or_wrong_process(self):
        root = tk.Tk()
        root.title("PixelTrigger window target test")
        try:
            root.geometry("320x180+80+90")
            root.update()
            hwnd, _, pid = next(w for w in list_windows() if w[1] == root.title())
            first = get_window_region(hwnd, pid)
            self.assertEqual(first[2:], (320, 180))
            root.geometry("420x240+140+150")
            root.update()
            second = get_window_region(hwnd, pid)
            self.assertEqual(second[2:], (420, 240))
            self.assertEqual((second[0] - first[0], second[1] - first[1]), (60, 60))
            self.assertIsNone(get_window_region(hwnd, pid + 1))
            root.iconify()
            root.update()
            self.assertIsNone(get_window_region(hwnd, pid))
            root.deiconify()
            root.update()
            self.assertIsNotNone(get_window_region(hwnd, pid))
            root.withdraw()
            root.update()
            self.assertIsNone(get_window_region(hwnd, pid))
        finally:
            root.destroy()
        self.assertIsNone(get_window_region(hwnd, pid))

    def test_scan_uses_current_region_and_cancels_when_window_disappears(self):
        combo = Combo(verify_text="ready")
        engine = HookEngine()
        engine.set_profile(Profile(window_title="Game", window_handle=10, window_pid=20,
                                   scan_region=(1, 2, 3, 4), combos=[combo]))
        cancelled = threading.Event()
        engine._action_stop_events[combo.id] = cancelled
        pool = Mock()
        pool.submit.return_value.done.return_value = True
        regions = [(80, 90, 320, 180), (-420, 150, 420, 240)]
        with patch("automation.get_window_region", side_effect=regions + [None]), \
                patch.object(engine._capture, "grab_frame", return_value=np.zeros((2, 2))) as grab, \
                patch.object(engine, "_interruptible_sleep"):
            engine._scan_frames(pool, {})
        self.assertEqual([c.args[0] for c in grab.call_args_list], regions)
        self.assertEqual([c.args[-1] for c in pool.submit.call_args_list], regions)
        self.assertTrue(engine._stop_event.is_set())
        self.assertTrue(cancelled.is_set())

    def test_profile_restore_and_explicit_selection_of_duplicate_titles(self):
        profile = Profile(window_title="Game", window_handle=10, window_pid=20)
        restored = Profile.from_dict(profile.to_dict())
        self.assertEqual(restored.window_title, "Game")
        self.assertEqual(restored.window_handle, 0)
        self.assertEqual(Profile.from_dict({}).window_title, "")
        engine = HookEngine()
        engine.set_profile(restored)
        with patch("automation.list_windows", return_value=[(10, "Game", 20), (11, "Game", 21)]):
            with self.assertRaises(ValueError):
                engine.start()
        with patch.object(App, "_load_initial_profile"), \
                patch("gui.list_windows", return_value=[(10, "Game", 20), (11, "Game", 21)]):
            app = App()
        app.root.withdraw()
        try:
            self.assertTrue(all(c.cget("state") == "normal" for c in app.region_controls))
            app.cmb_window.current(2)
            app._on_window_selected()
            self.assertTrue(all(c.cget("state") == "disabled" for c in app.region_controls))
            self.assertEqual((app.profile.window_handle, app.profile.window_pid), (11, 21))
            app._sync_region_from_ui()
            self.assertEqual(app.profile.window_title, "Game")
            app.cmb_window.current(0)
            app._on_window_selected()
            self.assertEqual(app.profile.window_title, "")
            self.assertTrue(all(c.cget("state") == "normal" for c in app.region_controls))
            app.profile = restored
            with patch("gui.list_windows", return_value=[]):
                app._sync_region_to_ui()
            self.assertTrue(all(c.cget("state") == "disabled" for c in app.region_controls))
        finally:
            app.root.destroy()


if __name__ == "__main__":
    unittest.main()
