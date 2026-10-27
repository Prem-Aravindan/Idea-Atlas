"""Loopback-only HTTP API + static UI. Idle cost: one thread blocked in select()."""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import subprocess
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import APP_NAME, __version__
from . import brief as B
from . import engine as E
from . import exporters as X
from . import search as S
from .config import data_dir, static_dir
from .db import Database

log = logging.getLogger("atlas.http")
ROUTES: list[tuple[str, re.Pattern, callable]] = []


def route(method: str, pattern: str):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


class App:
    """Shared state for handlers, plus the window lifecycle the main loop watches."""

    def __init__(self, db: Database, cfg: dict):
        self.db = db
        self.cfg = cfg
        self.last_seen = 0.0        # last request from a window
        self.closing_at = 0.0       # set when the window reports it closed; cleared by any later request
        self.hidden = False         # window minimised / in the background: the browser throttles or freezes its timers

    def seen(self):
        self.last_seen = time.time()
        self.closing_at = 0.0


# ---------------------------------------------------------------- routes

def _pid(a, m):
    return int(m.group(1))


@route("GET", r"/api/state")
def state(a: App, m, q, body):
    with a.db.read() as c:
        return {"app": APP_NAME, "version": __version__, "projects": E.list_projects(c),
                "data_dir": str(data_dir()), "kinds": E.NODE_KINDS, "edge_types": E.EDGE_TYPES,
                "xp": int(c.execute("SELECT value FROM meta WHERE key='xp'").fetchone()[0])}


@route("GET", r"/api/ping")
def ping(a: App, m, q, body):
    if "state" in q:                # sent on visibility changes; plain pings keep the last known state
        a.hidden = q["state"] == "hidden"
    return {"ok": True}


@route("POST", r"/api/window-closed")
def window_closed(a: App, m, q, body):
    # exit shortly unless the window comes back (a reload also sends this beacon)
    a.closing_at = time.time() + 6
    return {"ok": True}

@route("POST", r"/api/projects")
def create_project(a: App, m, q, body):
    with a.db.tx() as c:
        return E.create_project(c, body.get("name", ""), body.get("handle", ""), body.get("description", ""),
                                body.get("color", ""))


@route("PATCH", r"/api/projects/(\d+)")
def update_project(a: App, m, q, body):
    with a.db.tx() as c:
        return E.update_project(c, _pid(a, m), body)


@route("DELETE", r"/api/projects/(\d+)")
def delete_project(a: App, m, q, body):
    with a.db.tx() as c:
        E.delete_project(c, _pid(a, m))
    return {"ok": True}


@route("POST", r"/api/capture")
def capture(a: App, m, q, body):
    return E.capture(a.db, body.get("text", ""), default_pid=body.get("project_id"), dry_run=bool(body.get("dry_run")),
                     create_missing=bool(body.get("create_missing")), source=body.get("source", ""),
                     target_id=body.get("target_id"), target_mode=body.get("target_mode", "answer"))


@route("GET", r"/api/projects/(\d+)/graph")
def graph(a: App, m, q, body):
    focus = {int(x) for x in q.get("focus", "").split(",") if x.strip().isdigit()}
    kinds = set(filter(None, q.get("kinds", "").split(","))) or None
    if q.get("sources") != "1":
        kinds = (kinds or set(E.NODE_KINDS)) - {"reference"}
    with a.db.read() as c:
        return E.graph(c, _pid(a, m), focus_ids=focus or None, depth=int(q.get("depth", 1)), kinds=kinds,
                       include_suggested=q.get("suggested", "1") == "1", limit=int(q.get("limit", 70)))


@route("GET", r"/api/projects/(\d+)/nodes")
def nodes(a: App, m, q, body):
    pid = _pid(a, m)
    where, params = ["n.project_id=?"], [pid]
    if q.get("kind"):
        where.append("n.kind=?")
        params.append(q["kind"])
    if q.get("status"):
        where.append("n.status=?")
        params.append(q["status"])
    if q.get("confidence"):
        where.append("n.confidence=?")
        params.append(q["confidence"])
    order = {"updated": "n.updated DESC", "code": "n.kind, n.code", "title": "lower(n.title)",
             "links": "links DESC"}.get(q.get("sort", "updated"), "n.updated DESC")
    with a.db.read() as c:
        rows = c.execute(
            f"""SELECT n.*, (SELECT COUNT(*) FROM edges e WHERE (e.src=n.id OR e.dst=n.id) AND e.status!='rejected') AS links,
                (SELECT COUNT(DISTINCT note_id) FROM mentions WHERE node_id=n.id) AS sources
                FROM nodes n WHERE {' AND '.join(where)} ORDER BY {order} LIMIT 2000""", params).fetchall()
        return {"nodes": [dict(E.node_dict(r), links=r["links"], sources=r["sources"]) for r in rows]}


@route("GET", r"/api/projects/(\d+)/concepts")
def concepts(a: App, m, q, body):
    """Concepts and methods with their other names, for [[ suggestions while capturing."""
    with a.db.read() as c:
        return {"concepts": E.concept_list(c, _pid(a, m))}


@route("GET", r"/api/projects/(\d+)/requirements")
def requirements(a: App, m, q, body):
    with a.db.read() as c:
        return {"requirements": E.requirement_list(c, _pid(a, m)), "statuses": E.REQ_STATUSES}


@route("PATCH", r"/api/projects/(\d+)/requirements/([\w.\-]+)")
def requirement_update(a: App, m, q, body):
    with a.db.tx() as c:
        return E.update_requirement(c, _pid(a, m), m.group(2), body)


@route("GET", r"/api/projects/(\d+)/requirements/([\w.\-]+)/log")
def requirement_history(a: App, m, q, body):
    with a.db.read() as c:
        return {"log": E.requirement_log(c, _pid(a, m), m.group(2))}


@route("GET", r"/api/projects/(\d+)/tags")
def tags(a: App, m, q, body):
    with a.db.read() as c:
        return {"tags": E.tag_list(c, _pid(a, m))}


@route("POST", r"/api/nodes/(\d+)/split")
def node_split(a: App, m, q, body):
    with a.db.tx() as c:
        return E.split_from(c, int(m.group(1)), int(body["note_id"]))


@route("GET", r"/api/projects/(\d+)/glossary")
def glossary(a: App, m, q, body):
    with a.db.read() as c:
        return {"entries": E.glossary(c, _pid(a, m))}


@route("GET", r"/api/projects/(\d+)/notes")
def notes(a: App, m, q, body):
    with a.db.read() as c:
        return {"notes": E.list_notes(c, _pid(a, m))}


@route("GET", r"/api/projects/(\d+)/quests")
def quests(a: App, m, q, body):
    with a.db.read() as c:
        return E.quests(c, _pid(a, m))


@route("POST", r"/api/projects/(\d+)/quests")
def quest_state(a: App, m, q, body):
    with a.db.tx() as c:
        xp = E.set_quest_state(c, _pid(a, m), body["key"], body.get("status", "done"), int(body.get("xp", 0)))
    return {"xp": xp}


@route("GET", r"/api/projects/(\d+)/compare")
def compare(a: App, m, q, body):
    ids = [int(x) for x in q.get("ids", "").split(",") if x.strip().isdigit()]
    with a.db.read() as c:
        return {"rows": E.compare(c, ids)}


@route("GET", r"/api/projects/(\d+)/export")
def export(a: App, m, q, body):
    fmt = q.get("fmt", "md")
    if fmt not in X.EXPORTERS:
        raise E.AtlasError("unknown export format")
    fn, ext, mime = X.EXPORTERS[fmt]
    with a.db.read() as c:
        content = fn(c, _pid(a, m))
        name = X.filename(c, _pid(a, m), ext)
    return RawResponse(content.encode("utf-8"), mime, name)


@route("GET", r"/api/projects/(\d+)/search")
def search(a: App, m, q, body):
    with a.db.read() as c:
        res = S.search(c, _pid(a, m), q.get("q", ""), kind=q.get("kind") or None)
        if res["results"]:   # the top hit also gets its place in the map, like a featured result
            res["path"] = E.relationship_path(c, res["results"][0]["node"]["id"])
        return res


@route("GET", r"/api/projects/(\d+)/brief")
def topic_brief(a: App, m, q, body):
    with a.db.read() as c:
        b = B.brief(c, _pid(a, m), q.get("q", ""))
        if q.get("fmt") == "md":
            name = X._slug((b["topic"] or {}).get("title") or b["query"]) or "topic"
            return RawResponse(B.to_markdown(b, E.get_project(c, _pid(a, m))).encode("utf-8"),
                               "text/markdown; charset=utf-8", f"brief-{name}.md")
        return b


# never launch these from a link; show them in Explorer instead
RISKY_EXT = {".exe", ".bat", ".cmd", ".com", ".scr", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh",
             ".msi", ".msp", ".lnk", ".reg", ".hta", ".jar", ".cpl", ".pif", ".url", ".appref-ms", ".msc", ".dll"}


@route("POST", r"/api/nodes/(\d+)/open")
def open_location(a: App, m, q, body):
    with a.db.read() as c:
        n = E.get_node(c, int(m.group(1)))
    loc = (n["body"] or n["title"]).strip().strip('"')
    if re.match(r"^https?://", loc):
        os.startfile(loc)
        return {"opened": loc}
    if loc.lower().startswith("file:"):
        loc = unquote(urlparse(loc).path).lstrip("/").replace("/", "\\")
    if not re.match(r"^(?:[A-Za-z]:[\\/]|\\\\)", loc):
        raise E.AtlasError("this source is a label, not a file path or link")
    p = Path(loc)
    if not p.exists():
        raise E.AtlasError(f"not found (moved or renamed?): {loc}")
    if body.get("reveal") or (p.is_file() and p.suffix.lower() in RISKY_EXT):
        subprocess.Popen(["explorer", "/select,", str(p)] if p.is_file() else ["explorer", str(p)])
        return {"revealed": str(p)}
    os.startfile(str(p))
    return {"opened": str(p)}


@route("GET", r"/api/nodes/(\d+)")
def node(a: App, m, q, body):
    with a.db.read() as c:
        card = E.node_card(c, int(m.group(1)))
        card["path"] = E.relationship_path(c, int(m.group(1)))
        return card


@route("PATCH", r"/api/nodes/(\d+)")
def node_update(a: App, m, q, body):
    with a.db.tx() as c:
        return E.update_node(c, int(m.group(1)), body)


@route("DELETE", r"/api/nodes/(\d+)")
def node_delete(a: App, m, q, body):
    with a.db.tx() as c:
        E.delete_node(c, int(m.group(1)))
    return {"ok": True}


@route("POST", r"/api/nodes/(\d+)/merge")
def node_merge(a: App, m, q, body):
    with a.db.tx() as c:
        return E.merge_nodes(c, int(m.group(1)), int(body["into"]))


@route("POST", r"/api/edges")
def edge_create(a: App, m, q, body):
    with a.db.tx() as c:
        src = E.get_node(c, int(body["src"]))
        eid, _ = E.add_edge(c, src["project_id"], src["id"], int(body["dst"]), body.get("type", "relates to"),
                            status=body.get("status", "active"), confidence=body.get("confidence", "medium"),
                            origin="user", evidence=body.get("evidence", ""))
        if not eid:
            raise E.AtlasError("link not created (same node, or it already exists)")
        return E.edge_dict(c, c.execute("SELECT * FROM edges WHERE id=?", (eid,)).fetchone())


@route("PATCH", r"/api/edges/(\d+)")
def edge_update(a: App, m, q, body):
    with a.db.tx() as c:
        return E.set_edge(c, int(m.group(1)), body)


@route("DELETE", r"/api/edges/(\d+)")
def edge_delete(a: App, m, q, body):
    with a.db.tx() as c:
        c.execute("DELETE FROM edges WHERE id=?", (int(m.group(1)),))
    return {"ok": True}


@route("DELETE", r"/api/notes/(\d+)")
def note_delete(a: App, m, q, body):
    with a.db.tx() as c:
        return E.delete_note(c, int(m.group(1)))


@route("GET", r"/api/backup")
def backup(a: App, m, q, body):
    with a.db.read() as c:
        data = json.dumps(X.backup_all(c), ensure_ascii=False, indent=1).encode("utf-8")
    return RawResponse(data, "application/json", f"idea-atlas-backup-{time.strftime('%Y%m%d-%H%M')}.json")


@route("POST", r"/api/import")
def do_import(a: App, m, q, body):
    with a.db.tx() as c:
        return X.import_project(c, body)


# ---------------------------------------------------------------- plumbing

class RawResponse:
    def __init__(self, data: bytes, mime: str, filename: str | None = None):
        self.data, self.mime, self.filename = data, mime, filename


def make_handler(app: App, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    static_root = static_dir().resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = "IdeaAtlas"
        protocol_version = "HTTP/1.1"
        timeout = 120   # drop idle keep-alive connections so their threads exit

        def log_message(self, fmt, *args):
            log.debug("%s - %s", self.address_string(), fmt % args)

        def _send(self, code: int, data: bytes, mime: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _json(self, code: int, obj):
            self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8")

        def _handle(self, method: str):
            if self.headers.get("Host", "") not in allowed_hosts:   # blocks DNS-rebinding
                return self._send(403, b"forbidden", "text/plain")
            url = urlparse(self.path)
            if url.path.startswith("/api/"):
                # blocks cross-site requests; the "window closed" beacon cannot set headers and only shows a tip
                if self.headers.get("X-Atlas") != "1" and url.path != "/api/window-closed":
                    return self._send(403, b"forbidden", "text/plain")
                if url.path != "/api/window-closed":
                    app.seen()                                       # the window is alive
                q = {k: v[-1] for k, v in parse_qs(url.query).items()}
                body = {}
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    try:
                        body = json.loads(self.rfile.read(n).decode("utf-8"))
                    except ValueError:
                        return self._json(400, {"error": "invalid JSON"})
                for meth, rx, fn in ROUTES:
                    mm = rx.match(url.path)
                    if mm and meth == method:
                        try:
                            res = fn(app, mm, q, body)
                        except E.AtlasError as e:
                            return self._json(400, {"error": str(e)})
                        except Exception as e:  # report, keep serving
                            log.error("%s %s failed\n%s", method, url.path, traceback.format_exc())
                            return self._json(500, {"error": f"{type(e).__name__}: {e}"})
                        if isinstance(res, RawResponse):
                            extra = {"Content-Disposition": f'attachment; filename="{res.filename}"'} if res.filename else {}
                            return self._send(200, res.data, res.mime, extra)
                        return self._json(200, res)
                return self._json(404, {"error": "no such endpoint"})
            if method not in ("GET", "HEAD"):
                return self._send(405, b"method not allowed", "text/plain")
            rel = url.path.lstrip("/") or "index.html"
            target = (static_root / rel).resolve()
            if static_root not in target.parents or not target.is_file():
                return self._send(404, b"not found", "text/plain")
            mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            if mime.startswith("text/") or mime.endswith("javascript"):
                mime += "; charset=utf-8"
            headers = {"Cache-Control": "max-age=86400"} if "/vendor/" in url.path else {}
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", headers.get("Cache-Control", "no-cache"))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def do_GET(self):
            self._handle("GET")

        def do_HEAD(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_PATCH(self):
            self._handle("PATCH")

        def do_DELETE(self):
            self._handle("DELETE")

    return Handler


def make_server(app: App, port: int) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, port))
    srv.daemon_threads = True
    return srv
