"""Prepare an audited Tesseract runtime for offline Windows builds."""

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


APPROVED_VERSION = "5.5.0.20241111"
PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE = Path(r"C:\Program Files\Tesseract-OCR")
DEFAULT_OUTPUT = PROJECT_DIR / "vendor" / "tesseract"


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare(source=DEFAULT_SOURCE, output=DEFAULT_OUTPUT):
    source, output = Path(source), Path(output)
    executable = source / "tesseract.exe"
    required = [
        executable,
        source / "doc" / "LICENSE",
        source / "tessdata" / "eng.traineddata",
        source / "tessdata" / "chi_tra.traineddata",
        source / "tessdata" / "configs" / "tsv",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing required Tesseract files:\n" + "\n".join(missing))
    version = subprocess.run(
        [str(executable), "--version"], capture_output=True, text=True, timeout=10,
    ).stdout.splitlines()[0]
    if version != f"tesseract v{APPROVED_VERSION}":
        raise ValueError(f"Expected Tesseract {APPROVED_VERSION}, found: {version or 'unknown'}")
    if output.exists():
        raise FileExistsError(f"Remove the existing generated directory first: {output}")

    output.mkdir(parents=True)
    for path in [executable, *source.glob("*.dll")]:
        shutil.copy2(path, output / path.name)
    shutil.copytree(source / "tessdata" / "configs", output / "tessdata" / "configs")
    tessconfigs = source / "tessdata" / "tessconfigs"
    if tessconfigs.is_dir():
        shutil.copytree(tessconfigs, output / "tessdata" / "tessconfigs")
    for name in ("eng.traineddata", "chi_tra.traineddata"):
        destination = output / "tessdata" / name
        destination.parent.mkdir(exist_ok=True)
        shutil.copy2(source / "tessdata" / name, destination)
    shutil.copytree(source / "doc", output / "doc")

    files = sorted(path for path in output.rglob("*") if path.is_file())
    manifest = {
        "tesseract_version": APPROVED_VERSION,
        "source": str(source.resolve()),
        "files": {path.relative_to(output).as_posix(): _sha256(path) for path in files},
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = prepare(args.source, args.output)
    print(f"Prepared {len(result['files'])} files in {args.output}")
