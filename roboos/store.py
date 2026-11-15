"""Apps from other people: installed into data/apps/<id>/, loaded at start, managed by the Store app.

An app is a folder:
    data/apps/stopwatch/
        app.json     {"id": "stopwatch", "name": "Stopwatch", "version": "1.0.0", "api": 1, "class": "Stopwatch",
                      "author": "...", "description": "...", "permissions": ["voice"], "icon": "icon.png"}
        app.py       from roboos.sdk import *   then   class Stopwatch(App): ...
        icon.png     optional, 24x24 (or any square; scaled)
        data/        the app's own files (host.data_dir()), kept when the app is updated

Write one by hand into data/apps/ (it loads at the next start, or tap Reload in the Store), or install from the
store: the Store app reads an index (config desktop.store_url, default the roboos-apps repo's GitHub Pages) and
installs zips after checking their SHA-256. Apps from the official index are reviewed before they are listed.

Honest limit: an app is Python running inside RoboOS, with the same rights as RoboOS (root on the robot).
"permissions" in app.json say what it uses so people can decide; nothing enforces them. Install apps you trust.
"""
import hashlib
import importlib.util
import io
import json
import re
import shutil
import threading
import zipfile
from urllib.parse import urljoin

from PIL import Image

from . import API
from .desktop import (DARK, F_BOLD, F_UI, FACE, ICON_IMAGES, NAVY, RED, SHADOW, App, ListApp, bevel, button, draw_icon,
                      fit_text, inside)
from .paths import APPS_DIR

STORE_URL = "https://cperos-xr.github.io/roboos-apps/index.json"
ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
MAX_ZIP = 4 * 1024 * 1024
PERMS = {"voice": "talks", "ai": "uses the AI", "net": "uses the internet", "camera": "uses the camera",
         "mic": "uses the microphone", "files": "reads and writes files", "screen": "draws to the screen directly"}
_loads = 0


def read_manifest(folder):
    m = json.loads((folder / "app.json").read_text())
    if not isinstance(m, dict):
        raise ValueError("app.json is not an object")
    if not ID_RE.match(str(m.get("id", ""))):
        raise ValueError("id must be 2-32 lower-case letters, digits or _ and start with a letter")
    if m["id"] != folder.name:
        raise ValueError(f"id {m['id']!r} does not match its folder {folder.name!r}")
    if int(m.get("api", 1)) > API:
        raise ValueError(f"needs RoboOS app API {m.get('api')} (this RoboOS has {API}): update RoboOS")
    for k in ("name", "version"):
        if not str(m.get(k, "")).strip():
            raise ValueError(f"app.json has no {k}")
    return m


def load_app(folder, builtin_names=()):
    """Import one installed app. Returns its App class (name = the app id). Raises with a readable reason."""
    global _loads
    m = read_manifest(folder)
    if m["id"] in builtin_names:
        raise ValueError(f"id {m['id']!r} is taken by a built-in app")
    _loads += 1                                        # a fresh module name each load: updates without a restart
    spec = importlib.util.spec_from_file_location(f"roboos_apps.{m['id']}_{_loads}", folder / m.get("entry", "app.py"))
    if not spec or not spec.loader:
        raise ValueError("no app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    want = m.get("class")
    cls = getattr(mod, want, None) if want else next(
        (v for v in vars(mod).values() if isinstance(v, type) and issubclass(v, App) and v.__module__ == mod.__name__), None)
    if not (isinstance(cls, type) and issubclass(cls, App)):
        raise ValueError(f"app.py has no App class{' ' + want if want else ''}")
    cls.name, cls.portable, cls.manifest = m["id"], True, m
    if cls.title in ("App", None):
        cls.title = m["name"]
    cls.short = cls.short or m["name"][:12]
    icon = m.get("icon")
    if icon and (folder / icon).is_file():
        try:
            ICON_IMAGES[m["id"]] = Image.open(folder / icon).convert("RGBA")
            cls.icon = m["id"]
        except Exception:
            pass
    elif icon and not str(icon).endswith(".png"):
        cls.icon = icon                                # one of RoboOS's own icons, e.g. "games"
    if cls.icon in (None, "about") and m["id"] not in ICON_IMAGES:
        cls.icon = "app"
    return cls


def load_installed(desk):
    """At start: every folder in data/apps with an app.json (skipping ones turned off in the Store)."""
    off = set(desk.host.get("desktop", "apps_off", []) or [])
    builtin = set(desk.app_by_name)
    if not APPS_DIR.is_dir():
        return
    for folder in sorted(APPS_DIR.iterdir()):
        if not folder.is_dir() or folder.name.startswith(".") or not (folder / "app.json").exists() or folder.name in off:
            continue
        try:
            cls = load_app(folder, builtin)
            desk.apps.append(cls)
            desk.app_by_name[cls.name] = cls
            desk.broken.pop(folder.name, None)
        except Exception as e:
            desk.broken[folder.name] = str(e)[:120]
            desk.host.log(f"store: app {folder.name} did not load: {e}", "warn")


def installed():
    """{id: manifest} of the folders in data/apps (loaded or not)."""
    out = {}
    if APPS_DIR.is_dir():
        for folder in APPS_DIR.iterdir():
            try:
                out[folder.name] = read_manifest(folder)
            except Exception:
                pass
    return out


def _vkey(v):
    return [int(x) if x.isdigit() else x for x in re.split(r"[.\-+]", str(v))]


def install_zip(data, expect_id, sha256=None):
    """Unpack an app zip into data/apps/<id> (its data/ folder is kept). Returns the manifest."""
    if sha256 and hashlib.sha256(data).hexdigest() != sha256.lower():
        raise ValueError("the download does not match its checksum")
    APPS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = APPS_DIR / f".new-{expect_id}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        prefix = f"{expect_id}/" if all(n.startswith(f"{expect_id}/") for n in names) else ""
        for info in z.infolist():
            rel = info.filename[len(prefix):]
            if not rel or rel.endswith("/"):
                continue
            target = (tmp / rel).resolve()
            if tmp.resolve() not in target.parents or rel.startswith("data/"):
                raise ValueError(f"bad path in the zip: {info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(info))
    dest = APPS_DIR / expect_id
    try:
        if not (tmp / "app.json").exists():
            raise ValueError("the zip has no app.json")
        m = json.loads((tmp / "app.json").read_text())
        if m.get("id") != expect_id:
            raise ValueError(f"the zip is app {m.get('id')!r}, not {expect_id!r}")
        if (dest / "data").is_dir():
            shutil.move(str(dest / "data"), str(tmp / "data"))
        old = APPS_DIR / f".old-{expect_id}"
        shutil.rmtree(old, ignore_errors=True)
        if dest.exists():
            dest.rename(old)
        tmp.rename(dest)
        shutil.rmtree(old, ignore_errors=True)
        return read_manifest(dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class Store(ListApp):
    """Browse, install, update and remove apps (data/apps). The index: config desktop.store_url."""
    name, title, icon = "store", "App Store", "store"
    short = "Store"
    portable = True
    row_h = 30

    def __init__(self, desk):
        super().__init__(desk)
        self.index, self.err, self.busy, self.note = None, "", "", ""
        self.tab = "all"                               # all | installed
        self.sel = None                                # app id whose details show
        self.btns = []
        self.fetch()

    def url(self):
        return self.host.get("desktop", "store_url") or STORE_URL

    def fetch(self):
        self.err, self.busy = "", "Loading the app list..."
        self.d.dirty = True

        def work():
            try:
                import requests
                r = requests.get(self.url(), timeout=15, headers={"User-Agent": "RoboOS-Store/1"})
                r.raise_for_status()
                idx = r.json()
                if not isinstance(idx.get("apps"), list):
                    raise ValueError("not a RoboOS app index")
                self.index = idx
            except Exception as e:
                self.err = f"Could not load the store: {str(e)[:70]}"
            self.busy = ""
            self.d.dirty = True
        threading.Thread(target=work, daemon=True, name="store-fetch").start()

    # ---- the list: store apps (with what is installed), plus installed apps the store does not list
    def entries(self):
        have = installed()
        listed = {a.get("id"): a for a in (self.index or {}).get("apps", []) if isinstance(a, dict) and ID_RE.match(str(a.get("id", "")))}
        out = []
        for aid in sorted(set(listed) | set(have), key=lambda i: (listed.get(i) or have.get(i) or {}).get("name", i).lower()):
            a, m = listed.get(aid), have.get(aid)
            if self.tab == "installed" and not m and aid not in self.d.broken:
                continue
            state = ("broken" if aid in self.d.broken else
                     "update" if a and m and _vkey(a.get("version", "0")) > _vkey(m.get("version", "0")) else
                     "installed" if m else "get")
            if a and int(a.get("api", 1)) > API and state == "get":
                state = "too new"
            out.append({"id": aid, "a": a, "m": m, "state": state, "info": a or m or {}})
        return out

    def rows(self):
        return self.entries()

    def body(self, box):
        x0, y0, x1, y1 = box
        return (x0, y0 + 22, x1, y1 - (58 if self.sel else 16))

    def draw(self, img, d, box):
        x0, y0, x1, y1 = box
        d.rectangle(box, fill=FACE)
        self.btns = []
        for i, (tab, label) in enumerate((("all", "All apps"), ("installed", "Installed"))):
            b = (x0 + 3 + i * 72, y0 + 3, x0 + 72 + i * 72, y0 + 18)
            button(d, b, label, pressed=self.tab == tab, color=NAVY if self.tab == tab else DARK)
            self.btns.append((b, lambda t=tab: self.set_tab(t)))
        b = (x1 - 58, y0 + 3, x1 - 3, y0 + 18)
        button(d, b, "Reload")
        self.btns.append((b, self.reload))
        self.draw_list(d, self.body(box))
        if not self.rows():
            msg = self.busy or self.err or ("Nothing installed yet." if self.tab == "installed" else "No apps listed yet.")
            d.text((x0 + 8, y0 + 30), fit_text(d, msg, x1 - x0 - 30), font=F_UI, fill=RED if self.err else DARK)
        if self.sel:
            self.draw_details(d, box)
        else:
            status = self.note or self.busy or self.err or "Tap an app to see it. Apps run with full access: install ones you trust."
            d.text((x0 + 4, y1 - 13), fit_text(d, status, x1 - x0 - 8), font=F_UI, fill=RED if self.err and not self.note else SHADOW)

    def draw_row(self, d, box, e, i):
        x0, y0, x1, y1 = box
        if e["id"] == self.sel:
            d.rectangle((x0, y0, x1, y1), fill=(220, 225, 245))
        info = e["info"]
        cls = self.d.app_by_name.get(e["id"])
        draw_icon(d, getattr(cls, "icon", None) or info.get("icon_name") or "app", x0 + 3, y0 + 3, 24)
        d.text((x0 + 32, y0 + 2), fit_text(d, info.get("name", e["id"]), x1 - x0 - 110, F_BOLD), font=F_BOLD, fill=DARK)
        d.text((x0 + 32, y0 + 15), fit_text(d, info.get("description", ""), x1 - x0 - 40), font=F_UI, fill=SHADOW)
        tag = {"get": "Get", "installed": "Installed", "update": "Update", "broken": "Broken", "too new": "Needs update"}[e["state"]]
        col = {"get": NAVY, "update": NAVY, "broken": RED}.get(e["state"], SHADOW)
        d.text((x1 - 4 - d.textlength(tag, font=F_BOLD), y0 + 2), tag, font=F_BOLD, fill=col)

    def draw_details(self, d, box):
        x0, y0, x1, y1 = box
        e = next((e for e in self.entries() if e["id"] == self.sel), None)
        if not e:
            self.sel = None
            return
        r = (x0 + 2, y1 - 56, x1 - 2, y1 - 1)
        bevel(d, r, raised=True)
        info = e["info"]
        line1 = f"{info.get('name', e['id'])} {info.get('version', '')}" + (f" by {info['author']}" if info.get("author") else "")
        d.text((r[0] + 5, r[1] + 3), fit_text(d, line1, r[2] - r[0] - 10, F_BOLD), font=F_BOLD, fill=NAVY)
        perms = [PERMS.get(p, p) for p in info.get("permissions", []) if isinstance(p, str)]
        line2 = self.d.broken.get(e["id"]) or ("It " + ", ".join(perms) + "." if perms else "Uses nothing special.")
        d.text((r[0] + 5, r[1] + 17), fit_text(d, line2, r[2] - r[0] - 10), font=F_UI,
               fill=RED if e["id"] in self.d.broken else DARK)
        acts = []
        if e["state"] in ("get", "update") and e["a"]:
            acts.append(("Install" if e["state"] == "get" else "Update", lambda: self.install(e)))
        if e["m"] and e["id"] in self.d.app_by_name:
            acts.append(("Open", lambda: self.d.open_app(e["id"])))
        if e["m"] or e["id"] in self.d.broken:
            acts.append(("Remove", lambda: self.remove(e)))
        acts.append(("Close", lambda: setattr(self, "sel", None)))
        bx = r[0] + 5
        for label, fn in acts:
            b = (bx, r[3] - 20, bx + 62, r[3] - 4)
            button(d, b, label, color=RED if label == "Remove" else DARK)
            self.btns.append((b, fn))
            bx += 66

    # ---- actions
    def set_tab(self, t):
        self.tab, self.sel, self.top = t, None, 0

    def reload(self):
        """Load apps added by hand to data/apps, and fetch the store list again."""
        for aid in list(installed()):
            if aid not in self.d.app_by_name or aid in self.d.broken:
                self.load(aid)
        self.fetch()

    def load(self, aid):
        try:
            self.d.add_app(load_app(APPS_DIR / aid, {a.name for a in self.d.apps if not getattr(a, "manifest", None)}))
            self.d.broken.pop(aid, None)
            return True
        except Exception as e:
            self.d.broken[aid] = str(e)[:120]
            self.host.log(f"store: app {aid} did not load: {e}", "warn")
            return False

    def install(self, e):
        a = e["a"]
        perms = [PERMS.get(p, p) for p in a.get("permissions", []) if isinstance(p, str)]
        text = (f"{a.get('name', e['id'])} " + ("(" + ", ".join(perms) + ") " if perms else "") +
                "runs with full access to this computer.")
        self.d.confirm(f"Install {a.get('name', e['id'])}?", text, lambda: self._install(e))

    def _install(self, e):
        a = e["a"]
        self.busy, self.note = f"Installing {a.get('name', e['id'])}...", ""
        self.d.dirty = True

        def work():
            try:
                import requests
                url = urljoin(self.url(), a["zip"])
                r = requests.get(url, timeout=60, stream=True, headers={"User-Agent": "RoboOS-Store/1"})
                r.raise_for_status()
                data = r.raw.read(MAX_ZIP + 1, decode_content=True)
                if len(data) > MAX_ZIP:
                    raise ValueError("the app is too big")
                if not a.get("sha256"):
                    raise ValueError("the store did not give a checksum")
                was_open = [w for w in self.d.windows if w.name == e["id"]]
                for w in was_open:
                    self.d.close_app(w)
                install_zip(data, e["id"], a["sha256"])
                ok = self.load(e["id"])
                self.note = f"{a.get('name')} {a.get('version')} installed." if ok else f"Installed, but it did not load: {self.d.broken.get(e['id'])}"
                self.host.log(f"store: installed {e['id']} {a.get('version')}")
            except Exception as ex:
                self.note = f"Install failed: {str(ex)[:80]}"
            self.busy = ""
            self.d.dirty = True
        threading.Thread(target=work, daemon=True, name="store-install").start()

    def remove(self, e):
        name = e["info"].get("name", e["id"])
        self.d.confirm(f"Remove {name}?", "The app and its saved data are deleted.", lambda: self._remove(e["id"], name))

    def _remove(self, aid, name):
        self.d.remove_app(aid)
        self.d.broken.pop(aid, None)
        ICON_IMAGES.pop(aid, None)
        shutil.rmtree(APPS_DIR / aid, ignore_errors=True)
        icons = self.host.section("desktop").get("icons", [])
        if aid in icons:
            icons.remove(aid)
            self.host.save()
        self.sel, self.note = None, f"{name} removed."
        self.host.log(f"store: removed {aid}")
        self.d.dirty = True

    # ---- input
    def click(self, x, y):
        for b, fn in self.btns:
            if inside(b, x, y):
                fn()
                self.d.dirty = True
                return
        i = self.list_click(x, y)
        if i is not None:
            rows = self.rows()
            if i < len(rows):
                self.sel = None if self.sel == rows[i]["id"] else rows[i]["id"]
        self.d.dirty = True

    def tick(self):
        return bool(self.busy)
