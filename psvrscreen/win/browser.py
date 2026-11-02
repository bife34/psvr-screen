"""Which page a browser window shows: the text of its address bar, read with UI Automation (the same way
screen readers read it). Works with Chrome, Edge, Brave, Firefox, in any language.

Slow-ish (tens of ms, the first time up to a second): call from a background thread.
"""
import re

LOOKS_LIKE_ADDRESS = re.compile(r"^(https?://)?[\w-]+(\.[\w-]+)+(/\S*)?$")
SEARCH_DEPTH = 14


class BrowserReader:
    """One per background thread (UI Automation needs COM set up in the thread that uses it)."""

    def __init__(self):
        import uiautomation   # slow to import: only in the thread that reads addresses
        self._auto = uiautomation
        self._com = uiautomation.UIAutomationInitializerInThread()

    def page_address(self, hwnd):
        """The address shown in the browser window `hwnd`, or None."""
        auto = self._auto
        try:
            window = auto.ControlFromHandle(hwnd)
            if window is None:
                return None
            for index in (1, 2):   # the address bar is the first text box (rarely the second)
                edit = window.EditControl(searchDepth=SEARCH_DEPTH, foundIndex=index)
                if not edit.Exists(0, 0):
                    return None
                value = (edit.GetValuePattern().Value or "").strip()
                if LOOKS_LIKE_ADDRESS.match(value):
                    return value if value.startswith("http") else "https://" + value
        except Exception:
            return None
        return None

    def close(self):
        del self._com
