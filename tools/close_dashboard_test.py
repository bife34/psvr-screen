"""Test: close the SteamVR menu by switching the dashboard off and straight back on."""
import time

import openvr

openvr.init(openvr.VRApplication_Background)
try:
    overlay = openvr.VROverlay()
    settings = openvr.VRSettings()
    print("menu open before:", overlay.isDashboardVisible())
    settings.setBool("dashboard", "enableDashboard", False)
    time.sleep(1.5)
    print("menu open while disabled:", overlay.isDashboardVisible())
    settings.setBool("dashboard", "enableDashboard", True)
    time.sleep(1.5)
    print("menu open after re-enable:", overlay.isDashboardVisible())
    print("dashboard enabled setting now:", settings.getBool("dashboard", "enableDashboard"))
finally:
    openvr.shutdown()

