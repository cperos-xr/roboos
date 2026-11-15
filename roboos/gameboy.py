"""Game Boy emulator core for RoboOS: Peanut-GB (MIT, roboos/gameboy_c/peanut_gb.h) behind a small C wrapper.

- The C code is compiled on the robot the first time a game starts (gcc, about 15 s) into data/gameboy/gbcore.so, and
  again whenever the source is newer than that library. Nothing binary is kept in the repo.
- Emulator runs the game in its own thread at the real speed (59.73 frames a second, about 5% of one CPU core) and
  keeps the newest frame. The screen shows as many of those frames as the USB link to it can carry.
- Original Game Boy games only (.gb, and .gbc games that also run on the original); no Game Boy Color, no sound yet.
- Battery saves go to data/gameboy/saves/<game>.sav (raw cartridge memory, the format most emulators use): every
  10 s while the game writes to it, when the game pauses and when it closes.
- Games are read from data/files/games/ and data/files/ (the shared folder: web app > Files > Upload).
"""
import ctypes
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .paths import DATA

SRC = Path(__file__).resolve().parent / "gameboy_c"
HOME = DATA / "gameboy"
LIB = HOME / "gbcore.so"
SAVES = HOME / "saves"
ROM_DIRS = [DATA / "files" / "games", DATA / "files"]
W, H = 160, 144
FPS = 4194304 / 70224                     # 59.73 frames a second, like the real one
BUTTONS = {"A": 0x01, "B": 0x02, "SELECT": 0x04, "START": 0x08, "RIGHT": 0x10, "LEFT": 0x20, "UP": 0x40, "DOWN": 0x80}
INIT_ERRORS = {1: "this kind of cartridge is not supported", 2: "not a Game Boy game (bad header checksum)",
               100: "not enough memory"}
FATAL = 2                                 # gbc_run_frame: invalid instruction (GB_INVALID_OPCODE + 1)

_lib = None
_build_lock = threading.Lock()


def build(log=None):
    """Compile the C core if needed and load it. Raises RuntimeError with a short reason."""
    global _lib
    with _build_lock:
        if _lib:
            return _lib
        newest = max(p.stat().st_mtime for p in (SRC / "gbcore.c", SRC / "peanut_gb.h"))
        if not LIB.exists() or LIB.stat().st_mtime < newest:
            cc = shutil.which("cc") or shutil.which("gcc")
            if not cc:
                raise RuntimeError("no C compiler on this computer (apt install gcc)")
            HOME.mkdir(parents=True, exist_ok=True)
            tmp = LIB.with_name("gbcore.tmp.so")
            t0 = time.time()
            r = subprocess.run([cc, "-O2", "-shared", "-fPIC", "-o", str(tmp), str(SRC / "gbcore.c")],
                               capture_output=True, text=True, timeout=600)
            if r.returncode:
                lines = [ln for ln in r.stderr.splitlines() if "error" in ln] or r.stderr.splitlines() or ["?"]
                raise RuntimeError("could not build the emulator: " + lines[0][-160:])
            os.replace(tmp, LIB)
            if log:
                log(f"gameboy: emulator built in {time.time() - t0:.0f} s")
        lib = ctypes.CDLL(str(LIB))
        V, S, I = ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int
        sig = {"gbc_new": ([ctypes.c_char_p, S, ctypes.POINTER(I)], V), "gbc_free": ([V], None),
               "gbc_run_frame": ([V], I), "gbc_set_buttons": ([V, ctypes.c_uint8], None), "gbc_frame": ([V], V),
               "gbc_save_size": ([V], S), "gbc_save_data": ([V], V), "gbc_load_save": ([V, ctypes.c_char_p, S], None),
               "gbc_take_dirty": ([V], I), "gbc_reset": ([V], None), "gbc_error": ([V, ctypes.POINTER(I)], I),
               "gbc_delta": ([V, ctypes.c_char_p, ctypes.c_char_p, S], S), "gbc_delta_reset": ([V], None),
               "gbc_peek": ([V, ctypes.c_uint16, ctypes.c_char_p, S], None)}
        for name, (args, res) in sig.items():
            f = getattr(lib, name)
            f.argtypes, f.restype = args, res
        _lib = lib
        return lib


def find_roms():
    """Game files in the shared folder, sorted by name."""
    seen, out = set(), []
    for folder in ROM_DIRS:
        try:
            files = sorted(folder.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for p in files:
            if p.suffix.lower() in (".gb", ".gbc") and p.is_file() and p.resolve() not in seen:
                seen.add(p.resolve())
                out.append(p)
    return out


def rom_title(data):
    raw = data[0x134:0x143].split(b"\0")[0]
    return "".join(ch for ch in raw.decode("ascii", "replace") if ch.isprintable()).strip()


class Emulator:
    """One running game. Set .buttons (bits from BUTTONS), read .frame (160 x 144 bytes, shade 0 light .. 3 dark) and .seq
    (counts frames). It runs while .alive is fresh (the app touches it while its window is in front), else it pauses."""

    def __init__(self, path, log=None):
        self.path = Path(path)
        self.log = log or (lambda *a, **k: None)
        data = self.path.read_bytes()
        if len(data) < 0x150:
            raise ValueError("too small to be a Game Boy game")
        if len(data) > 8 << 20:
            raise ValueError("too big for a Game Boy game (over 8 MB)")
        if data[0x143] == 0xC0:
            raise ValueError("a Game Boy Color game: only original Game Boy games work")
        self.title = rom_title(data) or self.path.stem
        self.lib = build(self.log)
        err = ctypes.c_int(0)
        self.core = self.lib.gbc_new(data, len(data), ctypes.byref(err))
        if not self.core:
            raise ValueError(INIT_ERRORS.get(err.value, f"could not start it (error {err.value})"))
        self.save_path = SAVES / (self.path.stem + ".sav")
        try:
            sav = self.save_path.read_bytes()
            if sav:
                self.lib.gbc_load_save(self.core, sav, len(sav))
        except OSError:
            pass
        self.buttons = 0
        self.frame, self.seq = bytes(W * H), 0
        self._out = ctypes.create_string_buffer(0)
        self.fps = 0.0                    # emulated frames per second, measured
        self.error = ""
        self.alive = time.time()
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True, name="gameboy")
        self.thread.start()
        self.log(f"gameboy: started {self.title} ({self.path.name})", persist=False)

    @property
    def paused(self):
        return time.time() - self.alive > 0.6

    def _run(self):
        lib, core = self.lib, self.core
        step = 1 / FPS
        nxt = time.perf_counter()
        last_save = time.time()
        n, t_fps = 0, time.perf_counter()
        while self.running:
            if self.paused:
                self._save_if_dirty()
                self.fps, n, t_fps = 0.0, 0, time.perf_counter()
                time.sleep(0.05)
                nxt = time.perf_counter()
                continue
            lib.gbc_set_buttons(core, self.buttons & 0xFF)
            if lib.gbc_run_frame(core) == FATAL:
                addr = ctypes.c_int(0)
                lib.gbc_error(core, ctypes.byref(addr))
                self.error = f"the game stopped (invalid instruction at {addr.value:04X})"
                self.log(f"gameboy: {self.title}: {self.error}", "warn")
                break
            self.frame = ctypes.string_at(lib.gbc_frame(core), W * H)
            self.seq += 1
            n += 1
            p = time.perf_counter()
            if p - t_fps >= 2:
                self.fps, n, t_fps = n / (p - t_fps), 0, p
            if time.time() - last_save > 10:
                last_save = time.time()
                self._save_if_dirty()
            nxt += step
            dt = nxt - time.perf_counter()
            if dt > 0:
                time.sleep(dt)
            elif dt < -0.2:
                nxt = time.perf_counter()     # the robot was busy: skip the lost time instead of rushing to catch up
        self._save_if_dirty()

    def _save_if_dirty(self):
        """Only called from the game thread, or after it stopped."""
        try:
            if not self.core or not self.lib.gbc_take_dirty(self.core):
                return
            size = self.lib.gbc_save_size(self.core)
            if not size:
                return
            data = ctypes.string_at(self.lib.gbc_save_data(self.core), size)
            SAVES.mkdir(parents=True, exist_ok=True)
            tmp = self.save_path.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, self.save_path)
        except Exception as e:
            self.log(f"gameboy: could not save {self.save_path.name}: {e}", "warn")

    def delta(self, cap):
        """The rows that changed since the last delta() (all of them after delta_reset()), encoded for the screen's
        "gbf" message. Called from the desktop thread; touches only the copy kept for this, not the running game."""
        if not self.core:
            return b""
        if len(self._out) < cap:
            self._out = ctypes.create_string_buffer(cap)
        n = self.lib.gbc_delta(self.core, self.frame, self._out, cap)
        return self._out.raw[:n]

    def peek(self, addr, n=1):
        """n bytes of the Game Boy's memory from addr (a racy read while the game runs: fine for watching it)."""
        if not self.core:
            return bytes(n)
        buf = ctypes.create_string_buffer(n)
        self.lib.gbc_peek(self.core, addr, buf, n)
        return buf.raw

    def delta_reset(self):
        if self.core:
            self.lib.gbc_delta_reset(self.core)

    def _stop(self):
        """Stop the game thread. False if it did not stop (then the core must be left alone)."""
        self.running = False
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            return False
        self._save_if_dirty()
        return True

    def reset(self):
        """Like switching the Game Boy off and on (the save stays)."""
        if not self.core or not self._stop():
            return
        self.lib.gbc_reset(self.core)
        self.error = ""
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True, name="gameboy")
        self.thread.start()

    def close(self):
        if self.core and self._stop():
            self.lib.gbc_free(self.core)
            self.core = None
