"""Paths and user settings."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)

DEFAULTS = {
    "port": 47321,
    "browser": "auto",          # "auto" (Edge app window) or "default" (system browser tab)
}


def app_dir() -> Path:
    """Project root: holds data\\ and the program\\ folder (frozen), or the source tree."""
    if FROZEN:
        exe_dir = Path(sys.executable).resolve().parent
        return exe_dir.parent if exe_dir.name.lower() == "program" else exe_dir
    return Path(__file__).resolve().parents[2]


def static_dir() -> Path:
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS")) / "static"
    return Path(__file__).resolve().parents[1] / "static"


def data_dir() -> Path:
    d = Path(os.environ.get("IDEA_ATLAS_DATA") or app_dir() / "data")
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return data_dir() / "config.json"


def _read(p: Path) -> dict:
    try:
        # utf-8-sig: Notepad and PowerShell may save the file with a byte-order mark
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        import logging
        logging.getLogger("atlas").warning("config.json could not be read (%s); using defaults", e)
        return {}


def load_config() -> dict:
    p = config_path()
    saved = _read(p)
    if not p.exists():
        p.write_text(json.dumps(DEFAULTS, indent=2), encoding="utf-8")
    cfg = dict(DEFAULTS, **saved)
    if not FROZEN:
        # a run from source (development/tests) never takes the installed app's port, even on the real data
        # folder; tests set "dev_port" explicitly
        cfg["port"] = saved.get("dev_port", 47390)
    return cfg
