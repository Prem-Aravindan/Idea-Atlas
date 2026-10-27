"""Entry point: start the local server, open the window, and exit once the window has been closed.

Lifecycle (no tray, no background mode):
  * double-click -> server starts on 127.0.0.1 -> the app window opens
  * the page pings every 25 s; closing the window sends a "closed" beacon
  * the app exits a few seconds after the window closes (a reload cancels that), or if the page
    has not been seen for 3 minutes (e.g. the browser crashed)
  * a hidden/minimised window reports so: browsers throttle or freeze its timers, so the limit is then
    HIDDEN_LIMIT instead; time the computer spent asleep never counts
  * double-clicking while it is already running just opens another window on the same server
"""
from __future__ import annotations

import logging
import sys
import threading
import time
import urllib.request

from . import __version__
from .config import data_dir, load_config
from .db import Database
from .desktop import message_box, open_ui
from .server import App, make_server

CLOSE_GRACE = 6          # seconds after the window closes before exiting (lets a reload reconnect)
IDLE_LIMIT = 180         # exit if the page has not been seen for this long (pings arrive every 25 s)
HIDDEN_LIMIT = 8 * 3600  # same, while the window is minimised or in the background (pings may stop entirely)
SLEEP_JUMP = 30          # a 1-second tick that took longer than this means the computer was asleep
FIRST_CONTACT = 120      # exit if no window ever connects


def _setup_logging():
    path = data_dir() / "atlas.log"
    try:
        if path.exists() and path.stat().st_size > 1_000_000:
            path.replace(path.with_suffix(".log.old"))
    except OSError:
        pass
    logging.basicConfig(filename=str(path), level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _already_running(port: int) -> bool:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/ping", headers={"X-Atlas": "1"})
        urllib.request.urlopen(req, timeout=2).read()
        return True
    except OSError:
        return False


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    _setup_logging()
    log = logging.getLogger("atlas")
    cfg = load_config()
    port = int(cfg["port"])
    url = f"http://127.0.0.1:{port}/"
    no_window = "--no-window" in argv          # tests only

    if _already_running(port):
        if not no_window:
            open_ui(url, cfg.get("browser", "auto"))
        return 0

    db = Database(data_dir() / "atlas.db")
    app = App(db, cfg)
    try:
        server = make_server(app, port)
    except OSError as e:
        message_box(f"Idea Atlas could not start on 127.0.0.1:{port} ({e}).\n"
                    f"Change \"port\" in {data_dir() / 'config.json'}.", error=True)
        return 1
    threading.Thread(target=server.serve_forever, name="http", daemon=True).start()
    log.info("Idea Atlas %s on %s, data in %s", __version__, url, data_dir())
    if not no_window:
        open_ui(url, cfg.get("browser", "auto"))

    started = time.time()
    tick = started
    while True:                                 # one check per second; nothing else runs
        time.sleep(1)
        now = time.time()
        if now - tick > SLEEP_JUMP and app.last_seen:
            app.last_seen = now                 # woke from sleep: give the window time to ping again
        tick = now
        if app.closing_at and now >= app.closing_at:
            reason = "window closed"
            break
        limit = HIDDEN_LIMIT if app.hidden else IDLE_LIMIT
        if app.last_seen and now - app.last_seen > limit:
            reason = f"window not seen for {limit // 60} minutes" + (" (hidden)" if app.hidden else "")
            break
        if not app.last_seen and now - started > FIRST_CONTACT:
            reason = "no window connected"
            break
    server.shutdown()
    db.close()
    log.info("stopped (%s)", reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
