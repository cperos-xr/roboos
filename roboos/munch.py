"""RoboMunch: a small maze-chase game for RoboOS (dots, power pellets, four ghosts, three lives).

- Swipe or tap a direction (the robot's yellow mouth turns that way), or use the arrow keys / WASD.
  Tap, swipe or press an arrow to start. Tap or press N for a new game when it is over.
- Eat every dot to go to the next level (the ghosts get faster). A power pellet makes the ghosts blue for a few
  seconds: eat them for 200, 400, 800... points.
- The best score is kept in data/config.json under "desktop" as munch_best.
"""
import random
import time

from PIL import Image, ImageDraw

from .desktop import DARK, F_BOLD, F_UI, FACE, LIGHT, NAVY, YELLOW, bevel
from .games import GameApp, center_text, talk

# '#' wall, '.' dot, 'o' power pellet, anything else = empty path. Row 7 is a tunnel: it wraps left/right.
MAZE = [
    "########################",
    "#o.........##.........o#",
    "#.####.###.##.###.####.#",
    "#.####.###.##.###.####.#",
    "#......................#",
    "#.####.#.######.#.####.#",
    "#.####.#.######.#.####.#",
    "......#..........#......",
    "#.####.#........#.####.#",
    "#......#.######.#......#",
    "#o.........##.........o#",
    "#.####.###.##.###.####.#",
    "#.####.###.##.###.####.#",
    "########################",
]
COLS, ROWS = 24, 14
T = 11                              # tile size in pixels
BW, BH = COLS * T, ROWS * T         # board: 264 x 154
START_PAC = (12, 4)
GHOST_HOME = [(9, 7), (12, 7), (15, 7), (12, 8)]
GHOST_COLORS = [(255, 0, 0), (255, 184, 255), (0, 255, 255), (255, 184, 82)]
DIRS = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}
KEYDIR = {"w": (0, -1), "s": (0, 1), "a": (-1, 0), "d": (1, 0)}
ANGLE = {(1, 0): 0, (0, 1): 90, (-1, 0): 180, (0, -1): 270}     # PIL angles: 0 = right, clockwise
WALL = (30, 60, 220)
DOT = (255, 210, 170)
FRIGHT = (40, 40, 255)


def dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def draw_pac(d, cx, cy, ang, mouth_open, r=5):
    m = 38 if mouth_open else 4
    d.pieslice((cx - r, cy - r, cx + r, cy + r), (ang + m) % 360, (ang - m) % 360, fill=YELLOW)


def draw_ghost(d, cx, cy, col, fright, look=(1, 0), r=5):
    d.pieslice((cx - r, cy - r, cx + r, cy + r), 180, 360, fill=col)
    d.rectangle((cx - r, cy, cx + r, cy + r - 1), fill=col)
    if fright:
        for ex in (-2, 2):
            d.rectangle((cx + ex - 1, cy - 2, cx + ex, cy - 1), fill=LIGHT)
        return
    for ex in (-2, 2):
        d.rectangle((cx + ex - 1, cy - 3, cx + ex, cy - 2), fill=LIGHT)
        d.point((cx + ex + look[0], cy - 2 + look[1]), fill=NAVY)


class Munch(GameApp):
    """Eat all the dots and dodge the ghosts. Tap or swipe to steer the robot's yellow mouth."""
    name, title, icon = "munch", "RoboMunch", "munch"
    short = "Munch"
    menu = False                  # from the Games launcher (and desktop icons), not the Start menu
    fast = True                   # the desktop calls tick() often, so the steps happen on time
    video = True                  # moving pictures: smaller frames, sent more often
    long_press = None             # a hold does nothing (no context menu on the board)

    def __init__(self, desk):
        super().__init__(desk)
        self.box = (0, 16, 319, 197)
        self.p0 = None
        self.new_game()

    # ---- setup and rules
    def new_game(self):
        self.score, self.lives, self.level = 0, 3, 1
        self.over, self.over_t = False, 0
        self.build_level()

    def build_level(self):
        self.walls = Image.new("RGB", (BW, BH), DARK)
        wd = ImageDraw.Draw(self.walls)
        self.dots, self.pellets = set(), set()
        wall = lambda x, y: not (0 <= x < COLS and 0 <= y < ROWS) or MAZE[y][x] == "#"    # outside counts as wall
        for y, row in enumerate(MAZE):
            for x, ch in enumerate(row):
                if ch == "#":
                    # hollow walls: blue only along the edges that face a path, dark inside
                    X, Y, e = x * T, y * T, 2
                    wd.rectangle((X, Y, X + T - 1, Y + T - 1), fill=WALL)
                    wd.rectangle((X if wall(x - 1, y) else X + e, Y if wall(x, y - 1) else Y + e,
                                  X + T - 1 if wall(x + 1, y) else X + T - 1 - e,
                                  Y + T - 1 if wall(x, y + 1) else Y + T - 1 - e), fill=DARK)
                    for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):          # inside corners keep their edge
                        if wall(x + sx, y) and wall(x, y + sy) and not wall(x + sx, y + sy):
                            cx = X if sx < 0 else X + T - e
                            cy = Y if sy < 0 else Y + T - e
                            wd.rectangle((cx, cy, cx + e - 1, cy + e - 1), fill=WALL)
                elif ch == ".":
                    self.dots.add((x, y))
                elif ch == "o":
                    self.pellets.add((x, y))
        self.dots.discard(START_PAC)
        self.reset_actors()

    def reset_actors(self):
        now = time.time()
        self.pac, self.pdir, self.want = START_PAC, (0, 0), (0, 0)
        self.mouth = False
        self.ghosts = [{"pos": h, "home": h, "dir": (-1, 0), "fright": False, "color": GHOST_COLORS[i]}
                       for i, h in enumerate(GHOST_HOME)]
        self.started = False          # waits for the first tap / key of each life
        self.paused = False           # stopped because the window was covered or the screen left RoboOS
        self.wait = 0                 # a short pause after a lost life or a new level (time.time() value)
        self.power_end, self.power_left, self.eaten = 0, 0, 0
        self.t_pac = self.t_gh = self.t_tick = now

    def passable(self, x, y):
        return 0 <= y < ROWS and MAZE[y][x % COLS] != "#"

    def step_to(self, p, dv):
        return ((p[0] + dv[0]) % COLS, p[1] + dv[1])

    def speeds(self):
        lv = self.level - 1
        return max(0.085, 0.14 - 0.008 * lv), max(0.12, 0.18 - 0.008 * lv)     # seconds per step: robot, ghosts

    def step_pac(self, now):
        if self.want != (0, 0) and self.passable(*self.step_to(self.pac, self.want)):
            self.pdir = self.want
        nxt = self.step_to(self.pac, self.pdir)
        if self.pdir != (0, 0) and self.passable(*nxt):
            self.pac = nxt
            self.mouth = not self.mouth
        if self.pac in self.dots:
            self.dots.discard(self.pac)
            self.score += 10
        elif self.pac in self.pellets:
            self.pellets.discard(self.pac)
            self.score += 50
            self.power_end = now + max(2.5, 7 - 0.7 * (self.level - 1))
            self.eaten = 0
            for g in self.ghosts:
                g["fright"] = True
        if not self.dots and not self.pellets:
            self.level_clear(now)
            return
        self.check_ghosts(now)

    def step_ghosts(self, now):
        for g in self.ghosts:
            opts = [dv for dv in ANGLE if self.passable(*self.step_to(g["pos"], dv))]
            back = (-g["dir"][0], -g["dir"][1])
            fwd = [dv for dv in opts if dv != back] or opts
            if g["fright"]:
                if random.random() < 0.4:          # frightened ghosts are slower
                    continue
                g["dir"] = random.choice(fwd)
            elif random.random() < 0.75:           # most ghosts chase the robot
                g["dir"] = min(fwd, key=lambda dv: dist(self.step_to(g["pos"], dv), self.pac))
            else:
                g["dir"] = random.choice(fwd)
            g["pos"] = self.step_to(g["pos"], g["dir"])
        self.check_ghosts(now)

    def check_ghosts(self, now):
        for g in self.ghosts:
            if g["pos"] != self.pac:
                continue
            if g["fright"]:
                pts = 200 * 2 ** self.eaten
                self.eaten += 1
                self.score += pts
                g["pos"], g["fright"], g["dir"] = g["home"], False, (-1, 0)
                self.d.toast(f"+{pts}", 1)
            else:
                self.lose_life(now)
                return

    def lose_life(self, now):
        self.lives -= 1
        if self.lives <= 0:
            self.over, self.started, self.over_t = True, False, now
            record = self.score > self.cfg().get("munch_best", 0)
            self.save_best()
            self.d.toast("Game over. Tap to play again.", 4)
            if not self.demo_mode:
                talk(self, f"New record, {self.score} points!" if record else f"Game over. {self.score} points.",
                     "happy" if record else "sad")
                if record or self.score >= 1000:
                    self.d.game_moment(self, f"Someone played RoboMunch on my screen and scored {self.score} points"
                                             + (" - a new record!" if record else "."), score=2 if record else 1)
            return
        self.reset_actors()
        self.wait = now + 1.5
        self.d.toast("Ouch! Tap to go on.", 2)

    def level_clear(self, now):
        self.level += 1
        self.build_level()
        self.wait = now + 2
        self.d.toast(f"Level {self.level}!", 3)

    def save_best(self):
        c = self.cfg()
        if self.score > c.get("munch_best", 0):
            c["munch_best"] = self.score
            self.save()

    def go(self, dv):
        """Steer the robot. The first steer of each life (or after a pause) starts the game."""
        if self.over or self.wait:
            return
        self.want = dv
        if not self.started:
            now = time.time()
            self.started, self.paused = True, False
            self.t_pac = self.t_gh = self.t_tick = now
            if self.power_left:                          # blue ghosts keep the time they had left
                self.power_end, self.power_left = now + self.power_left, 0

    def pause(self, now):
        self.started, self.paused = False, True
        if self.power_end:
            self.power_left, self.power_end = max(0.5, self.power_end - now), 0

    def close(self):
        self.save_best()                                 # leaving mid-game still counts for the best score

    def tick(self):
        if self.over:
            return False
        now = time.time()
        gap, self.t_tick = now - self.t_tick, now
        focused = getattr(self.d, "focused", lambda: self)
        if self.started and (focused() is not self or gap > 1.0):
            self.pause(now)                              # covered, minimized, or the screen showed something else
            return True
        if self.wait:
            if now < self.wait:
                return False
            self.wait = 0
            return True
        if not self.started:
            return False
        changed = False
        if self.power_end and now > self.power_end:
            self.power_end = 0
            for g in self.ghosts:
                g["fright"] = False
            changed = True
        pac_dt, gh_dt = self.speeds()
        if now - self.t_pac >= pac_dt:
            self.t_pac = now
            self.step_pac(now)
            changed = True
        if self.wait or self.over:
            return True
        if now - self.t_gh >= gh_dt:
            self.t_gh = now
            self.step_ghosts(now)
            changed = True
        return changed

    # ---- drawing
    def origin(self):
        x0, y0, x1, _ = self.box
        return x0 + (x1 - x0 - BW) // 2, y0 + 20

    def centre(self, p):
        bx, by = self.origin()
        return bx + p[0] * T + T // 2, by + p[1] * T + T // 2

    def draw(self, img, d, box):
        self.box = box
        x0, y0, x1, y1 = box
        d.rectangle(box, fill=DARK)
        d.text((x0 + 6, y0 + 3), f"Score {self.score}", font=F_BOLD, fill=YELLOW)
        best = max(self.cfg().get("munch_best", 0), self.score)
        center_text(d, (x0 + 90, y0 + 1, x1 - 90, y0 + 17), f"Level {self.level}   best {best}", F_UI, LIGHT)
        for i in range(self.lives):
            draw_pac(d, x1 - 12 - i * 14, y0 + 9, 0, True, r=4)
        bx, by = self.origin()
        img.paste(self.walls, (bx, by))
        for x, y in self.dots:
            cx, cy = self.centre((x, y))
            d.rectangle((cx - 1, cy - 1, cx, cy), fill=DOT)
        for x, y in self.pellets:
            cx, cy = self.centre((x, y))
            d.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=DOT)
        now = time.time()
        for g in self.ghosts:
            cx, cy = self.centre(g["pos"])
            col = g["color"]
            if g["fright"]:
                col = (235, 235, 235) if self.power_end - now < 2 and int(now * 4) % 2 else FRIGHT
            draw_ghost(d, cx, cy, col, g["fright"], look=g["dir"])
        cx, cy = self.centre(self.pac)
        draw_pac(d, cx, cy, ANGLE.get(self.pdir, 0), self.mouth or not self.started)
        if self.over:
            self.overlay(d, "Game over", "tap to play again")
        elif self.wait:
            self.overlay(d, "Get ready!", "")
        elif self.paused:
            self.overlay(d, "Paused", "tap or swipe to go on")
        elif not self.started:
            self.overlay(d, "Tap or swipe to start", "arrows / WASD work too")

    def overlay(self, d, line1, line2):
        bx, by = self.origin()
        cx, cy = bx + BW // 2, by + BH - 26             # low on the board: the ghosts' start stays visible
        box = (cx - 74, cy - 17, cx + 74, cy + 17)
        bevel(d, box, fill=FACE)
        center_text(d, (box[0], box[1] + 2, box[2], box[1] + 16), line1, F_BOLD, NAVY)
        if line2:
            center_text(d, (box[0], box[1] + 17, box[2], box[1] + 31), line2, F_UI, DARK)

    # ---- input
    def press(self, x, y):
        self.p0 = (x, y)

    def release(self, x, y):
        """A swipe steers along its longer side; a tap steers towards the tap from the robot."""
        p, self.p0 = self.p0, None
        if self.over:
            if time.time() - self.over_t > 1.2:          # not the steering tap that was already on its way
                self.new_game()
            return
        if self.wait:
            return
        dx, dy = (x - p[0], y - p[1]) if p else (0, 0)
        if max(abs(dx), abs(dy)) < 10:                  # a tap, not a swipe
            cx, cy = self.centre(self.pac)
            dx, dy = x - cx, y - cy
        if max(abs(dx), abs(dy)) < 4:
            return
        if abs(dx) >= abs(dy):
            self.go((1 if dx > 0 else -1, 0))
        else:
            self.go((0, 1 if dy > 0 else -1))

    def key(self, k):
        dv = DIRS.get(k) or KEYDIR.get(k.lower() if len(k) == 1 else k)
        if dv:
            self.go(dv)
        elif k in (" ", "ENTER"):
            if self.over:
                if time.time() - self.over_t > 1.2:
                    self.new_game()
            else:
                self.go(self.pdir if self.pdir != (0, 0) else (1, 0))
        elif k in ("n", "N"):
            self.new_game()
