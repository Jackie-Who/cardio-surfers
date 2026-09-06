# PyInstaller spec for Cardio Surfers.
#
# Build with:  python scripts/build_exe.py
#
# One file, one exe. The awkward parts and why they are here:
#
# - **mediapipe** ships binary graph definitions and .tflite resources next to
#   its Python code. `collect_all` is the only reliable way to pull them in;
#   without it the app imports fine and then fails at `create_from_options`.
# - **The pose model** is not in the wheel -- it is fetched by
#   `scripts/fetch_model.py` -- so it is added as data and resolved at runtime
#   through `config.resolve_asset`.
# - **sounddevice** carries the PortAudio DLL in `_sounddevice_data`.
# - **pygrabber** drives DirectShow through comtypes, which PyInstaller cannot
#   see because the interfaces are built dynamically.
# - `console=True` on purpose: this app depends on cameras, audio devices and a
#   pose model, and when one of those is missing the console message is the
#   whole diagnosis. Set it to False for a silent windowed build.

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [
    ("config.example.json", "."),
    ("models/pose_landmarker_lite.task", "models"),
    ("tools/keytest.html", "tools"),
]
binaries = []
hiddenimports = [
    "comtypes",
    "comtypes.stream",
    "pygrabber",
    "pygrabber.dshow_graph",
]

for package in ("mediapipe", "sounddevice", "pygrabber", "comtypes"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

hiddenimports += collect_submodules("cardio_surfers")

a = Analysis(
    # Not src/cardio_surfers/__main__.py: PyInstaller runs the entry script as
    # a top-level module, where its relative imports have no parent package.
    ["launcher.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # matplotlib is NOT excludable: mediapipe.tasks.python.vision imports
    # drawing_utils at module load, which imports matplotlib. Dropping it makes
    # the exe smaller and then fails with ModuleNotFoundError the moment the
    # pose model is opened. tkinter goes, though -- matplotlib only needs it
    # for its interactive backends, which nothing here uses.
    excludes=["tkinter", "pytest", "PyQt5", "PySide2", "IPython", "notebook"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CardioSurfers",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
