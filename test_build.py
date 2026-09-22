import contextlib
import io
import runpy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class BuildModeTest(unittest.TestCase):
    def test_modes_and_failure_exit(self):
        script = Path(__file__).with_name("build.py")
        for standalone in (False, True):
            with self.subTest(standalone=standalone):
                argv = [str(script)] + (["--standalone"] if standalone else [])
                output = io.StringIO()
                with patch.object(sys, "argv", argv), \
                        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)) as run, \
                        contextlib.redirect_stdout(output):
                    runpy.run_path(str(script), run_name="__main__")
                command = run.call_args.args[0]
                self.assertIn("--standalone" if standalone else "--onefile", command)
                self.assertNotIn("--onefile" if standalone else "--standalone", command)
                self.assertIn("--include-data-dir=models=models", command)
                self.assertIn("--include-package=ttkbootstrap", command)
                self.assertIn("--include-package-data=ttkbootstrap", command)
                target = "dist/standalone/main.dist/pixel_trigger.exe" if standalone else "dist/pixel_trigger.exe"
                self.assertIn(target, output.getvalue())
                self.assertIn("耗時", output.getvalue())
                self.assertIn("--output-dir=dist/standalone" if standalone else "--output-dir=dist", command)
        with patch.object(sys, "argv", [str(script), "--standalone"]), \
                patch("subprocess.run", return_value=SimpleNamespace(returncode=7)), \
                contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit) as failure:
            runpy.run_path(str(script), run_name="__main__")
        self.assertEqual(failure.exception.code, 7)

    def test_pyinstaller_keeps_ocr_and_adjacent_data(self):
        script = Path(__file__).with_name("build.py")
        output = io.StringIO()
        with patch.object(sys, "argv", [str(script), "--pyinstaller"]), \
                patch("subprocess.run", return_value=SimpleNamespace(returncode=0)) as run, \
                contextlib.redirect_stdout(output):
            runpy.run_path(str(script), run_name="__main__")
        command = run.call_args.args[0]
        self.assertEqual(command[1:3], ["-m", "PyInstaller"])
        for option in ("--onedir", "--contents-directory=.", "--collect-all=easyocr", "--collect-all=ttkbootstrap"):
            self.assertIn(option, command)
        self.assertIn(f"--add-data={script.resolve().parent / 'models'}:models", command)
        for directory in ("profiles", "templates"):
            self.assertNotIn(f"--add-data={script.resolve().parent / directory}:{directory}", command)
        self.assertIn("dist/pyinstaller/pixel_trigger/pixel_trigger.exe", output.getvalue())


if __name__ == "__main__":
    unittest.main()
