"""The Start menu's apps (desktop and Store apps), for the VR "Apps" launcher."""
import json
import os
import subprocess

SKIP_WORDS = ("uninstall", "desinstal", "readme", "help", "ajuda", "website", "manual")


def load_start_apps():
    """[(name, app id)] sorted by name - the same list the Start menu uses (PowerShell Get-StartApps)."""
    command = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
               "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True,
                             timeout=60, creationflags=subprocess.CREATE_NO_WINDOW).stdout
        items = json.loads(out.decode("utf-8-sig") or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    if isinstance(items, dict):
        items = [items]
    apps = {(i["Name"], i["AppID"]) for i in items
            if i.get("Name") and i.get("AppID") and not any(w in i["Name"].lower() for w in SKIP_WORDS)}
    return sorted(apps, key=lambda a: a[0].lower())


def launch(app_id):
    """Start an app the way the Start menu does."""
    os.startfile("shell:AppsFolder\\" + app_id)
