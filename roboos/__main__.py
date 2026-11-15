"""Run RoboOS on its own: a CYD screen (ESP32-2432S028R with the RoboOS screen firmware) on USB, any Linux board.

    python3 -m roboos                    find the screen and show the desktop (USB mouse + keyboard work too)
    python3 -m roboos --port /dev/ttyUSB0
    python3 -m roboos --snapshot d.png   draw the desktop into a PNG and exit (no screen needed: for testing)
    python3 -m roboos --snapshot d.png --app store

Settings and everything personal: data/ next to roboos/ (or ROBOOS_ROOT/data). See README.md.
"""
import argparse
import time
from collections import defaultdict

from . import VERSION
from .host import Config, Host, Log
from .screen import CydLink


class Bus:
    """Messages from the screen ("cyd:dt" = a touch on the desktop page, ...)."""

    def __init__(self):
        self.handlers = defaultdict(list)

    def on(self, name, fn):
        self.handlers[name].append(fn)

    def emit(self, name, *a, **kw):
        for fn in list(self.handlers.get(name, [])):
            try:
                fn(*a, **kw)
            except Exception as e:
                print(f"handler error for {name}: {e!r}")


def main():
    ap = argparse.ArgumentParser(prog="roboos", description=f"RoboOS {VERSION}: a tiny desktop on a CYD touchscreen")
    ap.add_argument("--port", help="the screen's serial port (default: the first CH340 USB serial adapter)")
    ap.add_argument("--snapshot", metavar="PNG", help="draw the desktop into this PNG and exit (no screen needed)")
    ap.add_argument("--app", help="with --snapshot: open this app first")
    args = ap.parse_args()

    log, bus, cfg = Log(), Bus(), Config()
    screen = CydLink(bus, log)
    if args.port:
        screen.PATTERN = args.port
    host = Host(screen, cfg, log)
    from .desktop import Desktop
    desk = Desktop(host)

    if args.snapshot:
        if args.app:
            desk.open_app(args.app)
            time.sleep(1.5)                     # apps that load in the background (the Store's list) get a moment
            for w in desk.windows:
                w.tick()
        with open(args.snapshot, "wb") as f:
            f.write(desk.snapshot_png())
        print(f"wrote {args.snapshot}; apps: {', '.join(a.name for a in desk.apps)}"
              + (f"; did not load: {desk.broken}" if desk.broken else ""))
        return

    bus.on("cyd:dt", lambda m: desk.on_touch(m))
    bus.on("cyd:desk_redraw", lambda m: desk.force_full())
    bus.on("cyd:open_app", lambda m: desk.open_app(str(m.get("name", ""))))
    screen.start()
    log(f"RoboOS {VERSION} on {host.board()}: waiting for the screen ({screen.PATTERN})")
    asked = 0
    while True:
        if screen.up and screen.hello_seen:
            if screen.page == "desktop":
                desk.run()                      # returns when the screen leaves the desktop page or the link drops
            elif time.time() - asked > 2:       # RoboOS on its own: the screen always comes back to the desktop
                asked = time.time()
                screen.send({"t": "page", "name": "desktop"})
        time.sleep(0.3)


if __name__ == "__main__":
    main()
