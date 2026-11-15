"""RoboOS host: the one door a RoboOS app uses to reach the machine it runs on (API version 1).

Apps talk to `self.host`, never to whatever program runs RoboOS. This base class is a complete host for RoboOS on its
own (`python -m roboos`: a CYD screen on any small Linux board): settings in data/config.json, sound on the screen's
speaker, system information from /proc. robotd subclasses it (robotd/desktop_host.py: RobotHost) to add the robot's
voice, camera, AI brain and Bluesky clips. Parts a machine does not have quietly do nothing: say() is silent without
a voice, ai_model() is (None, None) without an AI. Check `host.has("voice")` etc. when it matters.

Portable apps (all built-in RoboOS apps, every store app) use only this and `self.d` (the desktop). The self-test
`roboos_portable` fails if code in roboos/ reaches for the robot.

Keep this API small and stable: an app written for API 1 must keep working. Add methods; never change or remove one
without bumping roboos.API.
"""
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import wave
from collections import deque
from pathlib import Path

import numpy as np

from .paths import DATA, ROOT

FEATURES = ("screen", "sysinfo", "voice", "ai", "camera", "social")


class Config:
    """data/config.json as .data (a dict) with save(). The robot passes its own config object with the same shape."""

    def __init__(self, path=DATA / "config.json"):
        self.path = Path(path)
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {}
        self.lock = threading.Lock()

    def save(self):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1))
            os.replace(tmp, self.path)


class Log:
    """print + the last 200 lines (the Logs app shows them)."""

    def __init__(self):
        self.lines = deque(maxlen=200)

    def __call__(self, msg, kind="info", persist=True, **_):
        self.lines.append((time.time(), kind, msg))
        print(time.strftime("%H:%M:%S ") + msg, flush=True)


class Host:
    program = "roboos"               # shown in Monitor, About, Shut Down
    machine = "this computer"        # "What do you want <machine> to do?"

    def __init__(self, screen, cfg=None, log=None, robot=None):
        self._screen = screen        # roboos.screen.CydLink (or anything with .info .page .up .send .send_image)
        self.cfg = cfg if cfg is not None else Config()
        self._log = log if log is not None else Log()
        self.robot = robot           # robotd only: robot-only apps use it; portable code never does
        self.t0 = time.time()
        self._cpu = None

    # ---- what is here
    def has(self, feature):
        """Is this part present? One of FEATURES."""
        return feature in ("screen", "sysinfo")

    def board(self):
        """The computer's name, e.g. "Xunlong Orange Pi Zero" (from the device tree), else "this computer"."""
        try:
            return Path("/proc/device-tree/model").read_text().strip("\x00 \n") or "this computer"
        except OSError:
            return "this computer"

    def apps(self, builtin):
        """The apps for the Start menu, in order: the built-in RoboOS apps plus any the host adds."""
        return list(builtin)

    # ---- log
    def log(self, msg, level="info", persist=True):
        self._log(msg, level, persist=persist)

    def recent_log(self, n=120):
        """[(time, kind, message)], newest first (the Logs app)."""
        return list(reversed(list(getattr(self._log, "lines", []))[-n:]))

    # ---- settings (data/config.json; RoboOS keeps its own in the "desktop" section)
    def section(self, name="desktop"):
        """A settings section as a dict you may change; call save() after changing it."""
        return self.cfg.data.setdefault(name, {})

    def get(self, section, key, default=None):
        return self.cfg.data.get(section, {}).get(key, default)

    def save(self):
        try:
            self.cfg.save()
        except Exception:
            pass

    # ---- files
    def data_dir(self, app_name):
        """A folder of the app's own (data/apps/<name>/data), created on first use. Never committed, never public."""
        p = DATA / "apps" / app_name / "data"
        p.mkdir(parents=True, exist_ok=True)
        return p

    # ---- voice
    def say(self, text, mood=None):
        """Say it out loud in the background (no voice: nothing happens). Moods: ok, happy, sad, think, win."""

    def game_talk(self):
        """Settings > Sound > Talk during games: may games say things out loud?"""
        return bool(self.get("desktop", "game_talk", True))

    # ---- the screen link (CYD): .info (the screen's hello: ver, gbf, img_max...), .page, .up, .send(msg),
    # .send_image(payload, timeout, kind, **fields). Only apps that draw straight to the screen need it.
    @property
    def screen(self):
        return self._screen

    # ---- sound: AudioOut asks where to play. None = the screen's own speaker (streamed over USB).
    def audio_begin(self):
        """Called before a sound plays: an ALSA device name (config desktop.audio_device), or None for the screen."""
        return self.get("desktop", "audio_device") or None

    def audio_end(self):
        pass

    def wav_to_u8(self, data, rate):
        """WAV bytes -> unsigned 8-bit mono at `rate` (the screen speaker's format; used when ffmpeg is missing)."""
        with wave.open(io.BytesIO(data)) as w:
            ch, width, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
        x = np.frombuffer(raw, {1: np.uint8, 2: np.int16, 4: np.int32}[width]).astype(np.float32)
        if width == 1:
            x = (x - 128) * 256
        elif width == 4:
            x /= 65536
        x = x.reshape(-1, ch).mean(axis=1) / 32768
        if sr != rate and len(x):
            x = np.interp(np.arange(0, len(x), sr / rate), np.arange(len(x)), x)
        return (np.clip(x, -1, 1) * 127 + 128).astype(np.uint8).tobytes()

    def bt_wav(self, data):
        """WAV bytes for a Bluetooth speaker (48 kHz stereo). Only robots with Bluetooth audio need it."""
        return data

    def mic_device(self):
        """The ALSA device to record from: config desktop.mic, else the first card that can record."""
        want = self.get("desktop", "mic")
        if want:
            return want
        try:
            out = subprocess.run(["arecord", "-l"], capture_output=True, text=True, timeout=3).stdout
            cards = re.findall(r"card \d+: (\S+) \[", out)
            if cards:
                return f"plughw:CARD={cards[0]},DEV=0"
        except Exception:
            pass
        return "default"

    # ---- camera (the taskbar's privacy switch shows only with a camera)
    def camera_privacy(self):
        return False

    def set_camera_privacy(self, on):
        pass

    # ---- recordings: a game ended well; frames = [(time, JPEG bytes)] of the last seconds
    def post_moment(self, app_name, note, frames, score=2):
        pass

    # ---- AI
    def ai_model(self):
        """(provider, model id) of the active AI, or (None, None). Without a robot: config ai.provider + ai.model."""
        a = self.cfg.data.get("ai", {})
        return (a.get("provider"), a.get("model")) if a.get("model") else (None, None)

    # ---- manual (RoboWeb's about:manual): Markdown pages
    def manual_dirs(self):
        return [ROOT / "docs", ROOT / "knowledge" / "manual"]

    def manual_pages(self):
        return sorted({p.stem for d in self.manual_dirs() if d.is_dir() for p in d.glob("*.md")})

    def read_manual(self, page):
        page = (page or "README").strip().removesuffix(".md")
        for d in self.manual_dirs():
            p = d / f"{page}.md"
            if p.is_file():
                return p.read_text()
        return f"No page '{page}'. Pages: {', '.join(self.manual_pages())}"

    def bookmarks(self):
        """Extra RoboWeb home links: [(title, url)]."""
        return []

    # ---- power (the Shut Down app): restart | reboot | shutdown
    def power(self, action):
        self.log(f"power: {action}")
        if action == "restart":
            if os.environ.get("INVOCATION_ID"):        # started by systemd: it starts us again
                subprocess.Popen(["systemctl", "restart", os.environ.get("ROBOOS_SERVICE", "roboos")])
            else:
                os.execv(sys.executable, [sys.executable] + sys.argv)
        elif action in ("reboot", "shutdown"):
            subprocess.Popen(["systemctl", "reboot" if action == "reboot" else "poweroff"])

    # ---- system information
    def cpu_pct(self):
        try:
            v = [int(x) for x in Path("/proc/stat").read_text().split("\n", 1)[0].split()[1:]]
        except OSError:
            return 0.0
        idle, total = v[3] + (v[4] if len(v) > 4 else 0), sum(v)
        prev, self._cpu = self._cpu, (idle, total)
        if not prev or total == prev[1]:
            return 0.0
        return max(0.0, 100.0 * (1 - (idle - prev[0]) / (total - prev[1])))

    def mem(self):
        """(used MB, total MB)"""
        try:
            m = {ln.split(":")[0]: int(ln.split()[1]) for ln in Path("/proc/meminfo").read_text().splitlines()}
            return (m["MemTotal"] - m.get("MemAvailable", m.get("MemFree", 0))) / 1024, m["MemTotal"] / 1024
        except (OSError, KeyError, ValueError):
            return 0.0, 0.0

    def temp(self):
        try:
            return int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
        except (OSError, ValueError):
            return 0.0

    def ips(self):
        """{interface: address}"""
        try:
            out = subprocess.run(["ip", "-4", "-o", "addr"], capture_output=True, text=True, timeout=3).stdout
            return {m.group(1): m.group(2) for m in re.finditer(r"\d+: (\S+)\s+inet (\S+?)/", out) if m.group(1) != "lo"}
        except Exception:
            return {}

    def uptime(self):
        """Seconds since the program that runs RoboOS started."""
        return time.time() - self.t0
