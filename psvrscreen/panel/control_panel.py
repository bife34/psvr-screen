"""Control panel on the main monitor: live 3D preview of the VR layout + sliders that apply instantly."""
import math
import time
import tkinter as tk
from tkinter import ttk

from ..apps.desktop import new_panel_for
from ..core.settings import LIMITS, SHORTCUT_ACTIONS, new_panel, shown_title
from ..shell import workspace
from ..win import displays, windows
from ..win import shortcuts as shortcut_keys

BG = "#14181f"
PANEL = "#1b2029"
TEXT = "#e6e9ef"
MUTED = "#8b93a3"
ACCENT = "#4f9cf9"
GRID = "#262d38"
SCREEN_FILL = "#1f3b63"
SCREEN_EDGE = "#7db4ff"
GAZE = "#45d483"
GAZE_DARK = "#1f7a4a"
BAR_BG = "#0e1117"
TAB_BG = "#1b2029"
TAB_SELECTED = "#26406a"

EYE_HEIGHT = 1.2       # seated eye height used for the floor in the preview (meters)
PSVR_FOV_DEG = 100

SLIDERS = [
    # (setting, label, format) - all for the selected window
    ("yaw", "Position left / right", "{:+.0f} deg"),
    ("width", "Size (width)", "{:.2f} m"),
    ("distance", "Distance from you", "{:.2f} m"),
    ("height", "Height (up / down)", "{:+.2f} m"),
    ("curvature", "Curve", "{:.2f}"),
    ("tilt", "Tilt (lean back / forward)", "{:+.0f} deg"),
    ("turn", "Turn (left / right edge back)", "{:+.0f} deg"),
    ("roll", "Rotate (like a picture on a wall)", "{:+.0f} deg"),
    ("opacity", "Opacity", "{:.0%}"),
    ("text_zoom", "Text size in this window", "{:.2f}x"),
]

LENS_SLIDERS = [
    ("lens_fov", "Field of view (world size)", "{:.0f} deg"),
    ("ipd", "Eye distance (IPD)", "{:.3f} m"),
    ("lens_k1", "Lens distortion K1", "{:.2f}"),
    ("lens_k2", "Lens distortion K2", "{:.2f}"),
    ("lens_apart", "Eye pictures apart (line up with your eyes)", "{:+.3f}"),
    ("lens_height", "Left eye picture higher / lower", "{:+.3f}"),
]
LENS_DEFAULTS = {"lens_fov": 95.0, "ipd": 0.063, "lens_k1": 0.22, "lens_k2": 0.24, "lens_apart": 0.0,
                 "lens_height": 0.0}

CONTROLS_HELP = (
    "Left stick: move the mouse\n"
    "Cross / R2: click        Circle / L2: right click\n"
    "Right stick: scroll\n"
    "D-pad up / down: screen bigger / smaller\n"
    "D-pad left / right: screen closer / farther\n"
    "L1 + D-pad up / down: screen higher / lower\n"
    "L1 + D-pad left / right: tilt\n"
    "Triangle: recenter       Options: mouse to VR / back to the PC screen\n"
    "Share + Options: controller mouse on / off (for games)\n"
    "\n"
    "Headset remote, mic-mute button: recenter in front of you"
)


class Viewer3D:
    """Tiny software 3D renderer on a Tk canvas. Coordinates are relative to your eyes: -Z is forward."""

    def __init__(self, canvas, store):
        self.c = canvas
        self.store = store
        self.hide_titles = False   # set by the control panel (private mode / a video playing)
        self.yaw, self.pitch, self.radius = math.radians(40), math.radians(22), 4.2
        canvas.bind("<ButtonPress-1>", self._press)
        canvas.bind("<B1-Motion>", self._drag)
        canvas.bind("<ButtonRelease-1>", lambda e: setattr(self, "_moving", None))
        canvas.bind("<MouseWheel>", self._wheel)
        canvas.bind("<Double-Button-1>", lambda e: self.reset())
        self._last = None
        self._moving = None       # (panel index, start x, start y, panel yaw, panel height) while dragging one
        self._hit_polys = []      # (depth, panel index, polygon) from the last draw, for picking

    def reset(self):
        self.yaw, self.pitch, self.radius = math.radians(40), math.radians(22), 4.2

    def _press(self, e):
        self._last = (e.x, e.y)
        self._moving = None
        for _, index, poly in sorted(self._hit_polys, key=lambda h: h[0]):   # nearest first
            if _inside(poly, e.x, e.y):
                p = self.store.get().panels[index]
                self.store.update(selected=index)
                self._moving = (index, e.x, e.y, p["yaw"], p["height"])
                break

    def _drag(self, e):
        if self._moving:
            # Drag a window: left / right = around you, up / down = higher / lower
            index, x0, y0, yaw0, height0 = self._moving
            self.store.update_panel(index, yaw=max(-170.0, min(170.0, yaw0 + (e.x - x0) * 0.35)),
                                    height=max(-1.5, min(1.5, height0 - (e.y - y0) * 0.006)))
            return
        if self._last:
            dx, dy = e.x - self._last[0], e.y - self._last[1]
            self.yaw -= dx * 0.01
            self.pitch = max(math.radians(-10), min(math.radians(85), self.pitch + dy * 0.01))
        self._last = (e.x, e.y)

    def _wheel(self, e):
        self.radius = max(1.5, min(12.0, self.radius * (0.9 if e.delta > 0 else 1.1)))

    def _camera(self, target):
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        cam = (target[0] + self.radius * math.sin(self.yaw) * cp,
               target[1] + self.radius * sp,
               target[2] + self.radius * math.cos(self.yaw) * cp)
        fwd = _norm(_sub(target, cam))
        right = _norm(_cross(fwd, (0.0, 1.0, 0.0)))
        up = _cross(right, fwd)
        return cam, fwd, right, up

    def draw(self, s, aspects, look_yaw, look_pitch):
        c = self.c
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50 or h < 50:
            return
        focal = min(w, h) * 1.1
        target = (0.0, 0.0, -0.8)
        cam, fwd, right, up = self._camera(target)

        def project(p):
            v = _sub(p, cam)
            z = _dot(v, fwd)
            if z < 0.05:
                return None, z
            return (w / 2 + focal * _dot(v, right) / z, h / 2 - focal * _dot(v, up) / z), z

        def line(a, b, **kw):
            pa, _ = project(a)
            pb, _ = project(b)
            if pa and pb:
                c.create_line(*pa, *pb, **kw)

        # Floor grid
        floor = -EYE_HEIGHT
        for i in range(-6, 7):
            t = i * 0.5
            line((t, floor, -3.0), (t, floor, 3.0), fill=GRID)
            line((-3.0, floor, t), (3.0, floor, t), fill=GRID)

        # Field of view wedge (what the headset can show at once)
        half = math.radians(PSVR_FOV_DEG / 2)
        for a in (-half, half):
            yaw = look_yaw + a
            line((0, 0, 0), (-math.sin(yaw) * 2.2, 0, -math.cos(yaw) * 2.2), fill="#2f4a3a", dash=(3, 4))

        # Panels (curved strips), all quads sorted back to front so nearer panels cover farther ones
        quads, edges, labels = [], [], []
        selected_id = s.panels[s.selected]["id"] if s.panels else None
        for index, p in enumerate(s.panels):
            if not p.get("shown", True):
                continue
            is_sel = p["id"] == selected_id
            self._panel_geometry(p, index, aspects.get(p["id"], 9 / 16), is_sel, project, quads, edges, labels)
        self._hit_polys = [(depth, index, poly) for depth, poly, _, index in quads]
        for _, poly, fill, _ in sorted(quads, key=lambda q: -q[0]):
            c.create_polygon(poly, fill=fill, outline=fill)
        for a, b, color, width in edges:
            line(a, b, fill=color, width=width)
        for pos, text, color in labels:
            pt, _ = project(pos)
            if pt:
                c.create_text(*pt, text=text, fill=color, font=("Segoe UI", 9, "bold"))

        sel = s.panels[s.selected] if s.panels else None
        if sel:
            center = self._place(sel, 0, 0, 0)
            line((0, 0, 0), center, fill=MUTED, dash=(2, 3))
            mid, _ = project(tuple(v / 2 for v in center))
            if mid:
                c.create_text(mid[0] + 8, mid[1], text=f"{sel['distance']:.2f} m", fill=MUTED, anchor="w")

        # Your head + where you're looking
        head, z = project((0, 0, 0))
        if head:
            r = max(4, focal * 0.11 / max(z, 0.1))
            c.create_oval(head[0] - r, head[1] - r, head[0] + r, head[1] + r, fill="#3a4250", outline=TEXT)
        cp = math.cos(look_pitch)
        gaze = (-math.sin(look_yaw) * cp * 0.6, math.sin(look_pitch) * 0.6, -math.cos(look_yaw) * cp * 0.6)
        line((0, 0, 0), gaze, fill=GAZE, width=3, arrow="last")

        angle = math.degrees(2 * math.atan(s.width / 2 / max(s.distance, 0.01)))
        mode = "follow your head" if s.follow_head else "are fixed in the room"
        c.create_text(12, 12, anchor="nw", fill=MUTED, font=("Segoe UI", 9),
                      text=f"Windows {mode}. Selected one (blue) fills {angle:.0f} deg of your ~{PSVR_FOV_DEG} deg view.\n"
                           f"Drag a window to move it (left/right = around you, up/down = height).  "
                           f"Drag empty space to rotate the view, scroll to zoom, double-click to reset.")

    @staticmethod
    def _place(p, x, y, z):
        """Panel-local point to world - same order as the VR renderer: roll, tilt, turn (around the panel
        middle), then out to its height and distance, then around you by yaw."""
        r = math.radians(-p.get("roll", 0.0))
        x, y = x * math.cos(r) - y * math.sin(r), x * math.sin(r) + y * math.cos(r)
        t = math.radians(-p["tilt"])
        y, z = y * math.cos(t) - z * math.sin(t), y * math.sin(t) + z * math.cos(t)
        u = math.radians(p.get("turn", 0.0))
        x, z = x * math.cos(u) + z * math.sin(u), -x * math.sin(u) + z * math.cos(u)
        y, z = y + p["height"], z - p["distance"]
        a = math.radians(-p["yaw"])
        return (math.cos(a) * x + math.sin(a) * z, y, -math.sin(a) * x + math.cos(a) * z)

    def _panel_geometry(self, p, index, aspect, selected, project, quads, edges, labels):
        sw, sh = p["width"], p["width"] * aspect
        cols = 24
        radius = sw / (2 * math.pi * p["curvature"]) if p["curvature"] > 0.001 else None
        pts = []
        for i in range(cols + 1):
            u = i / cols - 0.5
            if radius:
                theta = u * sw / radius
                pts.append((radius * math.sin(theta), radius * (1 - math.cos(theta))))
            else:
                pts.append((u * sw, 0.0))
        fill = SCREEN_FILL if selected else "#2b3442"
        edge = SCREEN_EDGE if selected else "#5b6577"
        for i in range(cols):
            (x0, z0), (x1, z1) = pts[i], pts[i + 1]
            corners = [self._place(p, x0, sh / 2, z0), self._place(p, x1, sh / 2, z1),
                       self._place(p, x1, -sh / 2, z1), self._place(p, x0, -sh / 2, z0)]
            projected = [project(c) for c in corners]
            if all(pt for pt, _ in projected):
                depth = sum(z for _, z in projected) / 4
                quads.append((depth, [coord for pt, _ in projected for coord in pt], fill, index))
        top = [self._place(p, x, sh / 2, z) for x, z in pts]
        bottom = [self._place(p, x, -sh / 2, z) for x, z in pts]
        for strip in (top, bottom):
            edges += [(a, b, edge, 2 if selected else 1) for a, b in zip(strip, strip[1:])]
        edges += [(top[0], bottom[0], edge, 2 if selected else 1), (top[-1], bottom[-1], edge, 2 if selected else 1)]
        labels.append((self._place(p, 0, 0, 0), _short(shown_title(p["title"], p.get("exe"), self.hide_titles)),
                       TEXT if selected else MUTED))


class ControlPanel:
    def __init__(self, store, engine, pad):
        self.store, self.engine, self.pad = store, engine, pad
        self.root = tk.Tk()
        self.root.title("PSVR Screen")
        self.root.geometry("1220x760")
        self.root.configure(bg=BG)
        self._style()

        # Header with the main actions (like SteamVR's)
        header = tk.Frame(self.root, bg=BAR_BG)
        header.pack(side="top", fill="x")
        tk.Label(header, text="  PSVR Screen", bg=BAR_BG, fg=TEXT, font=("Segoe UI Semibold", 13)).pack(
            side="left", padx=(6, 18), pady=10)
        self.mouse_button = self._header_button(header, "", self._toggle_mouse, ACCENT)
        self._header_button(header, "⟲  Recenter", engine.recenter)
        self._header_button(header, "＋  Add window", self._add_menu)

        # Fixed bottom bar: one tab per window in VR + add button
        self.bar = tk.Frame(self.root, bg=BAR_BG)
        self.bar.pack(side="bottom", fill="x")
        self._bar_key = None

        left = tk.Frame(self.root, bg=BG)
        left.pack(side="left", fill="both", expand=True, padx=(12, 6), pady=12)
        self.status = tk.Label(left, text="", bg=BG, fg=MUTED, justify="left", anchor="w", font=("Segoe UI", 9))
        self.status.pack(side="bottom", fill="x", pady=(6, 0))
        self.canvas = tk.Canvas(left, bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.viewer = Viewer3D(self.canvas, store)

        tabs = ttk.Notebook(self.root)
        tabs.pack(side="right", fill="y", padx=(6, 12), pady=12)
        side = ttk.Frame(tabs, style="Panel.TFrame", padding=14)
        pad_tab = ttk.Frame(tabs, style="Panel.TFrame", padding=14)
        headset_tab = ttk.Frame(tabs, style="Panel.TFrame", padding=14)
        keys_tab = ttk.Frame(tabs, style="Panel.TFrame", padding=14)
        tabs.add(side, text="  Screen  ")
        tabs.add(pad_tab, text="  Controller  ")
        tabs.add(headset_tab, text="  Headset  ")
        tabs.add(keys_tab, text="  Shortcuts  ")
        self._build_shortcuts_tab(keys_tab)

        s = store.get()
        self.vars = {}
        for name, label, fmt in SLIDERS:
            self._slider(side, name, label, fmt, getattr(s, name))

        self.mode = tk.StringVar(value="head" if s.follow_head else "room")
        row = ttk.Frame(side, style="Panel.TFrame")
        row.pack(fill="x", pady=(10, 2))
        ttk.Radiobutton(row, text="Fixed in the room", value="room", variable=self.mode,
                        command=self._mode_changed).pack(side="left")
        ttk.Radiobutton(row, text="Follow my head", value="head", variable=self.mode,
                        command=self._mode_changed).pack(side="left", padx=(12, 0))
        ttk.Button(row, text="Recenter", command=engine.recenter).pack(side="right")
        ttk.Label(side, text="These settings change the selected window (blue). Pick one in the bar below.",
                  style="Muted.TLabel", font=("Segoe UI", 8)).pack(anchor="w", pady=(8, 0))

        self.vr_pointer = tk.BooleanVar(value=False)
        pointer = getattr(engine, "pointer", None)
        if pointer is not None:
            ttk.Checkbutton(pad_tab, text="Mouse in VR (off = mouse on the main screen)",
                            variable=self.vr_pointer,
                            command=lambda: setattr(pointer, "enabled", self.vr_pointer.get())).pack(anchor="w")
            ttk.Label(pad_tab, text="Switch anywhere: Ctrl+Alt+P, or Options on the DS4.\n"
                                    "In VR: middle-button drag moves a window, Alt + wheel = closer / farther.",
                      style="Muted.TLabel", font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 12))
        self.auto_add = tk.BooleanVar(value=s.auto_add_windows)
        ttk.Checkbutton(pad_tab, text="Apps opened from VR (click in VR) open in VR",
                        variable=self.auto_add,
                        command=lambda: store.update(auto_add_windows=self.auto_add.get())).pack(anchor="w",
                                                                                              pady=(0, 12))
        self.pad_mouse = tk.BooleanVar(value=s.gamepad_mouse)
        ttk.Checkbutton(pad_tab, text="Controller moves the mouse", variable=self.pad_mouse,
                        command=lambda: store.update(gamepad_mouse=self.pad_mouse.get())).pack(anchor="w")
        self._slider(pad_tab, "mouse_speed", "Mouse speed", "{:.1f}x", s.mouse_speed)

        ttk.Label(pad_tab, text=CONTROLS_HELP, style="Muted.TLabel", justify="left").pack(anchor="w", pady=(12, 0))

        ttk.Label(headset_tab, text="Mode (takes effect when you restart the app)", style="Muted.TLabel").pack(anchor="w")
        self.engine_mode = tk.StringVar(value=s.engine)
        ttk.Radiobutton(headset_tab, text="Direct - our own VR, SteamVR must be closed", value="direct",
                        variable=self.engine_mode,
                        command=lambda: store.update(engine=self.engine_mode.get())).pack(anchor="w", pady=(4, 0))
        ttk.Radiobutton(headset_tab, text="SteamVR - screen floats inside SteamVR / games", value="steamvr",
                        variable=self.engine_mode,
                        command=lambda: store.update(engine=self.engine_mode.get())).pack(anchor="w")
        self.own_room = tk.BooleanVar(value=s.own_room)
        ttk.Checkbutton(headset_tab, text="SteamVR mode: own room (hides the SteamVR menu)",
                        variable=self.own_room,
                        command=lambda: store.update(own_room=self.own_room.get())).pack(anchor="w", pady=(4, 0))


        ttk.Label(headset_tab, text="Lens fine-tuning (direct mode)", style="Title.TLabel").pack(anchor="w", pady=(18, 0))
        for name, label, fmt in LENS_SLIDERS:
            self._slider(headset_tab, name, label, fmt, getattr(s, name))
        ttk.Button(headset_tab, text="Reset lens values", command=self._reset_lens).pack(anchor="w", pady=(12, 0))

        self._seen_version = store.version
        self._dirty_since = None
        self._banner = None
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        # Open in front of other windows, then behave like a normal window.
        self.root.attributes("-topmost", True)
        self.root.after(800, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()
        self._tick()

    def _style(self):
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.configure(".", background=PANEL, foreground=TEXT, fieldbackground=PANEL, font=("Segoe UI", 10))
        st.configure("Panel.TFrame", background=PANEL)
        st.configure("TNotebook", background=BG, borderwidth=0)
        st.configure("TNotebook.Tab", background="#232a35", foreground=MUTED, padding=(10, 6))
        st.map("TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", TEXT)])
        st.configure("Title.TLabel", font=("Segoe UI", 12, "bold"), background=PANEL)
        st.configure("Muted.TLabel", foreground=MUTED, background=PANEL)
        st.configure("TButton", background="#2a3140", padding=6)
        st.map("TButton", background=[("active", ACCENT)])
        st.configure("TCheckbutton", background=PANEL)
        st.configure("TRadiobutton", background=PANEL)
        st.configure("Horizontal.TScale", background=PANEL, troughcolor="#2a3140")
        st.configure("TCombobox", fieldbackground="#2a3140", background="#2a3140", foreground=TEXT,
                     arrowcolor=TEXT)
        st.map("TCombobox", fieldbackground=[("readonly", "#2a3140")], foreground=[("readonly", TEXT)],
               selectbackground=[("readonly", "#2a3140")], selectforeground=[("readonly", TEXT)])

    def _slider(self, parent, name, label, fmt, value):
        lo, hi = LIMITS[name]
        head = ttk.Frame(parent, style="Panel.TFrame")
        head.pack(fill="x", pady=(8, 0))
        ttk.Label(head, text=label).pack(side="left")
        value_label = ttk.Label(head, text=fmt.format(value), style="Muted.TLabel")
        value_label.pack(side="right")
        var = tk.DoubleVar(value=value)

        def changed(_=None):
            v = var.get()
            value_label.config(text=fmt.format(v))
            self.store.update(**{name: v})

        ttk.Scale(parent, from_=lo, to=hi, variable=var, command=changed, length=380).pack(fill="x")
        self.vars[name] = (var, value_label, fmt)

    # -- shortcuts ---------------------------------------------------------------------------
    def _build_shortcuts_tab(self, tab):
        ttk.Label(tab, text="Keyboard shortcuts", style="Title.TLabel").pack(anchor="w")
        ttk.Label(tab, text="Click a shortcut, then press the new keys (with Ctrl / Alt / Shift). Esc cancels.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 10))
        self._shortcut_buttons = {}
        for action, label in SHORTCUT_ACTIONS.items():
            row = ttk.Frame(tab, style="Panel.TFrame")
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=label).pack(side="left")
            b = tk.Label(row, text="", bg="#2a3140", fg=TEXT, font=("Segoe UI Semibold", 10), width=16,
                         cursor="hand2", pady=5)
            b.pack(side="right")
            b.bind("<Button-1>", lambda e, a=action: self._record_shortcut(a))
            self._shortcut_buttons[action] = b
        ttk.Label(tab, text="DS4: Options = mouse into VR / back, Triangle = recenter.",
                  style="Muted.TLabel").pack(anchor="w", pady=(14, 0))
        self._shortcut_problem = ttk.Label(tab, text="", style="Muted.TLabel", foreground="#ff8a80")
        self._shortcut_problem.pack(anchor="w", pady=(8, 0))
        self._recording = None
        self._show_shortcuts()

    def _show_shortcuts(self):
        keys = self.store.get().shortcuts
        for action, b in self._shortcut_buttons.items():
            recording = action == self._recording
            b.config(text="press keys..." if recording else keys.get(action, ""),
                     bg=ACCENT if recording else "#2a3140")

    def _record_shortcut(self, action):
        self._recording = action
        self._show_shortcuts()
        self.root.bind("<KeyPress>", self._on_shortcut_key)

    def _on_shortcut_key(self, event):
        if event.keysym == "Escape":
            self._stop_recording()
            return
        text = shortcut_keys.from_tk(event.state, event.keysym)
        if text is None:
            return   # only modifiers so far, or no modifier: keep waiting
        keys = dict(self.store.get().shortcuts)
        keys[self._recording] = text
        self.store.update(shortcuts=keys)
        pointer = getattr(self.engine, "pointer", None)
        if pointer is not None:
            pointer.reload_shortcuts()
            self.root.after(400, self._check_shortcut_problems)
        self._stop_recording()

    def _stop_recording(self):
        self.root.unbind("<KeyPress>")
        self._recording = None
        self._show_shortcuts()

    def _check_shortcut_problems(self):
        pointer = getattr(self.engine, "pointer", None)
        problems = pointer.shortcut_problems if pointer is not None else []
        self._shortcut_problem.config(
            text=("Could not use: " + ", ".join(problems) + " (another program already uses it)") if problems else "")

    # -- header ------------------------------------------------------------------------------
    def _header_button(self, parent, text, action, bg="#2a3140"):
        b = tk.Label(parent, text=text, bg=bg, fg="white", font=("Segoe UI Semibold", 10), cursor="hand2",
                     padx=16, pady=7)
        b.pack(side="left", padx=4, pady=8)
        b.bind("<Button-1>", lambda e: action(e) if action == self._add_menu else action())
        return b

    def _update_banner(self, mouse_in_vr):
        """Big banner on the main screen while the mouse is in VR, so it's never a mystery why the mouse
        doesn't move here."""
        if mouse_in_vr and self._banner is None:
            b = tk.Toplevel(self.root)
            b.overrideredirect(True)
            b.attributes("-topmost", True)
            b.configure(bg=GAZE_DARK)
            keys = self.store.get().shortcuts.get("mouse_vr", "")
            tk.Label(b, text=f"🖱  The mouse is in VR  -  press {keys} (or Options on the DS4) to get it back",
                     bg=GAZE_DARK, fg="white", font=("Segoe UI Semibold", 14), padx=24, pady=12).pack()
            b.update_idletasks()
            main = displays.find("main")
            x = (main.left + (main.width - b.winfo_width()) // 2) if main else 200
            b.geometry(f"+{x}+{(main.top if main else 0) + 24}")
            self._banner = b
        elif not mouse_in_vr and self._banner is not None:
            self._banner.destroy()
            self._banner = None

    def _toggle_mouse(self):
        pointer = getattr(self.engine, "pointer", None)
        if pointer is not None:
            pointer.toggle()

    def _update_mouse_button(self):
        pointer = getattr(self.engine, "pointer", None)
        keys = self.store.get().shortcuts.get("mouse_vr", "")
        if pointer is None:
            self.mouse_button.config(text="🖱  Mouse in VR: not in this mode", bg="#2a3140")
        elif pointer.active:
            self.mouse_button.config(text=f"🖱  Mouse is in VR - click to bring it back   ({keys})", bg=GAZE_DARK)
        elif pointer.enabled:
            self.mouse_button.config(text="🖱  Mouse goes to VR when the headset picture runs", bg="#2a3140")
        else:
            self.mouse_button.config(text=f"🖱  Send mouse to VR   ({keys})", bg=ACCENT)

    # -- bottom bar --------------------------------------------------------------------------
    def _hide_titles(self):
        """Private mode, or a video playing in VR: the PC screen shows nothing about what you watch."""
        shell = getattr(self.engine, "shell", None)
        return self.store.get().private_mode or getattr(shell, "video", None) is not None

    def _build_bar(self, s):
        for child in self.bar.winfo_children():
            child.destroy()
        for i, p in enumerate(s.panels):
            shown = p.get("shown", True)
            bg = TAB_SELECTED if i == s.selected else TAB_BG
            tab = tk.Frame(self.bar, bg=bg)
            tab.pack(side="left", padx=(8, 0), pady=8)
            icon = "▣" if p["source"] == "window" else "🖥"
            title = shown_title(p["title"], p.get("exe"), self._hide_titles())
            label = tk.Label(tab, text=f" {icon}  {_short(title, 24)} ", bg=bg,
                             fg=TEXT if shown else MUTED, font=("Segoe UI", 10), cursor="hand2", pady=6)
            label.pack(side="left")
            label.bind("<Button-1>", lambda e, i=i: self.store.update(selected=i))
            for text, tip, action in (("–" if shown else "▢", "minimize" if shown else "show",
                                       lambda i=i, shown=shown: self.store.update_panel(i, shown=not shown)),
                                      ("✕", "remove from VR", lambda i=i: self.store.remove_panel(i))):
                b = tk.Label(tab, text=f" {text} ", bg=bg, fg=MUTED, font=("Segoe UI", 10, "bold"), cursor="hand2")
                b.pack(side="left")
                b.bind("<Button-1>", lambda e, action=action: action())
                b.bind("<Enter>", lambda e, b=b: b.config(fg=TEXT))
                b.bind("<Leave>", lambda e, b=b: b.config(fg=MUTED))

    def _add_menu(self, event):
        s = self.store.get()
        in_vr = {p.get("hwnd") for p in s.panels if p["source"] == "window"}
        menu = tk.Menu(self.root, tearoff=False, bg=PANEL, fg=TEXT, activebackground=ACCENT)
        open_windows = [w for w in windows.list_open_windows() if w[0] not in in_vr]
        for hwnd, title, exe in open_windows:
            menu.add_command(label=f"{_short(title, 50)}   ({exe})",
                             command=lambda h=hwnd, t=title, x=exe: self._add(new_panel_for(h, t, x)))
        if not open_windows:
            menu.add_command(label="(no other open windows - open an app first)", state="disabled")
        menu.add_separator()
        in_use = {p["source"] for p in s.panels}
        for kind in displays.view_kinds():
            if kind not in in_use:
                menu.add_command(label=f"Desktop view {kind[4:]} (a whole desktop with big text)",
                                 command=lambda k=kind: self._add(new_panel(
                                     k, f"Desktop view {k[4:]}", width=3.0, distance=1.6, curvature=0.2)))
        menu.add_command(label="Main monitor (whole screen - for videos)",
                         command=lambda: self._add(new_panel("main", "Main monitor")))
        menu.tk_popup(event.x_root, event.y_root)

    def _add(self, panel):
        """Into VR where you look; the windows there make room."""
        workspace.add_window(self.store, panel, -math.degrees(self.engine.look_yaw))

    def _reset_lens(self):
        self.store.update(**LENS_DEFAULTS)

    def _mode_changed(self):
        self.store.update(follow_head=self.mode.get() == "head")
        self.engine.recenter()

    def _sync_from_store(self):
        """Reflect changes made with the controller."""
        s = self.store.get()
        for name, (var, label, fmt) in self.vars.items():
            value = getattr(s, name)
            if abs(var.get() - value) > 1e-6:
                var.set(value)
                label.config(text=fmt.format(value))
        self.own_room.set(s.own_room)
        self.engine_mode.set(s.engine)
        self.pad_mouse.set(s.gamepad_mouse)
        self.mode.set("head" if s.follow_head else "room")

    def _tick(self):
        if self.store.version != self._seen_version:
            self._seen_version = self.store.version
            self._sync_from_store()
            self._dirty_since = self._dirty_since or time.monotonic()
        if self._dirty_since and time.monotonic() - self._dirty_since > 1.0:
            self.store.save()
            self._dirty_since = None

        s = self.store.get()
        bar_key = (s.selected, tuple((p["id"], p["title"], p.get("shown", True)) for p in s.panels),
                   self._hide_titles())
        self.viewer.hide_titles = self._hide_titles()
        if bar_key != self._bar_key:
            self._bar_key = bar_key
            self._build_bar(s)
        pointer = getattr(self.engine, "pointer", None)
        if pointer is not None and self.vr_pointer.get() != pointer.enabled:
            self.vr_pointer.set(pointer.enabled)   # changed with Ctrl+Alt+P
        self._update_mouse_button()
        self._update_banner(bool(pointer is not None and pointer.active))
        aspects = getattr(self.engine, "aspects", {})
        self.viewer.draw(s, aspects, self.engine.look_yaw, self.engine.look_pitch)
        fps = f"  ({self.engine.fps:.0f} fps)" if self.engine.fps else ""
        pointer_state = ""
        if pointer is not None:
            pointer_state = ("   |   Mouse: could not take over the mouse (hook refused)" if pointer.hook_ok is False
                             else "   |   Mouse: IN VR  (Ctrl+Alt+P = back to main screen)" if pointer.active
                             else "   |   Mouse: main screen  (Ctrl+Alt+P = into VR)")
        self.status.config(text=f"VR: {self.engine.status}{fps}{pointer_state}\nController: {self.pad.status}")
        self.root.after(33, self._tick)

    def run(self):
        self.root.mainloop()

    def close(self):
        self.store.save()
        self.root.destroy()


def _inside(poly, x, y):
    """Point in polygon (flat list x0, y0, x1, y1, ...)."""
    pts = list(zip(poly[0::2], poly[1::2]))
    inside = False
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def _short(text, n=24):
    return text if len(text) <= n else text[: n - 1] + "…"


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    length = math.sqrt(_dot(a, a)) or 1.0
    return (a[0] / length, a[1] / length, a[2] / length)
