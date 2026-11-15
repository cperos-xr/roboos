"""RoboOS games: RoboMines (find the mines), and Tic-Tac-Toe and Checkers played against the robot.

- The robot is the opponent in Tic-Tac-Toe and Checkers. Its face (robot_face) shows how it feels, a speech box says
  what it is thinking, and it says a short line out loud when a game ends. Turn the talking off with
  "desktop": {"game_talk": false} in data/config.json.
- The checkers brain runs in a background thread (alpha-beta search with a time limit) so the desktop stays smooth.
- Scores, levels and best times are kept in data/config.json under "desktop".
"""
import random
import threading
import time

from PIL import Image, ImageDraw

from .desktop import (App, DARK, F_BIG, F_BOLD, F_UI, FACE, GREEN, LIGHT, NAVY, RED, SHADOW, YELLOW, _font, bevel,
                      button, inside)

F_NUM = _font("DejaVuSansMono-Bold.ttf", 14)
ROBO_BLUE = (90, 160, 220)
_FONTS = {}


def bold(size):
    if size not in _FONTS:
        _FONTS[size] = _font("DejaVuSans-Bold.ttf", size)
    return _FONTS[size]


def center_text(d, box, text, font, fill):
    x0, y0, x1, y1 = box
    b = d.textbbox((0, 0), text, font=font)
    d.text(((x0 + x1 - (b[2] - b[0])) / 2 - b[0], (y0 + y1 - (b[3] - b[1])) / 2 - b[1]), text, font=font, fill=fill)


def wrap(d, text, width, font=F_UI):
    lines, cur = [], ""
    for word in text.split():
        t = (cur + " " + word).strip()
        if d.textlength(t, font=font) <= width or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def robot_face(d, box, mood="ok"):
    """The robot's face inside box. Moods: ok, press (surprised), think, happy, sad, dead, win."""
    x0, y0, x1, y1 = box
    s = min(x1 - x0, y1 - y0) / 40.0
    cx, top = (x0 + x1) / 2, y0 + (y1 - y0 - 40 * s) / 2
    P = lambda a, b: (cx + a * s, top + b * s)
    lw = max(1, int(round(2 * s)))
    light = {"dead": SHADOW, "sad": SHADOW, "win": YELLOW, "happy": YELLOW, "think": (255, 140, 0)}.get(mood, RED)
    d.line([P(0, 4), P(0, 10)], fill=NAVY, width=max(1, int(1.5 * s)))
    d.ellipse([P(-3, 1), P(3, 7)], fill=light, outline=NAVY)
    d.rounded_rectangle([P(-15, 10), P(15, 37)], radius=max(2, int(5 * s)), fill=ROBO_BLUE, outline=NAVY)
    for ex in (-6.5, 6.5):
        if mood == "dead":
            d.line([P(ex - 3, 17), P(ex + 3, 23)], fill=NAVY, width=lw)
            d.line([P(ex - 3, 23), P(ex + 3, 17)], fill=NAVY, width=lw)
        elif mood in ("happy", "win"):
            d.arc([P(ex - 4, 17), P(ex + 4, 26)], 200, 340, fill=NAVY, width=lw)
        elif mood == "press":
            d.ellipse([P(ex - 5, 15), P(ex + 5, 25)], fill=LIGHT, outline=NAVY)
            d.ellipse([P(ex - 2, 18), P(ex + 2, 22)], fill=NAVY)
        else:
            d.ellipse([P(ex - 4, 16), P(ex + 4, 24)], fill=LIGHT, outline=NAVY)
            px, py = (1.5, -1.5) if mood == "think" else (0, 0)
            d.ellipse([P(ex - 2 + px, 18 + py), P(ex + 2 + px, 22 + py)], fill=NAVY)
    if mood == "ok":
        d.arc([P(-7, 24), P(7, 33)], 20, 160, fill=NAVY, width=lw)
    elif mood in ("happy", "win"):
        d.chord([P(-8, 25), P(8, 35)], 0, 180, fill=LIGHT, outline=NAVY)
    elif mood == "press":
        d.ellipse([P(-3, 27), P(3, 34)], fill=NAVY)
    elif mood == "think":
        d.line([P(-5, 31), P(5, 29)], fill=NAVY, width=lw)
    elif mood == "sad":
        d.arc([P(-7, 29), P(7, 37)], 200, 340, fill=NAVY, width=lw)
    elif mood == "dead":
        d.line([P(-7, 31), P(-3.5, 29), P(0, 31), P(3.5, 29), P(7, 31)], fill=NAVY, width=lw)


def draw_flag(d, x, y, c):
    """A little red flag in a c x c square at x, y."""
    pole = x + c * 0.58
    d.line([(pole, y + c * 0.18), (pole, y + c * 0.74)], fill=DARK, width=max(1, int(c / 14)))
    d.polygon([(pole, y + c * 0.16), (x + c * 0.2, y + c * 0.33), (pole, y + c * 0.5)], fill=(230, 0, 0))
    d.rectangle((x + c * 0.3, y + c * 0.72, x + c * 0.82, y + c * 0.82), fill=DARK)


def draw_mine(d, x, y, c):
    cx, cy, r = x + c / 2, y + c / 2, c * 0.26
    w = max(1, int(c / 12))
    for a, b in (((-0.36, 0), (0.36, 0)), ((0, -0.36), (0, 0.36)), ((-0.26, -0.26), (0.26, 0.26)), ((-0.26, 0.26), (0.26, -0.26))):
        d.line([(cx + a[0] * c, cy + a[1] * c), (cx + b[0] * c, cy + b[1] * c)], fill=DARK, width=w)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=DARK)
    g = max(1, c * 0.09)
    d.rectangle((cx - r * 0.5, cy - r * 0.5, cx - r * 0.5 + g, cy - r * 0.5 + g), fill=LIGHT)


def draw_shovel(d, x, y, c):
    k = c / 16
    d.line([(x + 3 * k, y + 3 * k), (x + 9 * k, y + 9 * k)], fill=(140, 90, 30), width=max(2, int(2 * k)))
    d.line([(x + 1 * k, y + 4 * k), (x + 4 * k, y + 1 * k)], fill=(140, 90, 30), width=max(2, int(2 * k)))
    d.polygon([(x + 8 * k, y + 11 * k), (x + 11 * k, y + 8 * k), (x + 15 * k, y + 12 * k), (x + 15 * k, y + 15 * k),
               (x + 12 * k, y + 15 * k)], fill=(150, 150, 160), outline=DARK)


def talk(app, text, mood=None):
    """The robot says it out loud (in the background), unless desktop.game_talk is off."""
    if app.host.game_talk():
        app.host.say(text, mood)


class GameApp(App):
    menu = False                      # reached through the Games launcher (and desktop icons), not the Start menu
    demo_mode = False                 # True while the robot plays by itself for a show-off video: no score, no talking
    portable = True

    def cfg(self):
        return self.host.section("desktop")

    def save(self):
        self.host.save()


# ---------------------------------------------------------------- Games launcher
class Games(App):
    """A grid of game tiles, like the screen's own menu (the menu's Games tile opens this).

    To add a game: one line in GAMES (app name, tile label, one-line description) and, if it keeps a score, a case in
    line(). 3 columns: up to 6 games show as big tiles, 7-9 as smaller ones, more scroll (wheel or drag)."""
    name, title, icon = "games", "Games", "games"
    portable = True
    GAMES = [("mines", "RoboMines", "Find the mines without stepping on one."),
             ("munch", "RoboMunch", "Eat every dot and dodge the ghosts."),
             ("gameboy", "Game Boy", "Play Game Boy games from the shared folder."),
             ("tictactoe", "Tic-Tac-Toe", "Three in a row against the robot."),
             ("checkers", "Checkers", "Jump all of the robot's pieces.")]
    COLS, GAP = 3, 6

    def __init__(self, desk):
        super().__init__(desk)
        self.top = 0                      # first visible row when there are more than 3 rows
        self.hover = None                 # tile under the finger: its description shows at the top

    def line(self, name):
        """The short score line under a tile ('' = none yet)."""
        c = self.host.section("desktop")
        if name == "mines":
            best = c.get("mines_best", {})
            for lvl in ("Hard", "Medium", "Easy"):             # the hardest level beaten
                if lvl in best:
                    return f"{lvl} {best[lvl]}s"
            return ""
        if name == "munch":
            return f"best {c['munch_best']}" if c.get("munch_best") else ""
        if name == "gameboy":
            from .gameboy import find_roms
            n = len(find_roms())
            return f"{n} game{'s' if n != 1 else ''}" if n else "add games"
        key = {"tictactoe": "ttt", "checkers": "checkers"}.get(name)
        sc = c.get(key + "_score") if key else None
        return f"you {sc.get('you', 0)} : {sc.get('robot', 0)} robot" if sc else ""

    def draw(self, img, d, box):
        from .desktop import draw_icon
        x0, y0, x1, y1 = box
        d.rectangle(box, fill=FACE)
        tip = next((sub for name, _, sub in self.GAMES if name == self.hover), None)
        d.text((x0 + 8, y0 + 3), tip or "Pick a game!", font=F_UI if tip else F_BIG, fill=NAVY)
        g, cols = self.GAP, self.COLS
        rows = -(-len(self.GAMES) // cols)
        vis = min(rows, 3)
        self.top = max(0, min(self.top, rows - vis))
        gx0, gy0 = x0 + g, y0 + 22
        tw = (x1 - x0 + 1 - g * (cols + 1)) // cols
        th = (y1 - gy0 - g * vis) // vis
        isz = 36 if th >= 66 else 24
        self.btns = []
        for i, (name, title, sub) in enumerate(self.GAMES):
            r, c = divmod(i, cols)
            if not self.top <= r < self.top + vis:
                continue
            bx, by = gx0 + c * (tw + g), gy0 + (r - self.top) * (th + g)
            b = (bx, by, bx + tw - 1, by + th - 1)
            bevel(d, b, raised=name != self.hover)
            ty = by + 5 + isz + (3 if isz > 24 else 1)
            draw_icon(d, name, bx + (tw - isz) // 2, by + 5, isz)
            center_text(d, (bx, ty, bx + tw, ty + 14), title, F_BOLD, DARK)
            extra = self.line(name)
            if extra and th >= 56:
                while len(extra) > 4 and d.textlength(extra, font=F_UI) > tw - 6:
                    extra = extra[:-2] + "…"
                center_text(d, (bx, ty + 14, bx + tw, ty + 27), extra, F_UI, SHADOW)
            self.btns.append((b, name))
        if rows > vis:                    # a thin bar shows there is more below or above
            sx = x1 - 3
            d.line([(sx, gy0), (sx, y1 - g)], fill=SHADOW)
            seg = (y1 - g - gy0) * vis // rows
            off = (y1 - g - gy0 - seg) * self.top // (rows - vis)
            d.rectangle((sx - 1, gy0 + off, sx + 1, gy0 + off + seg), fill=NAVY)

    def _at(self, x, y):
        return next((name for b, name in getattr(self, "btns", []) if inside(b, x, y)), None)

    def press(self, x, y):
        self.hover = self._at(x, y)

    def drag(self, x, y, dx, dy):
        self.hover = None
        self._dy = getattr(self, "_dy", 0) + dy
        if abs(self._dy) > 30:
            self.scroll(-1 if self._dy > 0 else 1)
            self._dy = 0

    def release(self, x, y):
        self.hover, self._dy = None, 0

    def scroll(self, n):
        self.top += 1 if n > 0 else -1

    def click(self, x, y):
        name = self._at(x, y)
        if name:
            self.hover = None
            self.d.close_app(self)
            self.d.open_app(name)


# ---------------------------------------------------------------- RoboMines
MINE_LEVELS = [("Easy", 8, 6, 7), ("Medium", 12, 8, 15), ("Hard", 16, 11, 32)]
NUM_COLORS = [None, (0, 0, 230), (0, 128, 0), (220, 0, 0), (0, 0, 128), (128, 0, 0), (0, 128, 128), DARK, SHADOW]
HIDDEN, OPEN, FLAG = 0, 1, 2
OPEN_BG = (214, 214, 214)


class Mines(GameApp):
    """Find every square without a mine. Numbers say how many mines touch that square."""
    name, title, icon = "mines", "RoboMines", "mines"
    short = "Mines"
    long_press = 0.45                 # long-press flags a square (faster than the desktop's usual 0.7 s)
    PANEL = 58

    def __init__(self, desk):
        super().__init__(desk)
        lv = self.cfg().get("mines_level", 0)
        self.level = lv if isinstance(lv, int) and 0 <= lv < len(MINE_LEVELS) else 0
        self.flag_mode = False
        self.tiles = {}
        self.cs = 0
        self.new_game()

    def new_game(self):
        self.lname, self.cols, self.rows, self.nmines = MINE_LEVELS[self.level]
        n = self.cols * self.rows
        self.mine, self.state, self.count = [False] * n, [HIDDEN] * n, [0] * n
        self.nb = []
        for i in range(n):
            c, r = i % self.cols, i // self.cols
            self.nb.append([rr * self.cols + cc for rr in range(r - 1, r + 2) for cc in range(c - 1, c + 2)
                            if (rr, cc) != (r, c) and 0 <= rr < self.rows and 0 <= cc < self.cols])
        self.placed, self.over, self.boom, self.opened = False, None, None, 0
        self.t0 = self.t_end = None
        self.down, self.moved, self.ctx, self.kb = None, False, False, False
        self.sel = (self.rows // 2) * self.cols + self.cols // 2
        self.ver, self._board, self._shown = 0, None, -1

    # ---- rules
    def place(self, first):
        avoid = set(self.nb[first]) | {first}          # the first tap always opens a little area
        cand = [k for k in range(len(self.mine)) if k not in avoid]
        if len(cand) < self.nmines:
            cand = [k for k in range(len(self.mine)) if k != first]
        for k in random.sample(cand, self.nmines):
            self.mine[k] = True
        self.count = [sum(self.mine[j] for j in self.nb[k]) for k in range(len(self.mine))]
        self.placed, self.t0 = True, time.time()

    def reveal(self, i):
        if self.over or self.state[i] != HIDDEN:
            return
        if not self.placed:
            self.place(i)
        if self.mine[i]:
            self.lose(i)
            return
        stack = [i]
        while stack:
            j = stack.pop()
            if self.state[j] != HIDDEN:
                continue
            self.state[j] = OPEN
            self.opened += 1
            if self.count[j] == 0:
                stack.extend(k for k in self.nb[j] if self.state[k] == HIDDEN)
        self.ver += 1
        if self.opened == len(self.mine) - self.nmines:
            self.win()

    def chord(self, i):
        """Tap an open number whose mines are all flagged: open the rest of its neighbours."""
        if not self.count[i]:
            return
        if sum(self.state[k] == FLAG for k in self.nb[i]) != self.count[i]:
            return
        for k in self.nb[i]:
            if self.state[k] == HIDDEN:
                self.reveal(k)
                if self.over:
                    return

    def toggle_flag(self, i):
        if self.over or self.state[i] == OPEN:
            return
        self.state[i] = FLAG if self.state[i] == HIDDEN else HIDDEN
        self.ver += 1

    def open_cell(self, i):
        if self.over:
            self.d.toast("tap the robot for a new game")
        elif self.state[i] == OPEN:
            self.chord(i)
        else:
            self.reveal(i)

    def act(self, i):
        if self.flag_mode and not self.over and self.state[i] != OPEN:
            self.toggle_flag(i)
        else:
            self.open_cell(i)

    def lose(self, i):
        self.over, self.boom, self.t_end = "lose", i, time.time()
        self.ver += 1
        if self.demo_mode:
            return
        self.d.toast("Boom! Tap the robot to try again", 4)
        if self.opened > len(self.mine) // 3:
            self.d.game_moment(self, f"Someone was playing RoboMines ({self.lname}) on my screen and stepped on a mine after "
                                     f"clearing {self.opened} squares. So close!", score=1)

    def win(self):
        self.over, self.t_end = "win", time.time()
        for k, m in enumerate(self.mine):
            if m:
                self.state[k] = FLAG
        self.ver += 1
        if self.demo_mode:
            return
        secs = int(self.t_end - self.t0)
        best = self.cfg().setdefault("mines_best", {})
        old = best.get(self.lname)
        if old is None or secs < old:
            best[self.lname] = secs
            self.save()
            self.d.toast(f"You win in {secs} s - new best!", 5)
        else:
            self.d.toast(f"You win in {secs} s (best {old} s)", 5)
        talk(self, "You found every mine. Nice!", "happy")
        self.d.game_moment(self, f"My human cleared a {self.lname} RoboMines board on my screen in {secs} seconds"
                                 + (" - a new record!" if old is None or secs < old else "."), score=2)

    # ---- the robot playing by itself (show-off videos); it cheats a little: it knows where the mines are
    def demo(self, steps=0):
        self.demo_mode = True
        self.reveal((self.rows // 2) * self.cols + self.cols // 2)
        for _ in range(steps):
            if not self.demo_step():
                break

    def demo_step(self):
        if self.over:
            return False
        edge = [k for k in range(len(self.mine)) if self.state[k] == HIDDEN and any(self.state[j] == OPEN for j in self.nb[k])]
        mines = [k for k in edge if self.mine[k]]
        if mines and random.random() < 0.35:
            self.toggle_flag(random.choice(mines))
            return True
        safe = [k for k in edge if not self.mine[k]] or [k for k in range(len(self.mine)) if self.state[k] == HIDDEN and not self.mine[k]]
        if not safe:
            return False
        self.reveal(random.choice(safe))
        return not self.over

    def elapsed(self):
        if not self.t0:
            return 0
        return min(999, int((self.t_end or time.time()) - self.t0))

    def set_level(self, lv):
        self.level = lv
        self.cfg()["mines_level"] = lv
        self.save()
        self.new_game()

    # ---- drawing
    def tile(self, kind):
        key = (kind, self.cs)
        t = self.tiles.get(key)
        if t is not None:
            return t
        c = self.cs
        t = Image.new("RGB", (c, c), FACE)
        d = ImageDraw.Draw(t)
        if kind in ("hidden", "flag"):
            for i in range(2 if c >= 18 else 1):
                d.line([(i, c - 1 - i), (i, i), (c - 1 - i, i)], fill=LIGHT)
                d.line([(i + 1, c - 1 - i), (c - 1 - i, c - 1 - i), (c - 1 - i, i + 1)], fill=SHADOW)
            if kind == "flag":
                draw_flag(d, 0, 0, c)
        else:
            d.rectangle((0, 0, c - 1, c - 1), fill=RED if kind == "boom" else OPEN_BG)
            d.line([(0, c - 1), (0, 0), (c - 1, 0)], fill=SHADOW)
            if kind[0] == "n" and kind != "n0":
                n = int(kind[1:])
                center_text(d, (1, 1, c, c), str(n), bold(max(9, int(c * 0.62))), NUM_COLORS[n])
            elif kind in ("mine", "boom", "wrong"):
                draw_mine(d, 0, 0, c)
                if kind == "wrong":
                    w = max(2, c // 10)
                    d.line([(c * 0.2, c * 0.2), (c * 0.8, c * 0.8)], fill=RED, width=w)
                    d.line([(c * 0.2, c * 0.8), (c * 0.8, c * 0.2)], fill=RED, width=w)
        self.tiles[key] = t
        return t

    def kind(self, i, pressed):
        s = self.state[i]
        if self.over == "lose":
            if i == self.boom:
                return "boom"
            if self.mine[i] and s != FLAG:
                return "mine"
            if s == FLAG and not self.mine[i]:
                return "wrong"
        if s == FLAG:
            return "flag"
        if s == OPEN:
            return f"n{self.count[i]}"
        return "n0" if i == pressed else "hidden"

    def pressed_cell(self):
        i = self.down
        if i is None or self.over or self.flag_mode or self.state[i] != HIDDEN:
            return None
        return i

    def board_img(self):
        pressed = self.pressed_cell()
        key = (self.ver, pressed, self.sel if self.kb else None, self.cs)
        if self._board and self._board[0] == key:
            return self._board[1]
        c = self.cs
        im = Image.new("RGB", (self.cols * c, self.rows * c), FACE)
        for i in range(len(self.state)):
            im.paste(self.tile(self.kind(i, pressed)), ((i % self.cols) * c, (i // self.cols) * c))
        if self.kb:
            x, y = (self.sel % self.cols) * c, (self.sel // self.cols) * c
            ImageDraw.Draw(im).rectangle((x, y, x + c - 1, y + c - 1), outline=(255, 170, 0), width=2)
        self._board = (key, im)
        return im

    def counter(self, d, box, value, flag=False):
        d.rectangle(box, fill=DARK)
        x0, y0, x1, y1 = box
        txt = f"{value:03d}" if value >= 0 else f"-{min(99, -value):02d}"
        tw = d.textlength(txt, font=F_NUM)
        if flag:
            draw_flag(d, x0 + 1, y0 + 1, y1 - y0 - 2)
            d.text((x1 - tw - 3, y0 + 1), txt, font=F_NUM, fill=(255, 40, 40))
        else:
            d.text(((x0 + x1 - tw) / 2, y0 + 1), txt, font=F_NUM, fill=(255, 40, 40))

    def draw(self, img, d, box):
        x0, y0, x1, y1 = box
        d.rectangle(box, fill=FACE)
        aw, ah = x1 - self.PANEL - x0 - 4, y1 - y0 - 4
        self.cs = max(8, min(aw // self.cols, ah // self.rows))
        gw, gh = self.cs * self.cols, self.cs * self.rows
        self.gx, self.gy = x0 + 2 + (aw - gw) // 2, y0 + 2 + (ah - gh) // 2
        d.line([(self.gx - 1, self.gy + gh), (self.gx - 1, self.gy - 1), (self.gx + gw, self.gy - 1)], fill=SHADOW)
        d.line([(self.gx - 1, self.gy + gh), (self.gx + gw, self.gy + gh), (self.gx + gw, self.gy - 1)], fill=LIGHT)
        img.paste(self.board_img(), (self.gx, self.gy))
        # side panel
        px = x1 - self.PANEL + 1
        cx = px + self.PANEL // 2
        flags = sum(1 for s in self.state if s == FLAG)
        self.counter(d, (px + 4, y0 + 3, x1 - 4, y0 + 21), self.nmines - flags, flag=True)
        self.face_box = (cx - 21, y0 + 25, cx + 21, y0 + 67)
        bevel(d, self.face_box)
        mood = {"lose": "dead", "win": "win"}.get(self.over) or ("press" if self.pressed_cell() is not None else "ok")
        robot_face(d, (self.face_box[0] + 3, self.face_box[1] + 3, self.face_box[2] - 3, self.face_box[3] - 3), mood)
        self.counter(d, (px + 4, y0 + 71, x1 - 4, y0 + 89), self.elapsed())
        self.mode_box = (px + 4, y0 + 95, x1 - 4, y0 + 133)
        bevel(d, self.mode_box, raised=not self.flag_mode, fill=(250, 236, 170) if self.flag_mode else FACE)
        if self.flag_mode:
            draw_flag(d, cx - 9, y0 + 98, 18)
        else:
            draw_shovel(d, cx - 8, y0 + 99, 16)
        center_text(d, (px + 4, y0 + 117, x1 - 4, y0 + 131), "Flag" if self.flag_mode else "Dig", F_BOLD, DARK)
        self.level_box = (px + 4, y0 + 139, x1 - 4, y0 + 177)
        bevel(d, self.level_box)
        center_text(d, (px + 4, y0 + 142, x1 - 4, y0 + 157), self.lname, F_BOLD, NAVY)
        best = self.cfg().get("mines_best", {}).get(self.lname)
        center_text(d, (px + 4, y0 + 158, x1 - 4, y0 + 173), f"best {best}s" if best is not None else "best -", F_UI, DARK)

    # ---- input
    def cell_at(self, x, y):
        if not self.cs:
            return None
        c, r = (x - self.gx) // self.cs, (y - self.gy) // self.cs
        if 0 <= c < self.cols and 0 <= r < self.rows:
            return int(r * self.cols + c)
        return None

    def press(self, x, y):
        self.kb = self.moved = self.ctx = False
        self.down = self.cell_at(x, y)

    def drag(self, x, y, dx, dy):
        self.moved = True
        if not self.ctx:
            self.down = self.cell_at(x, y)
        return True

    def release(self, x, y):
        i, self.down = self.down, None
        if self.moved and not self.ctx and i is not None:      # a tap that wobbled: still counts
            self.act(i)
        self.moved = False

    def click(self, x, y):
        if inside(self.face_box, x, y):
            self.new_game()
        elif inside(self.mode_box, x, y):
            self.flag_mode = not self.flag_mode
        elif inside(self.level_box, x, y):
            nxt = (self.level + 1) % len(MINE_LEVELS)
            if self.placed and not self.over:
                self.d.confirm(f"Switch to {MINE_LEVELS[nxt][0]}?", "This game will be lost.", lambda: self.set_level(nxt))
            else:
                self.set_level(nxt)
        else:
            i = self.cell_at(x, y)
            if i is not None:
                self.act(i)

    def context(self, x, y):
        """Long-press or right-click: the opposite of the Dig/Flag button."""
        i = self.cell_at(x, y)
        if i is None:
            return
        self.ctx, self.down = True, None
        if self.flag_mode or self.state[i] == OPEN:
            self.open_cell(i)
        else:
            self.toggle_flag(i)

    def key(self, k):
        self.kb = True
        c, r = self.sel % self.cols, self.sel // self.cols
        moves = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}
        if k in moves:
            c = max(0, min(self.cols - 1, c + moves[k][0]))
            r = max(0, min(self.rows - 1, r + moves[k][1]))
            self.sel = r * self.cols + c
        elif k in (" ", "ENTER"):
            self.new_game() if self.over else self.open_cell(self.sel)
        elif k in ("f", "F"):
            self.toggle_flag(self.sel)
        elif k in ("n", "N"):
            self.new_game()

    def tick(self):
        if self.t0 and not self.over:
            e = self.elapsed()
            if e != self._shown:
                self._shown = e
                return True
        return False


# ---------------------------------------------------------------- shared look for games against the robot
class VersusApp(GameApp):
    """Board on the left, the robot on the right: face, speech box, score, New game and Level buttons."""
    LEVELS = ("Easy", "Normal", "Hard")
    KEY = "game"
    BOARD = 176

    def __init__(self, desk):
        super().__init__(desk)
        c = self.cfg()
        lv = c.get(self.KEY + "_level", 1)
        self.level = lv if isinstance(lv, int) and 0 <= lv < len(self.LEVELS) else 1
        self.score = c.setdefault(self.KEY + "_score", {"you": 0, "robot": 0, "draw": 0})
        self.mood, self.msg = "ok", ""
        self.you_first = True

    def say(self, msg, mood=None):
        self.msg = msg
        if mood:
            self.mood = mood

    def finish(self, who, line):
        """who: you / robot / draw."""
        self.over = who
        mood = {"you": "sad", "robot": "win", "draw": "ok"}[who]
        if self.demo_mode:
            self.say("Good game, me!" if who != "draw" else "A draw against myself!", mood)
            return
        self.score[who] = self.score.get(who, 0) + 1
        self.save()
        self.say(line + " Tap New game to play again.", mood)
        talk(self, line, {"you": "sad", "robot": "happy", "draw": None}[who])
        game = self.title
        note = {"you": f"My human just beat me at {game} on my touchscreen ({self.LEVELS[self.level]} level). I demand a rematch!",
                "robot": f"I just beat my human at {game} on my touchscreen ({self.LEVELS[self.level]} level)!",
                "draw": f"My human and I played {game} on my touchscreen and it was a draw."}[who]
        sc = self.score
        self.d.game_moment(self, note + f" Score so far: human {sc.get('you', 0)}, robot {sc.get('robot', 0)}.", score=2 if who != "draw" else 1)

    def draw_panel(self, d, box):
        x0, y0, x1, y1 = box
        px = x0 + self.BOARD + 10
        fb = (px, y0 + 4, px + 44, y0 + 48)
        bevel(d, fb)
        robot_face(d, (fb[0] + 3, fb[1] + 3, fb[2] - 3, fb[3] - 3), self.mood)
        d.text((px + 50, y0 + 8), "Robot", font=F_BIG, fill=NAVY)
        d.text((px + 50, y0 + 26), f"level: {self.LEVELS[self.level]}", font=F_UI, fill=DARK)
        sb = (px, y0 + 54, x1 - 4, y0 + 104)
        d.rounded_rectangle(sb, radius=5, fill=LIGHT, outline=DARK)
        d.polygon([(px + 16, sb[1] + 1), (px + 22, y0 + 46), (px + 28, sb[1] + 1)], fill=LIGHT)
        d.line([(px + 16, sb[1]), (px + 22, y0 + 46), (px + 28, sb[1])], fill=DARK)
        for i, line in enumerate(wrap(d, self.msg, sb[2] - sb[0] - 10)[:3]):
            d.text((px + 5, sb[1] + 4 + i * 14), line, font=F_UI, fill=DARK)
        self.score_box = (px, y0 + 110, x1 - 4, y0 + 140)
        cw = (self.score_box[2] - px) / 3
        for i, (label, k) in enumerate((("You", "you"), ("Robot", "robot"), ("Draw", "draw"))):
            bx = px + i * cw
            center_text(d, (bx, y0 + 110, bx + cw, y0 + 122), label, F_UI, SHADOW)
            center_text(d, (bx, y0 + 123, bx + cw, y0 + 139), str(self.score.get(k, 0)), F_BIG, DARK)
        self.new_box = (px, y0 + 146, px + 62, y0 + 174)
        self.lvl_box = (px + 66, y0 + 146, x1 - 4, y0 + 174)
        button(d, self.new_box, "New game", font=F_BOLD)
        button(d, self.lvl_box, self.LEVELS[self.level], font=F_BOLD)

    def panel_click(self, x, y):
        if inside(self.new_box, x, y):
            self.new_game()
            return True
        if inside(self.lvl_box, x, y):
            self.level = (self.level + 1) % len(self.LEVELS)
            self.cfg()[self.KEY + "_level"] = self.level
            self.save()
            self.say(f"OK, I'll play {self.LEVELS[self.level].lower()}." + (" Takes effect next game." if self.playing() else ""))
            return True
        return False

    def playing(self):
        return not self.over

    def context(self, x, y):
        if inside(getattr(self, "score_box", (0, 0, -1, -1)), x, y):
            self.d.confirm("Reset the score?", "Sets You, Robot and Draw to 0.", self.reset_score)

    def reset_score(self):
        self.score.update({"you": 0, "robot": 0, "draw": 0})
        self.save()


# ---------------------------------------------------------------- Tic-Tac-Toe
LINES = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]
_TTT = {}


def ttt_winner(b):
    for line in LINES:
        a, m, c = line
        if b[a] and b[a] == b[m] == b[c]:
            return b[a], line
    if all(b):
        return "draw", None
    return None, None


def ttt_value(b, me):
    """Best result for the player to move: > 0 win (sooner = bigger), 0 draw, < 0 loss."""
    key = (b, me)
    if key in _TTT:
        return _TTT[key]
    w, _ = ttt_winner(b)
    if w == "draw":
        v = 0
    elif w:
        v = -(1 + b.count(""))                 # the other player just won
    else:
        other = "O" if me == "X" else "X"
        v = max(-ttt_value(b[:i] + (me,) + b[i + 1:], other) for i in range(9) if not b[i])
    _TTT[key] = v
    return v


class TicTacToe(VersusApp):
    """You are X, the robot is O. Who goes first swaps every game."""
    name, title, icon = "tictactoe", "Tic-Tac-Toe", "tictactoe"
    short = "TicTacToe"
    KEY = "ttt"
    BOARD = 168
    MISTAKES = (0.45, 0.12, 0.0)             # chance the robot plays a random square, per level

    def __init__(self, desk):
        super().__init__(desk)
        self.cells = []
        self.new_game()

    def new_game(self):
        self.b = ("",) * 9
        self.over, self.line = None, None
        self.robot_at = 0
        first, self.you_first = self.you_first, not self.you_first
        if first:
            self.say("Your turn! You are X.", "ok")
        else:
            self.say("I'll go first this time...", "think")
            self.robot_at = time.time() + 0.9

    def playing(self):
        return not self.over and any(self.b)

    def put(self, i, who):
        self.b = self.b[:i] + (who,) + self.b[i + 1:]
        w, self.line = ttt_winner(self.b)
        if w == "X":
            self.finish("you", random.choice(["You win! Well played.", "You got me! Good game."]))
        elif w == "O":
            self.finish("robot", random.choice(["Three in a row. I win!", "I win this one!"]))
        elif w == "draw":
            self.finish("draw", random.choice(["A draw. Nobody wins!", "Tie game. You're good at this."]))
        return w

    def robot_move(self):
        free = [i for i in range(9) if not self.b[i]]
        if random.random() < self.MISTAKES[self.level]:
            i = random.choice(free)
        else:
            scored = [(-ttt_value(self.b[:i] + ("O",) + self.b[i + 1:], "X"), i) for i in free]
            best = max(s for s, _ in scored)
            i = random.choice([i for s, i in scored if s == best])
        if not self.put(i, "O"):
            self.say(random.choice(["Your turn.", "Your move!", "Beat that!", "Hmm, what will you do?"]), "ok")

    def tap(self, i):
        if self.over:
            self.d.toast("tap New game to play again")
        elif self.robot_at:
            self.d.toast("wait, it's my turn")
        elif self.b[i]:
            pass
        elif not self.put(i, "X"):
            self.say(random.choice(["Hmm...", "Let me think...", "Ooh, interesting.", "Thinking..."]), "think")
            self.robot_at = time.time() + random.uniform(0.6, 1.1)

    def tick(self):
        if self.robot_at and time.time() >= self.robot_at:
            self.robot_at = 0
            self.robot_move()
            return True
        return False

    def demo(self, steps=0):
        self.demo_mode = True
        self.b, self.robot_at = ("",) * 9, 0
        self.say("Watch me play against myself!", "think")
        for _ in range(steps):
            self.demo_step()

    def demo_step(self):
        if self.over:
            return False
        who = "X" if self.b.count("X") <= self.b.count("O") else "O"
        other = "O" if who == "X" else "X"
        free = [i for i in range(9) if not self.b[i]]
        if who == "X" and random.random() < 0.3:
            i = random.choice(free)
        else:
            scored = [(-ttt_value(self.b[:i] + (who,) + self.b[i + 1:], other), i) for i in free]
            best = max(v for v, _ in scored)
            i = random.choice([i for v, i in scored if v == best])
        if not self.put(i, who):
            self.say("Hmm..." if who == "X" else "Your move, me.", "think" if who == "X" else "ok")
        return not self.over

    def draw(self, img, d, box):
        x0, y0, x1, y1 = box
        d.rectangle(box, fill=FACE)
        s = self.BOARD // 3
        bx, by = x0 + 6, y0 + (y1 - y0 - self.BOARD) // 2
        bevel(d, (bx - 2, by - 2, bx + self.BOARD + 1, by + self.BOARD + 1), raised=False, fill=LIGHT)
        for k in (1, 2):
            d.line([(bx + k * s, by + 6), (bx + k * s, by + self.BOARD - 6)], fill=SHADOW, width=3)
            d.line([(bx + 6, by + k * s), (bx + self.BOARD - 6, by + k * s)], fill=SHADOW, width=3)
        self.cells = []
        for i in range(9):
            cx, cy = bx + (i % 3) * s, by + (i // 3) * s
            self.cells.append((cx, cy, cx + s - 1, cy + s - 1))
            m, v = 13, self.b[i]
            if v == "X":
                d.line([(cx + m, cy + m), (cx + s - m, cy + s - m)], fill=(210, 30, 30), width=7)
                d.line([(cx + m, cy + s - m), (cx + s - m, cy + m)], fill=(210, 30, 30), width=7)
            elif v == "O":
                d.ellipse((cx + m, cy + m, cx + s - m, cy + s - m), outline=(30, 90, 200), width=7)
        if self.line:
            a, c = self.cells[self.line[0]], self.cells[self.line[2]]
            d.line([((a[0] + a[2]) / 2, (a[1] + a[3]) / 2), ((c[0] + c[2]) / 2, (c[1] + c[3]) / 2)], fill=GREEN, width=5)
        self.draw_panel(d, box)

    def click(self, x, y):
        if self.panel_click(x, y):
            return
        for i, c in enumerate(self.cells):
            if inside(c, x, y):
                self.tap(i)
                return

    def key(self, k):
        if k in "123456789" and len(k) == 1:
            self.tap(int(k) - 1)
        elif k in ("n", "N"):
            self.new_game()


# ---------------------------------------------------------------- Checkers (English draughts rules)
# Board: 64 squares, index = row * 8 + col, row 0 at the top. You (+1, red) start at the bottom and move up,
# the robot (-1, blue) starts at the top. 2 / -2 = kings. Jumping is compulsory and multi-jumps must be finished;
# a man that reaches the far row becomes a king and its move ends.
MAN_DIRS = {1: ((-1, -1), (-1, 1)), -1: ((1, -1), (1, 1))}
KING_DIRS = ((-1, -1), (-1, 1), (1, -1), (1, 1))


class _Timeout(Exception):
    pass


def ck_start():
    b = [0] * 64
    for r in range(8):
        for c in range(8):
            if (r + c) % 2 == 1:
                b[r * 8 + c] = -1 if r < 3 else (1 if r > 4 else 0)
    return b


def _jumps(b, idx, p, dirs, path, caps, out):
    r, c = divmod(idx, 8)
    found = False
    for dr, dc in dirs:
        lr, lc = r + 2 * dr, c + 2 * dc
        if 0 <= lr < 8 and 0 <= lc < 8:
            m, land = (r + dr) * 8 + c + dc, lr * 8 + lc
            if b[m] * p < 0 and m not in caps and b[land] == 0:
                found = True
                if abs(p) == 1 and lr == (0 if p > 0 else 7):
                    out.append((path + (land,), caps + (m,)))           # crowned: the move ends here
                else:
                    _jumps(b, land, p, dirs, path + (land,), caps + (m,), out)
    if not found and len(path) > 1:
        out.append((path, caps))


def ck_moves(b, side):
    """All legal moves for side as (path, captured) tuples. Jumps only, if any jump exists."""
    jumps, steps = [], []
    for idx in range(64):
        p = b[idx]
        if p * side <= 0:
            continue
        dirs = KING_DIRS if abs(p) == 2 else MAN_DIRS[side]
        r, c = divmod(idx, 8)
        for dr, dc in dirs:
            rr, cc = r + dr, c + dc
            if 0 <= rr < 8 and 0 <= cc < 8:
                m = rr * 8 + cc
                if b[m] * p < 0:
                    lr, lc = rr + dr, cc + dc
                    if 0 <= lr < 8 and 0 <= lc < 8 and b[lr * 8 + lc] == 0:
                        b2 = b[:]
                        b2[idx] = 0
                        _jumps(b2, idx, p, dirs, (idx,), (), jumps)
                        break
                elif b[m] == 0 and not jumps:
                    steps.append(((idx, m), ()))
    if jumps:
        return list(dict.fromkeys(jumps))
    return steps


def ck_apply(b, mv):
    path, caps = mv
    nb = b[:]
    p = nb[path[0]]
    nb[path[0]] = 0
    for m in caps:
        nb[m] = 0
    end = path[-1]
    if p == 1 and end < 8:
        p = 2
    elif p == -1 and end >= 56:
        p = -2
    nb[end] = p
    return nb


def ck_eval(b, side):
    s = 0
    for i, p in enumerate(b):
        if not p:
            continue
        r, c = divmod(i, 8)
        center = 4 if 2 <= c <= 5 and 2 <= r <= 5 else 0
        if p == 1:
            s += 100 + (7 - r) * 3 + (8 if r == 7 else 0) + center
        elif p == -1:
            s -= 100 + r * 3 + (8 if r == 0 else 0) + center
        elif p == 2:
            s += 170 + center
        else:
            s -= 170 + center
    return s * side


def ck_search(b, side, depth, alpha, beta, deadline, ply=0):
    if ply % 4 == 0 and time.time() > deadline:
        raise _Timeout
    ms = ck_moves(b, side)
    if not ms:
        return -10000 + ply                      # no moves = lost (later is less bad)
    if depth <= 0 and (not ms[0][1] or depth < -6):
        return ck_eval(b, side)
    best = -100000
    ms.sort(key=lambda m: -len(m[1]))
    for mv in ms:
        v = -ck_search(ck_apply(b, mv), -side, depth - 1, -beta, -alpha, deadline, ply + 1)
        if v > best:
            best = v
            if v > alpha:
                alpha = v
                if alpha >= beta:
                    break
    return best


def ck_best(b, side, max_depth, seconds):
    """Iterative deepening; returns the best move found before the time runs out."""
    ms = ck_moves(b, side)
    if len(ms) <= 1:
        return ms[0] if ms else None
    random.shuffle(ms)
    deadline = time.time() + seconds
    best = ms[0]
    for depth in range(1, max_depth + 1):
        try:
            scored = []
            alpha = -100000
            for mv in ms:
                v = -ck_search(ck_apply(b, mv), -side, depth - 1, -100000, -alpha, deadline, 1)
                scored.append((v, mv))
                alpha = max(alpha, v)
        except _Timeout:
            break
        scored.sort(key=lambda t: -t[0])
        best = scored[0][1]
        ms = [mv for _, mv in scored]           # search the best moves first next round
        if scored[0][0] > 9000:
            break                                # found a forced win
    return best


class Checkers(VersusApp):
    """You are red (bottom), the robot is blue (top). Tap a piece, then where it goes. Jumps are compulsory."""
    name, title, icon = "checkers", "Checkers", "checkers"
    KEY = "checkers"
    BOARD = 176
    # level: (search depth, seconds, chance of a random move)
    BRAINS = ((2, 0.5, 0.25), (4, 1.5, 0.0), (9, 3.0, 0.0))
    LIGHT_SQ, DARK_SQ = (236, 216, 176), (120, 80, 50)

    def __init__(self, desk):
        super().__init__(desk)
        self.new_game()

    def new_game(self):
        self.b = ck_start()
        self.over = None
        self.sel = []
        self.last = None
        self.quiet = 0                     # plies without a capture or a man moving (80 = draw)
        self.thinking = False
        self.result, self.ready_at = None, 0
        self.press_sq = None
        self.gen = getattr(self, "gen", 0) + 1
        first, self.you_first = self.you_first, not self.you_first
        self.turn = 1 if first else -1
        self.legal = ck_moves(self.b, 1)
        if first:
            self.say("You are red and go first. Tap a piece, then a square.", "ok")
        else:
            self.say("I'll go first this time.", "think")
            self.start_robot()

    def playing(self):
        return not self.over and self.b != ck_start()

    def count(self, side):
        return sum(1 for p in self.b if p * side > 0)

    # ---- moves
    def do_move(self, mv, who):
        p = self.b[mv[0][0]]
        crowned = abs(p) == 1 and (mv[0][-1] < 8 if p > 0 else mv[0][-1] >= 56)
        self.quiet = 0 if (mv[1] or abs(p) == 1) else self.quiet + 1
        self.b = ck_apply(self.b, mv)
        self.last = (who, mv)
        self.sel = []
        self.turn = -who
        if not ck_moves(self.b, self.turn):
            if self.turn == -1:
                self.finish("you", random.choice(["You win! I have no moves left.", "You beat me! Good game."]))
            else:
                self.finish("robot", random.choice(["I win! You have no moves left.", "Game over. I win this one!"]))
            return
        if self.quiet >= 80:
            self.finish("draw", "Forty moves without a capture. It's a draw!")
            return
        if self.demo_mode:
            if mv[1]:
                self.say("Gotcha!" if len(mv[1]) == 1 else ("Double jump!" if len(mv[1]) == 2 else f"{len(mv[1])} in one go!"), "happy")
            else:
                self.say("King me!" if crowned else random.choice(["Hmm...", "Your move, me.", "Thinking..."]), "happy" if crowned else "think")
            self.legal = ck_moves(self.b, 1)
            return
        if who == 1:
            if crowned:
                self.say("A king! Nice.", "sad")
            elif mv[1]:
                self.say("Ouch!" if len(mv[1]) == 1 else f"Ouch, {len(mv[1])} pieces!", "sad")
            else:
                self.say(random.choice(["Hmm...", "Let me think...", "Thinking...", "Interesting."]), "think")
            self.start_robot()
        else:
            if crowned:
                self.say("King me! Your move.", "happy")
            elif mv[1]:
                self.say("Gotcha!" if len(mv[1]) == 1 else "Double jump!", "happy")
            else:
                self.say(random.choice(["Your move.", "Your turn!", "Go ahead.", "Try this one."]), "ok")
            self.legal = ck_moves(self.b, 1)
            if self.legal and self.legal[0][1]:
                self.msg += " You must jump!"

    def demo(self, steps=0):
        self.demo_mode = True
        self.say("Watch me play checkers against myself!", "think")
        for _ in range(steps):
            if not self.demo_step():
                break

    def demo_step(self):
        if self.over:
            return False
        ms = ck_moves(self.b, self.turn)
        if not ms:
            return False
        mv = random.choice(ms) if random.random() < 0.15 else ck_best(self.b, self.turn, 2, 0.3)
        self.do_move(mv, self.turn)
        return not self.over

    def start_robot(self):
        self.thinking, self.result = True, None
        self.ready_at = time.time() + 0.8
        depth, secs, rnd = self.BRAINS[self.level]
        b, gen = self.b[:], self.gen

        def work():
            if random.random() < rnd:
                ms = ck_moves(b, -1)
                mv = random.choice(ms) if ms else None
            else:
                mv = ck_best(b, -1, depth, secs)
            if gen == self.gen:
                self.result = mv

        threading.Thread(target=work, daemon=True, name="checkers-ai").start()

    def tick(self):
        if self.thinking and self.result and time.time() >= self.ready_at:
            mv, self.result, self.thinking = self.result, None, False
            self.do_move(mv, -1)
            return True
        return False

    # ---- your taps
    def options(self):
        n = len(self.sel)
        return [m for m in self.legal if m[0][:n] == tuple(self.sel) and len(m[0]) > n]

    def tap(self, sq):
        if self.over:
            self.d.toast("tap New game to play again")
            return
        if self.turn != 1:
            self.d.toast("wait, I'm thinking")
            return
        if self.sel:
            nxt = [m for m in self.options() if m[0][len(self.sel)] == sq]
            if nxt:
                self.sel.append(sq)
                done = [m for m in nxt if len(m[0]) == len(self.sel)]
                if done:
                    self.do_move(done[0], 1)
                return
            if len(self.sel) > 1:
                self.d.toast("finish the jump!")
                return
            if sq == self.sel[0]:
                self.sel = []
                return
        if any(m[0][0] == sq for m in self.legal):
            self.sel = [sq]
        elif self.b[sq] > 0:
            self.d.toast("you must jump!" if self.legal and self.legal[0][1] else "that piece can't move")
            self.sel = []

    def sq_at(self, x, y):
        if not hasattr(self, "bx"):
            return None
        c, r = (x - self.bx) // self.cs, (y - self.by) // self.cs
        if 0 <= c < 8 and 0 <= r < 8:
            return int(r * 8 + c)
        return None

    def press(self, x, y):
        self.press_sq, self.dragged = self.sq_at(x, y), False

    def drag(self, x, y, dx, dy):
        self.dragged = True
        return True

    def release(self, x, y):
        """Dragging a piece onto its square works too (a plain tap is handled by click)."""
        a, self.press_sq = self.press_sq, None
        if a is None or not self.dragged:
            return
        b = self.sq_at(x, y)
        if b is not None and b != a:
            if not self.sel or self.sel[-1] != a:
                self.tap(a)
            if self.sel and self.sel[-1] == a:
                self.tap(b)

    def click(self, x, y):
        if self.panel_click(x, y):
            return
        sq = self.sq_at(x, y)
        if sq is not None:
            self.tap(sq)

    def key(self, k):
        if k in ("n", "N"):
            self.new_game()

    # ---- drawing
    def shown_board(self):
        """The board with your half-finished multi-jump applied, so you can see where the piece is."""
        if len(self.sel) < 2:
            return self.b
        opts = self.options() or [m for m in self.legal if m[0][:len(self.sel)] == tuple(self.sel)]
        if not opts:
            return self.b
        k = len(self.sel) - 1
        return ck_apply(self.b, (tuple(self.sel), opts[0][1][:k]))

    def draw(self, img, d, box):
        x0, y0, x1, y1 = box
        d.rectangle(box, fill=FACE)
        self.cs = s = self.BOARD // 8
        self.bx, self.by = x0 + 5, y0 + (y1 - y0 - 8 * s) // 2
        bx, by = self.bx, self.by
        d.rectangle((bx - 2, by - 2, bx + 8 * s + 1, by + 8 * s + 1), outline=DARK, width=2)
        b = self.shown_board()
        marks = {}
        if self.last:
            who, mv = self.last
            for q in mv[0]:
                marks[q] = (150, 110, 70)
            for q in mv[1]:
                marks[q] = (160, 70, 60)
        for idx in range(64):
            r, c = divmod(idx, 8)
            x, y = bx + c * s, by + r * s
            col = self.DARK_SQ if (r + c) % 2 else self.LIGHT_SQ
            if (r + c) % 2 and idx in marks:
                col = marks[idx]
            d.rectangle((x, y, x + s - 1, y + s - 1), fill=col)
        must = self.turn == 1 and not self.over and not self.sel and self.legal and self.legal[0][1]
        movable = {m[0][0] for m in self.legal} if must else set()
        for idx, p in enumerate(b):
            if not p:
                continue
            r, c = divmod(idx, 8)
            x, y = bx + c * s, by + r * s
            main, rim = ((215, 40, 40), (120, 10, 10)) if p > 0 else ((60, 130, 220), (10, 40, 110))
            d.ellipse((x + 3, y + 4, x + s - 3, y + s - 2), fill=rim)
            d.ellipse((x + 3, y + 2, x + s - 3, y + s - 4), fill=main, outline=rim)
            d.ellipse((x + 7, y + 6, x + s - 7, y + s - 8), outline=rim)
            if abs(p) == 2:
                cx, cy = x + s / 2, y + s / 2 - 1
                d.polygon([(cx - 5, cy + 3), (cx - 6, cy - 3), (cx - 2.5, cy), (cx, cy - 5), (cx + 2.5, cy),
                           (cx + 6, cy - 3), (cx + 5, cy + 3)], fill=YELLOW, outline=(120, 90, 0))
            if idx in movable:
                d.ellipse((x + 1, y + 1, x + s - 2, y + s - 2), outline=YELLOW, width=2)
        if self.sel:
            q = self.sel[-1]
            r, c = divmod(q, 8)
            d.rectangle((bx + c * s, by + r * s, bx + c * s + s - 1, by + r * s + s - 1), outline=YELLOW, width=2)
            for m in self.options():
                q = m[0][len(self.sel)]
                r, c = divmod(q, 8)
                cx, cy = bx + c * s + s / 2, by + r * s + s / 2
                d.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=(80, 230, 80), outline=DARK)
        self.draw_panel(d, box)
        px = x0 + self.BOARD + 10
        # remaining pieces under the level line
        d.ellipse((px + 50, y0 + 41, px + 58, y0 + 49), fill=(215, 40, 40))
        d.text((px + 61, y0 + 39), str(self.count(1)), font=F_UI, fill=DARK)
        d.ellipse((px + 80, y0 + 41, px + 88, y0 + 49), fill=(60, 130, 220))
        d.text((px + 91, y0 + 39), str(self.count(-1)), font=F_UI, fill=DARK)
