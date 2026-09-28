# Pixel Trigger

[English](README.md) | [繁體中文](README.zh-TW.md)

Pixel Trigger is a Windows desktop automation tool that reacts to visual
changes on screen. It combines image matching, OCR, and color tracking with
configurable mouse, keyboard, and sound actions through a Tkinter interface.

## Features

- Match images at multiple scales with OpenCV.
- Verify Traditional Chinese and English text with EasyOCR.
- Track colored markers and color bands inside selected regions.
- Trigger clicks, key presses, holds, rapid input, or sound alerts.
- Restrict capture to a screen region or a selected application window.
- Save reusable automation profiles as JSON.
- Record recognized balance changes in daily CSV ledgers.
- Inspect matches with logs, previews, and a click-through debug overlay.
- Package the application and OCR models for offline Windows use.

## Requirements

- Windows 10 or later
- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)

Global hotkeys, window targeting, and input automation depend on Windows APIs.

## Getting Started

```powershell
git clone https://github.com/programzheng/pixel_trigger.git
cd pixel_trigger
uv sync --dev
uv run python main.py
```

EasyOCR downloads its language models on first use. To prepare local model
files before creating an offline build, run:

```powershell
uv run python prepare_models.py
```

## Usage

1. Create or select a profile.
2. Choose a capture region or target window.
3. Add a combo and provide a trigger image, OCR condition, or color rule.
4. Select the action and adjust its confidence and timing settings.
5. Start or stop detection with the configured global hotkey.

Profiles and captured templates are stored locally in `profiles/` and
`templates/`. Both directories are excluded from version control.

## Testing

The test suite uses the Python standard library's `unittest` framework:

```powershell
uv run python -m unittest discover
```

Some UI and Windows integration behavior should also be verified manually.

## Building

Create the default Nuitka one-file executable:

```powershell
uv run python prepare_models.py
uv run python build.py
```

The executable is written to `dist/pixel_trigger.exe`. Alternative build modes
are available with `--standalone` and `--pyinstaller`.

## Project Structure

| Path | Purpose |
| --- | --- |
| `gui.py` | Tkinter interface, editors, selectors, and debug overlay |
| `automation.py` | Detection loop, trigger state, and action scheduling |
| `screen_capture.py` | Screen capture, template matching, OCR, and color detection |
| `input_controller.py` | Interruptible mouse, keyboard, and sound actions |
| `models.py` | Typed profile, combo, action, and trigger models |
| `profile_manager.py` | JSON profile persistence |
| `calc.py` | Background balance recognition and CSV ledger handling |
| `window_target.py` | Windows window discovery and client-area targeting |
| `test_*.py` | Unit and integration-focused regression tests |

## Contributing

See [AGENTS.md](AGENTS.md) for repository conventions, development commands,
testing expectations, and pull request guidance.

Use automation only in applications and environments where you have permission
to do so.
