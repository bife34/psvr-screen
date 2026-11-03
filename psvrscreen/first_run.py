"""The downloadable package's first start - it has no installer (Windows' Smart App Control blocks unsigned
installers and programs on many PCs; the package runs on Python's own signed program instead).

A small window on the PC screen, before anything else:
  - the app's files lose their "downloaded from the internet" mark (Windows would block its scripts, like the
    driver installer)
  - Start menu and desktop shortcuts (with the PSVR Screen icon)
  - the Virtual Display Driver - the VR monitors and the 4K screen (Windows asks for permission once)
  - links to the optional parts (DS4Windows, Steam + SteamVR, iVRy) at their official pages
Done once (DATA_DIR/setup-done.txt); Settings can't break it - running it again just repeats it.
"""
import ctypes
import os
import subprocess
from ctypes import wintypes

from .core.paths import APP_DIR, DATA_DIR, PACKAGED

DONE_FILE = DATA_DIR / "setup-done.txt"
DRIVERS = APP_DIR / "drivers"
OPTIONAL = [
    ("DS4Windows - the DS4 in non-Steam PC games (not needed in VR)", "https://github.com/schmaldeo/DS4Windows/releases"),
    ("Steam + SteamVR - for VR games (free)", "https://store.steampowered.com/about/"),
    ("iVRy - the PSVR in SteamVR, for VR games (paid)", "https://store.steampowered.com/app/992490/"),
]


def needed():
    return PACKAGED and not DONE_FILE.exists()


def unblock(folder=APP_DIR):
    """Remove the "downloaded from the internet" mark from every file of the app (what Windows' "Unblock" does)."""
    count = 0
    for root, _, files in os.walk(folder):
        for name in files:
            try:
                os.remove(os.path.join(root, name) + ":Zone.Identifier")
                count += 1
            except OSError:
                pass
    return count


def make_shortcuts(desktop=True):
    """Start menu (and desktop) shortcuts to PSVR Screen.exe, with its icon."""
    import comtypes.client
    shell = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
    places = [os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs")]
    if desktop:
        places.append(shell.SpecialFolders("Desktop"))
    for place in places:
        link = shell.CreateShortcut(os.path.join(place, "PSVR Screen.lnk"))
        link.TargetPath = str(APP_DIR / "PSVR Screen.exe")
        link.WorkingDirectory = str(APP_DIR)
        link.IconLocation = str(APP_DIR / "psvr_screen.ico")
        link.Description = "PSVR Screen - your PC in the PS4 VR headset"
        link.Save()


def vdd_installed():
    """Is the Virtual Display Driver on this PC (installed, whether its monitors are on now or not)?"""
    devcon = DRIVERS / "vdd" / "devcon.exe"
    if not devcon.exists():
        return True                     # (a build without the driver: nothing to offer)
    try:
        out = subprocess.run([str(devcon), "find", r"Root\MttVDD"], capture_output=True, text=True, timeout=20,
                             creationflags=subprocess.CREATE_NO_WINDOW).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "matching device" in out and not out.strip().lower().startswith("no matching")


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]


def install_vdd():
    """Install the Virtual Display Driver as administrator (Windows asks). True when it's there afterwards."""
    script = DRIVERS / "install_vdd.ps1"
    info = _ShellExecuteInfo(cbSize=ctypes.sizeof(_ShellExecuteInfo), fMask=0x40,   # SEE_MASK_NOCLOSEPROCESS
                             lpVerb="runas", lpFile="powershell.exe",
                             lpParameters=f'-NoProfile -ExecutionPolicy Bypass -File "{script}"', nShow=0)
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)) or not info.hProcess:
        return False                    # (said no to Windows' question)
    ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 120000)
    ctypes.windll.kernel32.CloseHandle(info.hProcess)
    return vdd_installed()


def show():
    """The welcome window; returns when it's done (or skipped). Main thread, before the app starts."""
    import tkinter as tk
    import webbrowser
    unblock()
    has_vdd = vdd_installed()
    root = tk.Tk()
    root.title("PSVR Screen - first start")
    root.configure(bg="#14161c")
    root.resizable(False, False)
    fg, muted, accent = "#f4f5f8", "#969cac", "#0a84ff"
    font = ("Segoe UI", 11)
    tk.Label(root, text="Welcome to PSVR Screen", bg="#14161c", fg=fg, font=("Segoe UI Semibold", 18)).pack(
        anchor="w", padx=24, pady=(20, 4))
    tk.Label(root, text="A few things, once, then it starts:", bg="#14161c", fg=muted, font=font).pack(
        anchor="w", padx=24, pady=(0, 12))
    shortcuts = tk.BooleanVar(value=True)
    desktop = tk.BooleanVar(value=True)
    driver = tk.BooleanVar(value=not has_vdd)
    box = dict(bg="#14161c", fg=fg, selectcolor="#2c313d", activebackground="#14161c", activeforeground=fg,
               font=font, anchor="w")
    tk.Checkbutton(root, text="PSVR Screen in the Start menu", variable=shortcuts, **box).pack(fill="x", padx=20)
    tk.Checkbutton(root, text="...and on the desktop", variable=desktop, **box).pack(fill="x", padx=40)
    driver_text = ("The VR monitors are already installed" if has_vdd else
                   "Install the VR monitors (Virtual Display Driver - needed; Windows will ask for permission)")
    check = tk.Checkbutton(root, text=driver_text, variable=driver, **box)
    check.pack(fill="x", padx=20, pady=(6, 0))
    if has_vdd:
        check.configure(state="disabled")
    tk.Label(root, text="Optional, from their official pages:", bg="#14161c", fg=muted, font=font).pack(
        anchor="w", padx=24, pady=(16, 2))
    for text, url in OPTIONAL:
        link = tk.Label(root, text="  " + text, bg="#14161c", fg=accent, font=font, cursor="hand2")
        link.pack(anchor="w", padx=24)
        link.bind("<Button-1>", lambda e, url=url: webbrowser.open(url))
    status = tk.Label(root, text="", bg="#14161c", fg=muted, font=font)
    status.pack(anchor="w", padx=24, pady=(14, 0))

    def go():
        notes = []
        try:
            if shortcuts.get():
                make_shortcuts(desktop.get())
                notes.append("shortcuts made")
        except Exception:
            from .core.log import log_error
            log_error("first start: shortcuts")
            notes.append("shortcuts failed (see the log)")
        if driver.get() and not has_vdd:
            status.configure(text="Installing the VR monitors... (answer Windows' question)")
            root.update()
            notes.append("VR monitors installed" if install_vdd() else
                         "VR monitors NOT installed - start PSVR Screen again to retry")
        DONE_FILE.write_text("; ".join(notes) + "\n", encoding="utf-8")
        root.destroy()

    def skip():
        root.destroy()

    row = tk.Frame(root, bg="#14161c")
    row.pack(fill="x", padx=24, pady=20)
    tk.Button(row, text="Set up and start", command=go, bg=accent, fg="white", relief="flat", font=font,
              padx=16, pady=6, cursor="hand2").pack(side="right")
    tk.Button(row, text="Skip", command=skip, bg="#2c313d", fg=fg, relief="flat", font=font, padx=16, pady=6,
              cursor="hand2").pack(side="right", padx=8)
    root.eval("tk::PlaceWindow . center")
    root.mainloop()
