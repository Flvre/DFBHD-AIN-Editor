# Building the Windows executable

Use 64-bit Python 3.14 on Windows with tkinter installed.

```powershell
python -m pip install -r requirements-build.txt
python -m PyInstaller --clean --noconfirm dfbhdain_v1.spec
```

The single-file executable is written to `dist/dfbhdain_v1.exe`.
The supplied icon is embedded in the executable and used for Tk windows.

The build keeps standard streams available for the generator worker protocol.
PyInstaller hides the console when the application owns it; launching from an
existing terminal keeps that terminal available. Generator subprocesses use
`CREATE_NO_WINDOW`.

The portable EXE writes preferences and diagnostic logs beside itself. Extract
the application to a writable folder before running it. Game assets and personal
configuration are not bundled.

Distribute the executable with `LICENSE` and `docs/AIN_Editor_Guide.pdf`.
