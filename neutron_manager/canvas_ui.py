"""Lightweight controls drawn on one tk.Canvas per page, with the look of Swing Editor's widgets.

Why: every CustomTkinter widget is its own canvas window. A settings page had ~150 of them, and
Windows took 30-100 ms to paint a page when it was shown, far above the 8.3 ms of a frame at 120 Hz.
Here a whole page is a single window: showing it, updating the tuner or moving a slider only changes
a few canvas items. Rounded shapes are drawn once with antialiasing (PIL, 4x supersampled) and reused
as images; everything is sized with the window's DPI scaling, so it looks the same on any monitor.
"""
import ctypes
import sys
import tkinter as tk
import tkinter.font as tkfont

from PIL import Image, ImageDraw, ImageTk

FRAME_MS = 8                  # animation tick: 120 frames per second
SCROLL_STEP = 64              # pixels (unscaled) per mouse wheel notch
SCROLL_EASE = 0.32            # fraction of the remaining distance covered per frame


def enable_fine_timers():
    """Windows timers tick every 15.6 ms by default, too coarse for 8 ms frames: ask for 1 ms."""
    if sys.platform == "win32":
        try:
            ctypes.windll.winmm.timeBeginPeriod(1)
        except (AttributeError, OSError):
            pass


def disable_fine_timers():
    if sys.platform == "win32":
        try:
            ctypes.windll.winmm.timeEndPeriod(1)
        except (AttributeError, OSError):
            pass


class Painter:
    """Fonts and antialiased rounded shapes at the window's scaling, cached."""

    def __init__(self, root, scale, family, colors):
        self.root, self.s, self.family, self.c = root, scale, family, colors
        self._images = {}
        self._fonts = {}

    def px(self, value):
        return int(round(value * self.s))

    def font(self, size, weight="normal"):
        key = (size, weight)
        if key not in self._fonts:
            self._fonts[key] = tkfont.Font(root=self.root, family=self.family, size=-self.px(size), weight=weight)
        return self._fonts[key]

    def rounded(self, w, h, r, fill, border=None, bw=1, bg=None):
        """PhotoImage of a rounded rectangle; sizes in final pixels.

        It is composited onto the colour it will sit on (bg, the card by default) and saved without
        transparency: Tk draws opaque images several times faster than ones it must blend."""
        bg = bg or self.c["CARD"]
        key = ("r", w, h, r, fill, border, bw, bg)
        image = self._images.get(key)
        if image is None:
            k = 4
            im = Image.new("RGBA", (max(1, w) * k, max(1, h) * k), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            if border:
                d.rounded_rectangle([0, 0, w * k - 1, h * k - 1], r * k, fill=border)
                i = bw * k
                d.rounded_rectangle([i, i, w * k - 1 - i, h * k - 1 - i], max(0, r - bw) * k, fill=fill)
            else:
                d.rounded_rectangle([0, 0, w * k - 1, h * k - 1], r * k, fill=fill)
            im = im.resize((max(1, w), max(1, h)), Image.LANCZOS)
            base = Image.new("RGBA", im.size, bg)
            base.alpha_composite(im)
            image = ImageTk.PhotoImage(base.convert("RGB"), master=self.root)
            self._images[key] = image
        return image

    def circle(self, d, fill, bg=None):
        return self.rounded(d, d, d // 2, fill, bg=bg)


class Page(tk.Canvas):
    """A scrollable canvas page, laid out once at a known width: build(page) draws it, returns its height."""

    def __init__(self, master, painter, bg, build, width):
        super().__init__(master, bg=bg, highlightthickness=0, bd=0, yscrollincrement=1, xscrollincrement=1)
        self.p = painter
        self.bg = bg
        self.width = width
        self._offset = 0.0
        self._target = 0.0
        self._animating = False
        self._bar = None
        self.height = build(self)
        self.configure(scrollregion=(0, 0, width, self.height))
        self.bind("<Configure>", self._on_configure)
        self.bind("<Enter>", lambda e: self.bind_all("<MouseWheel>", self._on_wheel))
        self.bind("<Leave>", lambda e: self.unbind_all("<MouseWheel>"))

    def _on_configure(self, event):
        if event.height > 1:
            self.configure(scrollregion=(0, 0, self.width, max(self.height, event.height)))
            self._clamp()
            self._draw_bar()

    # ------------------------------------------------------------ smooth scrolling at 120 fps
    def _max_offset(self):
        return max(0, self.height - self.winfo_height())

    def _clamp(self):
        self._target = min(max(self._target, 0), self._max_offset())

    def _on_wheel(self, event):
        if self._max_offset() <= 0:
            return
        self._target -= event.delta / 120 * self.p.px(SCROLL_STEP)
        self._clamp()
        if not self._animating:
            self._animating = True
            self.after(FRAME_MS, self._animate)

    def _animate(self):
        distance = self._target - self._offset
        if abs(distance) < 0.5:
            self._offset = self._target
            self._animating = False
        else:
            self._offset += distance * SCROLL_EASE
            self.after(FRAME_MS, self._animate)
        self.yview_moveto(self._offset / max(1, self.height))
        self._draw_bar()

    def _draw_bar(self):
        """macOS-style overlay scroll indicator."""
        view = self.winfo_height()
        if self.height <= view or view < 2:
            if self._bar is not None:
                self.itemconfigure(self._bar, state="hidden")
            return
        length = max(self.p.px(30), view * view / self.height)
        top = self.canvasy(0) + (view - length) * (self._offset / max(1, self._max_offset()))
        x = self.width - self.p.px(7)
        if self._bar is None:
            self._bar = self.create_line(x, top, x, top + length, width=self.p.px(5), capstyle="round",
                                         fill=self.p.c["TRACK"])
        self.coords(self._bar, x, top + self.p.px(3), x, top + length - self.p.px(3))
        self.itemconfigure(self._bar, state="normal")
        self.tag_raise(self._bar)

    # ------------------------------------------------------------ text helpers
    def text(self, x, y, text, size=13, weight="normal", color=None, wrap=0, anchor="nw", justify="left"):
        item = self.create_text(x, y, text=text, anchor=anchor, font=self.p.font(size, weight),
                                fill=color or self.p.c["TEXT"], width=wrap or 0, justify=justify)
        return item

    def text_height(self, item):
        x0, y0, x1, y1 = self.bbox(item)
        return y1 - y0


class Segmented:
    """[ a | b | c ] with the chosen segment white on a grey track."""

    def __init__(self, page, right, y, labels, command):
        p, c = page.p, page.p.c
        self.page, self.labels, self.command = page, labels, command
        self.h = p.px(30)
        font = p.font(12)
        pad = p.px(10)
        self.widths = [max(font.measure(label) + 2 * pad, p.px(40)) for label in labels]
        total = sum(self.widths) + p.px(4)
        self.x0 = right - total
        self.y = y
        self.tag = f"seg{id(self)}"
        page.create_image(self.x0, y, anchor="nw", image=p.rounded(total, self.h, p.px(7), c["TRACK"]),
                          tags=self.tag)
        self.pill = page.create_image(0, 0, anchor="nw", state="hidden", tags=self.tag)
        self.texts = []
        x = self.x0 + p.px(2)
        self.starts = []
        for label, w in zip(labels, self.widths):
            self.starts.append(x)
            self.texts.append(page.text(x + w / 2, y + self.h / 2, label, 12, anchor="center"))
            page.addtag_withtag(self.tag, self.texts[-1])
            x += w
        page.tag_bind(self.tag, "<Button-1>", self._click)
        self.index = None

    def _click(self, event):
        x = self.page.canvasx(event.x)
        for i, (start, w) in enumerate(zip(self.starts, self.widths)):
            if start <= x < start + w and i != self.index:
                self.command(self.labels[i])
                return

    def set(self, label):
        index = self.labels.index(label)
        if index == self.index:
            return
        self.index = index
        p, c = self.page.p, self.page.p.c
        w = self.widths[index]
        self.page.itemconfigure(self.pill, state="normal",
                                image=p.rounded(w - p.px(2), self.h - p.px(4), p.px(6), c["BUTTON"], c["EDGE"],
                                                          bg=c["TRACK"]))
        self.page.coords(self.pill, self.starts[index] + p.px(1), self.y + p.px(2))
        self.page.tag_raise(self.texts[index])


class OptionBox:
    """A pop-up button: white box with the value and a blue arrow box; opens a native menu."""

    def __init__(self, page, right, y, labels, command, width=110):
        p, c = page.p, page.p.c
        self.page, self.labels, self.command = page, labels, command
        self.w, self.h = p.px(width), p.px(30)
        x0 = right - self.w
        self.x0, self.y = x0, y
        self.tag = f"opt{id(self)}"
        page.create_image(x0, y, anchor="nw", image=p.rounded(self.w, self.h, p.px(8), c["BUTTON"], c["BUTTON_EDGE"]),
                          tags=self.tag)
        box = p.px(28)
        page.create_image(x0 + self.w - box - p.px(1), y + p.px(1), anchor="nw",
                          image=p.rounded(box, self.h - p.px(2), p.px(7), c["ACCENT"]), tags=self.tag)
        cx, cy, k = x0 + self.w - box / 2 - p.px(1), y + self.h / 2, p.px(4)
        page.create_line(cx - k, cy - k / 2, cx, cy + k / 2, cx + k, cy - k / 2, fill="#ffffff",
                         width=max(1, p.px(1.6)), capstyle="round", joinstyle="round", tags=self.tag)
        self.value = page.text(x0 + p.px(10), y + self.h / 2, "", 12, anchor="w")
        page.addtag_withtag(self.tag, self.value)
        page.tag_bind(self.tag, "<Button-1>", self._open)
        self.current = None

    def _open(self, event):
        c = self.page.p.c
        menu = tk.Menu(self.page, tearoff=0, font=self.page.p.font(12), bg=c["CARD"], fg=c["TEXT"],
                       activebackground=c["SELECTED"], activeforeground="#ffffff", bd=0)
        rows = 24
        for i, label in enumerate(self.labels):
            menu.add_command(label=label, command=lambda l=label: self._pick(l), columnbreak=(i > 0 and i % rows == 0))
        x = self.page.winfo_rootx() + int(self.x0)
        y = self.page.winfo_rooty() + int(self.y - self.page.canvasy(0) + self.h)
        menu.tk_popup(x, y)

    def _pick(self, label):
        if label != self.current:
            self.command(label)

    def set(self, label):
        if label != self.current:
            self.current = label
            self.page.itemconfigure(self.value, text=label)


class Spin:
    """[-] value [+] over a list of allowed values."""

    def __init__(self, page, right, y, values, command, text=str, width=64):
        p, c = page.p, page.p.c
        self.page, self.values, self.command, self.fmt = page, list(values), command, text
        self.h = p.px(30)
        bw = p.px(28)
        vw = p.px(width)
        x0 = right - (2 * bw + vw + p.px(8))
        image = p.rounded(bw, self.h, p.px(7), c["BUTTON"], c["BUTTON_EDGE"])
        for i, (x, sign, delta) in enumerate(((x0, "−", -1), (right - bw, "+", 1))):
            tag = f"spin{id(self)}_{i}"
            page.create_image(x, y, anchor="nw", image=image, tags=tag)
            page.addtag_withtag(tag, page.text(x + bw / 2, y + self.h / 2, sign, 13, anchor="center"))
            page.tag_bind(tag, "<Button-1>", lambda e, d=delta: self.step(d))
        self.label = page.text(x0 + bw + p.px(4) + vw / 2, y + self.h / 2, "", 13, "bold", anchor="center")
        self.value = None

    def set(self, value):
        if value != self.value:
            self.value = value
            self.page.itemconfigure(self.label, text=self.fmt(value))

    def step(self, delta):
        if self.value not in self.values:
            return
        i = min(len(self.values) - 1, max(0, self.values.index(self.value) + delta))
        if self.values[i] != self.value:
            self.command(self.values[i])


class Slider:
    """Track, blue progress and a round knob; dragging sends values low..high."""

    def __init__(self, page, right, y, low, high, command, text=str, width=200):
        p, c = page.p, page.p.c
        self.page, self.low, self.high, self.command, self.fmt = page, low, high, command, text
        self.h = p.px(30)
        label_w = p.px(52)
        self.x1 = right - label_w - p.px(8)
        self.x0 = self.x1 - p.px(width)
        self.cy = y + self.h / 2
        thick = p.px(4)
        self.track = page.create_line(self.x0, self.cy, self.x1, self.cy, width=thick, capstyle="round",
                                      fill=c["TRACK"])
        self.fill = page.create_line(self.x0, self.cy, self.x0, self.cy, width=thick, capstyle="round",
                                     fill=c["ACCENT"])
        self.knob_d = p.px(18)
        self.knob = page.create_image(self.x0, self.cy, anchor="center", image=p.circle(self.knob_d, c["ACCENT"]))
        self.label = page.text(right, self.cy, "", 13, "bold", anchor="e")
        self.value = None
        tag = f"sl{id(self)}"
        for item in (self.track, self.fill, self.knob):
            page.addtag_withtag(tag, item)
        page.tag_bind(tag, "<Button-1>", self._drag)
        page.tag_bind(tag, "<B1-Motion>", self._drag)

    def _drag(self, event):
        x = self.page.canvasx(event.x)
        frac = min(1.0, max(0.0, (x - self.x0) / (self.x1 - self.x0)))
        value = int(round(self.low + frac * (self.high - self.low)))
        if value != self.value:
            self.set(value)
            self.command(value)

    def set(self, value):
        if value == self.value:
            return
        self.value = value
        x = self.x0 + (self.x1 - self.x0) * (value - self.low) / (self.high - self.low)
        self.page.coords(self.fill, self.x0, self.cy, x, self.cy)
        self.page.coords(self.knob, x, self.cy)
        self.page.itemconfigure(self.label, text=self.fmt(value))


class Button:
    """A push button drawn on a page; primary = blue. state is "normal" or "disabled"."""

    def __init__(self, page, x, y, text, command, width=120, primary=False, anchor="nw"):
        p, c = page.p, page.p.c
        self.page, self.command, self.primary = page, command, primary
        self.w, self.h = p.px(width), p.px(34)
        if anchor == "ne":
            x -= self.w
        self.x, self.y = x, y
        self.tag = f"btn{id(self)}"
        self.image = page.create_image(x, y, anchor="nw", tags=self.tag)
        self.label = page.text(x + self.w / 2, y + self.h / 2, text, 13, anchor="center")
        page.addtag_withtag(self.tag, self.label)
        page.tag_bind(self.tag, "<Button-1>", self._click)
        page.tag_bind(self.tag, "<Enter>", lambda e: self._hover(True))
        page.tag_bind(self.tag, "<Leave>", lambda e: self._hover(False))
        self.state = None
        self._hovered = False
        self.configure(state="normal")

    def _look(self):
        p, c = self.page.p, self.page.p.c
        if self.primary:
            fill = c["ACCENT_HOVER"] if self._hovered and self.state == "normal" else c["ACCENT"]
            image = p.rounded(self.w, self.h, p.px(7), fill if self.state == "normal" else "#9cc8ff")
            text = "#ffffff"
        else:
            fill = c["BUTTON_HOVER"] if self._hovered and self.state == "normal" else c["BUTTON"]
            image = p.rounded(self.w, self.h, p.px(7), fill, c["BUTTON_EDGE"])
            text = c["TEXT"] if self.state == "normal" else c["DIM"]
        self.page.itemconfigure(self.image, image=image)
        self.page.itemconfigure(self.label, fill=text)

    def _hover(self, on):
        self._hovered = on
        self._look()

    def _click(self, event):
        if self.state == "normal":
            self.command()

    def configure(self, state=None, text=None):
        if text is not None:
            self.page.itemconfigure(self.label, text=text)
        if state is not None and state != self.state:
            self.state = state
            self._look()

    def cget(self, key):
        return self.state if key == "state" else self.page.itemcget(self.label, "text")


class NavList(tk.Canvas):
    """The sidebar: one canvas, a highlight pill moved to the chosen section."""

    def __init__(self, master, painter, items, command, separator_before=None, width=204):
        p, c = painter, painter.c
        super().__init__(master, bg=c["SIDEBAR"], highlightthickness=0, bd=0, width=p.px(width))
        self.p, self.command = p, command
        self.item_h, self.top, self.pad_x = p.px(34), p.px(12), p.px(12)
        self.pill_w = p.px(width) - 2 * self.pad_x
        self.hover = self.create_image(0, 0, anchor="nw", state="hidden",
                                       image=p.rounded(self.pill_w, self.item_h, p.px(7), c["SIDEBAR_HOVER"], bg=c["SIDEBAR"]))
        self.pill = self.create_image(0, 0, anchor="nw", state="hidden",
                                      image=p.rounded(self.pill_w, self.item_h, p.px(7), c["SELECTED"], bg=c["SIDEBAR"]))
        self.rows = {}
        y = self.top
        for key, label in items:
            if key == separator_before:
                y += p.px(10)
                self.create_line(self.pad_x + p.px(4), y, self.pad_x + self.pill_w - p.px(4), y, fill=c["EDGE"])
                y += p.px(10)
            text = self.create_text(self.pad_x + p.px(10), y + self.item_h / 2, text=label, anchor="w",
                                    font=p.font(13), fill=c["TEXT"])
            self.rows[key] = (y, text)
            y += self.item_h + p.px(2)
        self.footer = self.create_text(0, 0, text="", anchor="s", font=p.font(11), fill=c["MUTED"])
        self.selected = None
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self.itemconfigure(self.hover, state="hidden"))
        self.bind("<Configure>", lambda e: self.coords(self.footer, e.width / 2, e.height - p.px(14)))

    def _key_at(self, y):
        for key, (top, _) in self.rows.items():
            if top <= y < top + self.item_h:
                return key
        return None

    def _click(self, event):
        key = self._key_at(event.y)
        if key is not None:
            self.command(key)

    def _motion(self, event):
        key = self._key_at(event.y)
        if key is None or key == self.selected:
            self.itemconfigure(self.hover, state="hidden")
            return
        self.coords(self.hover, self.pad_x, self.rows[key][0])
        self.itemconfigure(self.hover, state="normal")

    def select(self, key):
        if key == self.selected:
            return
        c = self.p.c
        if self.selected is not None:
            self.itemconfigure(self.rows[self.selected][1], fill=c["TEXT"])
        self.selected = key
        top, text = self.rows[key]
        self.coords(self.pill, self.pad_x, top)
        self.itemconfigure(self.pill, state="normal")
        self.itemconfigure(self.hover, state="hidden")
        self.itemconfigure(text, fill="#ffffff")
        self.tag_raise(text)

    def set_footer(self, text):
        self.itemconfigure(self.footer, text=text)
