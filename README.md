# RoboOS

A tiny Windows-95-style desktop for a $15 touchscreen. Plug a **CYD** (ESP32-2432S028R, "Cheap Yellow Display")
into any small Linux computer by USB, and that computer gets a touch desktop with a Start menu, a terminal, a file
browser, a text web browser, music, paint, games, a Game Boy emulator and an app store.

The computer draws everything (Python + Pillow) and sends only the changed part of the picture to the screen over
USB. The screen sends touches back. A USB mouse and keyboard plugged into the computer work too.

RoboOS started as the face of [Pip](https://github.com/cperos-xr/pip-robot), a small home robot, and runs fine
without the robot. On the robot it also talks, watches you play Game Boy, and posts its game wins; on its own it is a
desktop for your Orange Pi, Raspberry Pi or any other board that has USB.

## What you need

- A CYD: ESP32-2432S028R (320x240 resistive touch, CH340 USB). Other CYD variants may need firmware changes.
- A Linux computer with USB: Raspberry Pi, Orange Pi, an old laptop... Tested on an Orange Pi Zero (Armbian,
  Python 3.13). It is light: the Orange Pi Zero has 484 MB of RAM.
- A USB cable from the CYD to the computer. Optional: a USB mouse and keyboard.

## Install

```sh
git clone https://github.com/cperos-xr/roboos.git
cd roboos
./install.sh             # apt packages + a Python venv; --service also starts RoboOS at boot
```

### Put the RoboOS firmware on the screen

Every push builds the screen firmware (GitHub Actions > firmware > artifact `cyd-firmware`). On a fresh CYD write the
whole image once:

```sh
venv/bin/esptool.py --chip esp32 --port /dev/ttyUSB0 --baud 230400 write_flash 0x0 cyd_dashboard.ino.merged.bin
```

(230400 baud: faster speeds failed on some USB links. A black screen after a failed write means the chip is still in
its bootloader: run the command again.) Or build it yourself: `firmware/cyd_dashboard`, Arduino ESP32 core 3.3.12,
LovyanGFX 1.2.7, ArduinoJson 7.4.1, partition scheme "Huge APP".

The firmware is the one Pip uses, so it also has the robot's face and menus. RoboOS sends it straight to the desktop
and brings it back there if you leave.

### Run

```sh
venv/bin/python -m roboos                       # finds the screen (the first CH340 adapter)
venv/bin/python -m roboos --port /dev/ttyUSB0
venv/bin/python -m roboos --snapshot desk.png   # draw the desktop into a PNG, no screen needed
```

Reading the mouse and keyboard needs the `input` group (or root); the screen needs `dialout`. `install.sh` adds you.

## Apps

| App | |
|---|---|
| Terminal, Files, Notepad | a real shell with an on-screen keyboard; browse, view, edit |
| RoboWeb, RoboTube | a text web browser; YouTube at about 3 frames a second (needs yt-dlp, Node.js, OpenCV) |
| Music, Sound Recorder | play audio files, record from a USB mic |
| Paint | finger painting |
| Games | RoboMines, RoboMunch, Tic-Tac-Toe, Checkers |
| Game Boy | original Game Boy games (`.gb`) from `data/files/games` (homebrew games are free; none come with RoboOS). With an AI key, the AI can play, or watch you play and comment |
| System Monitor, Logs, About, Shut Down | |
| **App Store** | apps other people made: [roboos-apps](https://github.com/cperos-xr/roboos-apps) |

## Make an app

An app is a folder with `app.json` and `app.py`, about 20 lines for a first one. Read
[docs/WRITING_APPS.md](docs/WRITING_APPS.md), try it in `data/apps/<id>/`, then share it through
[roboos-apps](https://github.com/cperos-xr/roboos-apps).

## Settings

Everything personal lives in `data/` (never committed): `data/config.json`, saves, music, photos, installed apps.

| Setting (`data/config.json`) | |
|---|---|
| `desktop.audio_device` | play sound on an ALSA device (e.g. `"plughw:1,0"`) instead of the screen's little speaker |
| `desktop.mic` | the ALSA device to record from (default: the first card that can record) |
| `desktop.store_url` | another app index for the Store |
| `desktop.gb_ai_model` | a vision model for the Game Boy's AI player (OpenRouter), key `OPENROUTER_API_KEY` in `data/secrets.env` |
| `ai.provider`, `ai.model` | the AI apps may use (`host.ai_model()`) |

## How it fits together

- `roboos/desktop.py`: the window manager, input and the built-in apps. `roboos/media.py`, `games.py`, `munch.py`,
  `gameboy*.py`: more apps. `roboos/store.py`: installed apps and the Store.
- `roboos/host.py`: everything apps need from the machine goes through a `Host` (settings, log, sound, system info,
  AI...). Pip's robot software subclasses it to add its voice and camera; RoboOS itself never imports the robot.
- `roboos/screen.py`: the USB serial link. `docs/screen.md`: the messages between the computer and the screen.

This repository is a snapshot of RoboOS from the Pip project. Pull requests are welcome here and are carried back.

## License

MIT (see `LICENSE`). The Game Boy core is [Peanut-GB](https://github.com/deltabeard/Peanut-GB) by Mahyar
Koshkouei (MIT). See `NOTICE.md`.
