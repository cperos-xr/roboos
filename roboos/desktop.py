"""RoboOS: a tiny Windows-95-style desktop for the CYD touchscreen, drawn on the Linux board it is plugged into.

RoboOS is its own package (roboos/): it never imports the robot (robotd). Everything it needs from the machine comes
through a Host (roboos/host.py); robotd gives it a RobotHost, `python -m roboos` runs it on any board with a CYD.

How it works
- The Pi renders the whole desktop with PIL (320x220, the area under the screen's top bar) and sends only the part
  that changed as a small JPEG (slot "desk"). The mouse pointer is drawn into the picture, so moving it costs a few
  hundred bytes per update.
- The screen forwards touches on its Desktop page as {"t":"dt","e":"d|m|u","x":..,"y":..}. A USB mouse and keyboard
  plugged into the Pi are read straight from /dev/input (no extra libraries).
- Apps are small classes (see APPS at the bottom). To add a built-in one: subclass App, give it name/title/icon,
  draw() and the input methods you need, then add it to APPS. Apps from other people install into data/apps/<id>/
  (roboos/store.py, the Store app). Either way it shows up in the Start menu automatically.
"""
import io
import math
import os
import pty
import queue
import re
import select
import shutil
import struct
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import VERSION
from .paths import DATA, PHOTOS, ROOT

W, H = 320, 220            # desktop area on the screen (below the screen's own 20 px bar)
SCREEN_Y = 20
TASK_H = 22
TITLE_H = 16

# Windows 95 palette
TEAL = (0, 128, 128)
FACE = (192, 192, 192)
LIGHT = (255, 255, 255)
SHADOW = (128, 128, 128)
DARK = (0, 0, 0)
NAVY = (0, 0, 128)
TITLE2 = (16, 132, 208)
INACTIVE = (128, 128, 128)
SELECT = (0, 0, 128)
YELLOW = (255, 214, 64)
RED = (200, 30, 30)
GREEN = (20, 160, 60)


def _font(name, size):
    for base in ("/usr/share/fonts/truetype/dejavu/",):
        try:
            return ImageFont.truetype(base + name, size)
        except OSError:
            pass
    return ImageFont.load_default()


F_UI = _font("DejaVuSans.ttf", 10)
F_BOLD = _font("DejaVuSans-Bold.ttf", 10)
F_BIG = _font("DejaVuSans-Bold.ttf", 13)
F_MONO = _font("DejaVuSansMono.ttf", 10)
MONO_W = max(1, int(round(F_MONO.getlength("M"))))
MONO_H = 12


# ---------------------------------------------------------------- drawing helpers
def bevel(d, box, raised=True, fill=FACE):
    x0, y0, x1, y1 = box
    d.rectangle(box, fill=fill)
    hi, lo = (LIGHT, DARK) if raised else (DARK, LIGHT)
    d.line([(x0, y1), (x0, y0), (x1, y0)], fill=hi)
    d.line([(x0, y1), (x1, y1), (x1, y0)], fill=lo)
    if x1 - x0 > 4 and y1 - y0 > 4:
        d.line([(x0 + 1, y1 - 1), (x1 - 1, y1 - 1), (x1 - 1, y0 + 1)], fill=SHADOW if raised else FACE)


def button(d, box, label, pressed=False, font=F_UI, fill=FACE, color=DARK):
    bevel(d, box, not pressed, fill)
    x0, y0, x1, y1 = box
    tw = d.textlength(label, font=font)
    d.text(((x0 + x1 - tw) / 2 + (1 if pressed else 0), (y0 + y1) / 2 - 6 + (1 if pressed else 0)), label, font=font, fill=color)


def inside(box, x, y):
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def fit_text(d, text, width, font=F_UI):
    if d.textlength(text, font=font) <= width:
        return text
    while text and d.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


_DITHER = None


def _dither():
    global _DITHER
    if _DITHER is None:
        a = np.zeros((H, 16, 3), np.uint8)
        a[:, :] = FACE
        a[0::2, 0::2] = LIGHT
        a[1::2, 1::2] = LIGHT
        _DITHER = Image.fromarray(a)
    return _DITHER


SBW = 20          # scrollbar width: wide on purpose, easy to hit with a finger on a resistive screen


def scrollbar(d, box, pos, total, view):
    """Classic scrollbar. Returns (up_box, down_box, thumb_box or None) for hit testing."""
    x0, y0, x1, y1 = box
    a = x1 - x0                                     # square arrow buttons
    up, down = (x0, y0, x1, y0 + a), (x0, y1 - a, x1, y1)
    t0, t1 = y0 + a + 1, y1 - a - 1
    if t1 > t0:
        d._image.paste(_dither().crop((0, 0, a + 1, t1 - t0 + 1)), (x0, t0))   # ImageDraw keeps its image in _image
    bevel(d, up); bevel(d, down)
    cx, m = (x0 + x1) // 2, a // 2
    d.polygon([(cx - 5, y0 + m + 3), (cx + 5, y0 + m + 3), (cx, y0 + m - 3)], fill=DARK)
    d.polygon([(cx - 5, y1 - m - 3), (cx + 5, y1 - m - 3), (cx, y1 - m + 3)], fill=DARK)
    thumb = None
    track = t1 - t0
    if total > view and track > 12:
        th = max(14, int(track * view / total))
        ty = t0 + int((track - th) * pos / max(1, total - view))
        thumb = (x0, ty, x1, ty + th)
        bevel(d, thumb)
        for k in (-3, 0, 3):                        # grip lines
            gy = ty + th // 2 + k
            d.line([(x0 + 5, gy), (x1 - 5, gy)], fill=SHADOW)
            d.line([(x0 + 5, gy + 1), (x1 - 5, gy + 1)], fill=LIGHT)
    return up, down, thumb


class Scroll:
    """A scrollbar that remembers where it was drawn. Tap arrows/track, drag the thumb, or hold an arrow to repeat."""

    def __init__(self, step=2):
        self.box, self.thumb, self.up, self.down = None, None, None, None
        self.pos = self.total = 0
        self.view, self.step = 1, step
        self._grab = None

    def draw(self, d, box, pos, total, view):
        self.box, self.pos, self.total, self.view = box, pos, total, view
        self.up, self.down, self.thumb = scrollbar(d, box, pos, total, view)

    def hit(self, x, y):
        return self.box is not None and inside(self.box, x, y)

    def clamp(self, v):
        return max(0, min(int(v), max(0, self.total - self.view)))

    def click(self, x, y):
        """New position for a tap on the bar."""
        if inside(self.up, x, y):
            return self.clamp(self.pos - self.step)
        if inside(self.down, x, y):
            return self.clamp(self.pos + self.step)
        if self.thumb and y < self.thumb[1]:
            return self.clamp(self.pos - max(1, self.view - 1))
        if self.thumb and y > self.thumb[3]:
            return self.clamp(self.pos + max(1, self.view - 1))
        return self.pos

    def on_arrow(self, x, y):
        return inside(self.up, x, y) or inside(self.down, x, y)

    def grab(self, x, y):
        """Start dragging if the press is on the thumb; returns True if grabbed."""
        self._grab = (y, self.pos) if self.thumb and inside(self.thumb, x, y) else None
        return self._grab is not None

    def drag(self, y):
        y0, p0 = self._grab
        track = (self.box[3] - self.box[1]) - 2 * (self.box[2] - self.box[0]) - 2
        th = self.thumb[3] - self.thumb[1]
        return self.clamp(p0 + (y - y0) * max(1, self.total - self.view) / max(1, track - th))

    def release(self):
        self._grab = None


ICON_IMAGES = {}                  # icon name -> RGBA picture (installed apps bring their own icon.png: roboos/store.py)
_icon_cache = {}


def draw_icon(d, kind, x, y, s=24, off=False):
    """Little pixel-art icons drawn with primitives (s = 24 or 16), or an installed app's own picture."""
    k = s / 24.0
    P = lambda a, b: (x + a * k, y + b * k)
    R = lambda a, b, c, e: (x + a * k, y + b * k, x + c * k, y + e * k)
    pic = ICON_IMAGES.get(kind)
    if pic is not None and getattr(d, "_image", None) is not None:
        key = (kind, s, id(pic))
        if key not in _icon_cache:
            _icon_cache[key] = pic.resize((s, s), Image.LANCZOS if pic.width > s else Image.NEAREST)
        im = _icon_cache[key]
        d._image.paste(im, (int(x), int(y)), im)
        return
    if kind == "store":                                    # a shopping bag
        d.rectangle(R(3, 8, 21, 22), fill=(40, 120, 220), outline=DARK)
        d.arc(R(7, 2, 17, 14), 180, 360, fill=DARK, width=max(1, int(2 * k)))
        d.polygon([P(12, 11), P(14, 15), P(18, 15), P(15, 18), P(16, 21), P(12, 19), P(8, 21), P(9, 18), P(6, 15),
                   P(10, 15)], fill=YELLOW)
        return
    if kind == "app":                                      # an installed app without its own icon: a window
        d.rectangle(R(2, 3, 22, 21), fill=LIGHT, outline=DARK)
        d.rectangle(R(2, 3, 22, 7), fill=NAVY)
        d.rectangle(R(6, 10, 18, 17), fill=(120, 200, 120), outline=SHADOW)
        return
    if kind == "terminal":
        d.rectangle(R(1, 3, 23, 21), fill=DARK, outline=SHADOW)
        d.rectangle(R(1, 3, 23, 6), fill=NAVY)
        d.text(P(4, 8), ">_", font=F_MONO if s >= 20 else F_UI, fill=(0, 255, 0))
    elif kind == "files":
        d.polygon([P(1, 6), P(9, 6), P(11, 8), P(23, 8), P(23, 21), P(1, 21)], fill=YELLOW, outline=(150, 110, 0))
        d.rectangle(R(1, 10, 23, 21), fill=(255, 230, 120), outline=(150, 110, 0))
    elif kind == "monitor":
        d.rectangle(R(2, 2, 22, 17), fill=DARK, outline=SHADOW)
        d.line([P(4, 14), P(8, 9), P(12, 12), P(16, 5), P(20, 8)], fill=(0, 255, 0), width=max(1, int(k * 1.5)))
        d.rectangle(R(9, 18, 15, 20), fill=SHADOW); d.rectangle(R(5, 20, 19, 22), fill=SHADOW)
    elif kind == "camera":
        d.rounded_rectangle(R(1, 7, 23, 20), radius=max(1, int(3 * k)), fill=(60, 60, 60), outline=DARK)
        d.rectangle(R(8, 4, 15, 7), fill=(60, 60, 60))
        d.ellipse(R(7, 9, 17, 19), fill=(150, 200, 255), outline=DARK)
        if off:
            d.line([P(2, 22), P(22, 3)], fill=RED, width=max(2, int(3 * k)))
    elif kind == "settings":                    # a gear
        cx, cy, w = 12, 12, max(1, int(3 * k))
        for a in range(0, 360, 45):
            ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
            d.line([P(cx + 6 * ca, cy + 6 * sa), P(cx + 10.5 * ca, cy + 10.5 * sa)], fill=(90, 90, 100), width=w)
        d.ellipse(R(5, 5, 19, 19), fill=(150, 150, 165), outline=(60, 60, 70))
        d.ellipse(R(9, 9, 15, 15), fill=FACE, outline=(60, 60, 70))
    elif kind == "robot":
        d.rounded_rectangle(R(3, 5, 21, 20), radius=max(1, int(4 * k)), fill=(90, 160, 220), outline=NAVY)
        d.ellipse(R(6, 9, 10, 13), fill=LIGHT); d.ellipse(R(14, 9, 18, 13), fill=LIGHT)
        d.line([P(8, 16), P(16, 16)], fill=NAVY, width=max(1, int(k * 1.5)))
        d.line([P(12, 1), P(12, 5)], fill=NAVY); d.ellipse(R(10, 0, 14, 3), fill=RED)
    elif kind == "logs":
        d.rectangle(R(4, 1, 20, 23), fill=LIGHT, outline=DARK)
        for i in range(5):
            d.line([P(7, 5 + i * 4), P(17 - (i % 2) * 3, 5 + i * 4)], fill=SHADOW)
    elif kind == "paint":
        d.ellipse(R(1, 3, 23, 21), fill=(230, 200, 150), outline=(120, 80, 30))
        for (a, b, c) in ((6, 8, RED), (12, 6, (40, 80, 220)), (17, 10, GREEN), (7, 14, YELLOW)):
            d.ellipse(R(a - 2, b - 2, a + 2, b + 2), fill=c)
        d.ellipse(R(13, 14, 18, 19), fill=TEAL)
    elif kind == "power":
        d.ellipse(R(3, 3, 21, 21), outline=RED, width=max(2, int(3 * k)))
        d.rectangle(R(10, 1, 14, 12), fill=FACE)
        d.line([P(12, 1), P(12, 11)], fill=RED, width=max(2, int(3 * k)))
    elif kind == "about":
        d.ellipse(R(2, 2, 22, 22), fill=NAVY)
        d.text(P(10, 4), "i", font=F_BIG if s >= 20 else F_BOLD, fill=LIGHT)
    elif kind == "keyboard":
        d.rectangle(R(1, 7, 23, 18), fill=LIGHT, outline=DARK)
        for r in range(2):
            for c in range(5):
                d.rectangle(R(3 + c * 4, 9 + r * 4, 5 + c * 4, 11 + r * 4), fill=SHADOW)
    elif kind == "mic":
        d.rounded_rectangle(R(8, 1, 16, 14), radius=max(1, int(4 * k)), fill=(90, 90, 90), outline=DARK)
        d.arc(R(5, 6, 19, 18), 0, 180, fill=DARK, width=max(1, int(2 * k)))
        d.line([P(12, 18), P(12, 22)], fill=DARK, width=max(1, int(2 * k))); d.line([P(7, 22), P(17, 22)], fill=DARK, width=max(1, int(2 * k)))
        d.ellipse(R(17, 1, 22, 6), fill=RED)
    elif kind == "music":
        c = (60, 120, 255)                                     # bright enough to show on the navy title bar too
        d.line([P(9, 4), P(9, 18)], fill=c, width=max(1, int(2 * k))); d.line([P(19, 2), P(19, 16)], fill=c, width=max(1, int(2 * k)))
        d.polygon([P(9, 4), P(19, 2), P(19, 6), P(9, 8)], fill=c)
        d.ellipse(R(3, 15, 10, 21), fill=c); d.ellipse(R(13, 13, 20, 19), fill=c)
    elif kind == "notepad":
        d.rectangle(R(4, 2, 20, 23), fill=LIGHT, outline=DARK)
        d.rectangle(R(4, 2, 20, 5), fill=(120, 170, 230))
        for i in range(4):
            d.line([P(7, 9 + i * 4), P(17, 9 + i * 4)], fill=(120, 120, 200))
    elif kind == "web":
        d.ellipse(R(2, 2, 22, 22), fill=(80, 150, 230), outline=NAVY)
        d.ellipse(R(8, 2, 16, 22), outline=LIGHT); d.line([P(2, 12), P(22, 12)], fill=LIGHT)
        d.arc(R(3, 4, 21, 12), 200, 340, fill=LIGHT); d.arc(R(3, 12, 21, 20), 20, 160, fill=LIGHT)
    elif kind == "tube":
        d.rounded_rectangle(R(1, 5, 23, 19), radius=max(1, int(5 * k)), fill=(230, 30, 30))
        d.polygon([P(10, 8), P(10, 16), P(16, 12)], fill=LIGHT)
    elif kind == "mines":
        d.rectangle(R(1, 1, 23, 23), fill=FACE, outline=SHADOW)
        w = max(1, int(2 * k))
        for a, b in (((4, 12), (20, 12)), ((12, 4), (12, 20)), ((6, 6), (18, 18)), ((6, 18), (18, 6))):
            d.line([P(*a), P(*b)], fill=DARK, width=w)
        d.ellipse(R(6, 6, 18, 18), fill=DARK)
        d.rectangle(R(9, 9, 11, 11), fill=LIGHT)
    elif kind == "tictactoe":
        d.rectangle(R(1, 1, 23, 23), fill=LIGHT, outline=SHADOW)
        for v in (8.5, 15.5):
            d.line([P(v, 3), P(v, 21)], fill=SHADOW, width=max(1, int(k)))
            d.line([P(3, v), P(21, v)], fill=SHADOW, width=max(1, int(k)))
        w = max(1, int(2 * k))
        d.line([P(3, 3), P(7, 7)], fill=RED, width=w); d.line([P(3, 7), P(7, 3)], fill=RED, width=w)
        d.line([P(17, 17), P(21, 21)], fill=RED, width=w); d.line([P(17, 21), P(21, 17)], fill=RED, width=w)
        d.ellipse(R(10, 10, 14, 14), outline=(30, 90, 200), width=w)
        d.ellipse(R(17, 3, 21, 7), outline=(30, 90, 200), width=w)
    elif kind == "checkers":
        for r in range(2):
            for c in range(2):
                d.rectangle(R(1 + c * 11, 1 + r * 11, 12 + c * 11, 12 + r * 11), fill=(120, 80, 50) if (r + c) % 2 else (236, 216, 176))
        d.ellipse(R(2, 13, 11, 22), fill=(215, 40, 40), outline=(120, 10, 10))
        d.ellipse(R(13, 2, 22, 11), fill=(60, 130, 220), outline=(10, 40, 110))
    elif kind == "munch":
        d.pieslice(R(2, 3, 22, 23), 40, 320, fill=YELLOW)
        d.ellipse(R(14, 9, 17, 12), fill=(20, 20, 20))
    elif kind == "gameboy":                     # a little grey handheld with a green screen
        d.rounded_rectangle(R(4, 1, 20, 23), radius=max(1, int(3 * k)), fill=(200, 200, 205), outline=DARK)
        d.rectangle(R(6, 3, 18, 11), fill=(48, 98, 48))
        d.rectangle(R(7, 15, 9, 19), fill=DARK); d.rectangle(R(6, 16, 10, 18), fill=DARK)
        d.ellipse(R(13, 16, 16, 19), fill=(150, 25, 85)); d.ellipse(R(16, 14, 19, 17), fill=(150, 25, 85))
    elif kind == "games":
        d.rounded_rectangle(R(1, 7, 23, 19), radius=max(2, int(6 * k)), fill=(70, 70, 90), outline=DARK)
        d.rectangle(R(4, 12, 10, 14), fill=LIGHT); d.rectangle(R(6, 10, 8, 16), fill=LIGHT)
        d.ellipse(R(15, 10, 18, 13), fill=RED); d.ellipse(R(18, 13, 21, 16), fill=YELLOW)
    elif kind == "bluetooth":
        d.rounded_rectangle(R(4, 1, 20, 23), radius=max(2, int(7 * k)), fill=(0, 90, 200))
        w = max(1, int(2 * k))
        d.line([P(11, 4), P(11, 20), P(16, 15), P(7, 8)], fill=LIGHT, width=w)
        d.line([P(11, 4), P(16, 9), P(7, 16)], fill=LIGHT, width=w)
    elif kind == "robodev":
        d.rounded_rectangle(R(1, 2, 23, 21), radius=max(1, int(3 * k)), fill=(30, 30, 40), outline=NAVY)
        d.rectangle(R(1, 2, 23, 6), fill=NAVY)
        d.text(P(3, 8), "</>", font=F_BOLD if s >= 20 else F_UI, fill=(80, 255, 140))
    elif kind == "blinkers":                    # the salvaged LED display: a battery and a 7
        w = max(1, int(1.5 * k))
        d.rectangle(R(1, 6, 23, 18), fill=(20, 20, 24), outline=SHADOW)
        d.rectangle(R(3, 8, 7, 16), outline=LIGHT)
        d.rectangle(R(4, 9, 6, 12), fill=LIGHT)
        d.line([P(10, 9), P(15, 9), P(15, 16)], fill=LIGHT, width=w)
        d.line([P(17, 9), P(21, 9), P(21, 16)], fill=LIGHT, width=w)
    elif kind == "weather":                     # a sun behind a cloud
        d.ellipse(R(3, 3, 15, 15), fill=YELLOW, outline=(220, 150, 0))
        for bx in (R(6, 11, 15, 20), R(10, 7, 20, 18), R(14, 11, 23, 20)):
            d.ellipse(bx, fill=LIGHT, outline=SHADOW)
        d.rectangle(R(9, 15, 19, 20), fill=LIGHT)
        d.line([P(8, 20), P(20, 20)], fill=SHADOW)
    elif kind == "battery":                     # a green battery, 3/4 full
        d.rectangle(R(2, 7, 20, 18), fill=LIGHT, outline=DARK, width=max(1, int(2 * k)))
        d.rectangle(R(20, 10, 23, 15), fill=DARK)
        d.rectangle(R(4, 9, 15, 16), fill=GREEN)
    elif kind == "mouse":
        d.rounded_rectangle(R(6, 3, 18, 21), radius=max(1, int(5 * k)), fill=LIGHT, outline=DARK)
        d.line([P(12, 3), P(12, 10)], fill=DARK)
    else:
        d.rectangle(R(2, 2, 22, 22), fill=FACE, outline=DARK)


CURSOR = [(0, 0), (0, 15), (4, 11), (7, 17), (9, 16), (6, 10), (11, 10)]


def draw_cursor(d, x, y):
    pts = [(x + a, y + b) for a, b in CURSOR]
    d.polygon(pts, fill=LIGHT, outline=DARK)


# ---------------------------------------------------------------- input devices (USB mouse / keyboard)
EV_SYN, EV_KEY, EV_REL = 0, 1, 2
REL_X, REL_Y, REL_WHEEL = 0, 1, 8
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 272, 273, 274
EVENT = struct.Struct("IIHHi")          # 32-bit ARM: timeval (2 x 4 bytes), type, code, value
KEYS = {2: "1!", 3: "2@", 4: "3#", 5: "4$", 6: "5%", 7: "6^", 8: "7&", 9: "8*", 10: "9(", 11: "0)", 12: "-_", 13: "=+",
        16: "qQ", 17: "wW", 18: "eE", 19: "rR", 20: "tT", 21: "yY", 22: "uU", 23: "iI", 24: "oO", 25: "pP", 26: "[{", 27: "]}",
        30: "aA", 31: "sS", 32: "dD", 33: "fF", 34: "gG", 35: "hH", 36: "jJ", 37: "kK", 38: "lL", 39: ";:", 40: "'\"", 41: "`~",
        43: "\\|", 44: "zZ", 45: "xX", 46: "cC", 47: "vV", 48: "bB", 49: "nN", 50: "mM", 51: ",<", 52: ".>", 53: "/?", 57: "  "}
NAMED = {1: "ESC", 14: "BKSP", 15: "TAB", 28: "ENTER", 96: "ENTER", 103: "UP", 108: "DOWN", 105: "LEFT", 106: "RIGHT",
         102: "HOME", 107: "END", 104: "PGUP", 109: "PGDN", 111: "DEL", 125: "SUPER", 126: "SUPER", 62: "F4"}
SHIFTS, CTRLS, ALTS = {42, 54}, {29, 97}, {56, 100}


class InputDevices(threading.Thread):
    """Finds USB mice and keyboards in /proc/bus/input/devices and turns their events into desktop events."""

    def __init__(self, on_event, log):
        super().__init__(daemon=True, name="desktop-input")
        self.on_event, self.log = on_event, log
        self.fds = {}            # fd -> (kind, path)
        self.mods = set()
        self.caps = False
        self.kinds = set()

    @staticmethod
    def scan():
        found = {}
        try:
            text = open("/proc/bus/input/devices").read()
        except OSError:
            return found
        for block in text.strip().split("\n\n"):
            name = re.search(r'N: Name="([^"]*)"', block)
            ev = re.search(r"B: EV=([0-9a-f]+)", block)
            key = re.search(r"B: KEY=([0-9a-f ]+)", block)
            rel = re.search(r"B: REL=([0-9a-f]+)", block)
            hnd = re.search(r"H: Handlers=.*?(event\d+)", block)
            if not hnd or not ev:
                continue
            evbits = int(ev.group(1), 16)
            words = key.group(1).split() if key else []
            keybits = 0
            for w in words:
                keybits = (keybits << 64) | int(w, 16)
            path = "/dev/input/" + hnd.group(1)
            if evbits & (1 << EV_REL) and rel and int(rel.group(1), 16) & 0b11:
                found[path] = ("mouse", name.group(1) if name else "")
            elif evbits & (1 << EV_KEY) and keybits >> 30 & 1 and keybits >> 16 & 1:   # has KEY_A and KEY_Q: a real keyboard
                found[path] = ("keyboard", name.group(1) if name else "")
        return found

    def run(self):
        last_scan = 0
        while True:
            if time.time() - last_scan > 3:
                last_scan = time.time()
                found = self.scan()
                open_paths = {p for _, p in self.fds.values()}
                for path, (kind, name) in found.items():
                    if path not in open_paths:
                        try:
                            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                            self.fds[fd] = (kind, path)
                            self.log(f"desktop: {kind} connected ({name})")
                        except OSError:
                            pass
                self.kinds = {k for k, _ in self.fds.values()}
            if not self.fds:
                time.sleep(1)
                continue
            try:
                ready, _, _ = select.select(list(self.fds), [], [], 1.0)
            except (OSError, ValueError):
                ready = []
            for fd in ready:
                kind, path = self.fds[fd]
                try:
                    data = os.read(fd, EVENT.size * 64)
                except OSError:
                    os.close(fd)
                    del self.fds[fd]
                    self.log(f"desktop: {kind} unplugged")
                    self.kinds = {k for k, _ in self.fds.values()}
                    continue
                dx = dy = 0
                for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
                    _, _, typ, code, val = EVENT.unpack_from(data, i)
                    if typ == EV_REL:
                        if code == REL_X:
                            dx += val
                        elif code == REL_Y:
                            dy += val
                        elif code == REL_WHEEL:
                            self.on_event(("wheel", -val))
                    elif typ == EV_KEY:
                        self._key(code, val)
                if dx or dy:
                    self.on_event(("mmove", dx, dy))

    def _key(self, code, val):
        if code in (BTN_LEFT, BTN_RIGHT, BTN_MIDDLE):
            self.on_event(("mbtn", {BTN_LEFT: "left", BTN_RIGHT: "right", BTN_MIDDLE: "middle"}[code], val != 0))
            return
        if code in SHIFTS | CTRLS | ALTS:
            (self.mods.add if val else self.mods.discard)(code)
            return
        if val == 0:                     # key release: only apps that hold buttons (the Game Boy) use it
            name = KEYS[code][0] if code in KEYS else NAMED.get(code)
            if name:
                self.on_event(("keyup", name))
            return
        if code == 58:
            self.caps = not self.caps
            return
        shift = bool(self.mods & SHIFTS)
        ctrl, alt = bool(self.mods & CTRLS), bool(self.mods & ALTS)
        if code in KEYS:
            ch = KEYS[code][1 if shift else 0]
            if self.caps and ch.isalpha():
                ch = ch.swapcase()
            if ctrl and ch.isalpha():
                self.on_event(("key", "CTRL_" + ch.upper()))
            else:
                self.on_event(("key", ch))
        elif code in NAMED:
            name = NAMED[code]
            if alt and name == "F4":
                name = "ALT_F4"
            elif alt and name == "TAB":
                name = "ALT_TAB"
            self.on_event(("key", name))


# ---------------------------------------------------------------- apps
class App:
    """All coordinates (draw boxes and input) are whole-desktop coordinates (0..320, 0..220).

    self.host is the app's door to the machine (robotd/desktop_host.py: settings, log, say, screen, AI, system info).
    portable = True: the app uses only self.host and self.d, never self.r (the robot), so it can run in a RoboOS
    without a robot; the self-test roboos_portable checks it. Robot-only apps leave it False and may use self.r."""
    name, title, icon = "app", "App", "about"
    short = None                      # shorter label for the desktop icon
    portable = False

    def __init__(self, desk):
        self.d = desk
        self.host = desk.host
        self.r = desk.r                   # the robot (robot-only apps); None when RoboOS runs without one

    def draw(self, img, d, box):
        pass

    def click(self, x, y):
        pass

    def press(self, x, y):
        pass

    def drag(self, x, y, dx, dy):
        return False

    def release(self, x, y):
        pass

    def context(self, x, y):
        pass

    def hold(self, x, y):
        """Called about 10 times a second while the finger rests (e.g. on a scroll arrow). Return True if used."""
        return False

    def scroll(self, n):
        pass

    def key(self, k):
        pass

    def key_up(self, k):
        """A USB keyboard key was let go (same names as key(); only lower-case letters). Most apps ignore it."""
        pass

    def tick(self):
        return False

    def close(self):
        pass


class ListApp(App):
    """Helper for apps that show a scrolling list of rows."""
    row_h = 16

    def __init__(self, desk):
        super().__init__(desk)
        self.top = 0
        self.box = (0, 0, 1, 1)
        self.sbar = Scroll()
        self._sbgrab = False

    def rows(self):
        return []

    def list_box(self, box):
        return box

    def draw_list(self, d, box):
        self.box = box
        x0, y0, x1, y1 = box
        rows = self.rows()
        view = (y1 - y0) // self.row_h
        self.top = max(0, min(self.top, len(rows) - view))
        d.rectangle((x0, y0, x1 - SBW - 1, y1), fill=LIGHT)
        for i, row in enumerate(rows[self.top:self.top + view]):
            self.draw_row(d, (x0, y0 + i * self.row_h, x1 - SBW - 1, y0 + (i + 1) * self.row_h - 1), row, self.top + i)
        self.sbar.draw(d, (x1 - SBW + 1, y0, x1, y1), self.top, len(rows), view)

    def draw_row(self, d, box, row, i):
        d.text((box[0] + 3, box[1] + 2), fit_text(d, str(row), box[2] - box[0] - 6), font=F_UI, fill=DARK)

    def row_at(self, y):
        i = self.top + (y - self.box[1]) // self.row_h
        rows = self.rows()
        return i if 0 <= i < len(rows) else None

    def list_click(self, x, y):
        """Returns the row index clicked, or None (also handles the scrollbar)."""
        if not inside(self.box, x, y):
            return None
        if self.sbar.hit(x, y):
            self.top = self.sbar.click(x, y)
            return None
        return self.row_at(y)

    def press(self, x, y):
        self._sbgrab = self.sbar.hit(x, y) and self.sbar.grab(x, y)

    def release(self, x, y):
        self._sbgrab = False
        self.sbar.release()

    def hold(self, x, y):
        if self.sbar.hit(x, y) and self.sbar.on_arrow(x, y):
            self.top = self.sbar.click(x, y)
            return True
        return False

    def drag(self, x, y, dx, dy):
        if self._sbgrab:
            self.top = self.sbar.drag(y)
            return True
        if self.sbar.hit(self.d.pressed["x"], self.d.pressed["y"]) if self.d.pressed else False:
            return True                               # a drag that started on the bar track: ignore
        self.d._drag_acc += dy
        steps = int(self.d._drag_acc / self.row_h)
        if steps:
            self.top -= steps
            self.d._drag_acc -= steps * self.row_h
        return True

    def scroll(self, n):
        self.top = max(0, self.top + n)


class Terminal(App):
    portable = True
    name, title, icon = "terminal", "Terminal", "terminal"
    QUICK = [("ls", "ls -la\n"), ("df", "df -h /\n"), ("free", "free -m\n"), ("ps", "ps -eo pid,pcpu,rss,comm --sort=-pcpu | head -8\n"),
             ("ip", "ip -4 -br addr\n"), ("clear", None)]
    OSK = ["1234567890", "qwertyuiop", "asdfghjkl-", "zxcvbnm./"]
    OSK_SYM = ["!@#$%^&*()", "~`|\\_+={}[", ":;'\"<>?,/-", "     "]

    def __init__(self, desk):
        super().__init__(desk)
        self.lines = deque([""], maxlen=400)
        self.pid = self.fd = None
        self.osk = "keyboard" not in desk.inputs.kinds    # a USB keyboard is plugged in: no on-screen keyboard
        self.shift = self.sym = False
        self.back = 0                      # lines scrolled back
        self.cols = (W - SBW - 8) // MONO_W
        self.tbar = Scroll(step=3)
        self._sbgrab = False
        self._rows = 10
        self.keys = []                      # hit boxes [(box, key)]
        self._dec = None
        self.start()

    def start(self):
        env = {"TERM": "dumb", "HOME": "/root", "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
               "PS1": "robot:\\w# ", "LANG": "C.UTF-8", "TZ": os.environ.get("TZ", "UTC"), "COLUMNS": str(self.cols), "LINES": "14"}
        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(str(ROOT))
            os.execvpe("bash", ["bash", "--norc", "--noprofile", "-i"], env)
        import fcntl
        import termios
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 14, self.cols, 0, 0))
        os.set_blocking(fd, False)
        self.pid, self.fd = pid, fd
        import codecs
        self._dec = codecs.getincrementaldecoder("utf-8")("replace")

    def alive(self):
        if not self.pid:
            return False
        try:
            p, _ = os.waitpid(self.pid, os.WNOHANG)
            return p == 0
        except ChildProcessError:
            return False

    def write(self, s):
        if not self.alive():
            self.lines.append("[shell restarted]")
            self.start()
        try:
            os.write(self.fd, s.encode())
        except OSError:
            pass
        self.back = 0

    def feed(self, text):
        text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|\x1b[=>]", "", text)
        for ch in text:
            cur = self.lines[-1]
            if ch == "\n":
                self.lines.append("")
            elif ch == "\r":
                pass
            elif ch == "\b":
                self.lines[-1] = cur[:-1]
            elif ch == "\t":
                self.lines[-1] = cur + " " * (4 - len(cur) % 4)
            elif ch == "\x07" or ord(ch) < 32:
                pass
            else:
                cur += ch
                if len(cur) > self.cols:
                    self.lines[-1] = cur[:self.cols]
                    self.lines.append(cur[self.cols:])
                else:
                    self.lines[-1] = cur

    def tick(self):
        if self.fd is None:
            return False
        got = False
        while True:
            try:
                data = os.read(self.fd, 4096)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                break
            if not data:
                break
            self.feed(self._dec.decode(data))
            got = True
        return got

    def draw(self, img, d, box):
        x0, y0, x1, y1 = box
        tb = (x0, y0, x1, y0 + 16)                       # quick-command toolbar
        d.rectangle(tb, fill=FACE)
        self.tools = []
        bx = x0 + 2
        for label, cmd in self.QUICK + [("⌨" if not self.osk else "hide ⌨", "OSK")]:
            w = int(d.textlength(label, font=F_UI)) + 10
            b = (bx, y0 + 1, bx + w, y0 + 15)
            button(d, b, label)
            self.tools.append((b, cmd if cmd is not None else "CLEAR"))
            bx += w + 2
        ky = y1 - (4 * 18 + 18) if self.osk else y1
        tbox = (x0, y0 + 17, x1, ky - 1)
        d.rectangle(tbox, fill=DARK)
        rows = (tbox[3] - tbox[1] - 2) // MONO_H
        self._rows = rows
        lines = list(self.lines)
        end = len(lines) - self.back
        self.tbar.draw(d, (x1 - SBW + 1, tbox[1], x1, tbox[3]), max(0, end - rows), len(lines), rows)
        for i, line in enumerate(lines[max(0, end - rows):end]):
            d.text((x0 + 3, tbox[1] + 2 + i * MONO_H), line, font=F_MONO, fill=(200, 255, 200))
        if self.back == 0 and int(time.time() * 2) % 2 == 0 and lines:
            n = min(rows, end) - 1
            cx = x0 + 3 + int(d.textlength(lines[-1], font=F_MONO))
            d.rectangle((cx, tbox[1] + 2 + n * MONO_H + 1, cx + MONO_W - 1, tbox[1] + 2 + n * MONO_H + 10), fill=(200, 255, 200))
        self.tbox = tbox
        if self.osk:
            okey = (self.shift, self.sym, (x0, ky, x1, y1))
            if getattr(self, "_osk_key", None) != okey:          # drawing 50 keys takes ~40 ms: cache it
                tile = Image.new("RGB", (x1 - x0 + 1, y1 - ky + 1), FACE)
                self.keys = []
                self.draw_osk(ImageDraw.Draw(tile), (0, 0, x1 - x0, y1 - ky))
                self.keys = [((b[0] + x0, b[1] + ky, b[2] + x0, b[3] + ky), k) for b, k in self.keys]
                self._osk_img, self._osk_key = tile, okey
            img.paste(self._osk_img, (x0, ky))
        else:
            self.keys = []

    def draw_osk(self, d, box):
        x0, y0, x1, y1 = box
        d.rectangle(box, fill=FACE)
        rows = self.OSK_SYM if self.sym else self.OSK
        kw = (x1 - x0 - 2) / 10
        for r, row in enumerate(rows):
            off = (10 - len(row)) * kw / 2
            for c, ch in enumerate(row):
                if ch == " " and self.sym:
                    continue
                label = ch.upper() if self.shift and ch.isalpha() else ch
                b = (int(x0 + 1 + off + c * kw), y0 + 1 + r * 18, int(x0 + off + (c + 1) * kw), y0 + 17 + r * 18)
                button(d, b, label, font=F_BOLD)
                self.keys.append((b, label))
        y = y0 + 1 + 4 * 18
        specials = [("⇧", "SHIFT", 1), ("#+", "SYM", 1), ("space", " ", 3), ("⌫", "BKSP", 1.5), ("Tab", "TAB", 1), ("^C", "CTRL_C", 1),
                    ("↑", "UP", 0.75), ("⏎", "ENTER", 1.5)]
        unit = (x1 - x0 - 2) / sum(s[2] for s in specials)
        bx = x0 + 1
        for label, k, u in specials:
            b = (int(bx), y, int(bx + u * unit) - 1, y + 16)
            button(d, b, label, pressed=(k == "SHIFT" and self.shift) or (k == "SYM" and self.sym), font=F_BOLD)
            self.keys.append((b, k))
            bx += u * unit

    def click(self, x, y):
        for b, cmd in getattr(self, "tools", []):
            if inside(b, x, y):
                if cmd == "OSK":
                    self.osk = not self.osk
                elif cmd == "CLEAR":
                    self.lines.clear(); self.lines.append(""); self.write("\n")
                else:
                    self.write(cmd)
                return
        for b, k in self.keys:
            if inside(b, x, y):
                if k == "SHIFT":
                    self.shift = not self.shift
                elif k == "SYM":
                    self.sym = not self.sym
                else:
                    self.key(k)
                    if self.shift and len(k) == 1:
                        self.shift = False
                return
        if self.tbar.hit(x, y):
            self._set_top(self.tbar.click(x, y))
            return
        if inside(getattr(self, "tbox", (0, 0, 0, 0)), x, y) and not self.d.inputs.kinds & {"keyboard"}:
            self.osk = True

    def key(self, k):
        seq = {"ENTER": "\r", "BKSP": "\x7f", "TAB": "\t", "ESC": "\x1b", "UP": "\x1b[A", "DOWN": "\x1b[B", "LEFT": "\x1b[D",
               "RIGHT": "\x1b[C", "HOME": "\x1b[H", "END": "\x1b[F", "DEL": "\x1b[3~"}.get(k)
        if k.startswith("CTRL_") and len(k) == 6:
            seq = chr(ord(k[5]) - 64)
        if k == "PGUP":
            self.back = min(len(self.lines) - 1, self.back + 8); return
        if k == "PGDN":
            self.back = max(0, self.back - 8); return
        if seq is None and len(k) == 1:
            seq = k
        if seq:
            self.write(seq)

    def _set_top(self, top):
        self.back = max(0, min(len(self.lines) - 1, len(self.lines) - self._rows - top))

    def press(self, x, y):
        self._sbgrab = self.tbar.hit(x, y) and self.tbar.grab(x, y)

    def release(self, x, y):
        self._sbgrab = False

    def hold(self, x, y):
        if self.tbar.hit(x, y) and self.tbar.on_arrow(x, y):
            self._set_top(self.tbar.click(x, y))
            return True
        return False

    def drag(self, x, y, dx, dy):
        if self._sbgrab:
            self._set_top(self.tbar.drag(y))
            return True
        self.d._drag_acc += dy
        steps = int(self.d._drag_acc / MONO_H)
        if steps:
            self.back = max(0, min(len(self.lines) - 1, self.back + steps))
            self.d._drag_acc -= steps * MONO_H
        return True

    def scroll(self, n):
        self.back = max(0, min(len(self.lines) - 1, self.back - n))

    def close(self):
        if self.pid and self.alive():
            try:
                os.kill(self.pid, 9)
                os.waitpid(self.pid, 0)
            except OSError:
                pass
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
        self.pid = self.fd = None


PROTECTED = re.compile(r"(secret|\.env$|api_token$|web_auth\.json$|token$|id_rsa|id_ed25519|shadow$)", re.I)
# Files may delete only the owner's own things (long-press > Delete), never code, settings or the database.
DELETABLE = ("music", "photos", "recordings", "notes", "security")


def deletable(p):
    try:
        p = Path(p).resolve()
        return p.is_file() and not PROTECTED.search(p.name) and any((DATA / d).resolve() in p.parents for d in DELETABLE)
    except OSError:
        return False


def delete_file(desk, p, after=None):
    """Ask, then delete one of the owner's files (shared by Files and Music)."""
    if not deletable(p):
        desk.toast("only files in data/" + ", ".join(DELETABLE) + " can be deleted")
        return

    def go():
        if desk.audio.playing and str(desk.audio.source) == str(p):
            desk.audio.stop()
        Path(p).unlink(missing_ok=True)
        desk.host.log(f"owner deleted {p} (RoboOS)")
        desk.toast("deleted")
        if after:
            after()
    desk.confirm("Delete file?", Path(p).name, go)


class Files(ListApp):
    portable = True
    name, title, icon = "files", "Files", "files"
    row_h = 16

    def __init__(self, desk):
        super().__init__(desk)
        self.path = ROOT
        self.entries = []
        self.view = None                  # ("text", lines) | ("image", PIL) | ("info", text)
        self.vtop = 0
        self.vbar = Scroll(step=3)
        self.load()

    def load(self):
        try:
            items = sorted(self.path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError as e:
            items = []
            self.d.toast(f"cannot open: {e.strerror}")
        self.entries = items
        self.top = 0

    def rows(self):
        return self.entries

    def draw(self, img, d, box):
        x0, y0, x1, y1 = box
        d.rectangle((x0, y0, x1, y0 + 17), fill=FACE)
        self.upbox = (x0 + 2, y0 + 1, x0 + 30, y0 + 16)
        button(d, self.upbox, "◀ Up" if self.view is None else "◀ Back")
        right = x1 - 2
        self.editbox = None
        if self.view is not None and self.view[0] == "text":
            self.editbox = (x1 - 36, y0 + 1, x1 - 2, y0 + 16)
            button(d, self.editbox, "Edit")
            right = x1 - 40
        bevel(d, (x0 + 34, y0 + 2, right, y0 + 15), raised=False, fill=LIGHT)
        shown = str(self.path) if self.view is None else self.vname
        d.text((x0 + 37, y0 + 3), fit_text(d, shown, x1 - x0 - 42), font=F_UI, fill=DARK)
        body = (x0, y0 + 18, x1, y1)
        if self.view is None:
            self.draw_list(d, body)
            return
        kind, val = self.view
        if kind == "image":
            d.rectangle(body, fill=DARK)
            im = val.copy()
            im.thumbnail((body[2] - body[0], body[3] - body[1]))
            img.paste(im, (body[0] + (body[2] - body[0] - im.width) // 2, body[1] + (body[3] - body[1] - im.height) // 2))
        else:
            d.rectangle((body[0], body[1], body[2] - SBW - 1, body[3]), fill=LIGHT)
            lines = val if kind == "text" else [val]
            view = (body[3] - body[1] - 2) // MONO_H
            self.vtop = max(0, min(self.vtop, len(lines) - view))
            for i, line in enumerate(lines[self.vtop:self.vtop + view]):
                d.text((body[0] + 3, body[1] + 2 + i * MONO_H), line[:60], font=F_MONO, fill=DARK)
            self.vbar.draw(d, (body[2] - SBW + 1, body[1], body[2], body[3]), self.vtop, len(lines), view)
            self.vbox = body

    def draw_row(self, d, box, p, i):
        try:
            isdir = p.is_dir()
            size = "" if isdir else self.human(p.stat().st_size)
        except OSError:
            isdir, size = False, "?"
        draw_icon(d, "files" if isdir else "logs", box[0] + 2, box[1], 16)
        d.text((box[0] + 22, box[1] + 2), fit_text(d, p.name, box[2] - box[0] - 80), font=F_UI, fill=DARK)
        if size:
            d.text((box[2] - 4 - d.textlength(size, font=F_UI), box[1] + 2), size, font=F_UI, fill=SHADOW)

    @staticmethod
    def human(n):
        for u in ("B", "K", "M", "G"):
            if n < 1024:
                return f"{n:.0f}{u}"
            n /= 1024
        return f"{n:.0f}T"

    def click(self, x, y):
        if inside(self.upbox, x, y):
            if self.view is not None:
                self.view = None
            elif self.path != Path("/"):
                self.path = self.path.parent
                self.load()
            return
        if self.editbox and inside(self.editbox, x, y):
            self.d.open_app("notepad")
            self.d.focused().load(self.vname)
            return
        if self.view is not None:
            if self.view[0] != "image" and self.vbar.hit(x, y):
                self.vtop = self.vbar.click(x, y)
            return
        i = self.list_click(x, y)
        if i is None:
            return
        p = self.entries[i]
        if p.is_dir():
            self.path = p
            self.load()
        else:
            self.open(p)

    def open(self, p):
        self.vname, self.vtop = str(p), 0
        if PROTECTED.search(p.name):
            self.view = ("info", "Protected file (secrets are never shown here).")
            return
        from .desktop_apps import AUDIO_EXT
        if p.suffix.lower() in AUDIO_EXT:
            self.d.audio.play(p)
            self.d.toast(f"playing {p.name}")
            self.view = None
            return
        try:
            if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".gif", ".bmp"):
                im = Image.open(p)
                im.thumbnail((640, 480))
                self.view = ("image", im.convert("RGB"))
                return
            raw = p.read_bytes()[:64000]
            if b"\x00" in raw[:2000]:
                self.view = ("info", f"Binary file, {self.human(p.stat().st_size)}.")
                return
            text = raw.decode("utf-8", "replace").replace("\t", "    ")
            self.view = ("text", [ln[i:i + 50] for ln in text.splitlines() for i in range(0, max(1, len(ln)), 50)][:3000])
        except OSError as e:
            self.view = ("info", f"Cannot open: {e.strerror}")

    def scroll(self, n):
        if self.view is not None:
            self.vtop = self.vbar.clamp(self.vtop + n)
        else:
            super().scroll(n)

    def press(self, x, y):
        if self.view is not None:
            self._sbgrab = self.vbar.hit(x, y) and self.vbar.grab(x, y)
        else:
            super().press(x, y)

    def hold(self, x, y):
        if self.view is not None:
            if self.vbar.hit(x, y) and self.vbar.on_arrow(x, y):
                self.vtop = self.vbar.click(x, y)
                return True
            return False
        return super().hold(x, y)

    def drag(self, x, y, dx, dy):
        if self.view is not None and self._sbgrab:
            self.vtop = self.vbar.drag(y)
            return True
        if self.view is not None:
            self.d._drag_acc += dy
            steps = int(self.d._drag_acc / MONO_H)
            if steps:
                self.vtop = max(0, self.vtop - steps)
                self.d._drag_acc -= steps * MONO_H
            return True
        return super().drag(x, y, dx, dy)

    def context(self, x, y):
        """Long-press (or right-click) a file: Open / Delete."""
        if self.view is not None or not inside(self.box, x, y):
            return
        i = self.row_at(y)
        if i is None:
            return
        p = self.entries[i]
        if p.is_dir():
            return
        self.d.popup = {"x": x, "y": y, "items": [("Open", lambda: self.open(p)),
                                                  ("Delete", lambda: delete_file(self.d, p, self.load))]}

    def key(self, k):
        if k == "BKSP":
            self.click(*self.upbox[:2])
        elif k in ("UP", "DOWN", "PGUP", "PGDN"):
            self.scroll({"UP": -1, "DOWN": 1, "PGUP": -8, "PGDN": 8}[k])


class Monitor(App):
    name, title, icon = "monitor", "System Monitor", "monitor"
    short = "Monitor"
    portable = True

    def __init__(self, desk):
        super().__init__(desk)
        self.cpu = deque([0.0] * 60, maxlen=60)
        self.procs = []
        self.last = 0

    def tick(self):
        if time.time() - self.last < 2:
            return False
        self.last = time.time()
        self.cpu.append(self.host.cpu_pct())
        try:
            out = subprocess.run(["ps", "-eo", "pid,pcpu,rss,comm", "--sort=-pcpu"], capture_output=True, text=True, timeout=3).stdout
            self.procs = [ln for ln in out.splitlines()[1:] if not ln.rstrip().endswith(" ps")][:5]
        except Exception:
            pass
        return True

    def draw(self, img, d, box):
        x0, y0, x1, y1 = box
        d.rectangle(box, fill=FACE)
        g = (x0 + 4, y0 + 14, x1 - 4, y0 + 64)
        d.text((x0 + 4, y0 + 1), f"CPU {self.cpu[-1]:.0f}%", font=F_BOLD, fill=DARK)
        bevel(d, g, raised=False, fill=DARK)
        for gy in range(g[1] + 10, g[3], 10):
            d.line([(g[0] + 1, gy), (g[2] - 1, gy)], fill=(0, 70, 0))
        pts = [(g[0] + 1 + i * (g[2] - g[0] - 2) / 59, g[3] - 1 - v / 100 * (g[3] - g[1] - 2)) for i, v in enumerate(self.cpu)]
        d.line(pts, fill=(0, 255, 0), width=1)
        used, total = self.host.mem()
        du = shutil.disk_usage("/")
        up = self.host.uptime()
        info = [f"Memory  {used:.0f} / {total:.0f} MB", f"Disk    {du.used / 1e9:.1f} / {du.total / 1e9:.1f} GB",
                f"Temp    {self.host.temp():.0f} °C", f"{self.host.program[:7]:<7} up {int(up // 3600)}h {int(up % 3600 // 60)}m",
                "IP      " + "  ".join(self.host.ips().values())]
        for i, line in enumerate(info):
            d.text((x0 + 4, y0 + 68 + i * 12), line, font=F_MONO, fill=DARK)
        py = y0 + 68 + len(info) * 12 + 2
        d.text((x0 + 4, py), "  PID  CPU    RSS  NAME", font=F_MONO, fill=NAVY)
        for i, line in enumerate(self.procs):
            p = line.split(None, 3)
            if len(p) == 4 and py + 12 + i * 12 < y1 - 10:
                d.text((x0 + 4, py + 12 + i * 12), f"{p[0]:>5} {p[1]:>4} {int(p[2]) // 1024:>5}M  {p[3][:16]}", font=F_MONO, fill=DARK)


class Logs(ListApp):
    name, title, icon = "logs", "Logs", "logs"
    portable = True
    row_h = 12

    def __init__(self, desk):
        super().__init__(desk)
        self.items, self.last = [], 0
        self.tick()

    def tick(self):
        if time.time() - self.last < 3:
            return False
        self.last = time.time()
        ev = self.host.recent_log(120)    # [(time, kind, message)], newest first
        new = [f"{time.strftime('%H:%M', time.localtime(ts))} {msg}" for ts, kind, msg in reversed(ev)]
        lines = [ln[i:i + 52] for ln in new for i in range(0, max(1, len(ln)), 52)]
        changed = lines != self.items
        if changed:
            at_end = self.top >= len(self.items) - 14
            self.items = lines
            if at_end:
                self.top = len(lines)
        return changed

    def rows(self):
        return self.items

    def draw(self, img, d, box):
        self.draw_list(d, box)

    def draw_row(self, d, box, row, i):
        d.text((box[0] + 2, box[1]), row, font=F_MONO, fill=DARK)

    def click(self, x, y):
        self.list_click(x, y)


class Paint(App):
    portable = True
    name, title, icon = "paint", "Paint", "paint"
    long_press = None                 # holding still is part of painting: no long-press menu
    COLORS = [DARK, LIGHT, RED, (255, 140, 0), YELLOW, GREEN, (40, 80, 220), (150, 60, 200), TEAL, SHADOW]

    def __init__(self, desk):
        super().__init__(desk)
        self.canvas = Image.new("RGB", (W - 4, H - TASK_H - TITLE_H - 24), LIGHT)
        self.color, self.size, self.prev = DARK, 3, None

    def draw(self, img, d, box):
        x0, y0, x1, y1 = box
        d.rectangle((x0, y0, x1, y0 + 20), fill=FACE)
        self.pal = []
        for i, c in enumerate(self.COLORS):
            b = (x0 + 2 + i * 17, y0 + 3, x0 + 16 + i * 17, y0 + 17)
            bevel(d, b, raised=c != self.color, fill=c)
            self.pal.append((b, c))
        bx = x0 + 2 + len(self.COLORS) * 17 + 4
        self.sizes = []
        for s in (2, 4, 8):
            b = (bx, y0 + 3, bx + 16, y0 + 17)
            bevel(d, b, raised=s != self.size)
            d.ellipse((bx + 8 - s // 2, y0 + 10 - s // 2, bx + 8 + s // 2, y0 + 10 + s // 2), fill=DARK)
            self.sizes.append((b, s))
            bx += 18
        self.clr = (bx + 2, y0 + 3, bx + 34, y0 + 17)
        button(d, self.clr, "Clear")
        self.save = (bx + 36, y0 + 3, x1 - 2, y0 + 17)
        button(d, self.save, "Save")
        self.cbox = (x0 + 2, y0 + 22, x0 + 2 + self.canvas.width, y0 + 22 + self.canvas.height)
        img.paste(self.canvas, self.cbox[:2])

    def _dot(self, x, y):
        cx, cy = x - self.cbox[0], y - self.cbox[1]
        cd = ImageDraw.Draw(self.canvas)
        if self.prev:
            cd.line([self.prev, (cx, cy)], fill=self.color, width=self.size * 2)
        cd.ellipse((cx - self.size, cy - self.size, cx + self.size, cy + self.size), fill=self.color)
        self.prev = (cx, cy)

    def press(self, x, y):
        self.prev = None
        if inside(getattr(self, "cbox", (0, 0, -1, -1)), x, y):
            self._dot(x, y)

    def drag(self, x, y, dx, dy):
        if inside(self.cbox, x, y):
            self._dot(x, y)
        else:
            self.prev = None
        return True

    def release(self, x, y):
        self.prev = None

    def click(self, x, y):
        for b, c in self.pal:
            if inside(b, x, y):
                self.color = c
                return
        for b, s in self.sizes:
            if inside(b, x, y):
                self.size = s
                return
        if inside(self.clr, x, y):
            self.canvas.paste(LIGHT, (0, 0, self.canvas.width, self.canvas.height))
        elif inside(self.save, x, y):
            PHOTOS.mkdir(parents=True, exist_ok=True)
            p = PHOTOS / time.strftime("paint_%Y%m%d_%H%M%S.png")
            self.canvas.save(p)
            self.d.toast(f"saved {p.name}")


class Power(App):
    name, title, icon = "power", "Shut Down", "power"
    portable = True

    def draw(self, img, d, box):
        x0, y0, x1, y1 = box
        d.rectangle(box, fill=FACE)
        d.text((x0 + 10, y0 + 6), f"What do you want {self.host.machine} to do?", font=F_BOLD, fill=DARK)
        self.btns = []
        for i, (label, act, note) in enumerate(((f"Restart {self.host.program}", "restart", "restart the software (about 40 s)"),
                                                ("Reboot the computer", "reboot", "restart the whole computer (about 1 min)"),
                                                ("Shut down", "shutdown", "safe to unplug when the green LED stops"))):
            b = (x0 + 10, y0 + 24 + i * 52, x1 - 10, y0 + 50 + i * 52)
            button(d, b, label, font=F_BIG)
            d.text((x0 + 12, b[3] + 3), note, font=F_UI, fill=SHADOW)
            self.btns.append((b, act, label))

    def click(self, x, y):
        for b, act, label in self.btns:
            if inside(b, x, y):
                self.d.confirm(f"{label}?", "Are you sure?", lambda a=act: self.do(a))

    def do(self, act):
        self.host.log(f"desktop: owner chose {act}")
        if act == "restart":
            self.d.toast(f"restarting {self.host.program}...")
        threading.Thread(target=self.host.power, args=(act,), daemon=True).start()


class About(App):
    name, title, icon = "about", "About RoboOS", "about"
    short = "About"
    portable = True

    def draw(self, img, d, box):
        x0, y0, x1, y1 = box
        d.rectangle(box, fill=FACE)
        draw_icon(d, "robot", x0 + 12, y0 + 10, 24)
        d.text((x0 + 44, y0 + 8), f"RoboOS {VERSION}", font=F_BIG, fill=NAVY)
        d.text((x0 + 44, y0 + 26), f"a tiny desktop for {self.host.machine}", font=F_UI, fill=DARK)
        try:
            commit = subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%h %s"], capture_output=True, text=True, timeout=2).stdout.strip()
        except Exception:
            commit = "?"
        info = [f"Computer: {self.host.board()[:26]}, {self.host.mem()[1]:.0f} MB",
                f"Screen firmware: {self.host.screen.info.get('ver', '?')}",
                f"{self.host.program}: {(commit or 'RoboOS ' + VERSION)[:44]}", "Input: touch" + "".join(" + " + k for k in sorted(self.d.inputs.kinds)),
                "Tip: long-press (or right-click) a Start", "menu item to put it on the desktop."]
        for i, line in enumerate(info):
            d.text((x0 + 12, y0 + 50 + i * 14), line, font=F_UI, fill=DARK)


from .media import Browser, Music, Notepad, Recorder, Tube          # noqa: E402  (they build on the classes above)
from .games import Checkers, Games, Mines, TicTacToe                # noqa: E402
from .munch import Munch                                            # noqa: E402
from .gameboy_app import GameBoy                                    # noqa: E402
from .store import Store, load_installed                            # noqa: E402

# The built-in apps in Start-menu order. A host adds its own (Host.apps(): the robot puts RoboDev first and its robot
# apps before Logs); apps installed from the store (data/apps) come after them.
APPS = [Terminal, Files, Notepad, Browser, Tube, Music, Recorder, Paint, Games, Mines, TicTacToe, Checkers, Munch, GameBoy,
        Monitor, Store, Logs, About, Power]
DEFAULT_ICONS = ["robodev", "terminal", "files", "browser", "tube", "music", "recorder", "notepad", "paint", "monitor", "camera", "settings", "power",
                 "mines", "munch", "gameboy", "tictactoe", "checkers", "store"]


# ---------------------------------------------------------------- the desktop (window manager)
class Desktop:
    def __init__(self, host):
        self.host = host                  # the machine: settings, log, screen, voice... (roboos/host.py)
        self.r = host.robot               # the robot for robot-only apps; None without one
        self.apps = list(host.apps(APPS))
        self.app_by_name = {a.name: a for a in self.apps}
        self.broken = {}                  # installed app id -> why it did not load
        self.q = queue.Queue()
        self.windows = []                 # open apps, last = focused
        self.minimized = set()
        self.start_open = False
        self.popup = None                 # {"box":..., "items":[(label, fn)]}
        self.dialog = None                # {"title","text","buttons":[(label, fn)]}
        self.toast_msg, self.toast_until = "", 0
        self.cursor = [W / 2, H / 2]
        self.cursor_seen = 0
        self.pressed = None               # {"x","y","t","moved","target","long_done"}
        self._drag_acc = 0
        self.last_sent = None
        self.force = True
        self.dirty = True
        self.wall = self._wallpaper()
        self._last_wake = 0
        self.inputs = InputDevices(self.on_input, host.log)
        self.inputs.start()
        from .media import AudioOut
        self.audio = AudioOut(host)              # one sound at a time, shared by Music / Recorder / RoboTube
        self.hits = []
        self._stale = True
        self.stats = {"sent": 0, "failed": 0, "bytes": 0, "full": 0, "last_ms": 0}
        self.lock = threading.RLock()     # render() and input handling; preview() borrows the window list under it
        self.replay = deque()             # (time, JPEG) of recent frames, only while a public app (a game...) is in front
        self._last_cap = 0
        load_installed(self)              # apps from the store (data/apps/<id>/)

    # ---- config
    def cfg(self):
        c = self.host.section("desktop")
        c.setdefault("icons", list(DEFAULT_ICONS))
        if not c.get("icons_v2"):                  # put the new apps on the desktop once
            c["icons"] = list(DEFAULT_ICONS)
            c["icons_v2"] = True
        if not c.get("icons_v3"):                  # the games: added once, the owner's own layout is kept
            c["icons"] += [n for n in ("mines", "tictactoe", "checkers") if n not in c["icons"]]
            c["icons_v3"] = True
        if not c.get("icons_v4"):                  # RoboDev (coding agent), first on the desktop, added once
            if "robodev" not in c["icons"]:
                c["icons"].insert(0, "robodev")
            c["icons_v4"] = True
        if not c.get("icons_v5"):                  # RoboMunch next to the other games, added once
            if "munch" not in c["icons"]:
                i = c["icons"].index("mines") + 1 if "mines" in c["icons"] else len(c["icons"])
                c["icons"].insert(i, "munch")
            c["icons_v5"] = True
        if not c.get("icons_v6"):                  # the Robot Control Panel became Settings (one settings list)
            c["icons"] = ["settings" if n == "robot" else n for n in c["icons"]]
            c["icons_v6"] = True
        if not c.get("icons_v7"):                  # the BLINKERS LED display app (2026-11-11), added once
            if "blinkers" not in c["icons"]:
                c["icons"].append("blinkers")
            c["icons_v7"] = True
        if not c.get("icons_v8"):                  # the Weather app (2026-11-12), added once
            if "weather" not in c["icons"]:
                c["icons"].append("weather")
            c["icons_v8"] = True
        if not c.get("icons_v9"):                  # the Battery app (INA219, 2026-11-14), added once
            if "battery" not in c["icons"]:
                c["icons"].append("battery")
            c["icons_v9"] = True
        if not c.get("icons_v10"):                 # the Game Boy (2026-11-14), next to RoboMunch, added once
            if "gameboy" not in c["icons"]:
                i = c["icons"].index("munch") + 1 if "munch" in c["icons"] else len(c["icons"])
                c["icons"].insert(i, "gameboy")
            c["icons_v10"] = True
        if not c.get("icons_v11"):                 # the App Store (2026-11-15), added once
            if "store" not in c["icons"]:
                c["icons"].append("store")
            c["icons_v11"] = True
        return c

    def _wallpaper(self):
        im = Image.new("RGB", (W, H - TASK_H), TEAL)
        d = ImageDraw.Draw(im)
        d.text((W - 92, H - TASK_H - 18), "RoboOS", font=F_BIG, fill=(0, 100, 100))
        return im

    # ---- public helpers for apps
    def toast(self, msg, sec=3):
        self.toast_msg, self.toast_until = msg, time.time() + sec
        self.dirty = True

    def confirm(self, title, text, on_yes):
        self.dialog = {"title": title, "text": text, "buttons": [("Yes", on_yes), ("No", None)]}
        self.dirty = True

    def set_privacy(self, on):
        self.host.set_camera_privacy(on)
        self.toast("camera OFF" if on else "camera allowed")

    def add_app(self, cls):
        """An app installed while RoboOS runs (the Store): it shows up in the Start menu now."""
        self.apps = [a for a in self.apps if a.name != cls.name] + [cls]
        self.app_by_name[cls.name] = cls
        self.dirty = True

    def remove_app(self, name):
        for w in [w for w in self.windows if w.name == name]:
            self.close_app(w)
        self.apps = [a for a in self.apps if a.name != name]
        self.app_by_name.pop(name, None)
        self.dirty = True

    def open_app(self, name):
        for w in self.windows:
            if w.name == name:
                self.windows.remove(w)
                self.windows.append(w)
                self.minimized.discard(w)
                break
        else:
            cls = self.app_by_name.get(name)
            if not cls:
                return
            try:
                self.windows.append(cls(self))
            except Exception as e:
                self.toast(f"{name} failed: {e}")
                return
        self.start_open = False
        self.popup = None
        self.dirty = True

    def close_app(self, w):
        try:
            w.close()
        except Exception:
            pass
        if w in self.windows:
            self.windows.remove(w)
        self.minimized.discard(w)
        self.dirty = True

    def focused(self):
        vis = [w for w in self.windows if w not in self.minimized]
        return vis[-1] if vis else None

    # ---- events from the screen / input thread
    def on_touch(self, m):
        e = m.get("e")
        x, y = int(m.get("x", 0)), int(m.get("y", 0))
        self.q.put(("touch", e, x, y))

    def snapshot_png(self):
        """Current desktop as PNG (for the web app and for testing)."""
        if self.host.screen.page != "desktop":          # the screen loop is not running: let apps catch up (terminal output...)
            for w in list(self.windows):
                try:
                    w.tick()
                except Exception:
                    pass
        buf = io.BytesIO()
        self.render().save(buf, "PNG")
        return buf.getvalue()

    def remote_input(self, m):
        """Input from the web app / tests: {"e":"d|m|u","x","y"} like the screen, {"click":[x,y]}, {"long":[x,y]} or {"key":"..."}."""
        self.fresh_hits()
        if "key" in m:
            self.q.put(("key", m["key"], "remote"))
        elif "click" in m:
            x, y = m["click"]
            self.q.put(("touch", "d", x, y)); self.q.put(("touch", "u", x, y))
        elif "long" in m:
            x, y = m["long"]
            self.pointer_down(x, y)
            self.pressed["t"] -= 1.0
            self.long_press_check()
            self.pointer_up(x, y)
        else:
            self.on_touch(m)
        self.dirty = True
        if self.host.screen.page != "desktop":         # nobody is running the loop: process now so screenshots update
            while not self.q.empty():
                self.handle(self.q.get_nowait())

    def force_full(self):
        self.force = True
        self.dirty = True

    # ---- recordings for show-off posts (only public apps: see screencap.SAFE_APPS)
    def capture(self, img):
        w = self.focused()
        now = time.time()
        own = ("games", "mines", "munch", "tictactoe", "checkers", "paint")
        if not self.host.has("social") or not w or self.dialog or not (w.name in own or (w.name == "gameboy" and w.recording())):
            return                                      # the Game Boy only with Settings > Posting > Record Game Boy games
        if now - self._last_cap < 0.12:
            return
        self._last_cap = now
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88)
        self.replay.append((now, buf.getvalue()))
        while self.replay and now - self.replay[0][0] > 40:
            self.replay.popleft()

    def game_moment(self, app, note, score=2):
        """A game ended on the real screen: make a clip of the last seconds and offer it to the social scheduler."""
        def go():
            time.sleep(2.5)                                 # include the final position for a moment
            frames = [f for f in list(self.replay) if f[0] > time.time() - 30]
            if len(frames) < 4 or frames[-1][0] - frames[0][0] < 6:
                return                                      # not enough real play to be worth a video
            self.host.post_moment(app.name, note, frames, score)
        threading.Thread(target=go, daemon=True).start()

    def preview(self, name, steps=0):
        """Draw an app off-screen (the real screen is not touched). Returns (image, app)."""
        cls = self.app_by_name[name]
        with self.lock:
            app = cls(self)
            if hasattr(app, "demo"):
                app.demo(steps)
            return self._offscreen(app), app

    def _offscreen(self, app):
        with self.lock:
            saved = (self.windows, self.minimized, self.start_open, self.popup, self.dialog, self.toast_until, self.cursor_seen)
            try:
                self.windows, self.minimized, self.start_open, self.popup, self.dialog = [app], set(), False, None, None
                self.toast_until = self.cursor_seen = 0
                return self._render()
            finally:
                (self.windows, self.minimized, self.start_open, self.popup, self.dialog, self.toast_until, self.cursor_seen) = saved
                self._stale = self.dirty = True

    def demo_clip(self, name, seconds=24, step_s=0.8):
        """The app plays by itself off-screen (e.g. the robot against itself at checkers). Returns a list of frames."""
        img, app = self.preview(name)
        frames, t = [(0.0, img)], 0.0
        while t < seconds:
            with self.lock:
                more = app.demo_step() if hasattr(app, "demo_step") else False
                t += step_s
                frames.append((t, self._offscreen(app)))
            if not more:
                frames.append((t + 2.5, frames[-1][1]))
                break
            time.sleep(0.02)                                # let the real desktop breathe
        return frames

    # ---- main loop (runs while the screen shows the Desktop page)
    def run(self):
        self.host.log("desktop started", persist=False)
        self.force = self.dirty = True
        last_tick = 0
        try:
            while self.host.screen.page == "desktop" and self.host.screen.up:
                fast = getattr(self.focused(), "fast", False)     # a game in front: do not sit 50 ms waiting for input
                try:
                    ev = self.q.get(timeout=0.008 if fast else 0.05)
                    self.handle(ev)
                    while True:
                        self.handle(self.q.get_nowait())
                except queue.Empty:
                    pass
                self.long_press_check()
                now = time.time()
                if now - last_tick > (0.015 if fast else 0.25):
                    last_tick = now
                    for w in list(self.windows):
                        try:
                            if w.tick() and (w is self.focused()):
                                self.dirty = True
                        except Exception as e:
                            self.host.log(f"desktop app {w.name}: {e}", "warn")
                    if self.toast_until and now > self.toast_until:
                        self.toast_until = 0
                        self.dirty = True
                    if time.strftime("%H:%M") != getattr(self, "_clock", ""):
                        self._clock = time.strftime("%H:%M")
                        self.dirty = True
                    phase = int(now * 2) % 2
                    if isinstance(self.focused(), Terminal) and phase != getattr(self, "_blink", None):
                        self._blink = phase
                        self.dirty = True          # blinking cursor
                if self.dirty:
                    self.dirty = False
                    self.send_frame()
                try:
                    self.send_direct()
                except Exception as e:                # an app's own drawing must not stop the desktop
                    if time.time() - getattr(self, "_direct_err_t", 0) > 10:
                        self._direct_err_t = time.time()
                        self.host.log(f"desktop direct drawing: {e}", "warn")
        except Exception as e:
            self.host.log(f"desktop crashed: {e}", "warn")
        finally:
            self.last_sent = None

    def on_input(self, ev):
        """Events from a USB mouse/keyboard. While another page is showing, the first one opens the desktop."""
        if self.host.screen.page == "desktop":
            self.q.put(ev)
        elif ev[0] != "keyup" and time.time() - self._last_wake > 2:
            self._last_wake = time.time()
            self.wake_desktop()

    def wake_desktop(self):
        """USB mouse/keyboard used while the screen shows another page: switch to the desktop (and wake the screen)."""
        if self.host.screen.page != "desktop" and self.host.screen.up:
            if (self.host.screen.info.get("hb") or {}).get("asleep"):
                self.host.screen.send({"t": "cfg", "sleep": False})
            self.host.screen.send({"t": "page", "name": "desktop"})
            self.host.log("desktop opened by the USB mouse/keyboard", persist=False)

    def handle(self, ev):
        with self.lock:
            self._handle(ev)

    def _handle(self, ev):
        kind = ev[0]
        self.dirty = True
        if kind in ("touch", "mbtn", "key", "wheel"):
            self.fresh_hits()
        if kind == "touch":
            _, e, x, y = ev
            self.cursor_seen = 0            # touch hides the mouse pointer
            if e == "d":
                self.pointer_down(x, y, "left")
            elif e == "m":
                self.pointer_move(x, y)
            elif e == "u":
                self.pointer_up(x, y)
        elif kind == "mmove":
            if self.host.screen.page != "desktop":
                return self.wake_desktop()
            self.cursor[0] = max(0, min(W - 1, self.cursor[0] + ev[1] * 0.7))
            self.cursor[1] = max(0, min(H - 1, self.cursor[1] + ev[2] * 0.7))
            self.cursor_seen = time.time()
            if self.pressed:
                self.pointer_move(int(self.cursor[0]), int(self.cursor[1]))
        elif kind == "mbtn":
            if self.host.screen.page != "desktop":
                return self.wake_desktop()
            self.cursor_seen = time.time()
            x, y = int(self.cursor[0]), int(self.cursor[1])
            _, btn, down = ev
            if btn == "right" and down:
                self.context_at(x, y)
            elif btn == "left":
                (self.pointer_down if down else self.pointer_up)(x, y, *(["left"] if down else []))
        elif kind == "wheel":
            w = self.focused()
            if w and not self.start_open:
                w.scroll(ev[1] * 3)
        elif kind == "key":
            if self.host.screen.page != "desktop" and len(ev) < 3:      # physical keyboard: first wake the desktop
                return self.wake_desktop()
            self.key(ev[1])
        elif kind == "keyup":
            w = self.focused()
            if w:
                w.key_up(ev[1])

    # ---- pointer logic
    def pointer_down(self, x, y, btn="left"):
        self.pressed = {"x": x, "y": y, "t": time.time(), "moved": False, "long_done": False, "target": None}
        self._drag_acc = 0
        w = self.focused()
        if w and not self.dialog and not self.popup and not self.start_open and inside(self.body_box(), x, y):
            self.pressed["target"] = w
            try:
                w.press(x, y)
            except Exception as e:
                self.toast(str(e))

    def pointer_move(self, x, y):
        p = self.pressed
        if not p:
            return
        if abs(x - p["x"]) > 6 or abs(y - p["y"]) > 6:
            p["moved"] = True
        w = p["target"]
        if w and p["moved"]:
            last = p.get("last", (p["x"], p["y"]))
            w.drag(x, y, x - last[0], y - last[1])
            p["last"] = (x, y)

    def pointer_up(self, x, y):
        p, self.pressed = self.pressed, None
        if not p:
            return
        self._stale = True
        if p["target"]:
            p["target"].release(x, y)
        if p["long_done"] or p.get("holding") or (p["moved"] and p["target"]):
            return
        self.click_at(p["x"], p["y"])

    def long_press_check(self):
        p = self.pressed
        if p and not p["moved"] and p["target"] and time.time() - p["t"] > 0.4 and time.time() - p.get("rep", 0) > 0.09:
            try:
                if p["target"].hold(p["x"], p["y"]):      # e.g. resting on a scroll arrow: keep scrolling
                    p["rep"], p["holding"] = time.time(), True
                    self.dirty = True
            except Exception:
                pass
        lp = getattr(p["target"], "long_press", 0.7) if p and p["target"] else 0.7     # an app can change or disable it
        if p and lp and not p["moved"] and not p["long_done"] and not p.get("holding") and time.time() - p["t"] > lp:
            p["long_done"] = True
            self.context_at(p["x"], p["y"])
            self.dirty = True

    def body_box(self):
        return (0, TITLE_H, W - 1, H - TASK_H - 1)

    # ---- hit testing
    def click_at(self, x, y):
        if self.dialog:
            for box, fn in self.dialog.get("hits", []):
                if inside(box, x, y):
                    self.dialog = None
                    if fn:
                        try:
                            fn()
                        except Exception as e:
                            self.toast(str(e))
                    return
            return
        if self.popup:
            for box, fn in self.popup.get("hits", []):
                if inside(box, x, y):
                    self.popup = None
                    fn()
                    return
            self.popup = None
            return
        for box, action in self.hits:              # taskbar, start menu, title bar, desktop icons
            if inside(box, x, y):
                action()
                return
        if self.start_open:
            self.start_open = False
            return
        w = self.focused()
        if w and inside(self.body_box(), x, y):
            try:
                w.click(x, y)
            except Exception as e:
                self.toast(f"{w.name}: {e}")

    def context_at(self, x, y):
        """Right-click / long-press."""
        self.fresh_hits()
        self._stale = True
        for box, name, where in getattr(self, "ctx_hits", []):
            if self.start_open and where == "desk":           # icons under the open menu are hidden
                continue
            if inside(box, x, y):
                icons = self.cfg()["icons"]
                if where == "start":
                    items = [("Open", lambda n=name: self.open_app(n))]
                    if name not in icons:
                        items.append(("Add to desktop", lambda n=name: self._add_icon(n)))
                    else:
                        items.append(("Remove from desktop", lambda n=name: self._remove_icon(n)))
                else:
                    items = [("Open", lambda n=name: self.open_app(n)), ("Remove from desktop", lambda n=name: self._remove_icon(n))]
                self.popup = {"x": x, "y": y, "items": items}
                return
        w = self.focused()
        if w and inside(self.body_box(), x, y) and not self.start_open:
            w.context(x, y)

    def _add_icon(self, name):
        icons = self.cfg()["icons"]
        if name not in icons:
            icons.append(name)
            self.host.save()
        self.start_open = False
        self.toast("added to the desktop")

    def _remove_icon(self, name):
        icons = self.cfg()["icons"]
        if name in icons:
            icons.remove(name)
            self.host.save()
        self.toast("removed from the desktop")

    def key(self, k):
        self._stale = True
        if k == "SUPER":
            self.start_open = not self.start_open
            return
        if k == "ESC" and (self.start_open or self.popup or self.dialog):
            self.start_open, self.popup, self.dialog = False, None, None
            return
        w = self.focused()
        if k == "ALT_F4" and w:
            self.close_app(w)
            return
        if k == "ALT_TAB" and len(self.windows) > 1:
            first = self.windows.pop(0)
            self.windows.append(first)
            self.minimized.discard(first)
            return
        if self.dialog and k == "ENTER":
            self.click_at(*self.dialog["hits"][0][0][:2])
            return
        if w:
            w.key(k)

    # ---- rendering
    def fresh_hits(self):
        """Hit boxes come from drawing; redraw (without sending) if something changed since the last frame."""
        if self._stale:
            self.render()

    def render(self):
        with self.lock:
            return self._render()

    def _render(self):
        self._stale = False
        img = Image.new("RGB", (W, H), TEAL)
        d = ImageDraw.Draw(img)
        self.hits, self.ctx_hits = [], []
        w = self.focused()
        if w:
            self.draw_window(img, d, w)
        else:
            img.paste(self.wall, (0, 0))
            self.draw_icons(d)
        self.draw_taskbar(d)
        if self.start_open:
            self.draw_start(img, d)
        if self.popup:
            self.draw_popup(d)
        if self.dialog:
            self.draw_dialog(d)
        if self.toast_until > time.time():
            tw = d.textlength(self.toast_msg, font=F_UI) + 12
            b = (W - tw - 4, H - TASK_H - 22, W - 4, H - TASK_H - 5)
            d.rectangle(b, fill=(255, 255, 225), outline=DARK)
            d.text((b[0] + 6, b[1] + 3), self.toast_msg, font=F_UI, fill=DARK)
        if self.cursor_seen and time.time() - self.cursor_seen < 30:
            draw_cursor(d, int(self.cursor[0]), int(self.cursor[1]))
        return img

    def draw_icons(self, d):
        icons = [n for n in self.cfg()["icons"] if n in self.app_by_name]
        for i, name in enumerate(icons[:20]):     # 5 columns x 4 rows
            col, row = divmod(i, 4)
            x, y = 6 + col * 64, 6 + row * 47
            app = self.app_by_name[name]
            draw_icon(d, app.icon, x + 20, y, 24, off=(name == "camera" and self.host.camera_privacy()))
            label = fit_text(d, app.short or app.title, 62)
            tw = d.textlength(label, font=F_UI)
            d.text((x + 32 - tw / 2 + 1, y + 28), label, font=F_UI, fill=DARK)
            d.text((x + 32 - tw / 2, y + 27), label, font=F_UI, fill=LIGHT)
            box = (x, y, x + 63, y + 44)
            self.hits.append((box, lambda n=name: self.open_app(n)))
            self.ctx_hits.append((box, name, "desk"))

    def draw_window(self, img, d, w):
        tb = (0, 0, W - 1, TITLE_H - 1)
        if not hasattr(self, "_title"):                         # title gradient, drawn once
            g = np.zeros((TITLE_H, W, 3), np.uint8)
            for i in range(3):
                g[:, :, i] = np.linspace(NAVY[i], TITLE2[i], W).astype(np.uint8)
            self._title = Image.fromarray(g)
        img.paste(self._title, (0, 0))
        draw_icon(d, w.icon, 2, 0, 16)
        d.text((21, 2), fit_text(d, w.title, W - 70), font=F_BOLD, fill=LIGHT)
        bmin = (W - 36, 2, W - 22, 13)
        bclose = (W - 18, 2, W - 4, 13)
        bevel(d, bmin); bevel(d, bclose)
        d.line([(bmin[0] + 4, bmin[3] - 3), (bmin[2] - 4, bmin[3] - 3)], fill=DARK, width=2)
        d.line([(bclose[0] + 4, bclose[1] + 3), (bclose[2] - 4, bclose[3] - 3)], fill=DARK, width=2)
        d.line([(bclose[0] + 4, bclose[3] - 3), (bclose[2] - 4, bclose[1] + 3)], fill=DARK, width=2)
        self.hits.append(((bmin[0] - 3, 0, bmin[2] + 1, TITLE_H), lambda: (self.minimized.add(w), setattr(self, "dirty", True))))
        self.hits.append(((bclose[0] - 1, 0, W, TITLE_H), lambda: self.close_app(w)))
        body = self.body_box()
        d.rectangle(body, fill=FACE)
        try:
            w.draw(img, d, body)
        except Exception as e:
            d.text((6, body[1] + 6), f"{w.name} error: {e}"[:60], font=F_UI, fill=RED)

    def draw_taskbar(self, d):
        y0 = H - TASK_H
        d.rectangle((0, y0, W - 1, H - 1), fill=FACE)
        d.line([(0, y0), (W - 1, y0)], fill=LIGHT)
        sb = (2, y0 + 3, 54, H - 3)
        bevel(d, sb, not self.start_open)
        draw_icon(d, "robot", 5, y0 + 3, 16)
        d.text((23, y0 + 5), "Start", font=F_BOLD, fill=DARK)
        self.hits.append((sb, lambda: setattr(self, "start_open", not self.start_open)))
        tray_w = 56 + 16 * len(self.inputs.kinds)          # camera + clock, plus a mouse/keyboard icon each
        x = 58
        n = max(1, len(self.windows))
        bw = min(70, (W - 58 - tray_w - 4) // n)
        for w in self.windows:
            b = (x, y0 + 3, x + bw - 2, H - 3)
            active = w is self.focused()
            bevel(d, b, not active, LIGHT if active else FACE)
            draw_icon(d, w.icon, x + 3, y0 + 4, 16)
            if bw > 30:
                d.text((x + 21, y0 + 5), fit_text(d, w.title, bw - 26), font=F_UI, fill=DARK)
            self.hits.append((b, lambda w=w: self._task_click(w)))
            x += bw
        tray = (W - tray_w, y0 + 3, W - 2, H - 3)
        bevel(d, tray, raised=False)
        cam_box = (tray[0] + 2, tray[1] + 1, tray[0] + 18, tray[3] - 1)
        if self.host.has("camera"):
            draw_icon(d, "camera", cam_box[0], cam_box[1], 16, off=self.host.camera_privacy())
            self.hits.append((cam_box, lambda: self.set_privacy(not self.host.camera_privacy())))
        ix = tray[0] + 19
        for k in sorted(self.inputs.kinds):
            draw_icon(d, k, ix, tray[1] + 1, 16)
            ix += 15
        clock = time.strftime("%H:%M")
        d.text((tray[2] - 3 - d.textlength(clock, font=F_UI), y0 + 6), clock, font=F_UI, fill=DARK)

    def _task_click(self, w):
        if w is self.focused():
            self.minimized.add(w)
        else:
            self.open_app(w.name)
        self.dirty = True

    def draw_start(self, img, d):
        first, cfirst = len(self.hits), len(self.ctx_hits)        # everything before this is underneath the menu
        items = [a for a in self.apps if a.name != "power" and getattr(a, "menu", True)]
        x0, y1 = 2, H - TASK_H
        for cols, cw in ((2, 132), (3, 96)):               # it must fit above the taskbar however many apps there are
            per_col = max(1, -(-len(items) // cols))
            row = min(18, (y1 - 12) // (per_col + 1))
            if row >= 15:
                break
        h = row * (per_col + 1) + 10
        y0 = y1 - h
        box = (x0, y0, x0 + 24 + cols * cw, y1)
        bevel(d, box)
        d.rectangle((x0 + 2, y0 + 2, x0 + 20, y1 - 2), fill=NAVY)
        banner = Image.new("RGBA", (h - 4, 18), (0, 0, 0, 0))
        ImageDraw.Draw(banner).text((4, 2), "RoboOS", font=F_BIG, fill=(255, 255, 255, 255))
        rb = banner.rotate(90, expand=True)
        img.paste(rb, (x0 + 3, y0 + 2), rb)
        for i, a in enumerate(items):
            col, r = divmod(i, per_col)
            xx, y = x0 + 22 + col * cw, y0 + 4 + r * row
            b = (xx, y, xx + cw - 2, y + row - 1)
            draw_icon(d, a.icon, xx + 3, y + (row - 16) // 2, 16, off=(a.name == "camera" and self.host.camera_privacy()))
            d.text((xx + 23, y + (row - 12) // 2), fit_text(d, a.short or a.title if d.textlength(a.title, font=F_UI) > cw - 28 else a.title, cw - 28),
                   font=F_UI, fill=DARK)
            self.hits.append((b, lambda n=a.name: self.open_app(n)))
            self.ctx_hits.append((b, a.name, "start"))
        y = y0 + 4 + per_col * row
        d.line([(x0 + 22, y + 1), (box[2] - 3, y + 1)], fill=SHADOW)
        d.line([(x0 + 22, y + 2), (box[2] - 3, y + 2)], fill=LIGHT)
        b = (x0 + 22, y + 4, x0 + 148, y + 4 + row - 1)
        draw_icon(d, "power", x0 + 25, y + 5, 16)
        d.text((x0 + 45, y + 7), "Shut Down...", font=F_UI, fill=DARK)
        # The menu's own items win over anything underneath, and a tap on a blank part of the menu does nothing.
        # (It used to move every hit whose corner was inside the menu to the front, which included the desktop icons
        # under it: tapping "Music" opened the Notepad icon behind it. Owner, 2026-11-08.)
        self.hits = [(b, lambda: self.open_app("power"))] + self.hits[first:] + [(box, lambda: None)] + self.hits[:first]
        self.ctx_hits = self.ctx_hits[cfirst:] + self.ctx_hits[:cfirst]

    def draw_popup(self, d):
        p = self.popup
        wmax = max(d.textlength(lbl, font=F_UI) for lbl, _ in p["items"]) + 20
        h = 16 * len(p["items"]) + 4
        x0 = min(p["x"], W - wmax - 2)
        y0 = min(p["y"], H - h - 2)
        box = (x0, y0, x0 + wmax, y0 + h)
        bevel(d, box)
        p["hits"] = []
        for i, (lbl, fn) in enumerate(p["items"]):
            b = (x0 + 2, y0 + 2 + i * 16, x0 + wmax - 2, y0 + 17 + i * 16)
            d.text((b[0] + 8, b[1] + 2), lbl, font=F_UI, fill=DARK)
            p["hits"].append((b, fn))

    def draw_dialog(self, d):
        g = self.dialog
        box = (40, 50, W - 40, 140)
        bevel(d, box)
        d.rectangle((43, 53, W - 43, 53 + 14), fill=NAVY)
        d.text((47, 54), g["title"], font=F_BOLD, fill=LIGHT)
        d.text((52, 78), g["text"], font=F_UI, fill=DARK)
        g["hits"] = []
        n = len(g["buttons"])
        for i, (lbl, fn) in enumerate(g["buttons"]):
            bx = (W - n * 64) // 2 + i * 64
            b = (bx, 108, bx + 58, 128)
            button(d, b, lbl)
            g["hits"].append((b, fn))

    # ---- direct drawing: an app (the Game Boy) sends part of its window to the screen in its own compact format
    def direct_target(self):
        """(focused app, its direct rectangle) while it may draw there itself, else (app, None): nothing must cover the
        rectangle (Start menu, popup, dialog, toast), and the app decides whether the screen can do it."""
        w = self.focused()
        rect = w.direct_rect() if w is not None and hasattr(w, "direct_rect") else None
        if rect and (self.start_open or self.popup or self.dialog or (self.toast_until and time.time() < self.toast_until)):
            rect = None
        return w, rect

    def send_direct(self):
        w, rect = self.direct_target()
        prev = getattr(self, "_direct_app", None)
        if prev is not None and prev is not w:
            prev.direct_active = False
        if w is None or not hasattr(w, "direct_rect"):
            self._direct_app = None
            return
        if rect and (prev is not w or not w.direct_active):
            w.direct_invalidate()                 # just switched on: the screen may show something else there
        w.direct_active = bool(rect)
        self._direct_app = w
        if rect:
            w.send_direct()

    # ---- sending: only the changed rectangle goes over the wire
    def send_frame(self):
        img = self.render()
        arr = np.asarray(img)
        w, direct = self.direct_target()
        if self.force or self.last_sent is None:
            box = (0, 0, W, H)
        else:
            diff = np.any(arr != self.last_sent, axis=2)
            if direct and getattr(w, "direct_active", False):
                x0, y0, x1, y1 = direct
                diff[y0:y1, x0:x1] = False         # the app keeps that part up to date itself (send_direct)
            ys, xs = np.nonzero(diff)
            if len(xs) == 0:
                return
            x0, x1 = (xs.min() // 8) * 8, min(W, (xs.max() // 8 + 1) * 8)
            y0, y1 = (ys.min() // 8) * 8, min(H, (ys.max() // 8 + 1) * 8)
            box = (x0, y0, x1, y1)
        self.capture(img)
        crop = img.crop(box)
        buf = io.BytesIO()
        if getattr(self.focused(), "video", False):          # video: smaller, faster frames
            crop.save(buf, "JPEG", quality=42)
        else:
            crop.save(buf, "JPEG", quality=88, subsampling=0)
        jpg = buf.getvalue()
        if len(jpg) > self.host.screen.info.get("img_max", 49152) - 1024:
            buf = io.BytesIO()
            crop.save(buf, "JPEG", quality=60)
            jpg = buf.getvalue()
        t0 = time.time()
        ok = self.host.screen.send_image(jpg, timeout=2.0, x=int(box[0]), y=int(box[1]) + SCREEN_Y,
                                   w=int(box[2] - box[0]), h=int(box[3] - box[1]), slot="desk")
        st = self.stats
        st["sent" if ok else "failed"] += 1
        st["bytes"] += len(jpg)
        st["full"] += box == (0, 0, W, H)
        st["last_ms"] = int((time.time() - t0) * 1000)
        st["last_box"] = [int(v) for v in box]
        if ok:
            self.last_sent = arr.copy() if box == (0, 0, W, H) or self.last_sent is None else self._merge(arr, box)
            self.force = False
            if direct and box[0] < direct[2] and direct[0] < box[2] and box[1] < direct[3] and direct[1] < box[3]:
                w.direct_invalidate()             # this JPEG painted over the app's own rectangle: it resends it all
        else:
            self.force = True
            self.dirty = True
            time.sleep(0.2)

    def _merge(self, arr, box):
        m = self.last_sent.copy()
        x0, y0, x1, y1 = box
        m[y0:y1, x0:x1] = arr[y0:y1, x0:x1]
        return m
