# PyInstaller recipe for PSVR Screen.exe (a folder build: fast start, no Python needed).
# Build from the psvr-screen folder:   .venv\Scripts\python -m PyInstaller packaging\psvr_screen.spec --noconfirm
# Result: dist\PSVR Screen\PSVR Screen.exe (+ its _internal folder) - the installer packs that folder.
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = SPECPATH + "\\.."
datas = [(ROOT + "\\environments", "environments"), (ROOT + "\\LICENSE", "."),
         (ROOT + "\\THIRD_PARTY_NOTICES.md", ".")]
binaries, hiddenimports = [], collect_submodules("psvrscreen")

# Libraries with native parts or plugins loaded at run time: take them whole
for package in ("windows_capture", "libusb_package", "uiautomation", "sounddevice", "glfw", "av", "hid",
                "OpenGL", "openvr", "winrt", "comtypes", "mss"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("yt_dlp")

a = Analysis([SPECPATH + "\\psvr_screen.py"], pathex=[ROOT], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, excludes=["tkinter.test", "unittest", "pytest"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PSVR Screen", icon=SPECPATH + "\\psvr_screen.ico",
          console=False, version=None)
coll = COLLECT(exe, a.binaries, a.datas, name="PSVR Screen")
