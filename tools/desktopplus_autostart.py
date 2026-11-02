"""Turn Desktop+ auto-start with SteamVR on or off:  python tools\\desktopplus_autostart.py on|off"""
import sys

import openvr

APP_KEY = "steam.overlay.1494460"

enable = len(sys.argv) > 1 and sys.argv[1].lower() == "on"
openvr.init(openvr.VRApplication_Utility)
try:
    apps = openvr.VRApplications()
    apps.setApplicationAutoLaunch(APP_KEY, enable)
    print(f"Desktop+ auto-start: {'on' if apps.getApplicationAutoLaunch(APP_KEY) else 'off'}")
finally:
    openvr.shutdown()
