"""RoboOS: a tiny Windows-95-style desktop for a cheap ESP32 touchscreen (CYD) plugged into a small Linux board.

It began as the face of a home robot (imalittlerobot / Pip) and runs on its own too: `python -m roboos`.
- roboos/desktop.py   the window manager, input, the built-in apps
- roboos/host.py      what apps use to reach the machine (Host API); robotd gives it a RobotHost
- roboos/store.py     apps from other people: data/apps/<id>/ and the Store app
- roboos/sdk.py       the one import an app needs: `from roboos.sdk import *`
- roboos/screen.py    the USB serial link to the screen
"""
VERSION = "1.2"
API = 1          # the app API (roboos/sdk.py + Host). Apps say which one they need in app.json ("api": 1).
