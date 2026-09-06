# Regression checks

Install the repository requirements first.

## Project JSON and seed history

```powershell
python tests/test_project_seed_history.py
```

Checks project saving, loading, autosaving, nested JSON data, saved seed retry settings, old projects, invalid seed records, and the history limit. Uses temporary files and extracts the actual application methods without starting the editor or generator workers.

## 2D keyboard dispatch

```powershell
python tests/check_navigation_bindings.py
```

Run on Windows with a desktop session. This test briefly focuses an invisible Tk window. It checks synthetic key dispatch for arrow movement, 2D keypad controls, plus/minus, and focus guards. It does not load a game map.

These checks cover specific regressions; they do not replace testing generation and rendering with real maps.
