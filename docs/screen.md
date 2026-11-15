# Screen: the CYD touchscreen

- Board: ESP32-2432S028R ("Cheap Yellow Display"). Screen ILI9341 320x240, touch XPT2046.
- Firmware: `firmware/cyd_dashboard/` (Arduino). The current version is in `common.h` as `FW_VER`.
- It connects to the Pi by USB serial: `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0` (usually `/dev/ttyUSB0`), 921600 baud.
- It also hosts the I2C sensors (IMU, PCF8574) and drives the earpiece.

## Pages

`face` (the robot's face, default), `menu`, `status`, `devices` (wiring map + list), `tests`, `camera` (live view + vision),
`photos`, `viewer`, `imu` (tilt view), `logs`, `ideas`, `settings`, `about`, `touchtest`, `desktop` (RoboOS, see `roboos.md`).

Settings page (firmware 0.8.0+): draws the Pi's one settings list (`settings.md`): group tabs on top (Sound, Screen,
Camera, AI, Posting, Robot), rows below. It is the same list as RoboOS Settings and the web app. Without the Pi it
falls back to the old local rows: Brightness, Theme, Auto brightness, Volume (0-3), AI brain, Rotate 180, Invert
colours, Panel driver, Touch (calibrate), Orange Pi (power menu), Desktop (RoboOS).
The screen's speaker volume is set by the Pi: the one volume while the screen speaker is the sound output, 0 while
another output (Bluetooth) is active. The screen's own beeps and chimes then play on that output instead.

About page (Menu > About): firmware info, memory, link status, **PAIR A PHONE** (QR code to log in to the web app).

Menu (firmware 0.7.0): 5 columns x 3 rows. The right column opens RoboOS: **Games** (the Games launcher), **RoboOS**
(the desktop) and **Bluetooth** (the speaker setup app). They send `open_app` so the Pi opens that RoboOS app.

Top bar (firmware 0.9.2+; owner: "back is top left makes more sense to me"): top left = "< Page title", middle = the
clock, top right = battery + link dot (tap the battery = Settings > Battery, see `battery.md`). Tap the left or middle
of the bar = back.
Face page top bar (it has no title): clock top left, the Bluesky numbers in the middle (heart = likes, person =
followers; tap = details).
(0.9.1 had the clock left and the title in the middle on every page; 0.9.0 the clock in the middle and a small title.)
The face's own MENU button (bottom right) opens the menu.

## Face expressions (who controls the face)

The screen keeps a **base mood** (normally `idle`) and can show a **temporary mood** over it (`{"t":"face","mood":m,"hold":ms}`);
when the hold ends it goes back to the base. A face message without `hold` changes the base.
- Since 2026-11-10 everything the robot does itself is temporary: `Robot.face()` adds `hold` (the words' `sec`, else 15 s),
  and the brain's `set_face` lasts 30 s. Reactions (smile, bump, listening, talking) never leave the face stuck.
- Only the owner's pick in the web app (Live tab, Face card, or `POST /api/face`) and shutdown change the base mood.
  The robot's reactions still play on top and return to that pick.
- **Auto** (web app) = `{"mood":"auto"}`: the base goes back to `idle`, so the face belongs to the robot again.

## How the Pi and screen talk

One JSON object per line. Binary data (JPEG pictures, audio) is sent as a JSON header plus raw bytes, and the
screen answers each one with `{"t":"ack","id":N,"ok":true}`.

Pi -> screen (`"t"` values): `face`, `toast`, `ask` (question with buttons), `page`, `beep`, `cfg` (brightness, volume,
theme...), `status`, `devices`, `tests`, `test_result`, `logs`/`log`, `ideas`, `files`, `img` (picture), `pcm` (audio),
`lips` (mouth movement), `brain`, `vision_mode`, `qr`, `listen_state`, `imu`, `led`, `time`, `calibrate`, `restart`, `ping`,
`shot` (send a screenshot), `social` (Bluesky numbers for the face corner), `gbf` (firmware 0.9.3+: the Game Boy
app's changed picture rows, 2 bits a pixel, drawn straight onto the desktop; format in `media.ino` `gbFrame`; the
screen's `hello` says `"gbf":1` when it can, and acks `ok:false` when it did not draw them, e.g. under a dialog).

Screen -> Pi: `hello` (version, memory, img_max), `hb` (heartbeat every few seconds), `touch`, `page`, `answer`
(button pressed), `run_test`, `snap`, `get_img`, `ls`, `read_text`, `describe`, `listen`, `brain_next`, `vision_next`,
`imu_raw` (IMU data at 50 Hz), `imu_level`, `imu_gyro_cal`, `pair_request`, `power`, `sleep`/`wake`, `rate`,
`dt` (raw touch on the desktop page), `desk_redraw` (please resend the whole desktop), `open_app` (menu tile),
`shot` rows + `shot_end` (screenshot: the LCD is read back row by row as base64 RGB565, big-endian; about 2.5 s,
the screen freezes meanwhile; `robotd/screencap.py` puts it together, `GET /api/screen.png`).

The Pi side of this is `robotd/links.py` (`CydLink`) and the handlers in `robotd/main.py`.

## Memory limit (important)

The ESP32 has about 190 KB of usable RAM. The face canvas takes 70 KB and the picture buffer up to 48 KB.
The screen reports its buffer sizes in `hello` (`img_max`, `prev_max`) and the Pi makes pictures fit.
**Do not add big features to the screen** (Bluetooth audio was tried and did not fit).

## Build and flash

1. You must ask the owner before flashing.
2. Build: GitHub Actions builds both firmwares on every push (`.github/workflows/firmware.yml`).
   - On the robot: `POST /api/firmware/fetch`, then `POST /api/firmware/flash {"target":"cyd","path":"<the .bin>"}`.
   - On the owner's PC: arduino-cli inside the Arduino IDE flatpak, board `esp32:esp32:esp32:PartitionScheme=huge_app`.
3. Flash by hand on the Pi (stop robotd first, it holds the port):
   ```bash
   systemctl stop robotd
   /root/robot/venv/bin/esptool.py --chip esp32 --port /dev/ttyUSB0 --baud 921600 write_flash -z 0x10000 app.bin
   systemctl start robotd
   ```
   Writing at 0x10000 keeps the settings and touch calibration (stored in NVS).
4. Check the version in the log: `CYD hello: cyd-dashboard <version>`.
5. If the build fails, **do not flash**. Fix the build first.
