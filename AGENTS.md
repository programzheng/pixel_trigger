# Repository Guidelines

## Project Structure & Module Organization

This is a Windows-focused Python desktop automation app with a flat module layout. `main.py` starts the Tkinter UI in `gui.py`; `automation.py` runs image/OCR matching and actions; `screen_capture.py` handles capture, template matching, and Tesseract OCR; `input_controller.py` sends mouse and keyboard input. Data models and profile serialization live in `models.py` and `profile_manager.py`. User profiles are JSON files under `profiles/`, reference images are under `templates/`, and the audited OCR runtime is generated under `vendor/tesseract/`. `prepare_tesseract.py` provisions that runtime, while `build.py` creates the Nuitka executable in `dist/`.

## Build, Test, and Development Commands

- `uv sync --dev` installs runtime and packaging dependencies from `pyproject.toml` and `uv.lock`.
- `uv run python main.py` launches the application locally.
- `uv run python prepare_tesseract.py` copies and verifies the approved OCR runtime needed for offline packaging.
- `uv run python build.py` produces the Windows one-file build at `dist/pixel_trigger.exe`.

Python 3.12 or newer is required; `.python-version` currently selects 3.12. Run the UI on Windows because global hotkeys, DPI handling, and input automation use Windows-specific behavior.

## Coding Style & Naming Conventions

Follow the existing PEP 8-style conventions: four-space indentation, `snake_case` functions and variables, `PascalCase` classes, and `UPPER_SNAKE_CASE` constants. Keep public data structures typed, using dataclasses and enums where they already model profile data. Prefer small changes within the existing flat modules over adding new layers. No formatter or linter is configured; keep imports grouped and match surrounding style.

## Testing Guidelines

The automated test suite uses the standard library `unittest`; no coverage threshold is configured. Run `uv run python -m unittest discover`. Before submitting, launch the app and manually verify profile load/save, hotkey start/stop, affected matching behavior, and action cancellation. For non-trivial pure logic, add a focused `test_*.py` using `unittest`.

## Commit & Pull Request Guidelines

After each completed set of application changes, update `project.version` in `pyproject.toml` once. Increment the patch version for fixes and small adjustments (for example, `0.2.0` to `0.2.1`); increment the minor version and reset the patch for larger features. Keep the project version in `uv.lock` synchronized. Documentation-only changes do not require a version bump. The UI and build must use the version from `pyproject.toml`; do not add timestamps or separate hardcoded versions.

Recent history uses Conventional Commit-style subjects such as `feat: support Nuitka packaging ...`; use a short imperative subject with a fitting prefix (`feat:`, `fix:`, `docs:`). Pull requests should explain the user-visible change, list manual verification steps, and note any profile or asset changes. Include screenshots for UI changes and link the relevant issue when one exists. Do not commit generated build directories, local profiles/templates, OCR runtimes, or virtual environments already covered by `.gitignore`.
