"""Idea Atlas command line, used for AI enrichment sessions and maintenance.

    python tools/atlas_cli.py status
    python tools/atlas_cli.py pending [--project HANDLE] [--limit N]
    python tools/atlas_cli.py dump --project HANDLE
    python tools/atlas_cli.py apply OPS.json [--dry-run]
    python tools/atlas_cli.py quests --project HANDLE
    python tools/atlas_cli.py search "query" --project HANDLE
    python tools/atlas_cli.py capture --project HANDLE "note text"
    python tools/atlas_cli.py brief "keyword or C-004" --project HANDLE   (Markdown: everything said about it)
    python tools/atlas_cli.py summaries --project HANDLE                  (stale summaries, busy topics without one)
    python tools/atlas_cli.py glossary --project HANDLE                   (abbreviations, note counts, summary state)
    python tools/atlas_cli.py requirements --project HANDLE               (traceability matrix with tracking state)

Safe to run while the app is open (SQLite WAL). See CLAUDE.md for the ops format.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from atlas import brief as B  # noqa: E402
from atlas import engine as E  # noqa: E402
from atlas import search as S  # noqa: E402
from atlas.config import data_dir  # noqa: E402
from atlas.db import Database  # noqa: E402


def out(obj):
    sys.stdout.write(json.dumps(obj, indent=1, ensure_ascii=False, default=str) + "\n")


def need_project(conn, handle):
    p = E.project_by_handle(conn, handle or "")
    if not p:
        raise SystemExit(f"unknown project @{handle}; known: {', '.join(x['handle'] for x in E.list_projects(conn))}")
    return p


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["status", "pending", "dump", "apply", "quests", "search", "capture", "brief",
                                    "summaries", "glossary", "requirements"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--project")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    db = Database(data_dir() / "atlas.db")

    if a.cmd == "status":
        with db.read() as c:
            out({"data": str(data_dir()), "projects": [
                {k: p[k] for k in ("handle", "name", "node_count", "note_count", "unreviewed", "archived")}
                for p in E.list_projects(c)]})
    elif a.cmd == "pending":
        with db.read() as c:
            res = []
            for p in E.list_projects(c, include_archived=False):
                if a.project and p["handle"] != a.project.lower():
                    continue
                notes = [n for n in E.list_notes(c, p["id"], limit=10000) if not n["ai_reviewed"]][-a.limit:]
                if not notes:
                    continue
                nodes = [{"code": r["code"], "kind": r["kind"], "title": r["title"], "definition": r["definition"],
                          "confidence": r["confidence"], "status": r["status"]}
                         for r in c.execute("SELECT * FROM nodes WHERE project_id=? ORDER BY kind, code", (p["id"],))]
                edges = [f"{e['src_code']} --{e['type']}{'?' if e['status'] == 'suggested' else ''}--> {e['dst_code']}"
                         for e in (E.edge_dict(c, r) for r in c.execute(
                             "SELECT * FROM edges WHERE project_id=? AND status IN ('active','suggested')", (p["id"],)))]
                res.append({"project": p["handle"], "name": p["name"], "description": p["description"],
                            "unreviewed_notes": [{"code": n["code"], "created": n["created"], "source": n["source"],
                                                  "text": n["text"], "parsed_into": [f"{x['code']} {x['title']}" for x in n["nodes"]]}
                                                 for n in reversed(notes)],
                            "map_nodes": nodes, "map_links": edges})
            out(res or {"pending": 0})
    elif a.cmd == "dump":
        with db.read() as c:
            out(E.export_project(c, need_project(c, a.project)["id"]))
    elif a.cmd == "quests":
        with db.read() as c:
            q = E.quests(c, need_project(c, a.project)["id"])
            out({"xp": q["xp"], "quests": [f"[{x['type']}] {x['detail']}" for x in q["quests"]]})
    elif a.cmd == "search":
        with db.read() as c:
            r = S.search(c, need_project(c, a.project)["id"], a.arg or "")
            out({"did_you_mean": r["did_you_mean"], "expanded": r["expanded"],
                 "results": [{"code": x["node"]["code"], "title": x["node"]["title"], "match": x["match"],
                              "snippet": "".join(s["t"] for s in x["snippet"])} for x in r["results"]]})
    elif a.cmd == "brief":
        with db.read() as c:
            p = need_project(c, a.project)
            sys.stdout.write(B.to_markdown(B.brief(c, p["id"], a.arg or ""), p) + "\n")
    elif a.cmd == "glossary":
        with db.read() as c:
            out([f"{g['abbr']} = {g['expansion']} [{g['kind']}{', p.' + str(g['page']) if g['page'] else ''}] "
                 f"{g['notes']} notes{', summary' + (' (stale)' if g['summary_stale'] else '') if g['summary'] else ''}"
                 for g in E.glossary(c, need_project(c, a.project)["id"])])
    elif a.cmd == "requirements":
        with db.read() as c:
            out([f"{r['rid']} {r['title']} [{r['status']} {r['progress']}%"
                 + (f", {r['owner']}" if r["owner"] else "") + (f", due {r['due']}" if r["due"] else "") + "] "
                 + f"{r['notes']} notes · {r['node_code'] or 'no concept'}" + (f" · note: {r['note']}" if r["note"] else "")
                 for r in E.requirement_list(c, need_project(c, a.project)["id"])])
    elif a.cmd == "summaries":
        with db.read() as c:
            out(B.summary_status(c, need_project(c, a.project)["id"]))
    elif a.cmd == "capture":
        with db.read() as c:
            pid = need_project(c, a.project)["id"]
        r = E.capture(db, a.arg or sys.stdin.read(), default_pid=pid, dry_run=a.dry_run)
        out([{"note": x["note"]["code"], "added": [f"{n['code']} {n['title']}" for n in x["added"]],
              "links": [f"{e['src_code']} {e['type']} {e['dst_code']}" for e in x["edges"]]} for x in r.get("results", [])] or r)
    elif a.cmd == "apply":
        payload = json.loads(Path(a.arg).read_text(encoding="utf-8"))
        batches = payload if isinstance(payload, list) else [payload]
        logs, refs = [], {}
        with db.tx(rollback=a.dry_run) as c:
            for b in batches:
                p = need_project(c, b["project"])
                logs.append({"project": p["handle"],
                             "log": E.apply_ops(c, p["id"], b["ops"], origin=b.get("origin", "ai"), refs=refs)})
        out({"dry_run": a.dry_run, "applied": logs})


if __name__ == "__main__":
    main()
