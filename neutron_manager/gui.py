"""Main window, with the same macOS-style look as Swing Editor and the nanoKONTROL 2 editor.

The toolbar and footer are CustomTkinter widgets; the sidebar and every page are single canvases
drawn with canvas_ui, so switching sections, scrolling and the live tuner stay within one frame
(8.3 ms at 120 Hz) on any monitor.

The Neutron stays linked while the window is open (link.py). Settings pages edit the factory firmware's
global settings; the tuner shows the readings the Neutron sends; the Firmware page backs up and
installs firmware through the ROM bootloader (dfu.py); the guide lists r15f's panel combinations.
"""
import ctypes
import math
import os
import queue
import sys
import threading
import time
import tkinter
from tkinter import filedialog, messagebox

import customtkinter as ctk

from . import __version__, canvas_ui, dfu, firmware, guide, presets, protocol
from .canvas_ui import Button, NavList, OptionBox, Page, Painter, Segmented, Slider, Spin
from .device import VerifyError
from .i18n import Translator
from .link import NeutronLink
from .midi_win import MidiError
from .protocol import ProtocolError

# macOS (light) system colours, as in Swing Editor
BG = "#ececec"
TOOLBAR = "#f6f6f6"
SIDEBAR = "#e7e7e9"
SIDEBAR_HOVER = "#dcdcdf"
CARD = "#ffffff"
BUTTON = "#ffffff"
BUTTON_HOVER = "#f6f6f6"
BUTTON_EDGE = "#cfcfcf"
TRACK = "#e3e3e3"
EDGE = "#d9d9d9"
TEXT = "#262626"
MUTED = "#808080"
DIM = "#a6a6a6"
ACCENT = "#007aff"
ACCENT_HOVER = "#1a88ff"
SELECTED = "#0063e1"
OK = "#1f9d3a"
WARN = "#c26a00"
ERROR = "#d70015"
FONT_CHOICES = ("Inter", "Segoe UI")
FONT = "Segoe UI"
COLORS = {k: v for k, v in globals().items() if k.isupper() and isinstance(v, str) and v.startswith("#")}

CONTENT_W, CONTENT_H = 700, 600
NAV_W = 204
WRITTEN_SECONDS = 2.0
POLL_MS = 8                   # how often link events are handled: one 120 Hz frame
SETTINGS_PAGES = protocol.SECTIONS
TOOL_PAGES = ("tuner", "firmware", "guide", "hardware")
PHASES = ("0°", "45°", "90°", "135°", "180°", "225°", "270°", "315°")
MARGIN = 22


class Control:
    """One setting: knows its labels, draws itself on a page, and shows panel values."""

    def __init__(self, app, param):
        self.app, self.param = app, param
        t, key = app.t, param.key
        self.widgets = []
        self.value = None
        if param.kind == "bool":
            self.kind = "segmented"
            self.labels = [t("off"), t("on")]
        elif param.kind == "choice":
            self.labels = [t(label) for label in param.labels]
            short = len(self.labels) <= 4 and sum(len(label) for label in self.labels) <= 34
            self.kind = "segmented" if short else "option"
        elif param.kind == "note":
            self.kind = "option"
            self.labels = [t("disabled") if v == 0 else protocol.note_name(v) for v in param.values]
        elif param.kind == "number":
            self.kind = "slider" if key.endswith("depth") else "spin"
        else:
            self.kind = "array"
            self.labels = list(t("shapes")) if key == "lfo_shape_order" else list(PHASES)

    def _set(self, value):
        self.app.set_value(self.param.key, value)

    def _pick(self, label):
        if self.param.kind == "bool":
            self._set(label == self.labels[1])
        else:
            self._set(self.param.values[self.labels.index(label)])

    def draw(self, page, right, y):
        """Create the control with its right edge at `right` and top at `y`; returns its height."""
        p = page.p
        if self.kind == "segmented":
            self.widgets = [Segmented(page, right, y, self.labels, self._pick)]
        elif self.kind == "option":
            width = 130 if self.param.kind == "note" else 150
            self.widgets = [OptionBox(page, right, y, self.labels, self._pick, width)]
        elif self.kind == "slider":
            self.widgets = [Slider(page, right, y, 0, 63, self._set, lambda v: f"{round(v * 100 / 63)} %")]
        elif self.kind == "spin":
            self.widgets = [Spin(page, right, y, self.param.values, self._set, number_text(self.param.key))]
        else:
            cell = p.px(96)
            gap = p.px(6)
            t = self.app.t
            for i in range(self.param.elements):
                x = page.p.px(MARGIN) + i * (cell + gap)
                caption = t("shape_slot", n=i + 1) if self.param.key == "lfo_shape_order" else t("shapes")[i]
                page.text(x + cell / 2, y, caption, 10, color=MUTED, anchor="n")
                self.widgets.append(OptionBox(page, x + cell, y + p.px(16), self.labels,
                                              lambda label, i=i: self._set_element(i, self.labels.index(label)),
                                              96))
            if self.value is not None:
                self._apply(self.value)
            return p.px(46)
        if self.value is not None:
            self._apply(self.value)
        return p.px(30)

    def _set_element(self, index, value):
        current = list(self.app.settings[self.param.key])
        current[index] = value
        self._set(tuple(current))

    def show(self, value):
        self.value = value
        if self.widgets:
            self._apply(value)

    def _apply(self, value):
        widget = self.widgets[0]
        if self.kind == "segmented":
            index = int(value) if self.param.kind == "bool" else self.param.values.index(value)
            widget.set(self.labels[index])
        elif self.kind == "option":
            widget.set(self.labels[self.param.values.index(value)])
        elif self.kind in ("slider", "spin"):
            widget.set(value)
        else:
            for menu, v in zip(self.widgets, value):
                menu.set(self.labels[v])


def number_text(key):
    if key.endswith("autoglide"):
        return lambda v: f"{v - 12:+d}" if v != 12 else "0"
    if key == "midi_channel":
        return lambda v: str(v + 1)
    return str


def nearest_note(freq):
    """(MIDI note, cents off) for a frequency, A4 = 440 Hz."""
    if freq <= 0:
        return None, 0.0
    exact = 69 + 12 * math.log2(freq / 440.0)
    note = round(exact)
    return note, (exact - note) * 100


class App(ctk.CTkFrame):
    def __init__(self, master, lang=None, link=None, autostart=True,
                 ask_yes_no=messagebox.askyesno, ask_open=filedialog.askopenfilename,
                 ask_save=filedialog.asksaveasfilename, clock=time.monotonic, bootloader_opener=dfu.open_bootloader):
        super().__init__(master, fg_color=BG, corner_radius=0)
        self.t = Translator(lang)
        self.link = link or NeutronLink()
        self.ask_yes_no, self.ask_open, self.ask_save, self.clock = ask_yes_no, ask_open, ask_save, clock
        self.bootloader_opener = bootloader_opener
        self.p = Painter(self, ctk.ScalingTracker.get_widget_scaling(self), FONT, COLORS)
        self.settings = None
        self.original = None
        self.section = SETTINGS_PAGES[0]
        self.controls = {p.key: Control(self, p) for p in protocol.PARAMS}
        self._shown = {}
        self._reading = False
        self._busy = False
        self._writing = False
        self._failed = False
        self._new_link = False
        self._outdated = None
        self._written_at = -100.0
        self._tuning = None
        self.boot = None              # open dfu.Bootloader
        self.backup = None            # firmware.Image of the full backup made in this session
        self._dfu_events = queue.Queue()
        self._dfu_busy = False

        self.master.title(self.t("title"))
        self.master.protocol("WM_DELETE_WINDOW", self.on_close)
        self.pack(fill="both", expand=True)
        self._build()
        self.refresh()
        if autostart:
            canvas_ui.enable_fine_timers()
            self.link.start()
            self.after(POLL_MS, self._poll)

    # ------------------------------------------------------------------ layout helpers
    def _button(self, parent, text, command, width=120):
        return ctk.CTkButton(parent, text=text, command=command, width=width, height=34, corner_radius=7,
                             fg_color=BUTTON, hover_color=BUTTON_HOVER, border_width=1, border_color=BUTTON_EDGE,
                             text_color=TEXT, text_color_disabled=DIM, font=(FONT, 13))

    def _show(self, key, value, apply):
        """Call apply(value) only if value changed since last time: nothing is redrawn needlessly."""
        if self._shown.get(key, object()) != value:
            self._shown[key] = value
            apply(value)

    def _page(self, build):
        return Page(self.content, self.p, CARD, build, self._page_width)

    # ------------------------------------------------------------------ build
    def _build(self):
        t = self.t
        toolbar = ctk.CTkFrame(self, fg_color=TOOLBAR, corner_radius=0)
        toolbar.pack(fill="x")
        ctk.CTkFrame(self, fg_color=EDGE, height=1, corner_radius=0).pack(fill="x")
        header = ctk.CTkFrame(toolbar, fg_color="transparent")
        header.pack(fill="x", padx=MARGIN, pady=(14, 12))
        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.pack(side="left", padx=(0, 28))
        ctk.CTkLabel(titles, text=t("heading"), font=(FONT, 22, "bold"), text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(titles, text=t("subheading"), font=(FONT, 12), text_color=MUTED).pack(anchor="w")

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.pack(side="right")
        self.action_buttons = {}
        for key, command, width in (("open", self.open_file, 80), ("save", self.save_file, 86),
                                    ("undo_all", self.undo_all, 132),
                                    ("restore_defaults", self.restore_defaults, 140)):
            button = self._button(actions, t(key), command, width)
            button.pack(side="left", padx=(0, 6))
            self.action_buttons[key] = button
        ctk.CTkFrame(actions, fg_color="transparent", width=6, height=1).pack(side="left")
        self.status_button = ctk.CTkButton(actions, text="", width=300, height=34, corner_radius=7,
                                           fg_color=BUTTON, border_width=1, border_color=BUTTON_EDGE,
                                           font=(FONT, 13, "bold"), command=self.on_status_click)
        self.status_button.pack(side="left")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=MARGIN, pady=(16, 0))
        nav_card = ctk.CTkFrame(body, fg_color=SIDEBAR, corner_radius=10)
        nav_card.pack(side="left", fill="y")
        self.nav = NavList(nav_card, self.p, [(s, t(s)) for s in SETTINGS_PAGES + TOOL_PAGES], self.show_section,
                           separator_before=TOOL_PAGES[0], width=NAV_W)
        self.nav.pack(fill="y", expand=True, padx=2, pady=8)
        self.content = ctk.CTkFrame(body, fg_color=CARD, corner_radius=10, border_width=1, border_color=EDGE,
                                    width=CONTENT_W, height=CONTENT_H)
        self.content.pack(side="left", fill="both", expand=True, padx=(14, 0))
        self.content.pack_propagate(False)
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=MARGIN, pady=(10, 12))
        self.message = ctk.CTkLabel(footer, text="", font=(FONT, 12), text_color=MUTED, anchor="w")
        self.message.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(footer, text=f"{t('disclaimer')}   v{__version__}", font=(FONT, 11),
                     text_color=DIM).pack(side="right")
        # Pages are drawn once at the card's real size. The window is not resizable, so that size follows
        # from what the toolbar, sidebar and card ask for (known before the window is shown).
        self.update_idletasks()
        inset = self.p.px(2)
        spare = self.winfo_reqwidth() - self.p.px(2 * MARGIN + 14) - nav_card.winfo_reqwidth()
        self._page_width = max(self.content.winfo_reqwidth(), spare) - 2 * inset
        self._page_height = self.content.winfo_reqheight() - 2 * inset

        self.placeholder = self._page(self._build_placeholder)
        self.pages = {s: self._page(lambda page, s=s: self._build_settings_page(page, s)) for s in SETTINGS_PAGES}
        self.pages["tuner"] = self._page(self._build_tuner_page)
        self.pages["firmware"] = self._page(self._build_firmware_page)
        self.pages["guide"] = self._page(self._build_guide_page)
        self.pages["hardware"] = self._page(self._build_hardware_page)
        # All pages are laid out once and stacked in the same place; switching only raises one.
        for page in [self.placeholder, *self.pages.values()]:
            tkinter.Place.place_configure(page, x=inset, y=inset, relwidth=1, relheight=1,
                                          width=-2 * inset, height=-2 * inset)

    def _title(self, page, text):
        page.text(page.p.px(MARGIN), page.p.px(18), text, 17, "bold")
        return page.p.px(18 + 34)

    def _build_placeholder(self, page):
        self.placeholder_text = page.text(page.width / 2, self._page_height / 2, self.t("no_neutron"), 15,
                                          color=MUTED, anchor="center", justify="center")
        return 0

    def _build_settings_page(self, page, section):
        p = page.p
        x, right = p.px(MARGIN), page.width - p.px(MARGIN + 8)
        y = self._title(page, self.t(section))
        for param in protocol.PARAMS:
            if param.section != section:
                continue
            control = self.controls[param.key]
            label = page.text(x, y, self.t.param_label(param.key), 13)
            text_h = page.text_height(label)
            help_text = self.t.param_help(param.key)
            if help_text:
                help_item = page.text(x, y + text_h + p.px(1), help_text, 11, color=MUTED, wrap=p.px(330))
                text_h += p.px(1) + page.text_height(help_item)
            if control.kind == "array":
                y += text_h + p.px(8)
                y += control.draw(page, right, y) + p.px(14)
            else:
                ctrl_h = p.px(30)
                top = y + max(0, (text_h - ctrl_h) / 2)
                control.draw(page, right, top)
                y += max(text_h, ctrl_h) + p.px(14)
        action = {"lfo": "reset_lfo_order", "keyboard": "reset_key_range"}.get(section)
        if action:
            Button(page, right, y, self.t("reset"), lambda: self.link.request_action(action), 110, anchor="ne")
            y += p.px(34 + 12)
        return y + p.px(8)

    def _build_tuner_page(self, page):
        p, t = page.p, self.t
        x = p.px(MARGIN)
        y = self._title(page, t("tuner"))
        hint = page.text(x, y, t("tuner_hint"), 12, color=MUTED, wrap=page.width - 2 * x)
        y += page.text_height(hint) + p.px(20)
        gap = p.px(10)
        card_w = int((page.width - 2 * x - gap) / 2)
        card_h = p.px(186)
        self.tuner = []
        for i, name in enumerate(("OSC 1", "OSC 2")):
            cx = x + i * (card_w + gap)
            page.create_image(cx, y, anchor="nw", image=p.rounded(card_w, card_h, p.px(10), BG))
            mid = cx + card_w / 2
            page.text(mid, y + p.px(14), name, 13, "bold", color=MUTED, anchor="n")
            note = page.text(mid, y + p.px(36), "—", 44, "bold", anchor="n")
            cents = page.text(mid, y + p.px(98), "", 15, color=MUTED, anchor="n")
            bar_w = p.px(220)
            by = y + p.px(134)
            page.create_line(mid - bar_w / 2, by, mid + bar_w / 2, by, width=p.px(8), capstyle="round", fill=TRACK)
            page.create_line(mid, by - p.px(7), mid, by + p.px(7), width=max(1, p.px(1)), fill=DIM)
            marker = page.create_line(mid, by, mid, by, width=p.px(8), capstyle="round", fill=OK, state="hidden")
            hz = page.text(mid, y + p.px(150), "", 12, color=MUTED, anchor="n")
            self.tuner.append({"note": note, "cents": cents, "marker": marker, "hz": hz, "mid": mid, "by": by,
                               "half": bar_w / 2, "page": page})
        self.tuner_midi = page.text(x, y + card_h + p.px(16), t("tuner_waiting"), 12, color=MUTED)
        return y + card_h + p.px(50)

    def _build_firmware_page(self, page):
        p, t = page.p, self.t
        x = p.px(MARGIN)
        wrap = page.width - 2 * x
        y = self._title(page, t("firmware"))
        for text, color in ((t("fw_intro"), TEXT), (t("fw_steps"), MUTED)):
            item = page.text(x, y, text, 12, color=color, wrap=wrap)
            y += page.text_height(item) + p.px(10)
        y += p.px(4)
        self.fw_buttons = {}
        bx = x
        for key, command, width, primary in (("fw_find", self.fw_find, 150, True),
                                             ("fw_backup", self.fw_backup, 140, False),
                                             ("fw_install", self.fw_install, 150, False),
                                             ("fw_restore", self.fw_restore, 136, False),
                                             ("fw_start", self.fw_start, 136, False)):
            button = Button(page, bx, y, t(key), command, width, primary)
            self.fw_buttons[key] = button
            bx += button.w + p.px(6)
        y += p.px(34 + 18)
        self.fw_bar = (page, x, page.width - x, y)
        page.create_line(x, y, page.width - x, y, width=p.px(6), capstyle="round", fill=TRACK)
        self.fw_bar_fill = page.create_line(x, y, x, y, width=p.px(6), capstyle="round", fill=ACCENT, state="hidden")
        y += p.px(16)
        self.fw_status_item = page.text(x, y, t("fw_none"), 12, color=MUTED, wrap=wrap)
        return y + p.px(120)

    def _build_guide_page(self, page):
        p = page.p
        x = p.px(MARGIN)
        y = self._title(page, self.t("guide"))
        for group, rows in guide.GUIDE[self.t.lang]:
            y += p.px(6)
            item = page.text(x, y, group, 14, "bold")
            y += page.text_height(item) + p.px(6)
            for combo, effect in rows:
                a = page.text(x, y, combo, 12, "bold", color=ACCENT, wrap=p.px(225))
                b = page.text(x + p.px(240), y, effect, 12, wrap=page.width - x - p.px(240) - p.px(MARGIN))
                y += max(page.text_height(a), page.text_height(b)) + p.px(8)
        return y + p.px(10)

    def _build_hardware_page(self, page):
        p = page.p
        x = p.px(MARGIN)
        y = self._title(page, self.t("hardware"))
        for title, text in (HARDWARE_ES if self.t.lang == "es" else HARDWARE_EN):
            item = page.text(x, y, title, 14, "bold")
            y += page.text_height(item) + p.px(3)
            item = page.text(x, y, text, 12, wrap=page.width - 2 * x)
            y += page.text_height(item) + p.px(14)
        return y

    # ------------------------------------------------------------------ actions
    def show_section(self, section):
        self.section = section
        self.refresh()

    def set_value(self, key, value):
        if self.settings is None:
            return
        try:
            new = self.settings.set(key, value)
        except ProtocolError as e:
            self.show(self.t("error", err=e), ERROR)
            self._shown.pop(("value", key), None)
            self.refresh()
            return
        if new != self.settings:
            self.settings = new
            self._failed = False
            self.link.push(new)
        self.refresh()

    def refresh(self):
        self._show("nav", self.section, self.nav.select)
        if self.section in TOOL_PAGES or self.settings is not None:
            page = self.pages[self.section]
        else:
            page = self.placeholder
            text = self.t("outdated_page", v=_version(self._outdated)) if self._outdated else self.t("no_neutron")
            self._show("placeholder", text,
                       lambda s: self.placeholder.itemconfigure(self.placeholder_text, text=s))
        self._show("page", page, tkinter.Misc.tkraise)   # Canvas.tkraise raises items, not the widget
        if self.settings is not None:
            for key, control in self.controls.items():
                self._show(("value", key), self.settings[key], control.show)
        state = "normal" if self.settings is not None else "disabled"
        self._show("save_state", state, lambda s: self.action_buttons["save"].configure(state=s))
        editable = "normal" if self.settings is not None and self.link.editable else "disabled"
        self._show("defaults_state", editable,
                   lambda s: self.action_buttons["restore_defaults"].configure(state=s))
        undo = "normal" if self.original is not None and self.link.editable else "disabled"
        self._show("undo_state", undo, lambda s: self.action_buttons["undo_all"].configure(state=s))
        self._refresh_firmware_buttons()
        self._draw_status()

    def _refresh_firmware_buttons(self):
        have = self.boot is not None and not self._dfu_busy
        states = {"fw_find": "disabled" if self._dfu_busy else "normal",
                  "fw_backup": "normal" if have else "disabled",
                  "fw_install": "normal" if have and self.backup is not None else "disabled",
                  "fw_restore": "normal" if have and self.backup is not None else "disabled",
                  "fw_start": "normal" if have else "disabled"}
        for key, state in states.items():
            self._show(("fw", key), state, lambda s, k=key: self.fw_buttons[k].configure(state=s))

    def show(self, text, color=MUTED):
        self._show("message", (text, color), lambda m: self.message.configure(text=m[0], text_color=m[1]))

    def _fw_status(self, text, color):
        page = self.pages["firmware"]
        page.itemconfigure(self.fw_status_item, text=text, fill=color)

    def _fw_progress(self, fraction):
        page, x0, x1, y = self.fw_bar
        if fraction <= 0:
            page.itemconfigure(self.fw_bar_fill, state="hidden")
            return
        page.coords(self.fw_bar_fill, x0, y, x0 + (x1 - x0) * min(1.0, fraction), y)
        page.itemconfigure(self.fw_bar_fill, state="normal")

    # ------------------------------------------------------------------ link events
    def _poll(self):
        self.process_link_events()
        self.process_dfu_events()
        self.after(POLL_MS, self._poll)

    def process_link_events(self):
        if self.link.events.empty():
            self._draw_status()   # "changes applied" turns back to "linked" after a while
            return
        tuning = None
        while not self.link.events.empty():
            kind, payload = self.link.events.get_nowait()
            if kind == "linked":
                self._reading, self._new_link, self._busy, self._outdated = True, True, False, None
                self.show(self.t("reading"))
            elif kind == "info":
                self.nav.set_footer(self.t("firmware_label", v=_version(payload[1])))
            elif kind == "outdated":
                self._reading, self._outdated = False, payload
                self.settings = None
            elif kind == "settings":
                was_reading, self._reading = self._reading, False
                if self._new_link:
                    self._new_link = False
                    self.original = payload
                if self.settings is None or not self.link.has_pending:
                    self.settings = payload
                else:
                    pending_keys = self.settings.differences(self.original)
                    self.settings = payload.with_values_from(self.settings, pending_keys)
                if was_reading and not self._failed:
                    self.show(self.t("read_ok"), OK)
            elif kind == "writing":
                self._writing = True
            elif kind == "written":
                self._writing, self._failed = False, False
                self._written_at = self.clock()
                if not self.link.has_pending:
                    self.settings = payload
            elif kind == "write_failed":
                self._writing, self._failed = False, True
                self.show(self.t("verify_error", err=payload) if isinstance(payload, VerifyError)
                          else self.t("write_error", err=payload), ERROR)
            elif kind == "searching":
                self._reading = self._writing = False
                self.nav.set_footer("")
                busy = isinstance(payload, MidiError) and "open" in str(payload).lower()
                if busy and not self._busy:
                    self.show(self.t("port_busy"), ERROR)
                elif not busy and self._busy:
                    self.show("")
                self._busy = busy
            elif kind == "tuning":
                tuning = payload
        if tuning is not None:
            self._show_tuning(tuning)
        self.refresh()

    def _show_tuning(self, tuning):
        """Only the changed canvas items are touched; a reading costs well under a millisecond."""
        self._tuning = tuning
        for i, (card, freq) in enumerate(zip(self.tuner, (tuning.osc1_hz, tuning.osc2_hz))):
            page = card["page"]
            midi, off = nearest_note(freq)
            if midi is None or not 0 <= midi <= 127:
                self._show(("tuner", i), None, lambda _: (
                    page.itemconfigure(card["note"], text="—", fill=TEXT),
                    page.itemconfigure(card["cents"], text=""), page.itemconfigure(card["hz"], text=""),
                    page.itemconfigure(card["marker"], state="hidden")))
                continue
            color = OK if abs(off) < 5 else WARN if abs(off) < 20 else ERROR
            cents = round(off)
            self._show(("tuner", i), (midi, cents, color), lambda v: (
                page.itemconfigure(card["note"], text=protocol.note_name(v[0]), fill=v[2]),
                page.itemconfigure(card["cents"], text=self.t("cents", c=v[1]), fill=v[2]),
                page.itemconfigure(card["marker"], fill=v[2], state="normal"),
                page.coords(card["marker"], *self._marker(card, v[1]))))
            self._show(("tuner_hz", i), f"{freq:.2f} Hz", lambda s: page.itemconfigure(card["hz"], text=s))
        self._show("tuner_midi", tuning.note, lambda n: self.tuner[0]["page"].itemconfigure(
            self.tuner_midi, text=f"{self.t('tuner_note')}: {protocol.note_name(n)} ({n})"))

    @staticmethod
    def _marker(card, cents):
        x = card["mid"] + card["half"] * max(-50, min(50, cents)) / 50
        return x, card["by"], x + 0.01, card["by"]

    def tuner_note_text(self, index):
        card = self.tuner[index]
        return card["page"].itemcget(card["note"], "text")

    def _status(self):
        if not self.link.linked:
            if self._busy:
                return "status_busy", ERROR
            return "status_searching", MUTED
        if self._outdated:
            return "status_outdated", ERROR
        if self._reading:
            return "status_reading", WARN
        if self._failed:
            return "status_failed", ERROR
        if self._writing or self.link.has_pending:
            return "status_writing", WARN
        if self.clock() - self._written_at < WRITTEN_SECONDS:
            return "status_written", OK
        return "status_linked", OK

    def _draw_status(self):
        key, fg = self._status()
        text = self.t(key, v=_version(self._outdated)) if key == "status_outdated" else self.t(key)
        self._show("status", text, lambda _: self.status_button.configure(
            text=text, fg_color=BUTTON, hover_color=BUTTON, text_color=fg))

    def on_status_click(self):
        if not self.link.editable:
            return
        if self._failed and self.settings is not None:
            self._failed = False
            self.link.push(self.settings)
            self.link.flush()
        else:
            self.link.request_read()
            self._reading = True
        self.refresh()

    # ------------------------------------------------------------------ files and undo
    def open_file(self):
        path = self.ask_open(filetypes=[(self.t("file_types_json"), "*.json")])
        if not path:
            return
        try:
            loaded = presets.load(path)
        except (ProtocolError, OSError) as e:
            self.show(self.t("error", err=e), ERROR)
            return
        self.settings = loaded
        self.link.push(self.settings)
        self.show(self.t("opened", path=path), OK)
        self.refresh()

    def save_file(self):
        if self.settings is None:
            return
        path = self.ask_save(filetypes=[(self.t("file_types_json"), "*.json")], defaultextension=".json")
        if not path:
            return
        try:
            presets.save(path, self.settings)
            self.show(self.t("saved", path=path), OK)
        except (ProtocolError, OSError) as e:
            self.show(self.t("error", err=e), ERROR)

    def undo_all(self):
        if self.original is None or self.settings is None:
            return
        if not self.ask_yes_no(self.t("undo_title"), self.t("undo_confirm")):
            return
        self.settings = self.original
        self.link.push(self.settings)
        self.show(self.t("undo_done"))
        self.refresh()

    def restore_defaults(self):
        if self.settings is None or not self.ask_yes_no(self.t("restore_defaults"), self.t("restore_confirm")):
            return
        self.link.request_action("restore_defaults")

    # ------------------------------------------------------------------ firmware (DFU), on a worker thread
    def _dfu_job(self, job):
        """Run job(progress) on a thread; it returns the text to show. Errors are shown in red."""
        if self._dfu_busy:
            return
        self._dfu_busy = True
        self._fw_progress(0)
        self.refresh()

        def progress(done, total):
            self._dfu_events.put(("progress", done / total if total else 0))

        def run():
            try:
                self._dfu_events.put(("done", job(progress)))
            except (dfu.DfuError, firmware.FirmwareError, OSError) as e:
                self._dfu_events.put(("failed", e))
            except Exception as e:   # libusb errors and anything unexpected: never leave the window stuck
                self._dfu_events.put(("failed", e))
        threading.Thread(target=run, daemon=True).start()

    def process_dfu_events(self):
        while not self._dfu_events.empty():
            kind, payload = self._dfu_events.get_nowait()
            if kind == "progress":
                self._fw_progress(payload)
            elif kind == "done":
                self._dfu_busy = False
                self._fw_status(payload, OK)
            elif kind == "failed":
                self._dfu_busy = False
                self._fw_status(self.t("error", err=payload), ERROR)
        self._refresh_firmware_buttons()

    def fw_find(self):
        def job(progress):
            try:
                self.boot = self.bootloader_opener()
            except dfu.DfuError as e:
                self.boot = None
                if "no Neutron" in str(e):
                    return self.t("fw_none")
                raise dfu.DfuError(self.t("fw_driver", err=e)) from e
            return self.t("fw_found")
        self._dfu_job(job)

    def fw_backup(self):
        if self.boot is None:
            return
        path = self.ask_save(filetypes=[(self.t("file_types_backup"), "*.bin")], defaultextension=".bin",
                             initialfile=f"neutron_backup_{time.strftime('%Y%m%d_%H%M%S')}_08000000.bin")
        if not path:
            return

        def job(progress):
            image = self.boot.backup(progress)
            with open(path, "wb") as f:
                f.write(image.data)
            self.backup = image
            return self.t("fw_backup_done", path=path, sha=image.sha256)
        self._dfu_job(job)

    def fw_install(self):
        if self.boot is None or self.backup is None:
            self._fw_status(self.t("fw_need_backup"), WARN)
            return
        path = self.ask_open(filetypes=[(self.t("file_types_fw"), "*.bin *.hex *.dfu")])
        if not path:
            return
        try:
            image = firmware.load(path)
        except firmware.FirmwareError as e:
            self._fw_status(self.t("error", err=e), ERROR)
            return
        if not self.ask_yes_no(self.t("fw_install"), self.t(
                "fw_install_confirm", pages=len(image.pages()), start=image.address, end=image.end - 1,
                path=path, sha=image.sha256)):
            return
        self._dfu_job(lambda progress: (self.boot.flash(image, self.backup, progress), self.t("fw_done"))[1])

    def fw_restore(self):
        if self.boot is None or self.backup is None:
            self._fw_status(self.t("fw_need_backup"), WARN)
            return
        path = self.ask_open(filetypes=[(self.t("file_types_backup"), "*.bin")])
        if not path:
            return
        try:
            image = firmware.load(path)
        except firmware.FirmwareError as e:
            self._fw_status(self.t("error", err=e), ERROR)
            return
        if len(image.data) != firmware.FLASH_SIZE:
            self._fw_status(self.t("error", err="not a full 128 KB backup"), ERROR)
            return
        if not self.ask_yes_no(self.t("fw_restore"), self.t("fw_restore_confirm", path=path)):
            return
        self._dfu_job(lambda progress: (self.boot.flash(image, self.backup, progress), self.t("fw_done"))[1])

    def fw_start(self):
        if self.boot is None:
            return

        def job(progress):
            self.boot.leave()
            self.boot = None
            return self.t("fw_none")
        self._dfu_job(job)

    def on_close(self):
        self.link.stop()
        canvas_ui.disable_fine_timers()
        self.master.destroy()


def _version(version):
    return ".".join(map(str, version)) if version else ""


HARDWARE_ES = (
    ("Cerebro digital", "STM32F072 (Cortex-M0, 48 MHz, 128 KB de flash, 16 KB de RAM). Dirige un synth analógico: "
                        "no genera el sonido principal."),
    ("Qué controla", "Un DAC de 12 bits multiplexado a 8 tensiones (afinación fina y gruesa de los dos osciladores, "
                     "corte del filtro, salida Assign y formas de onda), un DAC directo para el LFO a 48 kHz, "
                     "5 potenciómetros (formas de osc y LFO, velocidad del LFO, glide), botones y LEDs."),
    ("Límites físicos", "Máximo 2 notas (2 VCO, 1 VCF). Los mandos del filtro, envolventes y mezclador son analógicos "
                        "y el chip no los lee: no se pueden guardar patches completos ni enviarlos por SysEx."),
    ("Lo que sí se puede exprimir", "La salida del LFO es un canal de audio de 48 kHz: tercer oscilador, wavetables, "
                                    "modos polifónicos digitales (como hace r15f)."),
    ("Firmware r15f", "Sin USB-MIDI ni SysEx: con r15f instalado, el Neutron solo se controla por MIDI DIN y desde el "
                      "panel. Para volver, usa la copia completa de la pestaña Firmware."),
)
HARDWARE_EN = (
    ("Digital brain", "STM32F072 (Cortex-M0, 48 MHz, 128 KB flash, 16 KB RAM). It drives an analog synth: it does "
                      "not make the main sound."),
    ("What it controls", "One 12-bit DAC multiplexed to 8 voltages (fine and coarse pitch of both oscillators, "
                         "filter cutoff, Assign out and wave shapes), a direct DAC for the LFO at 48 kHz, 5 pots "
                         "(osc and LFO shapes, LFO rate, glide), buttons and LEDs."),
    ("Physical limits", "At most 2 notes (2 VCOs, 1 VCF). Filter, envelope and mixer knobs are analog and not read "
                        "by the chip: full patches cannot be stored or sent over SysEx."),
    ("What can be pushed", "The LFO output is a 48 kHz audio channel: a third oscillator, wavetables, digital "
                           "polyphonic modes (as r15f does)."),
    ("r15f firmware", "No USB-MIDI and no SysEx: with r15f installed, the Neutron is controlled only over DIN MIDI "
                      "and from the panel. To go back, use the full backup from the Firmware tab."),
)


def _asset(*parts):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "assets", *parts)


def _load_fonts():
    """Use the Inter fonts in assets/fonts for this process only, without installing them."""
    global FONT
    folder = _asset("fonts")
    if sys.platform == "win32" and os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            if name.lower().endswith(".ttf"):
                try:
                    ctypes.windll.gdi32.AddFontResourceExW(os.path.join(folder, name), 0x10, 0)   # FR_PRIVATE
                except (AttributeError, OSError):
                    pass
    import tkinter.font
    families = set(tkinter.font.families())
    FONT = next((f for f in FONT_CHOICES if f in families), FONT)


def main(lang=None):
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk(fg_color=BG)
    _load_fonts()
    root.resizable(False, False)
    try:
        root.iconbitmap(_asset("icon.ico"))
    except Exception:
        pass
    App(root, lang=lang)
    root.mainloop()


if __name__ == "__main__":
    main()
