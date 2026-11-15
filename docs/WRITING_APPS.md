# Writing a RoboOS app

An app is a folder. Put it in `data/apps/<id>/` on any RoboOS and it shows up in the Start menu (at the next start, or
tap **Reload** in the App Store). To share it, send it to [roboos-apps](https://github.com/cperos-xr/roboos-apps).

```
data/apps/stopwatch/
    app.json
    app.py
    icon.png      optional: 24x24 (any square is scaled)
```

## app.json

```json
{
  "id": "stopwatch",
  "name": "Stopwatch",
  "version": "1.0.0",
  "api": 1,
  "class": "Stopwatch",
  "author": "your GitHub name",
  "description": "A stopwatch and a countdown timer.",
  "permissions": ["voice"],
  "icon": "icon.png"
}
```

| Field | |
|---|---|
| `id` | 2-32 characters, lower-case letters, digits and `_`, starts with a letter. The same as the folder name. |
| `name`, `version` | shown in the Store. Bump `version` for every update (`1.0.1`, `1.1.0`...). |
| `api` | the RoboOS app API you wrote it for: `1`. |
| `class` | the `App` class in `app.py` (if left out: the first one). |
| `permissions` | what it uses: `voice`, `ai`, `net`, `camera`, `mic`, `files`, `screen`. Shown before install. |
| `icon` | `icon.png`, or the name of a built-in icon (`games`, `paint`, `music`, `terminal`, `app`...). |

## app.py

```python
from roboos.sdk import *


class Hello(App):
    title, icon = "Hello", "app"

    def __init__(self, desk):
        super().__init__(desk)
        self.taps = 0

    def draw(self, img, d, box):               # img: the PIL picture; d: ImageDraw on it; box: your window body
        x0, y0, x1, y1 = box
        d.rectangle(box, fill=FACE)
        center_text(d, (x0, y0, x1, y0 + 60), "Hello!", F_BIG, NAVY)
        button(d, (x0 + 110, y0 + 80, x1 - 110, y0 + 104), f"Tapped {self.taps}")

    def click(self, x, y):                     # desktop coordinates, like box
        self.taps += 1
        self.host.say("Hello!")                # out loud where there is a voice, silent elsewhere
```

The screen is 320 x 220 desktop pixels (`W`, `H`). Your window body is the `box` given to `draw()`; everything you get
(touches, clicks) is in the same coordinates.

### Methods you can override

| Method | When |
|---|---|
| `draw(img, d, box)` | the window needs drawing |
| `click(x, y)` | a tap (press and release in place) |
| `press(x, y)`, `drag(x, y, dx, dy)`, `release(x, y)` | finger down, moving, up |
| `hold(x, y)` | about 10 times a second while the finger rests; return True if you used it |
| `context(x, y)` | long-press or right-click |
| `key(k)`, `key_up(k)` | USB keyboard: `"a"`, `"ENTER"`, `"UP"`, `"ESC"`... (`key_up` only from real keyboards) |
| `scroll(n)` | mouse wheel |
| `tick()` | called often; return True to be redrawn (animations, clocks) |
| `close()` | the window closes: stop threads, save |

Class settings: `title`, `icon`, `short` (desktop label), `long_press = None` (holding is normal, e.g. drawing),
`fast = True` (tick very often: games), `video = True` (moving pictures: smaller, faster frames).

### The host: everything from the machine

Use only `self.host` and `self.d`. Never import RoboOS internals beyond `roboos.sdk`, and never reach for the robot:
the same app must run on a robot and on a bare screen.

| `self.host.` | |
|---|---|
| `section("myapp")`, `save()` | your settings: a dict saved in `data/config.json` (use your app id as the section) |
| `data_dir("myapp")` | a folder of your own for files (kept when your app is updated, deleted when removed) |
| `say(text, mood)`, `game_talk()` | speak (if there is a voice); whether games may talk |
| `log(msg)` | the log (Logs app) |
| `has("voice" / "ai" / "camera" / ...)` | is that part there? |
| `ai_model()` | `(provider, model)` of the AI, or `(None, None)` |
| `cpu_pct()`, `mem()`, `temp()`, `ips()`, `uptime()`, `board()` | system information |

| `self.d.` | |
|---|---|
| `toast(msg)` | a short message at the bottom |
| `confirm(title, text, on_yes)` | a Yes/No dialog |
| `open_app(id)` | open another app |
| `audio.play(path)`, `audio.stop()` | play a sound file (one sound at a time) |

Drawing helpers from `roboos.sdk`: colors `FACE DARK LIGHT SHADOW NAVY RED GREEN YELLOW TEAL`, fonts `F_UI F_BOLD
F_BIG F_MONO` and `font(name, size)`, `bevel`, `button`, `center_text`, `fit_text`, `inside`, `draw_icon`,
`scrollbar`/`Scroll`, `ListApp` (a scrolling list), `GameApp` (a game: settings and talk), `Keyboard` (on-screen
keyboard). This list is the API, version 1: it only grows.

## Try it without a screen

```sh
python3 -m roboos --snapshot shot.png --app stopwatch
```

## Rules for the store

- Your own work (or properly licensed), and original: no other games' names, characters, art, music or levels.
- `permissions` honest; no hidden network calls, no collecting anything about people.
- No secrets in the code. Keys go in `data/secrets.env` on the user's machine.
- Small and kind to a 32-bit board with 484 MB of RAM: no busy loops in `tick()`, threads stop in `close()`.
- An app runs with the same rights as RoboOS, so every app is read by a person before it is listed.
