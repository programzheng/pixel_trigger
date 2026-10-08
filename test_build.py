import contextlib
import io
import runpy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


@contextlib.contextmanager
def build_environment():
    report = SimpleNamespace(iter=lambda: iter(()))
    with patch("pathlib.Path.is_file", return_value=True), \
            patch("pathlib.Path.read_text", return_value="license"), \
            patch("pathlib.Path.write_text"), patch("pathlib.Path.mkdir"), \
            patch("xml.etree.ElementTree.parse", return_value=report), \
            patch("shutil.copy2"):
        yield


class BuildModeTest(unittest.TestCase):
    def test_modes_and_failure_exit(self):
        script = Path(__file__).with_name("build.py")
        for standalone in (False, True):
            with self.subTest(standalone=standalone):
                argv = [str(script)] + (["--standalone"] if standalone else [])
                output = io.StringIO()
                with build_environment(), patch.object(sys, "argv", argv), \
                        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)) as run, \
                        contextlib.redirect_stdout(output):
                    runpy.run_path(str(script), run_name="__main__")
                command = run.call_args.args[0]
                self.assertIn("--standalone" if standalone else "--onefile", command)
                self.assertNotIn("--onefile" if standalone else "--standalone", command)
                self.assertTrue(any("vendor\\tesseract=tesseract" in item for item in command))
                self.assertEqual(
                    [item.removeprefix("--nofollow-import-to=") for item in command
                     if item.startswith("--nofollow-import-to=")],
                    ["mouseinfo", "easyocr", "bidi", "torch", "torchvision"],
                )
                self.assertIn("--include-package=ttkbootstrap", command)
                self.assertIn("--include-package-data=ttkbootstrap", command)
                self.assertIn(f"--include-data-files={script.parent / 'pyproject.toml'}=pyproject.toml", command)
                target = "dist/standalone/main.dist/pixel_trigger.exe" if standalone else "dist/pixel_trigger.exe"
                self.assertIn(target, output.getvalue())
                self.assertIn("耗時", output.getvalue())
                self.assertIn("--output-dir=dist/standalone" if standalone else "--output-dir=dist", command)
        with build_environment(), patch.object(sys, "argv", [str(script), "--standalone"]), \
                patch("subprocess.run", return_value=SimpleNamespace(returncode=7)), \
                contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit) as failure:
            runpy.run_path(str(script), run_name="__main__")
        self.assertEqual(failure.exception.code, 7)

    def test_pyinstaller_keeps_ocr_and_adjacent_data(self):
        script = Path(__file__).with_name("build.py")
        output = io.StringIO()
        with build_environment(), patch.object(sys, "argv", [str(script), "--pyinstaller"]), \
                patch("subprocess.run", return_value=SimpleNamespace(returncode=0)) as run, \
                contextlib.redirect_stdout(output):
            runpy.run_path(str(script), run_name="__main__")
        command = run.call_args.args[0]
        self.assertEqual(command[1:3], ["-m", "PyInstaller"])
        self.assertIn(f"--add-data={script.parent / 'pyproject.toml'}:.", command)
        for option in ("--onedir", "--contents-directory=.", "--collect-all=ttkbootstrap"):
            self.assertIn(option, command)
        self.assertTrue(any("vendor\\tesseract:tesseract" in item for item in command))
        excluded = [command[index + 1] for index, item in enumerate(command[:-1])
                    if item == "--exclude-module"]
        self.assertEqual(excluded, ["mouseinfo", "easyocr", "bidi", "torch", "torchvision"])
        for directory in ("profiles", "templates"):
            self.assertNotIn(f"--add-data={script.resolve().parent / directory}:{directory}", command)
        self.assertIn("dist/pyinstaller/pixel_trigger/pixel_trigger.exe", output.getvalue())


if __name__ == "__main__":
    unittest.main()
