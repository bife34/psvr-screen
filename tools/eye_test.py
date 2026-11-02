"""Test picture on the headset's screen: left half red "LEFT", right half blue "RIGHT". Closes after 60 s."""
import tkinter as tk

import mss

with mss.mss() as sct:
    # The headset is the monitor that is not at (0, 0).
    hmd = next(m for m in sct.monitors[1:] if (m["left"], m["top"]) != (0, 0))

root = tk.Tk()
root.overrideredirect(True)
root.geometry(f"{hmd['width']}x{hmd['height']}+{hmd['left']}+{hmd['top']}")
canvas = tk.Canvas(root, highlightthickness=0, bg="black")
canvas.pack(fill="both", expand=True)
w, h = hmd["width"], hmd["height"]
canvas.create_rectangle(0, 0, w // 2, h, fill="#b00000", width=0)
canvas.create_rectangle(w // 2, 0, w, h, fill="#0040c0", width=0)
for x, label in ((w // 4, "LEFT"), (3 * w // 4, "RIGHT")):
    canvas.create_text(x, h // 2, text=label, fill="white", font=("Segoe UI", 72, "bold"))
    canvas.create_line(x - 40, h // 2 - 120, x + 40, h // 2 - 120, fill="white", width=4)
    canvas.create_line(x, h // 2 - 160, x, h // 2 - 80, fill="white", width=4)
root.after(60000, root.destroy)
root.mainloop()
