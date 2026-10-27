"""Minimal desktop integration: open the UI in a window, and show an error box.

Deliberately nothing else: no tray icon, global hotkey, synthetic input, autostart entry or
hidden windows. The app runs only while its window is open.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
from pathlib import Path


def find_edge() -> str | None:
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            p = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if p.exists():
                return str(p)
    return shutil.which("msedge")


def open_ui(url: str, mode: str = "auto"):
    """Open the app as an Edge app window in the user's normal Edge profile, else the default browser."""
    edge = find_edge() if mode == "auto" else None
    if edge:
        subprocess.Popen([edge, f"--app={url}"], close_fds=True)
    else:
        os.startfile(url)


def message_box(text: str, title: str = "Idea Atlas", error: bool = False):
    ctypes.windll.user32.MessageBoxW(None, text, title, 0x10 if error else 0x40)
