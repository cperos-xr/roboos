"""The CYD screen link: JSON lines + binary frames (JPEG pictures, Game Boy rows, PCM sound) over USB serial at
921600 baud. Messages from the screen are emitted on the bus as "cyd:<type>" (touches on the desktop page: "cyd:dt")."""
import glob
import json
import threading
import time
import zlib

import serial


def find_port(pattern):
    m = sorted(glob.glob(pattern))
    return m[0] if m else None


class CydLink(threading.Thread):
    PATTERN = "/dev/serial/by-id/usb-1a86_USB_Serial*"

    def __init__(self, bus, log):
        super().__init__(daemon=True, name="cyd")
        self.bus, self.log = bus, log
        self.ser = None
        self.wlock = threading.Lock()
        self.img_lock = threading.Lock()
        self.acks = {}
        self.ack_cv = threading.Condition()
        self.next_id = 1
        self.page = "face"
        self.info = {}
        self.last_rx = 0
        self.stats = {"tx_img": 0, "img_fail": 0, "rx": 0}
        self.waiters = {}
        self.wait_cv = threading.Condition()
        self.paused = threading.Event()
        self.hello_seen = False     # the screen's hello carries its I2C devices (IMU on CN1): much waits for it
        self._hello_ask = 0.0

    @property
    def up(self):
        return self.ser is not None and time.time() - self.last_rx < 6

    def send(self, obj):
        t = obj.get("t")
        if t in ("beep", "ask", "listen_state") or (t == "face" and obj.get("chime") is not None):
            self.bus.emit("cyd_sound", obj)     # the voice plays it on the Bluetooth speaker when that is the output
        ser = self.ser
        if not ser:
            return False
        data = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        try:
            with self.wlock:
                ser.write(data)
            return True
        except Exception:
            return False

    def send_pcm(self, pcm, rate=8000, chunk=2048):
        """Stream unsigned 8-bit mono audio to the CYD speaker; paced by the display's acks."""
        for i in range(0, len(pcm), chunk):
            if not self.send_image(pcm[i:i + chunk], timeout=4.0, kind="pcm", rate=rate):
                return False
        return True

    def send_image(self, jpeg, timeout=3.0, kind="img", **meta):
        """Send a binary payload (JPEG image or PCM audio) and wait for the display's ack."""
        if not self.ser or len(jpeg) > self.info.get("img_max", 48 * 1024):
            return False
        with self.img_lock:
            iid = self.next_id
            self.next_id += 1
            hdr = {"t": kind, "id": iid, "len": len(jpeg), "crc": zlib.crc32(jpeg) & 0xFFFFFFFF}
            hdr.update(meta)
            try:
                with self.wlock:
                    self.ser.write((json.dumps(hdr, separators=(",", ":")) + "\n").encode())
                    self.ser.write(jpeg)
            except Exception:
                return False
            with self.ack_cv:
                end = time.time() + timeout
                while iid not in self.acks and time.time() < end:
                    self.ack_cv.wait(end - time.time())
                ack = self.acks.pop(iid, None)
            ok = bool(ack and ack.get("ok"))
            self.stats["tx_img" if ok else "img_fail"] += 1
            return ok

    def pause(self, on):
        """Release the serial port (e.g. for esptool) and wait; pause(False) reconnects."""
        if on:
            self.paused.set()
            for _ in range(40):
                if self.ser is None:
                    break
                time.sleep(0.1)
        else:
            self.paused.clear()

    def request(self, msg, reply_type, timeout=2.0):
        """Send a message and wait for the first reply of type reply_type."""
        with self.wait_cv:
            self.waiters[reply_type] = None
        if not self.send(msg):
            return None
        with self.wait_cv:
            end = time.time() + timeout
            while self.waiters.get(reply_type) is None and time.time() < end:
                self.wait_cv.wait(end - time.time())
            return self.waiters.pop(reply_type, None)

    def _on_msg(self, msg):
        t = msg.get("t")
        if t in self.waiters:
            with self.wait_cv:
                self.waiters[t] = msg
                self.wait_cv.notify_all()
        self.last_rx = time.time()
        self.stats["rx"] += 1
        if t == "ack":
            with self.ack_cv:
                self.acks[msg.get("id")] = msg
                self.ack_cv.notify_all()
            return
        if t == "hello":
            self.hello_seen = True
            self.info = msg
            self.page = msg.get("page", "face")
            self.log(f"CYD hello: {msg.get('fw')} {msg.get('ver')} panel={msg.get('panel')} heap={msg.get('heap')} i2c={msg.get('i2c')}")
            self.send({"t": "hello_ack"})
            self.bus.emit("cyd_hello", msg)
            return
        if t in ("hb", "page"):
            new = msg.get("page") if t == "hb" else msg.get("name")
            if new and new != self.page:
                self.page = new
                self.bus.emit("page", new)
            if t == "hb":
                self.info["hb"] = msg
                if not self.hello_seen and time.time() - self._hello_ask > 3:
                    # the one "hello?" sent when the port opens can get lost (seen after a Pi reboot, 2026-11-08:
                    # no IMU stream, screen said "waiting for IMU..."): keep asking while the screen is alive
                    self._hello_ask = time.time()
                    self.log("CYD is alive but has not said hello yet: asking again")
                    self.send({"t": "hello?"})
                self.send({"t": "time", "hm": time.strftime("%H:%M")})   # keep the screen's link indicator alive
            return
        self.bus.emit("cyd:" + str(t), msg)

    def run(self):
        while True:
            if self.paused.is_set():
                time.sleep(0.3)
                continue
            port = find_port(self.PATTERN)
            if not port:
                time.sleep(2)
                continue
            try:
                ser = serial.Serial(port, 921600, timeout=0.05)
            except Exception as e:
                self.log(f"CYD port open failed: {e}", "warn")
                time.sleep(3)
                continue
            self.ser = ser
            self.log(f"CYD link open on {port}")
            self.hello_seen, self._hello_ask = False, time.time()
            self.send({"t": "hello?"})
            buf = b""
            try:
                while not self.paused.is_set():
                    chunk = ser.read(ser.in_waiting or 1)
                    if not chunk:
                        continue
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        line = line.strip()
                        if line.startswith(b"{"):
                            try:
                                self._on_msg(json.loads(line))
                            except ValueError:
                                pass
                    if len(buf) > 65536:
                        buf = b""
            except Exception as e:
                self.log(f"CYD link lost: {e}", "warn")
            finally:
                self.ser = None
                try:
                    ser.close()
                except Exception:
                    pass
                time.sleep(1)
