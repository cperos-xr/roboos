"""Where RoboOS keeps things. ROOT = the folder that holds roboos/ (on the robot: /root/robot, like robotd).
Override with ROBOOS_ROOT (or the robot's ROBOT_ROOT). Everything personal lives in ROOT/data, never committed."""
import os
from pathlib import Path

ROOT = Path(os.environ.get("ROBOOS_ROOT") or os.environ.get("ROBOT_ROOT") or Path(__file__).resolve().parent.parent)
DATA = ROOT / "data"
PHOTOS = DATA / "photos"
APPS_DIR = DATA / "apps"          # apps installed from the store or by hand: data/apps/<id>/app.json + app.py
