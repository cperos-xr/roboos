"""Pip plays / Pip watches: the robot's AI plays a Game Boy game, or sits next to you and watches you play
(RoboOS Game Boy app, "Play" and "Watch" buttons top right).

- Every few seconds it looks at the screen (and, for games it knows, reads a few facts straight from the game's
  memory: much more reliable than the picture), asks an AI model what to do, and presses up to 8 buttons.
- Model: config desktop.gb_ai_model if set, else the robot's active OpenRouter model (a free vision model is fine:
  free models are rate-limited, so a step can take a while or be skipped). It needs a model that can see pictures.
  Another OpenAI-compatible service works too: desktop.gb_ai_url (its chat/completions address) and
  desktop.gb_ai_key_env (the name of the key in data/secrets.env, e.g. FASTMETAL_API_KEY) plus desktop.gb_ai_model.
- It sees two pictures: the screen before its last moves and the screen now, plus a plain "nothing changed" line
  when its moves did nothing (where it wasted the most steps before).
- It keeps short notes per game (data/gameboy/ai/<game>.json: goals, what it learned) and its last few moves.
- A daily step limit (config desktop.gb_ai_steps_day, default 300) keeps paid models cheap; free models cost nothing.
- Touching any Game Boy button or key takes over: the AI pauses until you tap "Play" again.

Watch mode (the friend on the couch): Pip presses nothing. Every desktop.gb_watch_interval seconds (default 20) it looks
at the screen then and now plus the buttons YOU pressed in between, and may say a short remark out loud (Settings >
Sound > Talk during games; at most one every 25 s), give a tip when it is confident one helps, or write a note about how
the game works. Those notes go in the same per-game file Play mode reads, marked "(watched)": Pip learns from watching you.
A look is skipped (not counted) when you pressed nothing and the screen did not change, so a paused or idle game is free.
"""
import base64
import io
import json
import os
import re
import threading
import time

import requests
from PIL import Image

from .paths import DATA
from .gameboy import BUTTONS

AI_DIR = DATA / "gameboy" / "ai"
URL = "https://openrouter.ai/api/v1/chat/completions"
PRESS, GAP = 0.12, 0.10            # seconds a button is held, and the pause after it (about 7 and 6 frames)
SHADES = [(155, 188, 15), (139, 172, 15), (48, 98, 48), (15, 56, 15)]
NOTES_MAX = 30                     # notes kept per game (play and watch share them)
TALK_GAP = 25                      # watch mode: seconds between remarks said out loud (the rest only show on screen)


def _bcd(b):
    return int("".join(f"{x:02x}" for x in b) or "0")


# Facts read from memory for games whose memory layout is published. Red/Blue: pret/pokered; Yellow is shifted by one
# byte in this area (pret/pokeyellow). Best effort: the picture stays the main input.
def pokemon_facts(emu, yellow=False):
    o = -1 if yellow else 0
    p = lambda a, n=1: emu.peek(a + o, n)
    party = p(0xD163)[0]
    facts = {"map_id": p(0xD35E)[0], "x": p(0xD362)[0], "y": p(0xD361)[0],
             "in_battle": {0: "no", 1: "wild", 2: "trainer"}.get(p(0xD057)[0], "?"),
             "badges": bin(p(0xD356)[0]).count("1"), "money": _bcd(p(0xD347, 3)), "party_size": party}
    if 0 < party <= 6:
        hp = []
        for i in range(party):
            base = 0xD16B + i * 44
            cur, mx = int.from_bytes(p(base + 1, 2), "big"), int.from_bytes(p(base + 34, 2), "big")
            hp.append(f"{cur}/{mx} HP, level {p(base + 33)[0]}")
        facts["party"] = hp
    return facts


PROFILES = [
    (re.compile(r"POKEMON (RED|BLUE)"), lambda emu: pokemon_facts(emu), "Pokemon Red/Blue"),
    (re.compile(r"POKEMON YELLOW"), lambda emu: pokemon_facts(emu, yellow=True), "Pokemon Yellow"),
]


class AIPlayer:
    """Plays one running Emulator. .mask is the buttons it holds right now (the app ORs it into the joypad)."""

    def __init__(self, app, emu):
        self.app, self.emu, self.host = app, emu, app.host
        self.mask = 0
        self.on = False
        self.mode = "play"              # "play" (Pip presses buttons) or "watch" (Pip watches you and comments)
        self.gen = 0                    # bumped on every start/stop, so a slow answer from an old run is thrown away
        self.seen = []                  # watch: buttons the person pressed since the last look, in order
        self.human = 0                  # watch: the person's buttons held right now (to spot new presses)
        self.said = []                  # watch: Pip's last few remarks (so it does not repeat itself)
        self.t_said = 0
        self.t_look = self.t_prev = 0  # watch: when it last looked / when prev_frame was taken
        self.status = ""                # last thought, or why it is waiting (shown under the game)
        self.history = []               # last moves: (buttons, thought)
        self.prev_frame = None          # the screen when it last decided (before those moves)
        self.slug = re.sub(r"[^a-z0-9]+", "_", emu.path.stem.lower()).strip("_") or "game"
        self.notes = self._load_notes()
        self.profile = next(((f, name) for rx, f, name in PROFILES if rx.search(emu.title.upper())), (None, ""))
        self.thread = None

    # ---- settings and limits
    def cfg(self):
        return self.host.section("desktop")

    def model(self):
        m = self.cfg().get("gb_ai_model")
        if m:
            return m
        provider, model = self.host.ai_model()
        return model if provider == "openrouter" else None

    def endpoint(self):
        """(chat/completions URL, API key or None, is_openrouter)"""
        url = self.cfg().get("gb_ai_url") or URL
        key_env = self.cfg().get("gb_ai_key_env") or "OPENROUTER_API_KEY"
        key = os.environ.get(key_env)
        if not key:                                   # keys of other services live in data/secrets.env too
            try:
                for line in (DATA / "secrets.env").read_text().splitlines():
                    if line.strip().startswith(key_env + "="):
                        key = line.split("=", 1)[1].strip().strip("\"'")
            except OSError:
                pass
        return url, key, "openrouter.ai" in url

    def steps_left(self):
        c = self.cfg()
        day = time.strftime("%Y-%m-%d")
        used = c.get("gb_ai_used", {})
        if used.get("day") != day:
            used = c["gb_ai_used"] = {"day": day, "n": 0}
        return int(c.get("gb_ai_steps_day", 300)) - used["n"]

    def count_step(self):
        self.cfg()["gb_ai_used"]["n"] += 1
        self.host.save()

    # ---- notes (per game, kept between sessions)
    def _notes_path(self):
        return AI_DIR / f"{self.slug}.json"

    def _load_notes(self):
        try:
            return json.loads(self._notes_path().read_text())[-NOTES_MAX:]
        except Exception:
            return []

    def _save_notes(self):
        try:
            AI_DIR.mkdir(parents=True, exist_ok=True)
            self._notes_path().write_text(json.dumps(self.notes[-NOTES_MAX:], indent=1))
        except OSError:
            pass

    # ---- start / stop
    def start(self, mode="play"):
        if self.on and self.mode == mode:
            return
        if not self.model():
            self.status = "no model: set desktop.gb_ai_model or an OpenRouter brain"
            return
        if not self.endpoint()[1]:
            self.status = "no API key for the AI player"
            return
        self.stop()
        self.mode, self.on = mode, True
        self.prev_frame, self.seen, self.human, self.t_look = None, [], 0, 0
        self.status = "thinking..." if mode == "play" else "watching you play..."
        self.thread = threading.Thread(target=self._run, args=(self.gen,), daemon=True, name="gameboy-ai")
        self.thread.start()
        self.host.log(f"gameboy: Pip {'plays' if mode == 'play' else 'watches'} {self.emu.title} ({self.model()})", persist=False)

    def stop(self, why=""):
        self.on, self.mask = False, 0
        self.gen += 1
        if why:
            self.status = why

    def watching(self):
        return self.on and self.mode == "watch"

    def saw(self, mask):
        """Watch mode: the app reports the person's buttons every tick; new presses are kept for the next look."""
        new, self.human = mask & ~self.human, mask
        if new and self.watching():
            self.seen += [n for n, bit in BUTTONS.items() if new & bit]
            del self.seen[:-200]

    # ---- the loop
    def _run(self, gen):
        live = lambda: self.on and gen == self.gen
        while live() and self.emu.core and not self.emu.error:
            watch = self.mode == "watch"
            interval = float(self.cfg().get("gb_watch_interval" if watch else "gb_ai_interval", 20 if watch else 4))
            if self.emu.paused:
                time.sleep(0.3)
                continue
            if watch:
                if time.time() - self.t_look < interval:
                    time.sleep(0.3)
                    continue
                if not self.seen and self.prev_frame == self.emu.frame:   # nothing happened: do not spend a step
                    self.t_look = time.time()
                    continue
            if self.steps_left() <= 0:
                self.stop("daily step limit reached (Settings > AI)")
                break
            t0 = time.time()
            try:
                move = self.watch_step(gen) if watch else self.decide(gen)
            except requests.HTTPError as e:
                code = e.response.status_code if e.response is not None else 0
                if live():
                    self.status = "model busy, waiting..." if code in (429, 502, 503) else f"model error {code}"
                time.sleep(20 if code == 429 else 8)
                continue
            except Exception as e:
                if live():
                    self.status = f"error: {str(e)[:60]}"
                time.sleep(8)
                continue
            if not live() or move is None:
                break
            self.count_step()
            if watch:
                self.t_look = t0
            else:
                self.press(move["buttons"], gen)
                time.sleep(max(0.0, interval - (time.time() - t0)))
        if gen == self.gen:
            self.mask = 0
            self.on = False

    def press(self, names, gen):
        for name in names:
            if not self.on or gen != self.gen:
                return
            self.mask = BUTTONS[name]
            time.sleep(PRESS)
            self.mask = 0
            time.sleep(GAP)

    def screenshot(self, frame=None):
        im = Image.frombytes("P", (160, 144), frame or self.emu.frame)
        im.putpalette([v for c in SHADES for v in c])
        return im.convert("RGB").resize((320, 288), Image.NEAREST)

    def facts(self):
        f, game = self.profile
        if not f:
            return ""
        try:
            return f"Facts read from the game's memory ({game}): {json.dumps(f(self.emu))}\n"
        except Exception:
            return ""

    def ask(self, prompt, frames, max_tokens=1500):
        """Send the prompt and the pictures to the model; return the JSON object it answers with ({} if none)."""
        imgs = []
        for fr in frames:
            buf = io.BytesIO()
            self.screenshot(fr).save(buf, "PNG")
            imgs.append({"type": "image_url",
                         "image_url": {"url": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}})
        url, key, is_or = self.endpoint()
        body = {"model": self.model(), "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}] + imgs}]}
        if is_or:
            body["reasoning"] = {"effort": "low"}      # thinking models otherwise spend all the tokens thinking
        # (even on low a free thinking model used ~400 tokens before answering: 400-600 left nothing for the answer)
        resp = requests.post(url, timeout=90, json=body,
                             headers={"Authorization": "Bearer " + key, "X-Title": "ai-multi-bot gameboy"})
        resp.raise_for_status()
        choice = resp.json()["choices"][0]
        text = (choice["message"].get("content") or "").strip()
        if not text and choice.get("finish_reason") == "length":
            raise RuntimeError("the model ran out of tokens while thinking")
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else {}

    def add_note(self, note):
        if note and note not in self.notes:
            self.notes.append(note)
            self._save_notes()

    def notes_text(self):
        return "\n".join(f"- {n}" for n in self.notes) or "- (none yet)"

    # ---- play mode
    def decide(self, gen):
        frame = self.emu.frame
        before = self.prev_frame
        changed = "" if before is None else (
            "The screen did NOT change after your last moves: they did nothing here. Try something else.\n"
            if before == frame else "The screen changed after your last moves (compare the two pictures).\n")
        moves = "\n".join(f"- {' '.join(b) or '(nothing)'}: {t}" for b, t in self.history[-8:]) or "- (none yet)"
        prompt = (
            f"You are Pip, a small home robot, playing the Game Boy game {self.emu.title!r} by pressing buttons. "
            + ("Picture 1 is the screen before your last moves, picture 2 is the screen now. " if before else
               "The picture is the game screen right now. ")
            + "Play to make progress and have fun; read any text on screen. "
            "Buttons: A, B, START, SELECT, UP, DOWN, LEFT, RIGHT. Each button you list is pressed once, in order "
            "(walking one tile = one direction press). Press A to read or advance text.\n"
            + self.facts()
            + changed
            + f"Your last moves (oldest first):\n{moves}\n"
            f"Your notes about this game (notes marked (watched) you learned by watching your friend play):\n"
            f"{self.notes_text()}\n"
            'Reply with ONLY one JSON object: {"thought": "what you see and plan, max 15 words", '
            '"buttons": ["UP", "A"], "note": "a new fact or goal worth remembering, or empty"}. 1 to 8 buttons.')
        data = self.ask(prompt, ([before] if before else []) + [frame])
        if gen != self.gen:
            return None
        buttons = [b.upper() for b in data.get("buttons", []) if isinstance(b, str) and b.upper() in BUTTONS][:8]
        thought = str(data.get("thought", "")).strip()[:120]
        self.add_note(str(data.get("note", "")).strip()[:160])
        self.history = (self.history + [(buttons, thought)])[-8:]
        self.prev_frame = frame
        self.status = thought or ("pressing " + " ".join(buttons) if buttons else "looking...")
        return {"buttons": buttons, "thought": thought}

    # ---- watch mode: the friend on the couch
    @staticmethod
    def presses_text(names):
        """["RIGHT", "RIGHT", "A"] -> "RIGHT x2, A" (the last 40 runs)."""
        runs = []
        for n in names:
            if runs and runs[-1][0] == n:
                runs[-1][1] += 1
            else:
                runs.append([n, 1])
        return ", ".join(n if k == 1 else f"{n} x{k}" for n, k in runs[-40:]) or "nothing"

    def watch_step(self, gen):
        frame, before = self.emu.frame, self.prev_frame
        presses, self.seen = self.seen, []
        secs = int(time.time() - self.t_prev)
        stuck = ("They pressed buttons but the screen did not change: they may be stuck.\n"
                 if presses and before == frame else "")
        said = "\n".join(f"- {t}" for t in self.said[-6:]) or "- (nothing yet)"
        prompt = (
            f"You are Pip, a small friendly home robot. Your friend is playing the Game Boy game {self.emu.title!r} on "
            "your screen and you are sitting next to them watching, like a kid watching a friend play. You do not "
            "touch the buttons. "
            + (f"Picture 1 is the screen {secs} seconds ago, picture 2 is the screen now. " if before else
               "The picture is the game screen right now. ")
            + f"Buttons your friend pressed since then, in order: {self.presses_text(presses)}.\n"
            + self.facts() + stuck
            + f"What you said recently (do not repeat it):\n{said}\n"
            f"Your notes about this game:\n{self.notes_text()}\n"
            "Be a good friend to watch with: react to real moments (a win, a level up, a new place, a close call, a "
            "funny moment, losing a life), be encouraging, never bossy, and do not narrate every step: most of the "
            "time say nothing. Give a tip only when you are fairly sure it helps (they seem stuck, missed something "
            "obvious, are about to lose). Watch how they play and note what you learn about the game from it (what a "
            "button does, where to go, what worked), so you can play better yourself later.\n"
            'Reply with ONLY one JSON object: {"comment": "a short natural remark to say out loud, max 15 words, or '
            'empty", "tip": "one short suggestion, or empty", "note": "a new fact about HOW TO PLAY this game '
            '(controls, goals, where to go, what worked) learned from watching, not a description of the screen, '
            'or empty"}.')
        data = self.ask(prompt, ([before] if before else []) + [frame])
        if gen != self.gen:
            return None
        self.prev_frame, self.t_prev = frame, time.time()
        comment = str(data.get("comment", "")).strip()[:120]
        tip = str(data.get("tip", "")).strip()[:120]
        note = str(data.get("note", "")).strip()[:160]
        if note:
            self.add_note("(watched) " + note)
        if comment or tip:
            self.said = (self.said + [" ".join(x for x in (comment, "Tip: " + tip if tip else "") if x)])[-6:]
            self.status = ("Tip: " + tip) if tip else comment
            self.say(" ".join(x for x in (comment, tip) if x))
        elif self.status.startswith(("model", "error", "thinking")):
            self.status = "watching you play..."
        return {}

    def say(self, text):
        """Out loud now and then (Settings > Sound > Talk during games); it is always shown under the game."""
        if not text or time.time() - self.t_said < TALK_GAP:
            return
        if not self.host.game_talk():
            return
        self.t_said = time.time()
        self.host.say(text, "happy")
