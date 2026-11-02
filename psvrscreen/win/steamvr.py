"""SteamVR: is it running, start it, close it.

PSVR Screen and SteamVR (with iVRy) can't both drive the headset: for VR games the app lets go of it
(engine.give_to_steamvr) and SteamVR takes over; you come back with the control panel's button, which closes
SteamVR first if it's still running.
"""
import ctypes
import os
import subprocess
import time
from ctypes import wintypes

STEAMVR_APP = "250820"
PROCESSES = ("vrserver.exe", "vrmonitor.exe", "vrcompositor.exe")
CLOSE_WAIT_SECONDS = 20
NO_WINDOW = 0x08000000


class _PROCESSENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def _running_names():
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)   # TH32CS_SNAPPROCESS
    names = set()
    entry = _PROCESSENTRY32(dwSize=ctypes.sizeof(_PROCESSENTRY32))
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            names.add(entry.szExeFile.lower())
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return names


def running():
    """SteamVR is running (it has the headset)."""
    return bool(_running_names() & set(PROCESSES))


def start():
    """Start SteamVR (its home / dashboard) through Steam."""
    os.startfile(f"steam://rungameid/{STEAMVR_APP}")


def close():
    """Close SteamVR like its window's X does; if it hasn't ended after a while, end it. Returns when gone."""
    subprocess.run(["taskkill", "/IM", "vrmonitor.exe"], capture_output=True, creationflags=NO_WINDOW)
    deadline = time.monotonic() + CLOSE_WAIT_SECONDS
    while running() and time.monotonic() < deadline:
        time.sleep(0.5)
    if running():
        for name in PROCESSES:
            subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True, creationflags=NO_WINDOW)
        time.sleep(2)
