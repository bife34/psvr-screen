"""Builds the downloadable package: dist/portable/PSVR Screen (+ a .zip of it).

No exe of our own: Windows 11's Smart App Control (on by default on new PCs) blocks unsigned programs and
installers. The package runs on Python's own signed program (python.org's, signed by the Python Software
Foundation) renamed "PSVR Screen.exe" - renaming keeps the signature valid. Started with no arguments it starts
the app (Lib/sitecustomize.py); with arguments it's plain Python (the app's own restart, `--check`).
The libraries are the same files the source version uses (from .venv), so Windows knows them too.

    .venv\\Scripts\\python tools\\build_portable.py [--no-zip]

Then: dist/portable/PSVR Screen/python.exe -m psvrscreen --check  (everything loads?)
"""
import shutil
import sys
import time
import zipfile
from pathlib import Path

VERSION = "0.10.0"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dist" / "portable" / "PSVR Screen"
BASE = Path(sys.base_prefix)               # python.org's Python the .venv was made from
SITE = Path(sys.prefix) / "Lib" / "site-packages"
# not needed to run the app (building, linting) - or blocked by Smart App Control and not needed (hf_xet)
SKIP_PACKAGES = {"pip", "setuptools", "_distutils_hack", "distutils-precedence.pth", "PyInstaller",
                 "_pyinstaller_hooks_contrib", "altgraph", "pefile.py", "peutils.py", "ordlookup", "win32ctypes",
                 "pyflakes", "hf_xet", "vosk", "sympy", "mpmath", "isympy.py", "__pycache__"}
# inside libraries: their tools for making / converting models (never used here; their very long file names
# break unpacking the zip into a deep folder - Windows' 260-character path limit)
SKIP_INSIDE = {"onnxruntime": {"tools", "transformers", "quantization", "backend"}}
SKIP_STDLIB = {"site-packages", "test", "idlelib", "turtledemo", "ensurepip", "__pycache__", "lib2to3"}
AUTOSTART = '''"""PSVR Screen.exe (python.org's signed Python, renamed) started with no arguments: start the app."""
import sys

if sys.argv in ([""], []) and not sys.flags.interactive and not sys.flags.inspect:
    try:
        from psvrscreen.__main__ import main
        main()
    except BaseException as error:              # (main ends the program itself when all is well)
        import ctypes
        import os
        import traceback
        text = traceback.format_exc()
        try:
            folder = os.path.join(os.environ.get("LOCALAPPDATA", "."), "PSVRScreen")
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "psvr-screen.log"), "a", encoding="utf-8") as f:
                f.write("[start] " + text + "\\n")
        except OSError:
            pass
        ctypes.windll.user32.MessageBoxW(None, "PSVR Screen couldn't start:\\n\\n" + text[-1500:],
                                         "PSVR Screen", 0x10)
        os._exit(1)
'''


def _copy_tree(src, dst, skip):
    def ignore(folder, names):
        return [n for n in names if n in skip or n.endswith((".pyc", ".pyo"))]
    shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True)


def build():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    # Python itself (signed): the app's program is its pythonw.exe, renamed
    shutil.copy2(BASE / "pythonw.exe", OUT / "PSVR Screen.exe")
    shutil.copy2(BASE / "python.exe", OUT / "python.exe")          # (for --check and the like)
    for dll in BASE.glob("*.dll"):
        shutil.copy2(dll, OUT / dll.name)
    _copy_tree(BASE / "DLLs", OUT / "DLLs", {"__pycache__"})
    _copy_tree(BASE / "Lib", OUT / "Lib", SKIP_STDLIB)
    if (BASE / "tcl").exists():
        _copy_tree(BASE / "tcl", OUT / "tcl", set())              # (the control panel on the PC screen)
    (OUT / "Lib" / "sitecustomize.py").write_text(AUTOSTART, encoding="utf-8")
    # the libraries (the same files the source version runs)
    site_out = OUT / "Lib" / "site-packages"
    site_out.mkdir(parents=True)
    for item in SITE.iterdir():
        if item.name in SKIP_PACKAGES or any(item.name.lower().startswith(s.lower() + "-") and
                                             item.name.endswith(".dist-info") for s in SKIP_PACKAGES):
            continue
        if item.is_dir():
            _copy_tree(item, site_out / item.name, {"__pycache__"} | SKIP_INSIDE.get(item.name, set()))
        else:
            shutil.copy2(item, site_out / item.name)
    # the app
    _copy_tree(ROOT / "psvrscreen", site_out / "psvrscreen", {"__pycache__"})
    _copy_tree(ROOT / "environments", OUT / "environments", set())
    drivers = OUT / "drivers"
    _copy_tree(ROOT / "packaging" / "third_party" / "vdd", drivers / "vdd", set())
    for script in ("install_vdd.ps1", "uninstall_vdd.ps1"):
        shutil.copy2(ROOT / "packaging" / script, drivers / script)
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "README.md"):
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, OUT / name)
    shutil.copy2(ROOT / "packaging" / "psvr_screen.ico", OUT / "psvr_screen.ico")
    (OUT / "psvr-screen-build.txt").write_text(
        f"PSVR Screen {VERSION}, built {time.strftime('%Y-%m-%d %H:%M')} on Python {sys.version.split()[0]}\n"
        "Start: double-click PSVR Screen.exe (python.org's signed Python, renamed).\n", encoding="utf-8")
    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"built {OUT} ({size / 1e6:.0f} MB)")


def make_zip():
    target = OUT.parents[1] / f"PSVR-Screen-{VERSION}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(OUT.rglob("*")):
            if f.is_file():
                z.write(f, Path("PSVR Screen") / f.relative_to(OUT))
    print(f"zipped {target} ({target.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    build()
    if "--no-zip" not in sys.argv:
        make_zip()
