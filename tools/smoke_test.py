"""Headless smoke test: start RoboOS without a screen, open every app, draw it, tap it. Also installs a sample app
into a temporary data/apps and checks it loads. Exit code 1 on any failure. Used by CI."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="roboos-smoke-"))
os.environ["ROBOOS_ROOT"] = str(TMP)                   # data/ goes here, not into the checkout
(TMP / "roboos").symlink_to(ROOT / "roboos")
sys.path.insert(0, str(TMP))

app = TMP / "data" / "apps" / "smoke_hello"
app.mkdir(parents=True)
(app / "app.json").write_text(json.dumps({"id": "smoke_hello", "name": "Smoke", "version": "1.0.0", "api": 1}))
(app / "app.py").write_text("from roboos.sdk import *\n\nclass Smoke(App):\n    def draw(self, img, d, box):\n"
                            "        d.rectangle(box, fill=FACE)\n        center_text(d, box, 'hi', F_BIG, NAVY)\n")


class FakeScreen:
    info, page, up, hello_seen = {"img_max": 49152}, "face", False, False

    def send(self, msg):
        return False

    def send_image(self, *a, **k):
        return False


from roboos.desktop import Desktop          # noqa: E402
from roboos.host import Host                # noqa: E402

desk = Desktop(Host(FakeScreen()))
fails = []
if desk.broken:
    fails.append(f"installed apps did not load: {desk.broken}")
if "smoke_hello" not in desk.app_by_name:
    fails.append("the sample installed app is missing")
skip = {"power", "tube", "browser"}          # Shut Down acts on the machine; the web apps need the internet
for cls in desk.apps:
    if cls.name in skip:
        continue
    try:
        desk.open_app(cls.name)
        w = desk.windows[-1]
        png = desk.snapshot_png()
        box = desk.body_box()
        w.click((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
        w.tick()
        desk.snapshot_png()
        desk.close_app(w)
        print(f"ok   {cls.name} ({len(png)} bytes)")
    except Exception as e:
        fails.append(f"{cls.name}: {e!r}")
        print(f"FAIL {cls.name}: {e!r}")
print("\n".join(fails) or "all apps draw")
sys.exit(1 if fails else 0)
