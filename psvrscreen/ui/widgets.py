"""Widgets for VR interfaces: layout containers, labels, buttons, sliders, switches, seek bars, and the
pieces of app pages: pictures, tiles, cards, badges.

A widget has a size in image pixels and draws itself with Pillow. Interactive widgets have a stable `key`:
views are rebuilt whenever what they show changes, and the key is how hover / press survive a rebuild.
Callbacks (on_click, on_change) run on the render thread.
"""
from PIL import Image, ImageDraw

from . import theme

SLIDER_FULL_RANGE_DEGREES = 25.0   # pointer movement that takes a slider from one end to the other
SLIDER_WHEEL_STEPS = 100           # mouse wheel notches from one end of a slider to the other


class Widget:
    interactive = False

    def __init__(self, w=0, h=0, key=None, flex=0):
        self.w, self.h = w, h
        self.key = key
        self.flex = flex         # in a Row / Column: takes this share of the space left over
        self.box = (0, 0, w, h)

    def measure(self):
        """Natural size (w, h) before layout."""
        return self.w, self.h

    def layout(self, x, y, w, h):
        self.box = (x, y, x + w, y + h)

    def children(self):
        return ()

    def draw(self, d, ctx):
        for c in self.children():
            c.draw(d, ctx)

    def walk(self):
        yield self
        for c in self.children():
            yield from c.walk()

    def contains(self, x, y):
        x0, y0, x1, y1 = self.box
        return x0 <= x < x1 and y0 <= y < y1

    # Interactive widgets override these (ctx: the view's state, e: scene.events.PointerEvent)
    def press(self, ctx, e):
        pass

    def drag(self, ctx, e):
        pass

    def release(self, ctx, e, inside):
        pass

    def wheel(self, ctx, e):
        pass


# -- layout -----------------------------------------------------------------------------------------
class _Linear(Widget):
    """Row (horizontal) or Column (vertical) of children, with padding, gaps and an optional background.
    Children with `flex` share the space left over when the container has a fixed size."""
    horizontal = True

    def __init__(self, items, gap=0, pad=0, bg=None, radius=0, w=None, h=None, align="center", flex=0, key=None,
                 border=None):
        super().__init__(w or 0, h or 0, key, flex)
        self.items = [i for i in items if i is not None]
        self.gap, self.pad, self.bg, self.radius, self.align = gap, pad, bg, radius, align
        self.border = border     # an outline color (panels: theme.EDGE)
        self.fixed_w, self.fixed_h = w, h

    def children(self):
        return self.items

    def measure(self):
        sizes = [i.measure() for i in self.items]
        main = sum(s[0 if self.horizontal else 1] for s in sizes) + self.gap * max(0, len(sizes) - 1)
        cross = max((s[1 if self.horizontal else 0] for s in sizes), default=0)
        w, h = (main, cross) if self.horizontal else (cross, main)
        return (self.fixed_w or w + 2 * self.pad), (self.fixed_h or h + 2 * self.pad)

    def layout(self, x, y, w, h):
        super().layout(x, y, w, h)
        inner_main = (w if self.horizontal else h) - 2 * self.pad
        inner_cross = (h if self.horizontal else w) - 2 * self.pad
        sizes = [i.measure() for i in self.items]
        used = sum(s[0 if self.horizontal else 1] for s, i in zip(sizes, self.items) if not i.flex)
        used += self.gap * max(0, len(self.items) - 1)
        flex_total = sum(i.flex for i in self.items)
        left = max(0, inner_main - used)
        pos = (x if self.horizontal else y) + self.pad
        for item, size in zip(self.items, sizes):
            main = left * item.flex / flex_total if item.flex else size[0 if self.horizontal else 1]
            cross = size[1 if self.horizontal else 0]
            if self.align == "stretch" or cross == 0:
                cross = inner_cross
            offset = (inner_cross - cross) / 2 if self.align == "center" else 0
            if self.horizontal:
                item.layout(int(pos), int(y + self.pad + offset), int(main), int(cross))
            else:
                item.layout(int(x + self.pad + offset), int(pos), int(cross), int(main))
            pos += main + self.gap

    def draw(self, d, ctx):
        if self.bg:
            d.rounded_rectangle(self.box, self.radius, fill=self.bg, outline=self.border,
                                width=2 if self.border else 0)
        super().draw(d, ctx)


def Panel(items, w=None, h=None, pad=24, gap=theme.GAP, horizontal=False, **kw):
    """The glass panel everything floats in (dock, dialogs, app pages): dark glass, big round corners, a fine
    light edge."""
    cls = Row if horizontal else Column
    return cls(items, pad=pad, gap=gap, bg=theme.BG, radius=theme.RADIUS_PANEL, border=theme.EDGE, w=w, h=h, **kw)


class Row(_Linear):
    horizontal = True


class Column(_Linear):
    horizontal = False


class Spacer(Widget):
    def __init__(self, w=0, h=0, flex=0):
        super().__init__(w, h, flex=flex)


# -- content ----------------------------------------------------------------------------------------
class Label(Widget):
    def __init__(self, text, size=theme.TEXT, color=theme.FG, w=None, h=theme.ROW, align="left", icon=False,
                 flex=0, indent=0):
        self.text, self.size, self.color, self.align, self.icon = text, size, color, align, icon
        self.indent = indent
        fnt = theme.font(size, icon)
        super().__init__(w if w is not None else int(fnt.getlength(text)) + 4 + indent, h, flex=flex)

    def draw(self, d, ctx):
        x0, y0, x1, y1 = self.box
        x0 += self.indent
        fnt = theme.font(self.size, self.icon)
        text = theme.shorten(self.text, fnt, x1 - x0)
        anchor = {"left": "lm", "center": "mm", "right": "rm"}[self.align]
        x = {"left": x0, "center": (x0 + x1) / 2, "right": x1}[self.align]
        d.text((x, (y0 + y1) / 2), text, font=fnt, fill=self.color, anchor=anchor)


class Button(Widget):
    """Icon and/or text. style: "normal", "flat" (no background until hovered), "danger" (red on hover),
    "primary" (round, accent colored: the main action, like play). `selected` shows it as switched on /
    current. `on_right`: a right click on it (usually opens a menu of options)."""
    interactive = True

    def __init__(self, key, text=None, icon=None, on_click=None, w=None, h=theme.ROW, style="normal",
                 selected=False, muted=False, size=theme.TEXT, flex=0, on_right=None):
        self.text, self.icon, self.on_click, self.style = text, icon, on_click, style
        self.on_right = on_right
        self.selected, self.muted, self.size = selected, muted, size
        if w is None:
            w = (h if icon else 24) + (int(theme.font(size).getlength(text)) + 24 if text else 0)
        super().__init__(w, h, key, flex)

    def background(self, ctx):
        if self.style == "primary":
            return theme.ACCENT_LIGHT if ctx.hovered(self.key) else theme.ACCENT
        if ctx.hovered(self.key):
            return theme.DANGER if self.style == "danger" else theme.HOVER
        if self.selected:
            return theme.SELECTED
        return None if self.style in ("flat", "danger") else theme.BUTTON

    def draw(self, d, ctx):
        x0, y0, x1, y1 = self.box
        bg = self.background(ctx)
        if bg:
            radius = min(x1 - x0, y1 - y0) / 2 if self.style == "primary" else min(theme.RADIUS * 1.5, (y1 - y0) / 2)
            d.rounded_rectangle(self.box, radius, fill=bg)
        color = theme.MUTED if self.muted else (theme.ACCENT_LIGHT if self.selected and not self.text else theme.FG)
        h = y1 - y0
        cy = (y0 + y1) / 2
        if self.icon and not self.text:
            d.text(((x0 + x1) / 2, cy), self.icon, font=theme.font(self.size, True), fill=color, anchor="mm")
            return
        x = x0 + 24
        if self.icon:
            d.text((x0 + h * 0.45, cy), self.icon, font=theme.font(self.size - 4, True), fill=color, anchor="mm")
            x = x0 + h * 0.85
        fnt = theme.font(self.size)
        d.text((x, cy), theme.shorten(self.text, fnt, x1 - x - 12), font=fnt, fill=color, anchor="lm")

    def release(self, ctx, e, inside):
        if inside and self.on_click and e.button == "left":
            self.on_click()
        elif inside and self.on_right and e.button == "right":
            self.on_right()


class Slider(Widget):
    """label | track with knob | value, on one row. Grab anywhere on the row and move sideways: the change
    is relative to where you grabbed it (it never jumps), SLIDER_FULL_RANGE_DEGREES for the whole range.
    The mouse wheel moves it in small steps. `text(value)` may show the value differently than `fmt`."""
    interactive = True

    def __init__(self, key, label, value, lo, hi, fmt="{:.2f}", on_change=None, w=900, h=theme.ROW,
                 disabled=False, disabled_text="locked", text=None):
        super().__init__(w, h, key)
        self.label, self.value, self.lo, self.hi, self.fmt = label, value, lo, hi, fmt
        self.on_change, self.disabled, self.disabled_text = on_change, disabled, disabled_text
        self.text = text or fmt.format
        self.interactive = not disabled

    def draw(self, d, ctx):
        x0, y0, x1, y1 = self.box
        w, cy = x1 - x0, (y0 + y1) / 2
        d.text((x0 + 24, cy), self.label, font=theme.font(theme.TEXT), fill=theme.MUTED if self.disabled else theme.FG,
               anchor="lm")
        value_text = self.disabled_text if self.disabled else self.text(self.value)
        d.text((x1 - 24, cy), value_text, font=theme.font(theme.SMALL), fill=theme.MUTED, anchor="rm")
        t0, t1 = x0 + int(w * 0.32), x0 + int(w * 0.76)
        frac = max(0.0, min(1.0, (self.value - self.lo) / (self.hi - self.lo)))
        color = theme.DISABLED if self.disabled else theme.ACCENT
        d.rounded_rectangle((t0, cy - 6, t1, cy + 6), 6, fill=theme.TRACK)
        knob = t0 + frac * (t1 - t0)
        d.rounded_rectangle((t0, cy - 6, knob, cy + 6), 6, fill=color)
        r = 20 if (ctx.hovered(self.key) or ctx.pressed(self.key)) and not self.disabled else 15
        d.ellipse((knob - r, cy - r, knob + r, cy + r), fill=theme.MUTED if self.disabled else theme.FG,
                  outline=color, width=4)

    def press(self, ctx, e):
        ctx.drag_start = self.value

    def drag(self, ctx, e):
        if ctx.drag_start is None or not self.on_change:
            return
        frac = (e.yaw - e.start_yaw) / SLIDER_FULL_RANGE_DEGREES
        self.on_change(max(self.lo, min(self.hi, ctx.drag_start + frac * (self.hi - self.lo))))

    def wheel(self, ctx, e):
        if self.on_change and not self.disabled:
            step = (self.hi - self.lo) / SLIDER_WHEEL_STEPS * (1 if e.delta > 0 else -1)
            self.on_change(max(self.lo, min(self.hi, self.value + step)))


class Toggle(Widget):
    """label ... switch, on one row; click anywhere on the row."""
    interactive = True

    def __init__(self, key, label, value, on_change=None, w=900, h=theme.ROW):
        super().__init__(w, h, key)
        self.label, self.value, self.on_change = label, value, on_change

    def draw(self, d, ctx):
        x0, y0, x1, y1 = self.box
        cy = (y0 + y1) / 2
        d.text((x0 + 24, cy), self.label, font=theme.font(theme.TEXT), fill=theme.FG, anchor="lm")
        sx1, sx0 = x1 - 24, x1 - 24 - 96
        fill = theme.ACCENT if self.value else theme.TRACK
        if ctx.hovered(self.key):
            fill = theme.ACCENT_LIGHT if self.value else theme.HOVER
        d.rounded_rectangle((sx0, cy - 24, sx1, cy + 24), 24, fill=fill)
        knob = sx1 - 24 if self.value else sx0 + 24
        d.ellipse((knob - 18, cy - 18, knob + 18, cy + 18), fill=theme.FG)

    def release(self, ctx, e, inside):
        if inside and self.on_change and e.button == "left":
            self.on_change(not self.value)


class Handle(Widget):
    """An area you grab and drag (e.g. a window's title to move it). Gets every drag event."""
    interactive = True

    def __init__(self, key, child=None, on_press=None, on_drag=None, on_release=None, on_wheel=None, w=0, h=0,
                 flex=0, hover_bg=theme.HOVER):
        super().__init__(w, h, key, flex)
        self.child = child
        self.on_press, self.on_drag, self.on_release, self.on_wheel = on_press, on_drag, on_release, on_wheel
        self.hover_bg = hover_bg

    def children(self):
        return (self.child,) if self.child else ()

    def measure(self):
        return self.child.measure() if self.child and not (self.w or self.h) else (self.w, self.h)

    def layout(self, x, y, w, h):
        super().layout(x, y, w, h)
        if self.child:
            self.child.layout(x, y, w, h)

    def draw(self, d, ctx):
        if self.hover_bg and (ctx.hovered(self.key) or ctx.pressed(self.key)):
            d.rectangle(self.box, fill=self.hover_bg)
        super().draw(d, ctx)

    def press(self, ctx, e):
        if self.on_press:
            self.on_press(e)

    def drag(self, ctx, e):
        if self.on_drag:
            self.on_drag(e)

    def release(self, ctx, e, inside):
        if self.on_release:
            self.on_release(e)

    def wheel(self, ctx, e):
        if self.on_wheel:
            self.on_wheel(e)


class SeekBar(Widget):
    """A position bar (videos): press or drag anywhere on it to jump there. `value` and what `on_seek` gets
    are 0..1. While dragging, the knob follows the pointer and the jump happens on release."""
    interactive = True

    def __init__(self, key, value, on_seek=None, w=600, h=theme.ROW, flex=0):
        super().__init__(w, h, key, flex)
        self.value, self.on_seek = value, on_seek

    def _at(self, ctx, e):
        if e.u is None:
            return ctx.drag_start
        x0, _, x1, _ = self.box
        return max(0.0, min(1.0, (e.u * ctx.size[0] - x0 - 24) / max(1, x1 - x0 - 48)))

    def draw(self, d, ctx):
        x0, y0, x1, y1 = self.box
        cy = (y0 + y1) / 2
        t0, t1 = x0 + 24, x1 - 24
        frac = ctx.drag_start if ctx.pressed(self.key) and ctx.drag_start is not None else self.value
        knob = t0 + max(0.0, min(1.0, frac)) * (t1 - t0)
        d.rounded_rectangle((t0, cy - 7, t1, cy + 7), 7, fill=theme.TRACK)
        d.rounded_rectangle((t0, cy - 7, knob, cy + 7), 7, fill=theme.ACCENT)
        r = 20 if ctx.hovered(self.key) or ctx.pressed(self.key) else 14
        d.ellipse((knob - r, cy - r, knob + r, cy + r), fill=theme.FG, outline=theme.ACCENT, width=4)

    def press(self, ctx, e):
        ctx.drag_start = self._at(ctx, e)

    def drag(self, ctx, e):
        ctx.drag_start = self._at(ctx, e)

    def release(self, ctx, e, inside):
        if ctx.drag_start is not None and self.on_seek and e.button == "left":
            self.on_seek(ctx.drag_start)


# -- app pages: pictures, tiles, cards, badges ------------------------------------------------------------
def _rounded(image, size, radius):
    """`image` (PIL) scaled to fill `size` (cut to its shape), with round corners."""
    w, h = size
    iw, ih = image.size
    k = max(w / iw, h / ih)
    image = image.resize((max(1, int(iw * k)), max(1, int(ih * k))), Image.LANCZOS)
    left, top = (image.width - w) // 2, (image.height - h) // 2
    image = image.crop((left, top, left + w, top + h)).convert("RGBA")
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius, fill=255)
    image.putalpha(mask)
    return image


class Picture(Widget):
    """A picture (PIL image, or None while it loads: a calm placeholder with an icon) with round corners.
    fit: show it whole (bars around it) instead of filling the box."""

    def __init__(self, image, w, h, radius=theme.RADIUS_CARD, icon=None, fit=False, flex=0):
        super().__init__(w, h, flex=flex)
        self.image, self.radius, self.icon, self.fit = image, radius, icon, fit

    def draw(self, d, ctx):
        x0, y0, x1, y1 = (int(v) for v in self.box)
        if self.image is None or ctx.canvas is None:
            d.rounded_rectangle((x0, y0, x1, y1), self.radius, fill=theme.CARD)
            if self.icon:
                d.text(((x0 + x1) / 2, (y0 + y1) / 2), self.icon, font=theme.font(int((y1 - y0) * 0.3), True),
                       fill=theme.DISABLED, anchor="mm")
            return
        if self.fit:
            k = min((x1 - x0) / self.image.width, (y1 - y0) / self.image.height)
            w, h = max(1, int(self.image.width * k)), max(1, int(self.image.height * k))
            x0, y0 = x0 + (x1 - x0 - w) // 2, y0 + (y1 - y0 - h) // 2
            x1, y1 = x0 + w, y0 + h
        picture = _rounded(self.image, (x1 - x0, y1 - y0), self.radius)
        ctx.canvas.alpha_composite(picture, (x0, y0))


class Badge(Widget):
    """A small rounded label ("180° 3D", "12:31")."""

    def __init__(self, text, color=theme.ACCENT, size=theme.SMALL - 8, fg=theme.FG):
        self.text, self.color, self.size, self.fg = text, color, size, fg
        fnt = theme.font(size)
        super().__init__(int(fnt.getlength(text)) + 28, int(size * 1.6))

    def draw(self, d, ctx):
        if not self.text:
            return
        x0, y0, x1, y1 = self.box
        d.rounded_rectangle(self.box, (y1 - y0) / 2, fill=self.color)
        d.text(((x0 + x1) / 2, (y0 + y1) / 2), self.text, font=theme.font(self.size), fill=self.fg, anchor="mm")


class Tile(Widget):
    """A big launcher tile: a round colored icon and a name under it (Home)."""
    interactive = True

    def __init__(self, key, label, icon, color, on_click=None, w=300, h=300, subtitle=None):
        super().__init__(w, h, key)
        self.label, self.icon, self.color, self.on_click, self.subtitle = label, icon, color, on_click, subtitle

    def draw(self, d, ctx):
        x0, y0, x1, y1 = self.box
        hovered = ctx.hovered(self.key) or ctx.pressed(self.key)
        d.rounded_rectangle(self.box, theme.RADIUS_CARD, fill=theme.HOVER if hovered else theme.CARD)
        cx, r = (x0 + x1) / 2, (x1 - x0) * 0.22
        cy = y0 + (y1 - y0) * 0.38
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=self.color)
        d.text((cx, cy), self.icon, font=theme.font(int(r * 0.95), True), fill=theme.FG, anchor="mm")
        fnt = theme.font(theme.TEXT)
        d.text((cx, y0 + (y1 - y0) * 0.74), theme.shorten(self.label, fnt, x1 - x0 - 24), font=fnt, fill=theme.FG,
               anchor="mm")
        if self.subtitle:
            small = theme.font(theme.SMALL - 8, weight="regular")
            d.text((cx, y0 + (y1 - y0) * 0.88), theme.shorten(self.subtitle, small, x1 - x0 - 24), font=small,
                   fill=theme.MUTED, anchor="mm")

    def release(self, ctx, e, inside):
        if inside and self.on_click and e.button == "left":
            self.on_click()


class Card(Widget):
    """A video / photo in a grid: its picture, a title under it and badges on the picture. Click: open it;
    right click: its menu."""
    interactive = True

    def __init__(self, key, title, image, w, h, badges=(), on_click=None, on_right=None, icon=None, subtitle=None):
        super().__init__(w, h, key)
        self.title, self.image, self.badges, self.icon, self.subtitle = title, image, badges, icon, subtitle
        self.on_click, self.on_right = on_click, on_right

    def draw(self, d, ctx):
        x0, y0, x1, y1 = (int(v) for v in self.box)
        hovered = ctx.hovered(self.key) or ctx.pressed(self.key)
        if hovered:
            d.rounded_rectangle((x0 - 8, y0 - 8, x1 + 8, y1 + 8), theme.RADIUS_CARD + 6, fill=theme.HOVER)
        text_h = int(theme.SMALL * (2.6 if self.subtitle else 1.6))
        picture = Picture(self.image, x1 - x0, y1 - y0 - text_h, icon=self.icon)
        picture.layout(x0, y0, x1 - x0, y1 - y0 - text_h)
        picture.draw(d, ctx)
        bx = x0 + 12
        for text, color in self.badges:
            badge = Badge(text, color)
            bw, bh = badge.measure()
            badge.layout(bx, y0 + 12, bw, bh)
            badge.draw(d, ctx)
            bx += bw + 8
        fnt = theme.font(theme.SMALL)
        ty = y1 - text_h + int(theme.SMALL * 0.8)
        d.text((x0 + 6, ty), theme.shorten(self.title, fnt, x1 - x0 - 12), font=fnt, fill=theme.FG, anchor="lm")
        if self.subtitle:
            small = theme.font(theme.SMALL - 8, weight="regular")
            d.text((x0 + 6, ty + theme.SMALL), theme.shorten(self.subtitle, small, x1 - x0 - 12), font=small,
                   fill=theme.MUTED, anchor="lm")

    def release(self, ctx, e, inside):
        if inside and self.on_click and e.button == "left":
            self.on_click()
        elif inside and self.on_right and e.button == "right":
            self.on_right()


def Grid(items, columns, gap=theme.GAP * 2):
    """Widgets in rows of `columns` (the last row left-aligned)."""
    rows = []
    for i in range(0, len(items), columns):
        chunk = items[i:i + columns]
        rows.append(Row(chunk, gap=gap, align="start"))
    return Column(rows, gap=gap, align="start")
