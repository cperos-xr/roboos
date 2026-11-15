"""More RoboOS apps: Sound Recorder, Music, Notepad, RoboWeb (text web browser) and RoboTube.

Shared pieces:
- Keyboard: an on-screen keyboard any app can show (USB keyboards work too).
- AudioOut: plays a file or URL on the robot's voice output (the CYD earpiece by default, streamed at 11 kHz,
  paced so the screen never has to drop desktop pictures), or on an ALSA speaker. One sound at a time.
"""
import io
import re
import shutil
import subprocess
import threading
import time
import wave
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

import numpy as np
from PIL import Image, ImageDraw

from .paths import DATA, ROOT
from .desktop import (App, DARK, F_BIG, F_BOLD, F_MONO, F_UI, FACE, LIGHT, ListApp, MONO_H, MONO_W, NAVY, RED,
                      SBW, SHADOW, Scroll, W, bevel, button, fit_text, inside)

REC_DIR = DATA / "recordings"
MUSIC_DIR = DATA / "music"
NOTES_DIR = DATA / "notes"
AUDIO_EXT = {".wav", ".mp3", ".ogg", ".oga", ".m4a", ".flac", ".aac", ".opus", ".webm"}
BLUE = (0, 0, 200)
HAS_FFMPEG = shutil.which("ffmpeg") is not None


def mmss(sec):
    sec = max(0, int(sec or 0))
    return f"{sec // 60}:{sec % 60:02d}" if sec < 3600 else f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


# ---------------------------------------------------------------- on-screen keyboard
class Keyboard:
    ROWS = ["1234567890", "qwertyuiop", "asdfghjkl'", "zxcvbnm,.?"]
    SYM = ["!@#$%^&*()", "-_=+[]{};:", "\"/\\|<>~`@#", "&*%$!     "]
    HEIGHT = 5 * 18 + 2

    def __init__(self):
        self.shift = self.sym = False
        self.keys, self._img, self._key = [], None, None

    def draw(self, img, box):
        x0, y0, x1, y1 = box
        key = (self.shift, self.sym, box)
        if key != self._key:
            tile = Image.new("RGB", (x1 - x0 + 1, y1 - y0 + 1), FACE)
            d = ImageDraw.Draw(tile)
            keys = []
            kw = (x1 - x0 - 2) / 10
            for r, row in enumerate(self.SYM if self.sym else self.ROWS):
                for c, ch in enumerate(row):
                    if ch == " ":
                        continue
                    label = ch.upper() if self.shift and ch.isalpha() else ch
                    b = (int(1 + c * kw), 1 + r * 18, int((c + 1) * kw), 17 + r * 18)
                    button(d, b, label, font=F_BOLD)
                    keys.append((b, label))
            specials = [("⇧", "SHIFT", 1), ("#+", "SYM", 1), ("space", " ", 3.2), ("⌫", "BKSP", 1.4), ("◀", "LEFT", 0.9),
                        ("▶", "RIGHT", 0.9), ("⏎", "ENTER", 1.6)]
            unit = (x1 - x0 - 2) / sum(u for _, _, u in specials)
            bx, y = 1, 1 + 4 * 18
            for label, k, u in specials:
                b = (int(bx), y, int(bx + u * unit) - 1, y + 16)
                button(d, b, label, pressed=(k == "SHIFT" and self.shift) or (k == "SYM" and self.sym), font=F_BOLD)
                keys.append((b, k))
                bx += u * unit
            self._img, self._key = tile, key
            self.keys = [((b[0] + x0, b[1] + y0, b[2] + x0, b[3] + y0), k) for b, k in keys]
        img.paste(self._img, (x0, y0))

    def click(self, x, y):
        """Returns the key pressed (a character or ENTER/BKSP/LEFT/RIGHT), or None."""
        for b, k in self.keys:
            if inside(b, x, y):
                if k == "SHIFT":
                    self.shift = not self.shift
                    return None
                if k == "SYM":
                    self.sym = not self.sym
                    return None
                if self.shift and len(k) == 1:
                    self.shift = False
                return k
        return None


class LineEdit:
    """A one-line text field (address bar, search box)."""

    def __init__(self, text=""):
        self.text, self.cur = text, len(text)

    def key(self, k):
        if k == "BKSP":
            if self.cur:
                self.text = self.text[:self.cur - 1] + self.text[self.cur:]
                self.cur -= 1
        elif k == "DEL":
            self.text = self.text[:self.cur] + self.text[self.cur + 1:]
        elif k == "LEFT":
            self.cur = max(0, self.cur - 1)
        elif k == "RIGHT":
            self.cur = min(len(self.text), self.cur + 1)
        elif k == "HOME":
            self.cur = 0
        elif k == "END":
            self.cur = len(self.text)
        elif len(k) == 1:
            self.text = self.text[:self.cur] + k + self.text[self.cur:]
            self.cur += 1

    def draw(self, d, box, focused):
        bevel(d, box, raised=False, fill=LIGHT)
        w = box[2] - box[0] - 6
        start = 0
        while d.textlength(self.text[start:self.cur], font=F_UI) > w - 4:
            start += 1
        shown = self.text[start:]
        d.text((box[0] + 3, box[1] + 2), fit_text(d, shown, w) if not focused else shown, font=F_UI, fill=DARK)
        if focused and int(time.time() * 2) % 2 == 0:
            cx = box[0] + 3 + d.textlength(self.text[start:self.cur], font=F_UI)
            d.line([(cx, box[1] + 2), (cx, box[3] - 2)], fill=DARK)


# ---------------------------------------------------------------- audio output (one sound at a time)
class AudioOut:
    RATE = 11025

    def __init__(self, host):
        self.host = host
        self.stop_ev = threading.Event()
        self.thread = None
        self.name, self.source = "", None
        self.t0 = 0.0
        self.dur = 0.0
        self.playing = False
        self.finished = False             # True when the last sound ended by itself (for auto-next)
        self.procs = []

    def probe(self, src):
        if not HAS_FFMPEG:
            try:
                with wave.open(str(src)) as w:
                    return w.getnframes() / w.getframerate()
            except Exception:
                return 0.0
        try:
            out = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
                                 capture_output=True, text=True, timeout=20).stdout.strip()
            return float(out) if out and out != "N/A" else 0.0
        except Exception:
            return 0.0

    def play(self, src, name=None, dur=None):
        self.stop()
        self.stop_ev.clear()
        self.source, self.name = src, name or Path(str(src)).stem
        self.dur = dur if dur is not None else (self.probe(src) if not str(src).startswith("http") else 0.0)
        self.finished = False
        self.playing = True
        self.t0 = time.time()
        self.thread = threading.Thread(target=self._run, args=(src,), daemon=True, name="audio-out")
        self.thread.start()

    def position(self):
        return time.time() - self.t0 if self.playing else 0.0

    def stop(self):
        self.stop_ev.set()
        for p in self.procs:
            try:
                p.kill()
            except Exception:
                pass
        if self.thread and self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(3)
        self.procs = []
        self.playing = False

    def _decoder(self, src, args):
        if HAS_FFMPEG:
            cmd = ["ffmpeg", "-v", "quiet", "-nostdin"]
            if str(src).startswith("http"):
                cmd += ["-reconnect", "1", "-reconnect_streamed", "1"]
            return subprocess.Popen(cmd + ["-i", str(src)] + args + ["-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return None

    def _run(self, src):
        natural = False
        try:
            dev = self.host.audio_begin()            # an ALSA device, or None = the screen's earpiece
            if dev:                                   # a real ALSA speaker: let aplay do the work
                bt = dev.startswith("bluealsa")          # Bluetooth: always 48 kHz stereo (see voice.py)
                if HAS_FFMPEG:
                    dec = self._decoder(src, ["-f", "wav", "-ac", "2", "-ar", "48000" if bt else "44100"])
                    ap = subprocess.Popen(["aplay", "-q", "-D", dev, "-"], stdin=dec.stdout, stderr=subprocess.DEVNULL)
                    self.procs = [dec, ap]
                elif bt:
                    ap = subprocess.Popen(["aplay", "-q", "-D", dev, "-"], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
                    self.procs = [ap]
                    ap.stdin.write(self.host.bt_wav(Path(src).read_bytes()))
                    ap.stdin.close()
                else:
                    ap = subprocess.Popen(["aplay", "-q", "-D", dev, str(src)])
                    self.procs = [ap]
                while ap.poll() is None and not self.stop_ev.is_set():
                    time.sleep(0.2)
                natural = not self.stop_ev.is_set()
                return
            # the CYD earpiece: unsigned 8-bit mono, streamed in 2 KB chunks, kept ~0.7 s ahead of real time
            if HAS_FFMPEG:
                dec = self._decoder(src, ["-f", "u8", "-ac", "1", "-ar", str(self.RATE),
                                          "-af", "acompressor=threshold=0.08:ratio=4:makeup=3"])
                self.procs = [dec]
                read = lambda n: dec.stdout.read(n)
            else:
                data = self.host.wav_to_u8(Path(src).read_bytes(), self.RATE)
                buf = io.BytesIO(data)
                read = buf.read
            sent = 0
            start = time.time()
            self.t0 = start
            while not self.stop_ev.is_set():
                chunk = read(2048)
                if not chunk:
                    natural = True
                    break
                ahead = sent / self.RATE - (time.time() - start)
                if ahead > 0.7:
                    time.sleep(ahead - 0.7)
                if not self.host.screen.send_image(chunk, timeout=4.0, kind="pcm", rate=self.RATE):
                    time.sleep(0.1)
                sent += len(chunk)
        except Exception as e:
            self.host.log(f"audio: {e}", "warn")
        finally:
            self.host.audio_end()
            for p in self.procs:
                try:
                    p.kill()
                except Exception:
                    pass
            self.playing = False
            self.finished = natural


def audio_files():
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    files = [p for p in MUSIC_DIR.rglob("*") if p.suffix.lower() in AUDIO_EXT]
    return sorted(files, key=lambda p: p.name.lower())


# ---------------------------------------------------------------- Sound Recorder
class Recorder(ListApp):
    portable = True
    name, title, icon = "recorder", "Sound Recorder", "mic"
    short = "Recorder"
    row_h = 18

    def __init__(self, desk):
        super().__init__(desk)
        self.proc = None
        self.level = 0.0
        self.t0 = 0
        self.files = []
        self.err = ""
        self.reload()

    def reload(self):
        REC_DIR.mkdir(parents=True, exist_ok=True)
        self.files = sorted(REC_DIR.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)

    def rows(self):
        return self.files

    @property
    def recording(self):
        return self.proc is not None

    def start(self):
        self.d.audio.stop()
        REC_DIR.mkdir(parents=True, exist_ok=True)
        path = REC_DIR / time.strftime("rec_%Y%m%d_%H%M%S.wav")
        try:
            self.proc = subprocess.Popen(["arecord", "-q", "-D", self.host.mic_device(), "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "raw"],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as e:
            self.err = str(e)
            return
        self.t0, self.err = time.time(), ""
        threading.Thread(target=self._pump, args=(self.proc, path), daemon=True).start()

    def _pump(self, proc, path):
        wf = wave.open(str(path), "wb")
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
        try:
            while True:
                data = proc.stdout.read(1600)          # 50 ms
                if not data:
                    break
                wf.writeframes(data)
                x = np.frombuffer(data, np.int16).astype(np.float32)
                self.level = float(np.sqrt((x * x).mean()) / 8000) if len(x) else 0.0
                if time.time() - self.t0 > 600:          # 10 minutes max
                    proc.terminate()
        finally:
            wf.close()
            err = proc.stderr.read().decode(errors="replace").strip() if proc.stderr else ""
            if err and time.time() - self.t0 < 1.5:
                self.err = "microphone busy" if "busy" in err.lower() else err[:60]
                path.unlink(missing_ok=True)
            self.reload()

    def stop(self):
        if self.proc:
            self.proc.terminate()
            self.proc = None
            self.level = 0
            self.host.log("desktop: sound recording saved")

    def draw(self, img, d, box):
        x0, y0, x1, y1 = box
        d.rectangle((x0, y0, x1, y0 + 46), fill=FACE)
        self.recbtn = (x0 + 6, y0 + 4, x0 + 44, y0 + 42)
        bevel(d, self.recbtn, not self.recording)
        if self.recording:
            d.rectangle((x0 + 17, y0 + 15, x0 + 33, y0 + 31), fill=DARK)
        else:
            d.ellipse((x0 + 15, y0 + 13, x0 + 35, y0 + 33), fill=RED)
        if self.recording:
            d.text((x0 + 52, y0 + 4), "Recording  " + mmss(time.time() - self.t0), font=F_BIG, fill=RED)
        elif self.d.audio.playing and str(self.d.audio.source).startswith(str(REC_DIR)):
            d.text((x0 + 52, y0 + 4), f"Playing  {mmss(self.d.audio.position())} / {mmss(self.d.audio.dur)}", font=F_BIG, fill=NAVY)
        else:
            d.text((x0 + 52, y0 + 4), "Tap the red button to record", font=F_BOLD, fill=DARK)
        meter = (x0 + 52, y0 + 24, x1 - 6, y0 + 36)
        bevel(d, meter, raised=False, fill=DARK)
        lv = min(1.0, self.level) if self.recording else 0
        n = int((meter[2] - meter[0] - 4) * lv)
        for i in range(0, n, 4):
            c = (0, 200, 0) if i < (meter[2] - meter[0]) * 0.6 else ((230, 200, 0) if i < (meter[2] - meter[0]) * 0.85 else RED)
            d.rectangle((meter[0] + 2 + i, meter[1] + 2, meter[0] + 4 + i, meter[3] - 2), fill=c)
        if self.err:
            d.text((x0 + 52, y0 + 37), self.err, font=F_UI, fill=RED)
        if not self.files:
            d.rectangle((x0, y0 + 47, x1, y1), fill=LIGHT)
            d.text((x0 + 8, y0 + 56), "No recordings yet.", font=F_UI, fill=SHADOW)
            self.box = (x0, y0 + 47, x1, y1)
            return
        self.draw_list(d, (x0, y0 + 47, x1, y1))

    def draw_row(self, d, box, p, i):
        playing = self.d.audio.playing and self.d.audio.source == p
        d.text((box[0] + 4, box[1] + 3), ("▶ " if playing else "") + p.stem.replace("rec_", ""), font=F_UI, fill=NAVY if playing else DARK)
        try:
            secs = p.stat().st_size / 32000
        except OSError:
            secs = 0
        t = mmss(secs)
        d.text((box[2] - 6 - d.textlength(t, font=F_UI), box[1] + 3), t, font=F_UI, fill=SHADOW)

    def click(self, x, y):
        if inside(self.recbtn, x, y):
            self.stop() if self.recording else self.start()
            return
        i = self.list_click(x, y) if self.files else None
        if i is not None:
            p = self.files[i]
            if self.d.audio.playing and self.d.audio.source == p:
                self.d.audio.stop()
            else:
                self.d.audio.play(p, dur=p.stat().st_size / 32000)

    def context(self, x, y):
        i = self.row_at(y) if self.files and inside(self.box, x, y) else None
        if i is None:
            return
        p = self.files[i]
        self.d.popup = {"x": x, "y": y, "items": [
            ("Play", lambda: self.d.audio.play(p)),
            ("Copy to Music", lambda: (MUSIC_DIR.mkdir(parents=True, exist_ok=True), shutil.copy2(p, MUSIC_DIR / p.name),
                                       self.d.toast("copied to Music"))),
            ("Delete", lambda: self.d.confirm("Delete recording?", p.name, lambda: (p.unlink(missing_ok=True), self.reload())))]}

    def tick(self):
        return self.recording or self.d.audio.playing

    def close(self):
        self.stop()


# ---------------------------------------------------------------- Music player
class Music(ListApp):
    portable = True
    name, title, icon = "music", "Music", "music"
    row_h = 18

    def __init__(self, desk):
        super().__init__(desk)
        self.files = []
        self.cur = None
        self.last_scan = 0
        self.reload()

    def reload(self):
        REC_DIR.mkdir(parents=True, exist_ok=True)
        self.files = audio_files() + sorted(REC_DIR.glob("*.wav"), key=lambda p: p.name, reverse=True)
        self.last_scan = time.time()

    def rows(self):
        return self.files

    def play(self, i):
        if not self.files:
            return
        self.cur = i % len(self.files)
        self.d.audio.play(self.files[self.cur])

    def draw(self, img, d, box):
        x0, y0, x1, y1 = box
        d.rectangle((x0, y0, x1, y0 + 46), fill=FACE)
        a = self.d.audio
        mine = a.playing and self.cur is not None and self.cur < len(self.files) and a.source == self.files[self.cur]
        self.btns = []
        for j, (label, act) in enumerate((("◀◀", "prev"), ("■" if mine else "▶", "toggle"), ("▶▶", "next"))):
            b = (x0 + 4 + j * 34, y0 + 4, x0 + 34 + j * 34, y0 + 26)
            button(d, b, label, font=F_BIG)
            self.btns.append((b, act))
        title = self.files[self.cur].stem if self.cur is not None and self.cur < len(self.files) else "Nothing playing"
        d.text((x0 + 110, y0 + 4), fit_text(d, title, x1 - x0 - 116, F_BOLD), font=F_BOLD, fill=DARK)
        pos, dur = (a.position(), a.dur) if mine else (0, 0)
        d.text((x0 + 110, y0 + 17), f"{mmss(pos)} / {mmss(dur)}" if mine else ("ffmpeg ready: mp3, ogg, m4a..." if HAS_FFMPEG else "wav files only"),
               font=F_UI, fill=SHADOW)
        bar = (x0 + 4, y0 + 32, x1 - 4, y0 + 40)
        bevel(d, bar, raised=False, fill=LIGHT)
        if mine and dur:
            d.rectangle((bar[0] + 2, bar[1] + 2, bar[0] + 2 + int((bar[2] - bar[0] - 4) * min(1, pos / dur)), bar[3] - 2), fill=NAVY)
        if not self.files:
            d.rectangle((x0, y0 + 47, x1, y1), fill=LIGHT)
            for k, line in enumerate(("No songs yet.", f"Put audio files in {MUSIC_DIR},",
                                      "save one from RoboTube, or record one.")):
                d.text((x0 + 8, y0 + 56 + k * 14), line, font=F_UI, fill=SHADOW)
            self.box = (x0, y0 + 47, x1, y1)
            return
        self.draw_list(d, (x0, y0 + 47, x1, y1))

    def draw_row(self, d, box, p, i):
        mine = self.d.audio.playing and self.d.audio.source == p
        if mine:
            d.rectangle(box, fill=NAVY)
        icon = "♪ " if p.parent != REC_DIR else "● "
        d.text((box[0] + 4, box[1] + 3), fit_text(d, icon + p.stem, box[2] - box[0] - 10), font=F_UI, fill=LIGHT if mine else DARK)

    def click(self, x, y):
        for b, act in getattr(self, "btns", []):
            if inside(b, x, y):
                if act == "toggle":
                    if self.d.audio.playing:
                        self.d.audio.stop()
                    else:
                        self.play(self.cur or 0)
                elif act == "next":
                    self.play((self.cur if self.cur is not None else -1) + 1)
                elif act == "prev":
                    self.play((self.cur or 0) - 1)
                return
        i = self.list_click(x, y) if self.files else None
        if i is not None:
            self.play(i)

    def context(self, x, y):
        """Long-press (or right-click) a song: Play / Delete."""
        i = self.row_at(y) if self.files and inside(self.box, x, y) else None
        if i is None:
            return
        from .desktop import delete_file
        p = self.files[i]

        def gone():                                     # keep pointing at the same song (or none) after the list shrinks
            cur = self.files[self.cur] if self.cur is not None and self.cur < len(self.files) else None
            self.reload()
            self.cur = self.files.index(cur) if cur in self.files else None
        self.d.popup = {"x": x, "y": y, "items": [("Play", lambda: self.play(i)), ("Delete", lambda: delete_file(self.d, p, gone))]}

    def tick(self):
        a = self.d.audio
        if self.cur is not None and not a.playing and a.finished and a.source == self.files[self.cur % len(self.files)]:
            a.finished = False
            self.play(self.cur + 1)                     # auto-advance to the next song
            return True
        if time.time() - self.last_scan > 10:
            n = len(self.files)
            self.reload()
            return n != len(self.files) or a.playing
        return a.playing


# ---------------------------------------------------------------- Notepad
class Notepad(App):
    portable = True
    name, title, icon = "notepad", "Notepad", "notepad"

    def __init__(self, desk):
        super().__init__(desk)
        self.lines = [""]
        self.cy = self.cx = 0
        self.path = None
        self.modified = False
        self.kb = Keyboard()
        self.osk = "keyboard" not in desk.inputs.kinds
        self.top = 0
        self.mode = "edit"
        self.picker = []
        self.ptop = 0
        self.vbar = Scroll()
        self.cols = (W - SBW - 8) // MONO_W
        self._rows = 8
        self._grab = False

    # -- file handling
    def load(self, path):
        from .desktop import PROTECTED
        path = Path(path)
        if PROTECTED.search(path.name):
            self.d.toast("protected file")
            return
        try:
            text = path.read_text(errors="replace")
        except OSError as e:
            self.d.toast(f"cannot open: {e.strerror}")
            return
        self.lines = text.replace("\t", "    ").split("\n") or [""]
        self.cy = self.cx = self.top = 0
        self.path, self.modified, self.mode = path, False, "edit"
        self._title()

    def save(self):
        NOTES_DIR.mkdir(parents=True, exist_ok=True)
        if self.path is None:
            self.path = NOTES_DIR / time.strftime("note_%Y%m%d_%H%M%S.txt")
        try:
            self.path.write_text("\n".join(self.lines))
            self.modified = False
            self.d.toast(f"saved {self.path.name}")
        except OSError as e:
            self.d.toast(f"save failed: {e.strerror}")
        self._title()

    def _title(self):
        self.title = f"Notepad - {self.path.name if self.path else 'untitled'}{' *' if self.modified else ''}"

    def open_picker(self):
        NOTES_DIR.mkdir(parents=True, exist_ok=True)
        notes = sorted(NOTES_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
        manual = sorted((ROOT / "knowledge" / "manual").glob("*.md"))
        self.picker = [p for p in notes if p.is_file()] + manual
        self.mode, self.ptop = "open", 0

    # -- layout: soft-wrap each line to the window width
    def layout(self):
        rows = []
        for li, line in enumerate(self.lines):
            if not line:
                rows.append((li, 0, ""))
            for s in range(0, len(line), self.cols):
                rows.append((li, s, line[s:s + self.cols]))
        return rows

    def cursor_row(self, rows):
        for ri, (li, s, seg) in enumerate(rows):
            if li == self.cy and s <= self.cx <= s + len(seg) and (self.cx < s + self.cols or ri + 1 == len(rows) or rows[ri + 1][0] != li):
                return ri
        return 0

    def draw(self, img, d, box):
        x0, y0, x1, y1 = box
        d.rectangle((x0, y0, x1, y0 + 17), fill=FACE)
        self.tools = []
        bx = x0 + 2
        for label, act in (("New", "new"), ("Open", "open"), ("Save", "save"), ("hide ⌨" if self.osk else "⌨", "osk")):
            w = int(d.textlength(label, font=F_UI)) + 12
            b = (bx, y0 + 1, bx + w, y0 + 16)
            button(d, b, label)
            self.tools.append((b, act))
            bx += w + 2
        body = (x0, y0 + 18, x1, y1 - (Keyboard.HEIGHT if self.osk and self.mode == "edit" else 0))
        if self.mode == "open":
            d.rectangle(body, fill=LIGHT)
            d.text((x0 + 6, body[1] + 2), "Open a note (tap one):", font=F_BOLD, fill=DARK)
            self.phits = []
            for k, p in enumerate(self.picker[self.ptop:self.ptop + 9]):
                yy = body[1] + 18 + k * 17
                tag = "manual: " if p.suffix == ".md" and "manual" in str(p) else ""
                d.text((x0 + 10, yy), fit_text(d, tag + p.name, x1 - x0 - 20), font=F_UI, fill=NAVY)
                self.phits.append(((x0, yy - 2, x1, yy + 14), p))
            if not self.picker:
                d.text((x0 + 10, body[1] + 20), "No notes yet. Write one and tap Save.", font=F_UI, fill=SHADOW)
            return
        d.rectangle((body[0], body[1], body[2] - SBW - 1, body[3]), fill=LIGHT)
        rows = self.layout()
        view = max(1, (body[3] - body[1] - 4) // MONO_H)
        self._rows = view
        cr = self.cursor_row(rows)
        if cr < self.top:
            self.top = cr
        elif cr >= self.top + view:
            self.top = cr - view + 1
        self.top = max(0, min(self.top, max(0, len(rows) - view)))
        for k, (li, s, seg) in enumerate(rows[self.top:self.top + view]):
            d.text((body[0] + 3, body[1] + 2 + k * MONO_H), seg, font=F_MONO, fill=DARK)
        if int(time.time() * 2) % 2 == 0 and self.top <= cr < self.top + view:
            li, s, seg = rows[cr]
            cx = body[0] + 3 + (self.cx - s) * MONO_W
            cyp = body[1] + 2 + (cr - self.top) * MONO_H
            d.line([(cx, cyp), (cx, cyp + MONO_H - 1)], fill=DARK, width=2)
        self.vbar.draw(d, (body[2] - SBW + 1, body[1], body[2], body[3]), self.top, len(rows), view)
        self.tbody, self.rows_cache = body, rows
        if self.osk:
            self.kb.draw(img, (x0, y1 - Keyboard.HEIGHT + 1, x1, y1))

    def click(self, x, y):
        for b, act in getattr(self, "tools", []):
            if inside(b, x, y):
                if act == "new":
                    if self.modified:
                        self.d.confirm("Discard changes?", "Start a new note anyway?", self._new)
                    else:
                        self._new()
                elif act == "open":
                    self.open_picker() if self.mode == "edit" else setattr(self, "mode", "edit")
                elif act == "save":
                    self.save()
                elif act == "osk":
                    self.osk = not self.osk
                return
        if self.mode == "open":
            for b, p in getattr(self, "phits", []):
                if inside(b, x, y):
                    self.load(p)
                    return
            return
        if self.vbar.hit(x, y):
            self.top = self.vbar.click(x, y)
            return
        if self.osk:
            k = self.kb.click(x, y)
            if k is not None:
                self.key(k)
                return
        if inside(getattr(self, "tbody", (0, 0, -1, -1)), x, y):    # tap in the text: move the cursor there
            ri = self.top + (y - self.tbody[1] - 2) // MONO_H
            rows = self.rows_cache
            if rows:
                li, s, seg = rows[max(0, min(ri, len(rows) - 1))]
                self.cy = li
                self.cx = min(len(self.lines[li]), s + max(0, round((x - self.tbody[0] - 3) / MONO_W)))

    def _new(self):
        self.lines, self.cy, self.cx, self.path, self.modified, self.mode = [""], 0, 0, None, False, "edit"
        self._title()

    def key(self, k):
        if self.mode == "open":
            if k == "ESC":
                self.mode = "edit"
            return
        line = self.lines[self.cy]
        changed = True
        if k == "ENTER":
            self.lines[self.cy] = line[:self.cx]
            self.lines.insert(self.cy + 1, line[self.cx:])
            self.cy, self.cx = self.cy + 1, 0
        elif k == "BKSP":
            if self.cx:
                self.lines[self.cy] = line[:self.cx - 1] + line[self.cx:]
                self.cx -= 1
            elif self.cy:
                self.cx = len(self.lines[self.cy - 1])
                self.lines[self.cy - 1] += line
                del self.lines[self.cy]
                self.cy -= 1
        elif k == "DEL":
            if self.cx < len(line):
                self.lines[self.cy] = line[:self.cx] + line[self.cx + 1:]
            elif self.cy + 1 < len(self.lines):
                self.lines[self.cy] += self.lines.pop(self.cy + 1)
        elif k == "TAB":
            self.lines[self.cy] = line[:self.cx] + "    " + line[self.cx:]
            self.cx += 4
        elif len(k) == 1:
            self.lines[self.cy] = line[:self.cx] + k + line[self.cx:]
            self.cx += 1
        else:
            changed = False
            if k == "LEFT":
                if self.cx:
                    self.cx -= 1
                elif self.cy:
                    self.cy -= 1; self.cx = len(self.lines[self.cy])
            elif k == "RIGHT":
                if self.cx < len(line):
                    self.cx += 1
                elif self.cy + 1 < len(self.lines):
                    self.cy += 1; self.cx = 0
            elif k == "UP" and self.cy:
                self.cy -= 1; self.cx = min(self.cx, len(self.lines[self.cy]))
            elif k == "DOWN" and self.cy + 1 < len(self.lines):
                self.cy += 1; self.cx = min(self.cx, len(self.lines[self.cy]))
            elif k == "HOME":
                self.cx = 0
            elif k == "END":
                self.cx = len(line)
            elif k == "CTRL_S":
                self.save()
        if changed and not self.modified:
            self.modified = True
            self._title()

    def press(self, x, y):
        self._grab = self.vbar.hit(x, y) and self.vbar.grab(x, y)

    def release(self, x, y):
        self._grab = False

    def hold(self, x, y):
        if self.vbar.hit(x, y) and self.vbar.on_arrow(x, y):
            self.top = self.vbar.click(x, y)
            return True
        return False

    def drag(self, x, y, dx, dy):
        if self._grab:
            self.top = self.vbar.drag(y)
        elif self.mode == "open":
            self.ptop = max(0, self.ptop - int(dy / 17))
        return True

    def scroll(self, n):
        if self.mode == "open":
            self.ptop = max(0, min(len(self.picker) - 1, self.ptop + n))
        else:
            self.top = max(0, self.top + n)

    def tick(self):
        return True                                   # blinking cursor (the desktop only sends what changed)


# ---------------------------------------------------------------- RoboWeb: a tiny text web browser
HOME = [("Search the web (Brave)", "about:search"), ("Wikipedia", "https://en.m.wikipedia.org/wiki/Special:Random"),
        ("NPR news (text)", "https://text.npr.org/"), ("CNN lite", "https://lite.cnn.com/"),
        ("Hacker News", "https://news.ycombinator.com/"), ("Weather (wttr.in)", "https://wttr.in/?format=4"),
        ("Manual", "about:manual")]                     # + the host's own (Host.bookmarks())
UA = "Mozilla/5.0 (X11; Linux armv7l) RoboWeb/1.0 (tiny text browser on a home robot)"


class _HTML(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "template", "iframe", "select", "option", "button", "math"}
    BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header", "footer",
             "ul", "ol", "table", "pre", "blockquote", "hr", "form", "nav", "main", "figure", "figcaption"}   # paragraph gap
    BREAK = {"br", "li", "tr", "dd", "dt", "td", "th"}                                                       # just a new line

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []                   # ("text", style, href) or ("\n",)
        self.skip = 0
        self.href = []
        self.bold = 0
        self.pre = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self.skip += 1
            return
        if tag in self.BLOCK:
            self.out.append(("\n",))
        elif tag in self.BREAK:
            self.out.append(("\r",))
        if tag == "li":
            self.out.append(("• ", "n", None))
        if tag in ("b", "strong", "h1", "h2", "h3", "h4", "th"):
            self.bold += 1
        if tag == "pre":
            self.pre += 1
        if tag == "a":
            self.href.append(a.get("href"))
        if tag == "img" and a.get("alt"):
            self.out.append((f"[{a['alt'][:40]}] ", "dim", self.href[-1] if self.href else None))
        if tag == "input" and a.get("type", "text") in ("text", "search"):
            self.out.append(("[text box] ", "dim", None))

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if tag in ("b", "strong", "h1", "h2", "h3", "h4", "th"):
            self.bold = max(0, self.bold - 1)
        if tag == "pre":
            self.pre = max(0, self.pre - 1)
        if tag == "a" and self.href:
            self.href.pop()
        if tag in self.BLOCK:
            self.out.append(("\n",))
        elif tag in ("li", "tr", "dd", "dt"):
            self.out.append(("\r",))

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self.skip:
            return
        href = next((h for h in reversed(self.href) if h), None) if self.href else None
        style = "link" if href else ("b" if self.bold else ("pre" if self.pre else "n"))
        if self.pre:
            for i, part in enumerate(data.split("\n")):
                if i:
                    self.out.append(("\n",))
                if part:
                    self.out.append((part, "pre", href))
        else:
            text = re.sub(r"\s+", " ", data)
            if text.strip() or (text == " " and self.out and self.out[-1][0] not in ("\n", "\r")):
                self.out.append((text, style, href))


class _Links(HTMLParser):
    """Collects (href, text) of every link: used to turn a search page into a clean result list."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self._href, self._text, self.skip = [], None, [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "svg"):
            self.skip += 1
        if tag == "a":
            self._href, self._text = dict(attrs).get("href"), []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "svg"):
            self.skip = max(0, self.skip - 1)
        if tag == "a" and self._href:
            self.links.append((self._href, re.sub(r"\s+", " ", "".join(self._text)).strip()))
            self._href = None

    def handle_data(self, data):
        if self._href and not self.skip:
            self._text.append(data)


def search_results_html(raw, query):
    p = _Links()
    try:
        p.feed(raw)
    except Exception:
        pass
    seen, items = set(), []
    for href, text in p.links:
        host = urlparse(href).netloc
        if not href.startswith("http") or "brave.com" in host or len(text) < 12 or href in seen:
            continue
        seen.add(href)
        items.append(f'<li><a href="{href}">{text[:120]}</a><br>{host.removeprefix("www.")}</li>')
    body = "".join(items[:25]) or "<li>No results.</li>"
    return f"<h3>Results for: {query}</h3><ul>{body}</ul>"


def font_for(style):
    return {"b": F_BOLD, "pre": F_MONO}.get(style, F_UI)


class Browser(App):
    portable = True
    name, title, icon = "browser", "RoboWeb", "web"
    LINE_H = 13

    def __init__(self, desk):
        super().__init__(desk)
        self.url = "about:home"
        self.history = []
        self.lines = []                 # [[(x, text, style, href), ...], ...]
        self.top = 0
        self.loading = False
        self.addr = LineEdit("")
        self.editing = False
        self.kb = Keyboard()
        self.vbar = Scroll(step=3)
        self._grab = False
        self._ver = 0
        self._seen = -1
        self.page_title = "RoboWeb"
        self.go("about:home", push=False)

    # -- navigation
    def go(self, url, push=True):
        if push and self.url:
            self.history.append((self.url, self.top))
        self.url, self.top, self.loading = url, 0, True
        self.addr = LineEdit("" if url.startswith("about:") else url)
        threading.Thread(target=self._load, args=(url,), daemon=True).start()

    def _load(self, url):
        try:
            if url == "about:home":
                html = "<h2>RoboWeb</h2><p>A tiny text web browser. Tap a link:</p><ul>" + "".join(
                    f'<li><a href="{u}">{t}</a></li>' for t, u in HOME + self.host.bookmarks()) + "</ul><p>Or tap the address bar to type a web address or a search.</p>"
                self._set(html, "RoboWeb home", url)
            elif url == "about:search":
                self.editing = True
                self._set("<h3>Search</h3><p>Type in the address bar above and press ⏎.</p>", "Search", url)
            elif url == "about:manual":
                self._set("<h2>Manual</h2><ul>" + "".join(f'<li><a href="about:manual/{p}">{p}</a></li>' for p in self.host.manual_pages()) + "</ul>",
                          "Manual", url)
            elif url.startswith("about:manual/"):
                text = self.host.read_manual(url.split("/", 1)[1])
                self._set("<pre>" + text.replace("&", "&amp;").replace("<", "&lt;") + "</pre>", url.split("/", 1)[1], url)
            else:
                import requests
                r = requests.get(url, timeout=15, headers={"User-Agent": UA, "Accept-Language": "en"}, stream=True)
                raw = r.raw.read(1_500_000, decode_content=True)
                ctype = r.headers.get("content-type", "")
                enc = r.encoding or "utf-8"
                if "html" not in ctype and "xml" not in ctype:
                    text = raw.decode(enc, "replace") if "text" in ctype or "json" in ctype else f"({ctype or 'binary'} file, {len(raw)} bytes)"
                    self._set("<pre>" + text[:60000].replace("&", "&amp;").replace("<", "&lt;") + "</pre>", url, r.url)
                elif "search.brave.com/search" in url:              # clean result list instead of the whole page
                    q = parse_qs(urlparse(url).query).get("q", [""])[0]
                    self._set(search_results_html(raw.decode(enc, "replace"), q), f"Search: {q}", r.url)
                else:
                    self._set(raw.decode(enc, "replace"), None, r.url)
        except Exception as e:
            self._set(f"<h3>Could not load the page</h3><p>{str(e)[:200]}</p>", "Error", url)

    def _set(self, html, title, url):
        p = _HTML()
        try:
            p.feed(html)
        except Exception:
            pass
        self.base = url
        self.page_title = (title or p.title.strip() or url)[:60]
        self.title = f"RoboWeb - {self.page_title}"
        self.lines = self.layout(p.out, W - SBW - 10)
        self.loading = False
        self._ver += 1

    def layout(self, tokens, width):
        meas = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        lines, cur, x = [], [], 0

        def newline(force=False):
            nonlocal cur, x
            if cur or force:
                lines.append(cur)
            cur, x = [], 0

        for t in tokens:
            if t[0] == "\r":                     # line break
                if cur:
                    newline()
                continue
            if t[0] == "\n":                     # paragraph: at most one blank line
                if cur:
                    newline()
                if lines and lines[-1]:
                    newline(force=True)
                continue
            text, style, href = t
            f = font_for(style)
            for word in re.findall(r"\S+\s*|\s+", text):
                w = meas.textlength(word, font=f)
                if x + w > width and cur:
                    newline()
                    word = word.lstrip()
                    if not word:
                        continue
                    w = meas.textlength(word, font=f)
                while w > width:                   # very long word: hard split
                    n = max(1, int(len(word) * width / w) - 1)
                    cur.append((x, word[:n], style, href))
                    newline()
                    word = word[n:]
                    w = meas.textlength(word, font=f)
                if cur and cur[-1][2] == style and cur[-1][3] == href:
                    px, ptxt, _, _ = cur[-1]
                    cur[-1] = (px, ptxt + word, style, href)
                else:
                    cur.append((x, word, style, href))
                x += w
        newline()
        while lines and not lines[-1]:
            lines.pop()
        return lines[:4000]

    def follow(self, href):
        if not href:
            return
        if href.startswith("about:"):
            return self.go(href)
        url = urljoin(self.base, href)
        q = parse_qs(urlparse(url).query)
        if "duckduckgo.com" in url and "uddg" in q:            # DuckDuckGo result redirect: go straight to the site
            url = q["uddg"][0]
        if urlparse(url).scheme not in ("http", "https"):
            return self.d.toast("only web links work here")
        m = re.search(r"(?:youtube\.com/watch\?v=|youtu\.be/)([\w-]{11})", url)
        if m:                                                   # YouTube links open in RoboTube
            self.d.open_app("tube")
            tube = self.d.focused()
            tube.play({"id": m.group(1), "title": "YouTube video"})
            return
        self.go(url)

    def submit(self):
        text = self.addr.text.strip()
        self.editing = False
        if not text:
            return
        if " " in text or "." not in text:
            self.go("https://search.brave.com/search?q=" + quote_plus(text))
        else:
            self.go(text if re.match(r"^[a-z]+:", text) else "https://" + text)

    # -- drawing
    def draw(self, img, d, box):
        x0, y0, x1, y1 = box
        d.rectangle((x0, y0, x1, y0 + 18), fill=FACE)
        self.tools = []
        for j, (label, act) in enumerate((("◀", "back"), ("⌂", "home"), ("⟳", "reload"))):
            b = (x0 + 2 + j * 20, y0 + 1, x0 + 20 + j * 20, y0 + 17)
            button(d, b, label, font=F_BOLD)
            self.tools.append((b, act))
        self.addrbox = (x0 + 64, y0 + 2, x1 - 2, y0 + 16)
        if not self.editing and not self.addr.text:
            bevel(d, self.addrbox, raised=False, fill=LIGHT)
            d.text((self.addrbox[0] + 3, self.addrbox[1] + 2), "type a web address or search…", font=F_UI, fill=SHADOW)
        else:
            self.addr.draw(d, self.addrbox, self.editing)
        body = (x0, y0 + 19, x1, y1 - (Keyboard.HEIGHT if self.editing else 0))
        d.rectangle((body[0], body[1], body[2] - SBW - 1, body[3]), fill=LIGHT)
        view = max(1, (body[3] - body[1] - 4) // self.LINE_H)
        self.top = max(0, min(self.top, max(0, len(self.lines) - view)))
        self.links = []
        for k, line in enumerate(self.lines[self.top:self.top + view]):
            yy = body[1] + 2 + k * self.LINE_H
            for (lx, text, style, href) in line:
                f = font_for(style)
                col = BLUE if style == "link" else (SHADOW if style == "dim" else DARK)
                d.text((body[0] + 4 + lx, yy), text, font=f, fill=col)
                if style == "link" or href:
                    w = d.textlength(text, font=f)
                    if style == "link":
                        d.line([(body[0] + 4 + lx, yy + 11), (body[0] + 4 + lx + w, yy + 11)], fill=BLUE)
                    self.links.append(((body[0] + 4 + lx, yy - 1, body[0] + 4 + lx + w, yy + self.LINE_H), href))
        if self.loading:
            b = (body[0] + 80, body[1] + 60, body[2] - 80, body[1] + 84)
            bevel(d, b)
            d.text((b[0] + 30, b[1] + 6), "Loading…", font=F_BOLD, fill=DARK)
        self.vbar.draw(d, (body[2] - SBW + 1, body[1], body[2], body[3]), self.top, len(self.lines), view)
        self.view_rows = view
        if self.editing:
            self.kb.draw(img, (x0, y1 - Keyboard.HEIGHT + 1, x1, y1))

    def click(self, x, y):
        for b, act in getattr(self, "tools", []):
            if inside(b, x, y):
                if act == "back" and self.history:
                    url, top = self.history.pop()
                    self.go(url, push=False)
                    self._restore_top = top
                elif act == "home":
                    self.go("about:home")
                elif act == "reload":
                    self.go(self.url, push=False)
                return
        if inside(self.addrbox, x, y):
            self.editing = True
            self.addr.cur = len(self.addr.text)
            self.fresh = True                                   # first key typed replaces the old address
            return
        if self.editing:
            k = self.kb.click(x, y)
            if k is not None:
                self.key(k)
                return
        if self.vbar.hit(x, y):
            self.top = self.vbar.click(x, y)
            return
        for b, href in getattr(self, "links", []):
            if inside(b, x - 2, y) or inside(b, x + 2, y):
                self.editing = False
                return self.follow(href)
        if self.editing:
            self.editing = False

    def key(self, k):
        if self.editing:
            if k == "ENTER":
                self.submit()
            elif k == "ESC":
                self.editing = False
            else:
                if getattr(self, "fresh", False) and (len(k) == 1 or k == "BKSP"):
                    self.addr = LineEdit("")
                    if k == "BKSP":
                        k = ""
                self.fresh = False
                if k:
                    self.addr.key(k)
            return
        if k in ("UP", "DOWN", "PGUP", "PGDN", " "):
            self.scroll({"UP": -1, "DOWN": 1, "PGUP": -10, "PGDN": 10, " ": 10}[k])
        elif k == "BKSP" and self.history:
            url, top = self.history.pop()
            self.go(url, push=False)
        elif len(k) == 1:                         # typing anywhere starts editing the address
            self.editing = True
            self.addr = LineEdit(k)

    def press(self, x, y):
        self._grab = self.vbar.hit(x, y) and self.vbar.grab(x, y)

    def release(self, x, y):
        self._grab = False

    def hold(self, x, y):
        if self.vbar.hit(x, y) and self.vbar.on_arrow(x, y):
            self.top = self.vbar.click(x, y)
            return True
        return False

    def drag(self, x, y, dx, dy):
        if self._grab:
            self.top = self.vbar.drag(y)
            return True
        self.d._drag_acc += dy
        steps = int(self.d._drag_acc / self.LINE_H)
        if steps:
            self.top = max(0, self.top - steps)
            self.d._drag_acc -= steps * self.LINE_H
        return True

    def scroll(self, n):
        self.top = max(0, self.top + n)

    def tick(self):
        if self._ver != self._seen:
            self._seen = self._ver
            if getattr(self, "_restore_top", None) is not None and not self.loading:
                self.top, self._restore_top = self._restore_top, None
            return True
        return self.loading or self.editing


# ---------------------------------------------------------------- RoboTube
class Tube(App):
    portable = True
    name, title, icon = "tube", "RoboTube", "tube"
    FPS = 6

    def __init__(self, desk):
        super().__init__(desk)
        self.q = LineEdit("")
        self.editing = True
        self.kb = Keyboard()
        self.results = []
        self.status = "Search for a video."
        self.busy = False
        self.mode = "search"            # or "play"
        self.frame = None
        self.new_frame = False
        self.stop_ev = threading.Event()
        self.video = False              # tells the desktop to use fast, lower-quality frames
        self.fast = False
        self.now = None
        self.top = 0
        self.vbar = Scroll()
        self.controls_until = 0

    @staticmethod
    def _ydl(extra=None):
        import yt_dlp
        opts = {"quiet": True, "no_warnings": True, "skip_download": True, "js_runtimes": {"node": {}}}
        opts.update(extra or {})
        return yt_dlp.YoutubeDL(opts)

    def search(self):
        text = self.q.text.strip()
        if not text:
            return
        self.editing, self.busy, self.status, self.results = False, True, f"Searching “{text}”…", []
        threading.Thread(target=self._search, args=(text,), daemon=True).start()

    def _search(self, text):
        try:
            with self._ydl({"extract_flat": True}) as y:
                r = y.extract_info(f"ytsearch12:{text}", download=False)
            self.results = [e for e in r.get("entries", []) if e and e.get("id")]
            self.status = f"{len(self.results)} videos" if self.results else "Nothing found."
        except Exception as e:
            self.status = "Search failed: " + str(e)[:80]
        self.busy, self.top = False, 0

    def play(self, e):
        self.stop_play()
        self.stop_ev.clear()
        self.mode, self.now, self.frame, self.busy = "play", e, None, True
        self.status = "Loading video…"
        self.title = "RoboTube - " + (e.get("title") or "")[:40]
        self.fast = self.video = True
        threading.Thread(target=self._play, args=(e,), daemon=True).start()

    def _play(self, e):
        import cv2
        try:
            # YouTube serves video and audio separately now: a small H.264 video (OpenCV decodes it) + an audio stream
            fmt = "(133/134/160/bv*[height<=240][vcodec^=avc1]/bv*[height<=360])+(139/140/ba)/b[height<=360]/b"
            with self._ydl({"format": fmt}) as y:
                info = y.extract_info("https://www.youtube.com/watch?v=" + e["id"], download=False)
            parts = info.get("requested_formats") or [info]
            vurl = next((f["url"] for f in parts if f.get("vcodec") not in (None, "none")), None)
            aurl = next((f["url"] for f in parts if f.get("acodec") not in (None, "none")), None)
            if not vurl:
                raise RuntimeError("no playable video stream")
        except Exception as ex:
            self.status = "Could not play: " + str(ex).replace("ERROR: ", "")[:90]
            self.busy = False
            return
        cap = cv2.VideoCapture(vurl, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            self.status, self.busy = "Could not open the video stream.", False
            return
        if aurl:
            self.d.audio.play(aurl, name=e.get("title"), dur=float(info.get("duration") or 0))
        start = time.time()
        self.busy = False
        self.status = ""
        while not self.stop_ev.is_set():
            t = time.time() - start
            vt = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            while vt < t - 0.25 and not self.stop_ev.is_set():      # behind: skip frames
                if not cap.grab():
                    vt = None
                    break
                vt = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if vt is None:
                break
            ok, fr = cap.read()
            if not ok:
                break
            fr = cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)
            h, w = fr.shape[:2]
            scale = min(320 / w, 186 / h)
            fr = cv2.resize(fr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
            self.frame = Image.fromarray(fr)
            self.new_frame = True
            ahead = (cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0) - (time.time() - start)
            time.sleep(max(1.0 / self.FPS, ahead))
        cap.release()
        if not self.stop_ev.is_set():
            self.status = "Finished."

    def stop_play(self):
        self.stop_ev.set()
        if self.d.audio.playing and str(self.d.audio.source).startswith("http"):
            self.d.audio.stop()
        self.fast = self.video = False

    def save_audio(self, e):
        self.d.toast("saving audio to Music…", 4)

        def run():
            try:
                MUSIC_DIR.mkdir(parents=True, exist_ok=True)
                with self._ydl({"format": "bestaudio[ext=m4a]/bestaudio", "skip_download": False,
                                "outtmpl": str(MUSIC_DIR / "%(title).60s.%(ext)s")}) as y:
                    y.download(["https://www.youtube.com/watch?v=" + e["id"]])
                self.d.toast("saved to Music")
            except Exception as ex:
                self.d.toast("save failed: " + str(ex)[:40])
        threading.Thread(target=run, daemon=True).start()

    def draw(self, img, d, box):
        x0, y0, x1, y1 = box
        if self.mode == "play":
            d.rectangle(box, fill=DARK)
            if self.frame is not None:
                fx = x0 + (x1 - x0 + 1 - self.frame.width) // 2
                fy = y0 + (y1 - y0 + 1 - self.frame.height) // 2
                img.paste(self.frame, (fx, fy))
            if self.status:
                d.text((x0 + 8, y0 + 8), self.status, font=F_BOLD, fill=LIGHT)
            self.ctl = []
            if time.time() < self.controls_until or self.status:
                bar = (x0, y1 - 22, x1, y1)
                d.rectangle(bar, fill=FACE)
                for j, (label, act) in enumerate((("◀ Back", "back"), ("■ Stop", "stop"), ("♪ Save audio", "save"))):
                    b = (x0 + 4 + j * 92, y1 - 20, x0 + 92 + j * 92, y1 - 2)
                    button(d, b, label)
                    self.ctl.append((b, act))
            return
        d.rectangle((x0, y0, x1, y0 + 18), fill=FACE)
        self.qbox = (x0 + 2, y0 + 2, x1 - 58, y0 + 16)
        if not self.q.text and not self.editing:
            bevel(d, self.qbox, raised=False, fill=LIGHT)
            d.text((self.qbox[0] + 3, self.qbox[1] + 2), "search YouTube…", font=F_UI, fill=SHADOW)
        else:
            self.q.draw(d, self.qbox, self.editing)
        self.sbtn = (x1 - 54, y0 + 1, x1 - 2, y0 + 17)
        button(d, self.sbtn, "Search")
        body = (x0, y0 + 19, x1, y1 - (Keyboard.HEIGHT if self.editing else 0))
        d.rectangle((body[0], body[1], body[2] - SBW - 1, body[3]), fill=LIGHT)
        row_h = 26
        view = max(1, (body[3] - body[1]) // row_h)
        self.top = max(0, min(self.top, max(0, len(self.results) - view)))
        self.rhits = []
        if not self.results:
            d.text((body[0] + 8, body[1] + 8), self.status, font=F_UI, fill=SHADOW)
        for k, e in enumerate(self.results[self.top:self.top + view]):
            yy = body[1] + k * row_h
            d.text((body[0] + 4, yy + 2), fit_text(d, e.get("title") or "?", body[2] - body[0] - SBW - 10, F_BOLD), font=F_BOLD, fill=DARK)
            who = e.get("channel") or e.get("uploader") or ""
            sub = mmss(e.get("duration")) + (f"  ·  {who}" if who else "")
            d.text((body[0] + 4, yy + 13), fit_text(d, sub, body[2] - body[0] - SBW - 10), font=F_UI, fill=SHADOW)
            d.line([(body[0], yy + row_h - 1), (body[2] - SBW - 1, yy + row_h - 1)], fill=FACE)
            self.rhits.append(((body[0], yy, body[2] - SBW - 1, yy + row_h - 1), e))
        self.vbar.draw(d, (body[2] - SBW + 1, body[1], body[2], body[3]), self.top, len(self.results), view)
        if self.editing:
            self.kb.draw(img, (x0, y1 - Keyboard.HEIGHT + 1, x1, y1))

    def click(self, x, y):
        if self.mode == "play":
            for b, act in getattr(self, "ctl", []):
                if inside(b, x, y):
                    if act == "back":
                        self.stop_play()
                        self.mode, self.title, self.status = "search", "RoboTube", f"{len(self.results)} videos"
                    elif act == "stop":
                        self.stop_play()
                        self.status = "Stopped."
                    elif act == "save" and self.now:
                        self.save_audio(self.now)
                    return
            self.controls_until = time.time() + 4         # tap the video: show the controls
            return
        if inside(self.qbox, x, y):
            self.editing = True
            return
        if inside(self.sbtn, x, y):
            return self.search()
        if self.editing:
            k = self.kb.click(x, y)
            if k is not None:
                return self.key(k)
        if self.vbar.hit(x, y):
            self.top = self.vbar.click(x, y)
            return
        for b, e in getattr(self, "rhits", []):
            if inside(b, x, y):
                return self.play(e)
        self.editing = False

    def context(self, x, y):
        for b, e in getattr(self, "rhits", []):
            if inside(b, x, y) and self.mode == "search":
                self.d.popup = {"x": x, "y": y, "items": [("Play", lambda: self.play(e)), ("Save audio to Music", lambda: self.save_audio(e))]}
                return

    def key(self, k):
        if self.mode == "play":
            if k in ("ESC", "BKSP"):
                self.stop_play()
                self.mode, self.title = "search", "RoboTube"
            return
        if k == "ENTER":
            return self.search()
        if k == "ESC":
            self.editing = False
            return
        self.editing = True
        self.q.key(k)

    def drag(self, x, y, dx, dy):
        self.d._drag_acc += dy
        steps = int(self.d._drag_acc / 26)
        if steps:
            self.top = max(0, self.top - steps)
            self.d._drag_acc -= steps * 26
        return True

    def scroll(self, n):
        self.top = max(0, self.top + n)

    def tick(self):
        if self.new_frame:
            self.new_frame = False
            return True
        return self.busy or self.editing or (self.mode == "play" and time.time() < self.controls_until + 0.5)

    def close(self):
        self.stop_play()


# ---------------------------------------------------------------- Bluetooth
