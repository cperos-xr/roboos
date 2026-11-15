#!/bin/sh
# Install RoboOS on this computer: system packages, a Python venv, and (with --service) a systemd service.
#   ./install.sh             packages + venv
#   ./install.sh --service   also start RoboOS at boot (systemd unit "roboos")
# Safe to run again.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
SUDO=""
[ "$(id -u)" = 0 ] || SUDO="sudo"
USER_NAME="${SUDO_USER:-$(id -un)}"

echo "== system packages"
$SUDO apt-get update -q
$SUDO apt-get install -y -q python3 python3-venv python3-pil python3-numpy python3-serial python3-requests \
    fonts-dejavu-core alsa-utils gcc
$SUDO apt-get install -y -q ffmpeg || echo "(ffmpeg not installed: Music plays WAV only)"

echo "== Python venv ($DIR/venv)"
[ -x "$DIR/venv/bin/python" ] || python3 -m venv --system-site-packages "$DIR/venv"
"$DIR/venv/bin/pip" install -q -r "$DIR/requirements.txt"

if [ "$USER_NAME" != root ]; then
  echo "== groups: dialout (the screen), input (mouse and keyboard) for $USER_NAME"
  $SUDO usermod -aG dialout,input "$USER_NAME" || true
  echo "   (log out and in again for the groups to apply)"
fi

"$DIR/venv/bin/python" -m compileall -q "$DIR/roboos" >/dev/null
echo "== RoboOS compiles"

if [ "$1" = "--service" ]; then
  echo "== systemd service roboos"
  sed -e "s#@DIR@#$DIR#g" -e "s#@USER@#$USER_NAME#g" "$DIR/roboos.service" | $SUDO tee /etc/systemd/system/roboos.service >/dev/null
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now roboos
  echo "   started: journalctl -u roboos -f"
else
  echo "Run it:  $DIR/venv/bin/python -m roboos      (or ./install.sh --service to start it at boot)"
fi
