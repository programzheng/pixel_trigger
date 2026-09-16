import unittest
from unittest.mock import patch

from gui import App
from models import Combo, Profile


class ComboScaleDisplayTest(unittest.TestCase):
    def test_scale_survives_status_updates_and_refresh(self):
        with patch.object(App, "_load_initial_profile"):
            app = App()
        app.root.withdraw()
        try:
            combo = Combo(image_path="template.png")
            app.profile = Profile(combos=[combo])
            app._refresh_combo_tree()
            self.assertEqual(app.tree.set(combo.id, "scale"), "—")
            with patch.object(app.engine, "get_match_scale", return_value=0.75):
                app._on_combo_triggered(combo.id, True)
                app._poll_queue()
                self.assertEqual(app.tree.set(combo.id, "scale"), "75.0%")
                self.assertEqual(app.tree.set(combo.id, "status"), ">>>")
                app._debug_mode.set(True)
                app._on_debug_info(combo.id, False, 0.2, None)
                app._on_cycle_complete(1)
                app._poll_queue()
                self.assertEqual(app.lbl_cycles.cget("text"), "掃描: 1 次")
                self.assertEqual(app.tree.set(combo.id, "scale"), "75.0%")
                self.assertEqual(app.tree.set(combo.id, "status"), "0.20 X")
                app._refresh_combo_tree()
                self.assertEqual(app.tree.set(combo.id, "scale"), "75.0%")
            with patch.object(app.engine, "get_match_scale", return_value=1.5):
                app._poll_queue()
                self.assertEqual(app.tree.set(combo.id, "scale"), "150.0%")
        finally:
            app.root.destroy()


if __name__ == "__main__":
    unittest.main()
