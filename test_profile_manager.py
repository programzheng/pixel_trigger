import runpy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


class ProfileLocationTest(unittest.TestCase):
    def test_debug_hotkeys_default_and_round_trip(self):
        from models import Profile

        profile = Profile.from_dict({"name": "old profile"})
        self.assertEqual((profile.debug_hotkey, profile.background_debug_hotkey), ("F9", "F10"))
        profile.debug_hotkey = "ctrl+shift+d"
        profile.background_debug_hotkey = "ctrl+shift+b"
        restored = Profile.from_dict(profile.to_dict())
        self.assertEqual(restored.debug_hotkey, profile.debug_hotkey)
        self.assertEqual(restored.background_debug_hotkey, profile.background_debug_hotkey)

    def test_external_profiles_and_development_path(self):
        script = Path(__file__).resolve().with_name("profile_manager.py")
        development = runpy.run_path(str(script))
        self.assertEqual(development["PROFILES_DIR"], script.parent / "profiles")

        with tempfile.TemporaryDirectory() as directory:
            # 模擬搬移後的 exe 目錄，不是 __file__ 所在的原始／解壓目錄。
            packaged = runpy.run_path(
                str(script),
                init_globals={"__compiled__": SimpleNamespace(containing_dir=directory)},
            )
            expected = Path(directory) / "profiles"
            self.assertEqual(packaged["PROFILES_DIR"], expected)
            profile = packaged["create_default_fishing"]()
            path = packaged["save_profile"](profile, "portable.json")
            self.assertEqual(path, expected / "portable.json")
            self.assertEqual(packaged["list_profiles"](), [path])
            self.assertEqual(packaged["load_profile"](path).to_dict(), profile.to_dict())


if __name__ == "__main__":
    unittest.main()
