import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from prepare_tesseract import APPROVED_VERSION, prepare


class PrepareTesseractTest(unittest.TestCase):
    def test_prepares_audited_runtime_and_manifest(self):
        version = SimpleNamespace(
            stdout=f"tesseract v{APPROVED_VERSION}\n", returncode=0,
        )
        with patch.object(Path, "is_file", return_value=True), \
                patch.object(Path, "exists", return_value=False), \
                patch.object(Path, "is_dir", return_value=False), \
                patch.object(Path, "mkdir"), patch.object(Path, "glob", return_value=[]), \
                patch.object(Path, "rglob", return_value=[]), \
                patch.object(Path, "write_text") as write, \
                patch("prepare_tesseract.shutil.copy2"), \
                patch("prepare_tesseract.shutil.copytree"), \
                patch("prepare_tesseract.subprocess.run", return_value=version):
            manifest = prepare("source", "output")
        self.assertEqual(manifest, {
            "tesseract_version": APPROVED_VERSION,
            "source": str(Path("source").resolve()),
            "files": {},
        })
        write.assert_called_once()


if __name__ == "__main__":
    unittest.main()
