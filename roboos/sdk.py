"""Everything a RoboOS app needs, in one import:

    from roboos.sdk import *

    class Hello(App):
        title, icon = "Hello", "app"
        def draw(self, img, d, box):          # img: the PIL picture, d: its ImageDraw, box: (x0, y0, x1, y1)
            d.rectangle(box, fill=FACE)
            center_text(d, box, "Hello!", F_BIG, NAVY)
        def click(self, x, y):
            self.host.say("Hello!")           # silent where there is no voice

The screen is 320 x 220 desktop pixels (W x H); an app's window body is the `box` passed to draw(). All input comes
in desktop coordinates. Methods to override (all optional): draw, click, press, drag, release, hold, context, key,
key_up, scroll, tick (return True to be redrawn), close. Reach the machine only through self.host (roboos/host.py:
settings, log, say, data_dir, ai_model, system information...) and the desktop through self.d (toast, confirm,
open_app, audio). This list is the app API, version API (1): it only grows.
"""
from . import API, VERSION
from .desktop import (App, DARK, F_BIG, F_BOLD, F_MONO, F_UI, FACE, GREEN, H, INACTIVE, LIGHT, ListApp, MONO_H,
                      MONO_W, NAVY, RED, SBW, SCREEN_Y, SHADOW, TEAL, W, YELLOW, Scroll, _font as font, bevel, button,
                      draw_icon, fit_text, inside, scrollbar)
from .games import GameApp, center_text
from .media import Keyboard, LineEdit

__all__ = ["API", "VERSION", "App", "ListApp", "GameApp", "Keyboard", "LineEdit", "Scroll",
           "W", "H", "SCREEN_Y", "SBW", "MONO_W", "MONO_H",
           "DARK", "FACE", "GREEN", "INACTIVE", "LIGHT", "NAVY", "RED", "SHADOW", "TEAL", "YELLOW",
           "F_BIG", "F_BOLD", "F_MONO", "F_UI", "font",
           "bevel", "button", "center_text", "draw_icon", "fit_text", "inside", "scrollbar"]
