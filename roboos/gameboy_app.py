"""Game Boy for RoboOS: play original Game Boy games on the screen (emulator: roboos/gameboy.py, Peanut-GB).

- Games: .gb files in data/files/games (or data/files): web app > Files > Upload. None come with the robot; free
  homebrew games work well. Game Boy Color-only games do not run.
- Touch: D-pad on the left, A and B on the right, SELECT and START under them. Slide the finger from button to button.
  Touching between A and B presses both. The touchscreen feels one finger at a time, so for two buttons at once
  (run + jump) use a USB keyboard: arrows or WASD = D-pad, X or K = A, Z or J = B, Enter = START, Backspace or Tab = SELECT,
  Esc = back to the list.
- "Play" (top right): the AI plays the game (roboos/gameboy_ai.py). Touching any button takes over.
- "Watch" (next to it): you play, the robot watches like a friend on the couch: a remark now and then (out loud and
  under the game), a tip when it is sure, and notes on how the game works that Play mode uses later.
- The game pauses when its window is covered or the screen leaves RoboOS. Saves: data/gameboy/saves/<game>.sav.
- Not recorded for posts unless Settings > Posting > Record Game Boy games is on (off by default: commercial games
  belong to their publishers; many homebrew games may be shared). Then leaving a game after some play offers the last
  30 s as a clip to the social scheduler, like the robot's own games.
- The screen shows as many frames as the USB link carries; the game itself always runs at full speed. Screen firmware
  0.9.3+ draws the changed rows itself (send_direct, "gbf" message): about 34 frames a second in a small scene, 10.6 when the
  whole screen scrolls. Older firmware gets JPEGs through the desktop (about 5-8). The number top right is the frames per
  second that reached the screen.
"""
import threading
import time

from PIL import Image, ImageDraw

from .desktop import (DARK, F_BIG, F_BOLD, F_UI, FACE, H as DESK_H, LIGHT, NAVY, SCREEN_Y, SHADOW, W as DESK_W, bevel,
                      button, inside)
from .games import GameApp, center_text
from .gameboy import BUTTONS as B, H, SAVES, W, Emulator, find_roms
from .gameboy_ai import AIPlayer

PALETTE = [155, 188, 15, 139, 172, 15, 48, 98, 48, 15, 56, 15]      # the original green screen, light to dark
PAL565 = [(r >> 3) << 11 | (g >> 2) << 5 | b >> 3 for r, g, b in zip(PALETTE[0::3], PALETTE[1::3], PALETTE[2::3])]
KEYMAP = {"UP": "UP", "DOWN": "DOWN", "LEFT": "LEFT", "RIGHT": "RIGHT", "w": "UP", "s": "DOWN", "a": "LEFT", "d": "RIGHT",
          "x": "A", "k": "A", "z": "B", "j": "B", "ENTER": "START", " ": "START", "BKSP": "SELECT", "TAB": "SELECT"}
MIN_TAP = 0.1                    # a quick tap still holds the button this long, so the game sees it
KEY_HOLD = 0.6                   # a key counts as held this long unless its release (or a repeat) arrives first
BEZEL, PAD, PAD_HI = (85, 85, 110), (45, 45, 55), (110, 110, 130)
AB, AB_HI = (150, 25, 85), (230, 90, 160)
ROW_H = 22


class GameBoy(GameApp):
    """A Game Boy emulator: pick a game from the shared folder, then play with the on-screen buttons or a keyboard."""
    name, title, icon = "gameboy", "Game Boy", "gameboy"
    short = "Game Boy"
    fast = True                  # tick() often: frames are shown as soon as they are ready
    video = True                 # moving pictures: smaller, faster frames
    long_press = None            # holding a button is normal here, not a right-click

    def __init__(self, desk):
        super().__init__(desk)
        self.emu = None
        self.ai = None               # AIPlayer for the running game ("Play" / "Watch")
        self.shown_status = None
        self.loading = None          # file name while a game starts (the first start also builds the emulator)
        self.closed = False
        self.play_t = 0
        self.changed = True
        self.roms, self.t_roms = find_roms(), time.time()
        self.top, self.sel, self.hover = 0, 0, None
        self.touch_mask, self.touch_t, self.touch_until = 0, 0, 0
        self.keys = {}               # button name -> time it stops counting as held
        self.shown_seq, self.shown_err = -1, ""
        self.rate, self.t_rate, self.sent0 = 0.0, time.time(), self.d.stats.get("sent", 0)
        self.direct_active = False   # set by the desktop: the screen draws the game rows itself (send_direct)
        self.direct_seq, self.direct_n, self.direct0, self.direct_wait = -1, 0, 0, 0
        self._bg = self._bg_box = None
        self.box = (0, 16, DESK_W - 1, DESK_H - 23)

    # ---- layout (whole-desktop coordinates, from the window body box)
    def layout(self, box):
        x0, y0, x1, y1 = box
        sx, sy = x0 + (x1 - x0 + 1 - W) // 2, y0 + 20
        return {"screen": (sx, sy), "back": (x0 + 3, y0 + 2, x0 + 62, y0 + 17),
                "ai": (x1 - 46, y0 + 2, x1 - 3, y0 + 17), "watch": (x1 - 94, y0 + 2, x1 - 50, y0 + 17),
                "title": (x0 + 66, y0 + 1, x1 - 98, y0 + 18), "strip": (x0 + 76, y1 - 15, x1 - 76, y1 - 1),
                "dpad": (x0 + 40, sy + 72), "a": (x1 - 23, sy + 60), "b": (x1 - 57, sy + 84),
                "select": (x0 + 10, y1 - 15, x0 + 70, y1 - 2), "start": (x1 - 70, y1 - 15, x1 - 10, y1 - 2)}

    def hit(self, x, y):
        """The Game Boy buttons under the finger, as a bit mask."""
        L = self.layout(self.box)
        cx, cy = L["dpad"]
        dx, dy = x - cx, y - cy
        ax, ay = abs(dx), abs(dy)
        if ax <= 40 and ay <= 42:
            if max(ax, ay) < 5:
                return 0
            horiz = B["RIGHT"] if dx > 0 else B["LEFT"]
            vert = B["DOWN"] if dy > 0 else B["UP"]
            if ax >= ay:                                   # the main direction, plus the other one only on a clear diagonal
                return horiz | (vert if ay > 12 and ay > 0.6 * ax else 0)
            return vert | (horiz if ax > 12 and ax > 0.6 * ay else 0)
        da = ((x - L["a"][0]) ** 2 + (y - L["a"][1]) ** 2) ** 0.5
        db = ((x - L["b"][0]) ** 2 + (y - L["b"][1]) ** 2) ** 0.5
        if da < 24 and db < 24 and abs(da - db) < 7:       # between the two: both
            return B["A"] | B["B"]
        if min(da, db) < 24:
            return B["A"] if da <= db else B["B"]
        for name in ("select", "start"):
            bx0, by0, bx1, by1 = L[name]
            if inside((bx0 - 4, by0 - 6, bx1 + 4, by1 + 2), x, y):
                return B[name.upper()]
        return 0

    def human_mask(self):
        """The buttons the person holds (touch + keyboard)."""
        now = time.time()
        self.keys = {k: t for k, t in self.keys.items() if t > now}
        m = self.touch_mask
        for k in self.keys:
            m |= B[k]
        return m

    def mask(self):
        m = self.human_mask()
        if self.ai and self.ai.on and self.ai.mode == "play":
            m |= self.ai.mask
        return m

    def take_over(self):
        """A person pressed a game button: the AI lets go (when it was playing; a watching Pip keeps watching)."""
        if self.ai and self.ai.on and self.ai.mode == "play":
            self.ai.stop("you took over - tap Play to hand back")
            self.changed = True

    def push(self):
        if self.emu:
            self.emu.buttons = self.mask()
            if self.ai:
                self.ai.saw(self.human_mask())             # watch mode remembers what you pressed

    # ---- starting and stopping games
    def start(self, path):
        if self.loading:
            return
        self.loading, self.changed = path.name, True

        def work():
            emu = None
            try:
                emu = Emulator(path, self.host.log)
            except Exception as e:
                self.d.toast(f"{path.stem}: {e}", 6)
            self.loading = None
            if emu and self.closed:
                emu.close()
                return
            self.emu, self._bg, self.shown_seq, self.changed = emu, None, -1, True
            self.ai = AIPlayer(self, emu) if emu else None
            self.play_t = time.time()

        threading.Thread(target=work, daemon=True, name="gameboy-start").start()

    def recording(self):
        """May the desktop record this game for a post? (Settings > Posting > Record Game Boy games, off by default.)"""
        return bool(self.emu and self.host.get("social", "record_gameboy"))

    def offer_clip(self, emu):
        """Leaving a game after real play, with recording allowed: the last 30 s may become a post."""
        if emu and self.recording() and time.time() - self.play_t > 20:
            self.d.game_moment(self, f"Someone played {emu.title.title()} on my screen (a Game Boy game).", score=1)

    def back(self):
        if self.ai:
            self.ai.stop()
            self.ai = None
        emu = self.emu
        self.offer_clip(emu)
        self.emu = None
        if emu:
            emu.close()
        self.touch_mask, self.keys = 0, {}
        self.roms, self.changed = find_roms(), True

    def close(self):
        self.closed = True
        if self.ai:
            self.ai.stop()
        self.offer_clip(self.emu)
        if self.emu:
            self.emu.close()
            self.emu = None

    # ---- the desktop calls this often (fast = True)
    def tick(self):
        now = time.time()
        if now - self.t_rate >= 2:                         # frames per second that reached the screen
            sent, dt = self.d.stats.get("sent", 0), now - self.t_rate
            n = self.direct_n - self.direct0 if self.direct_active else sent - self.sent0
            self.rate, self.sent0, self.direct0, self.t_rate = n / dt, sent, self.direct_n, now
            self.changed = self.changed or bool(self.emu)
        if self.emu:
            if self.d.focused() is self:
                self.emu.alive = now                       # not touched while covered or off-screen: the game pauses
            if self.touch_until and now >= self.touch_until:
                self.touch_mask, self.touch_until = 0, 0
            self.push()
            status = self.ai.status if self.ai else ""
            if status != self.shown_status:
                self.changed = True
            if self.emu.error != self.shown_err or (self.emu.seq != self.shown_seq and not self.direct_active):
                self.changed = True                        # (in direct mode new frames go out in send_direct instead)
        elif not self.loading and now - self.t_roms > 3:   # games uploaded while the list is open show up
            self.t_roms = now
            roms = find_roms()
            if roms != self.roms:
                self.roms, self.changed = roms, True
        changed, self.changed = self.changed, False
        return changed

    # ---- the fast path: screen firmware 0.9.3+ draws the game's 2-bit rows itself ("gbf" message)
    def direct_rect(self):
        """The game picture's rectangle (desktop coordinates, right/bottom exclusive) while the screen can draw it from
        rows, else None (older screen firmware, no game, or the game stopped with a message over the picture)."""
        if not self.emu or self.emu.error or not self.host.screen.info.get("gbf"):
            return None
        sx, sy = self.layout(self.box)["screen"]
        return (sx, sy, sx + W, sy + H)

    def direct_invalidate(self):
        """Something else was drawn over the picture: the next send_direct() sends every row."""
        if self.emu:
            self.emu.delta_reset()

    def send_direct(self):
        """Called by the desktop loop while direct mode is on: send the rows that changed since last time."""
        emu = self.emu
        if not emu or emu.seq == self.direct_seq or time.time() < self.direct_wait:
            return
        rect = self.direct_rect()
        if not rect:
            return
        self.direct_seq = emu.seq
        payload = emu.delta(max(4096, self.host.screen.info.get("img_max", 16384) - 64))
        if not payload:
            return
        if self.recording() and time.time() - self.d._last_cap >= 0.12:
            self.d.capture(self.d.render())                # direct frames skip the desktop: draw one for the recording
        if self.host.screen.send_image(payload, timeout=1.0, kind="gbf", x=rect[0], y=rect[1] + SCREEN_Y, pal=PAL565):
            self.direct_n += 1
        else:                                              # not drawn (a message on the screen, a lost byte): all rows next time
            emu.delta_reset()
            self.direct_wait = time.time() + 0.3

    # ---- drawing
    def draw(self, img, d, box):
        self.box = box
        if self.emu:
            self.draw_game(img, d, box)
        else:
            self.draw_list(img, d, box)

    def make_bg(self, box):
        """Everything that does not move: buttons, the screen's frame, the title."""
        L = self.layout(box)
        full = Image.new("RGB", (DESK_W, DESK_H), FACE)
        d = ImageDraw.Draw(full)
        sx, sy = L["screen"]
        d.rounded_rectangle((sx - 6, sy - 5, sx + W + 5, sy + H + 4), radius=5, fill=BEZEL)
        button(d, L["back"], "< Games")
        title, tb = self.emu.title[:22], L["title"]
        while len(title) > 4 and d.textlength(title, font=F_BOLD) > tb[2] - tb[0]:
            title = title[:-2] + "…"
        center_text(d, tb, title, F_BOLD, NAVY)
        self.draw_buttons(d, L, 0)
        return full.crop((box[0], box[1], box[2] + 1, box[3] + 1))

    def draw_buttons(self, d, L, m):
        """The D-pad, A, B, SELECT and START. Buttons in mask m are drawn pressed (lighter)."""
        cx, cy = L["dpad"]
        d.rectangle((cx - 11, cy - 34, cx + 11, cy + 34), fill=PAD)
        d.rectangle((cx - 34, cy - 11, cx + 34, cy + 11), fill=PAD)
        arms = {"UP": (cx - 11, cy - 34, cx + 11, cy - 11), "DOWN": (cx - 11, cy + 11, cx + 11, cy + 34),
                "LEFT": (cx - 34, cy - 11, cx - 11, cy + 11), "RIGHT": (cx + 11, cy - 11, cx + 34, cy + 11)}
        tips = {"UP": [(cx, cy - 29), (cx - 5, cy - 21), (cx + 5, cy - 21)],
                "DOWN": [(cx, cy + 29), (cx - 5, cy + 21), (cx + 5, cy + 21)],
                "LEFT": [(cx - 29, cy), (cx - 21, cy - 5), (cx - 21, cy + 5)],
                "RIGHT": [(cx + 29, cy), (cx + 21, cy - 5), (cx + 21, cy + 5)]}
        for name, r in arms.items():
            if m & B[name]:
                d.rectangle(r, fill=PAD_HI)
            d.polygon(tips[name], fill=SHADOW if not m & B[name] else LIGHT)
        for name in ("a", "b"):
            x, y = L[name]
            on = m & B[name.upper()]
            d.ellipse((x - 16, y - 16, x + 16, y + 16), fill=AB_HI if on else AB, outline=DARK)
            center_text(d, (x - 16, y - 16, x + 16, y + 16), name.upper(), F_BIG, LIGHT)
        for name in ("select", "start"):
            r = L[name]
            on = m & B[name.upper()]
            d.rounded_rectangle(r, radius=6, fill=PAD_HI if on else SHADOW)
            center_text(d, r, name.upper(), F_UI, LIGHT)

    def draw_game(self, img, d, box):
        if self._bg is None or self._bg_box != box:
            self._bg, self._bg_box = self.make_bg(box), box
        img.paste(self._bg, (box[0], box[1]))
        L = self.layout(box)
        sx, sy = L["screen"]
        frame = Image.frombytes("P", (W, H), self.emu.frame)
        frame.putpalette(PALETTE)
        img.paste(frame.convert("RGB"), (sx, sy))
        self.shown_seq, self.shown_err = self.emu.seq, self.emu.error
        m = self.mask()
        if m:
            self.draw_buttons(d, L, m)
        for key, mode, label in (("watch", "watch", "Watch"), ("ai", "play", "Play")):
            on = bool(self.ai and self.ai.on and self.ai.mode == mode)
            button(d, L[key], label, pressed=on, color=NAVY if on else DARK)
        status = self.ai.status if self.ai else ""
        self.shown_status = status
        strip = L["strip"]
        if status:                                         # what the AI thinks (or why it waits), under the game
            line = status
            while len(line) > 4 and d.textlength(line, font=F_UI) > strip[2] - strip[0]:
                line = line[:-2] + "…"
            center_text(d, strip, line, F_UI, NAVY)
        elif self.rate >= 2:                               # below that the game is just standing still
            center_text(d, strip, f"{self.rate:4.1f} fps", F_UI, SHADOW)
        msg = self.emu.error or ("Paused" if self.emu.paused else "")
        if msg:
            r = (sx + 4, sy + H // 2 - 18, sx + W - 4, sy + H // 2 + 18)
            bevel(d, r, fill=FACE)
            center_text(d, (r[0], r[1] + 2, r[2], r[1] + 18), "Stopped" if self.emu.error else msg, F_BOLD, NAVY)
            if self.emu.error:
                center_text(d, (r[0], r[1] + 18, r[2], r[3] - 2), "tap < Games", F_UI, DARK)

    def visible_rows(self, box):
        return max(1, (box[3] - (box[1] + 24)) // ROW_H)

    def draw_list(self, img, d, box):
        x0, y0, x1, y1 = box
        d.rectangle(box, fill=FACE)
        if self.loading:
            center_text(d, (x0, y0 + 60, x1, y0 + 80), f"Starting {self.loading} ...", F_BIG, NAVY)
            center_text(d, (x0, y0 + 84, x1, y0 + 100), "(the very first time it builds the emulator: about 15 s)", F_UI, DARK)
            return
        if not self.roms:
            d.text((x0 + 10, y0 + 6), "No Game Boy games yet", font=F_BIG, fill=NAVY)
            lines = ["Put .gb files in data/files/games:", "web app > Files > Upload.", "",
                     "No games come with RoboOS. Free homebrew", "games work well (look for Game Boy homebrew).",
                     "Only original Game Boy games, not Color ones."]
            for i, line in enumerate(lines):
                d.text((x0 + 10, y0 + 30 + i * 14), line, font=F_UI, fill=DARK)
            return
        d.text((x0 + 8, y0 + 4), "Pick a game", font=F_BIG, fill=NAVY)
        d.text((x1 - 70, y0 + 7), f"{len(self.roms)} game{'s' if len(self.roms) != 1 else ''}", font=F_UI, fill=SHADOW)
        vis = self.visible_rows(box)
        self.sel = max(0, min(self.sel, len(self.roms) - 1))
        self.top = max(0, min(self.top, len(self.roms) - vis))
        self.rows = []
        for i, p in enumerate(self.roms[self.top:self.top + vis]):
            idx = self.top + i
            r = (x0 + 6, y0 + 24 + i * ROW_H, x1 - 10, y0 + 24 + i * ROW_H + ROW_H - 3)
            bevel(d, r, raised=idx != self.hover)
            if idx == self.sel:
                d.rectangle((r[0] + 2, r[1] + 2, r[0] + 4, r[3] - 2), fill=NAVY)
            d.text((r[0] + 9, r[1] + 4), p.stem[:34], font=F_BOLD, fill=DARK)
            note = "saved" if (SAVES / (p.stem + ".sav")).exists() else f"{max(1, p.stat().st_size // 1024)} KB"
            d.text((r[2] - 6 - d.textlength(note, font=F_UI), r[1] + 4), note, font=F_UI, fill=SHADOW)
            self.rows.append((r, idx))
        if len(self.roms) > vis:                           # a thin bar shows there is more
            sx, gy0, gy1 = x1 - 4, y0 + 24, y1 - 3
            d.line([(sx, gy0), (sx, gy1)], fill=SHADOW)
            seg = (gy1 - gy0) * vis // len(self.roms)
            off = (gy1 - gy0 - seg) * self.top // (len(self.roms) - vis)
            d.rectangle((sx - 1, gy0 + off, sx + 1, gy0 + off + seg), fill=NAVY)

    def _row_at(self, x, y):
        return next((idx for r, idx in getattr(self, "rows", []) if inside(r, x, y)), None)

    # ---- input
    def press(self, x, y):
        if self.emu:
            self.touch_mask, self.touch_t, self.touch_until = self.hit(x, y), time.time(), 0
            if self.touch_mask:
                self.take_over()
            self.push()
            self.changed = True
        else:
            self.hover, self._dy = self._row_at(x, y), 0

    def drag(self, x, y, dx, dy):
        if self.emu:
            m = self.hit(x, y)                             # sliding from button to button
            if m != self.touch_mask:
                self.touch_mask = m
                self.push()
                self.changed = True
            return
        self.hover = None
        self._dy = getattr(self, "_dy", 0) + dy
        if abs(self._dy) >= ROW_H:
            self.scroll(-1 if self._dy > 0 else 1)
            self._dy = 0

    def release(self, x, y):
        if self.emu:
            if self.touch_mask and time.time() - self.touch_t < MIN_TAP:
                self.touch_until = self.touch_t + MIN_TAP  # tick() lets go of it a moment later
            else:
                self.touch_mask = 0
                self.push()
            self.changed = True
        else:
            self.hover = None

    def hold(self, x, y):
        return bool(self.emu and self.touch_mask)          # resting on a game button: not a click

    def click(self, x, y):
        if self.emu:
            if inside(self.layout(self.box)["back"], x, y):
                self.back()
            elif self.ai:
                L = self.layout(self.box)
                for key, mode in (("ai", "play"), ("watch", "watch")):
                    if inside(L[key], x, y):              # each button toggles its mode; the other one turns off
                        if self.ai.on and self.ai.mode == mode:
                            self.ai.stop("stopped")
                        else:
                            self.ai.start(mode)
                        self.changed = True
            return
        idx = self._row_at(x, y)
        if idx is not None:
            self.sel = idx
            self.start(self.roms[idx])

    def scroll(self, n):
        if not self.emu:
            self.top += 1 if n > 0 else -1
            self.changed = True

    def key(self, k):
        name = KEYMAP.get(k if len(k) > 1 else k.lower())
        if self.emu:
            if k == "ESC":
                self.back()
            elif name:
                self.take_over()
                self.keys[name] = time.time() + KEY_HOLD   # a repeat while held keeps it pressed
                self.push()
                self.changed = True
            return
        if k in ("UP", "w"):
            self.sel = max(0, self.sel - 1)
        elif k in ("DOWN", "s"):
            self.sel = min(len(self.roms) - 1, self.sel + 1)
        elif k in ("ENTER", " ") and self.roms:
            self.start(self.roms[self.sel])
        vis = self.visible_rows(self.box)
        self.top = min(max(self.top, self.sel - vis + 1), self.sel)
        self.changed = True

    def key_up(self, k):
        name = KEYMAP.get(k if len(k) > 1 else k.lower())
        if self.emu and name and self.keys.pop(name, None):
            self.push()
            self.changed = True
