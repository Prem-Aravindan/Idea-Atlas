"""Atlas engine: integrates parsed notes into a project's semantic map and answers queries."""
from __future__ import annotations

import ntpath
import re
from collections import defaultdict, deque

from . import textparse as tp
from .db import Database, now

EDGE_TYPES = tp.EDGE_TYPES
NODE_KINDS = tp.NODE_KINDS
KIND_PREFIX = dict(tp.KIND_PREFIX, note="N")
STATEMENT_KINDS = {"idea", "solution", "question", "evidence", "decision", "assumption", "risk",
                   "explanation", "task", "claim", "method"}
HANDLE_OK = re.compile(r"^[a-z][a-z0-9_\-]{0,40}$")


class AtlasError(Exception):
    pass


# ---------------------------------------------------------------- small helpers

def _row(r):
    return dict(r) if r is not None else None


def node_dict(r) -> dict:
    return {k: r[k] for k in ("id", "project_id", "code", "kind", "title", "body", "definition",
                              "status", "confidence", "origin", "created", "updated")}


def get_node(conn, node_id: int) -> dict:
    r = conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
    if not r:
        raise AtlasError(f"node {node_id} not found")
    return node_dict(r)


def node_by_code(conn, pid: int, code: str) -> dict | None:
    r = conn.execute("SELECT * FROM nodes WHERE project_id=? AND code=?", (pid, code.upper())).fetchone()
    return node_dict(r) if r else None


def next_code(conn, pid: int, kind: str) -> str:
    prefix = KIND_PREFIX[kind]
    conn.execute("INSERT OR IGNORE INTO counters(project_id, prefix, n) VALUES(?,?,0)", (pid, prefix))
    conn.execute("UPDATE counters SET n=n+1 WHERE project_id=? AND prefix=?", (pid, prefix))
    n = conn.execute("SELECT n FROM counters WHERE project_id=? AND prefix=?", (pid, prefix)).fetchone()[0]
    return f"{prefix}-{n:03d}"


def alias_index(conn, pid: int) -> dict[str, int]:
    rows = conn.execute("SELECT a.norm, a.node_id FROM aliases a JOIN nodes n ON n.id=a.node_id "
                        "WHERE n.project_id=?", (pid,)).fetchall()
    return {r[0]: r[1] for r in rows}


def add_alias(conn, node_id: int, alias: str) -> bool:
    n = tp.norm(alias)
    if not n:
        return False
    cur = conn.execute("INSERT OR IGNORE INTO aliases(node_id, alias, norm) VALUES(?,?,?)", (node_id, alias.strip(), n))
    return cur.rowcount > 0


def create_node(conn, pid: int, kind: str, title: str, body: str = "", origin: str = "parser",
                confidence: str = "medium", definition: str = "") -> int:
    if kind not in KIND_PREFIX or kind == "note":
        raise AtlasError(f"unknown kind {kind}")
    t = now()
    code = next_code(conn, pid, kind)
    status = "open" if kind in ("question", "task", "risk", "assumption") else "active"
    cur = conn.execute(
        "INSERT INTO nodes(project_id, code, kind, title, body, definition, status, confidence, origin, created, updated)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (pid, code, kind, title.strip()[:200], body.strip(), definition.strip(), status, confidence, origin, t, t))
    nid = cur.lastrowid
    if len(title.split()) <= 6:
        add_alias(conn, nid, title)
    return nid


def add_mention(conn, node_id: int, note_id: int, excerpt: str, role: str = "mention"):
    conn.execute("INSERT OR IGNORE INTO mentions(node_id, note_id, excerpt, role) VALUES(?,?,?,?)",
                 (node_id, note_id, tp.unwiki(excerpt)[:400], role))


def add_edge(conn, pid: int, src: int, dst: int, etype: str, status: str = "active", confidence: str = "medium",
             origin: str = "parser", note_id: int | None = None, evidence: str = "") -> tuple[int | None, bool]:
    if not src or not dst or src == dst:
        return None, False
    if etype not in EDGE_TYPES:
        raise AtlasError(f"unknown relationship '{etype}'")
    ex = conn.execute("SELECT id, status FROM edges WHERE src=? AND dst=? AND type=?", (src, dst, etype)).fetchone()
    if ex:
        # a suggestion that the user now states explicitly becomes active
        if ex["status"] == "suggested" and status == "active":
            conn.execute("UPDATE edges SET status='active', origin=?, confidence=? WHERE id=?", (origin, confidence, ex["id"]))
            return ex["id"], True
        return ex["id"], False
    # an explicit relation supersedes a vague 'relates to' on the same pair
    if etype != "relates to" and status == "active":
        conn.execute("DELETE FROM edges WHERE src=? AND dst=? AND type='relates to' AND origin='parser'", (src, dst))
    elif etype == "relates to":
        if conn.execute("SELECT 1 FROM edges WHERE ((src=? AND dst=?) OR (src=? AND dst=?)) AND status!='rejected'",
                        (src, dst, dst, src)).fetchone():
            return None, False
    cur = conn.execute(
        "INSERT INTO edges(project_id, src, dst, type, status, confidence, origin, note_id, evidence, created)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)", (pid, src, dst, etype, status, confidence, origin, note_id, evidence[:400], now()))
    return cur.lastrowid, True


def edge_dict(conn, e) -> dict:
    s = conn.execute("SELECT code, title, kind FROM nodes WHERE id=?", (e["src"],)).fetchone()
    d = conn.execute("SELECT code, title, kind FROM nodes WHERE id=?", (e["dst"],)).fetchone()
    return {"id": e["id"], "src": e["src"], "dst": e["dst"], "type": e["type"], "status": e["status"],
            "confidence": e["confidence"], "origin": e["origin"], "evidence": e["evidence"],
            "src_code": s["code"], "src_title": s["title"], "src_kind": s["kind"],
            "dst_code": d["code"], "dst_title": d["title"], "dst_kind": d["kind"]}


def touch(conn, node_id: int):
    conn.execute("UPDATE nodes SET updated=? WHERE id=?", (now(), node_id))


# ---------------------------------------------------------------- projects

def list_projects(conn, include_archived: bool = True) -> list[dict]:
    rows = conn.execute("""
        SELECT p.*, (SELECT COUNT(*) FROM nodes n WHERE n.project_id=p.id) AS node_count,
               (SELECT COUNT(*) FROM notes o WHERE o.project_id=p.id) AS note_count,
               (SELECT COUNT(*) FROM notes o WHERE o.project_id=p.id AND o.ai_reviewed=0) AS unreviewed,
               (SELECT COUNT(*) FROM requirements q WHERE q.project_id=p.id) AS requirement_count
        FROM projects p ORDER BY p.archived, p.updated DESC""").fetchall()
    return [dict(r) for r in rows if include_archived or not r["archived"]]


def project_by_handle(conn, handle: str) -> dict | None:
    return _row(conn.execute("SELECT * FROM projects WHERE handle=?", (handle.lower().lstrip("@"),)).fetchone())


def get_project(conn, pid: int) -> dict:
    p = _row(conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone())
    if not p:
        raise AtlasError("project not found")
    return p


def make_handle(name: str) -> str:
    h = re.sub(r"[^a-z0-9_\-]+", "-", name.lower()).strip("-")
    if not h or not h[0].isalpha():
        h = "p-" + h
    return h[:40]


def create_project(conn, name: str, handle: str = "", description: str = "", color: str = "") -> dict:
    name = name.strip()
    if not name:
        raise AtlasError("project name is required")
    handle = (handle or make_handle(name)).lower().lstrip("@")
    if not HANDLE_OK.match(handle):
        raise AtlasError("handle must start with a letter and use only letters, digits, - or _")
    if project_by_handle(conn, handle):
        raise AtlasError(f"@{handle} already exists")
    palette = ["#5b7cfa", "#12a594", "#e5484d", "#f76b15", "#8e4ec6", "#0090ff", "#d6409f", "#46a758"]
    count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    t = now()
    cur = conn.execute("INSERT INTO projects(handle, name, description, color, created, updated) VALUES(?,?,?,?,?,?)",
                       (handle, name, description.strip(), color or palette[count % len(palette)], t, t))
    return get_project(conn, cur.lastrowid)


def update_project(conn, pid: int, fields: dict) -> dict:
    p = get_project(conn, pid)
    if "handle" in fields:
        h = fields["handle"].lower().lstrip("@")
        if h != p["handle"]:
            if not HANDLE_OK.match(h):
                raise AtlasError("invalid handle")
            if project_by_handle(conn, h):
                raise AtlasError(f"@{h} already exists")
            conn.execute("UPDATE projects SET handle=? WHERE id=?", (h, pid))
    for k in ("name", "description", "color", "archived"):
        if k in fields:
            conn.execute(f"UPDATE projects SET {k}=? WHERE id=?", (fields[k], pid))
    conn.execute("UPDATE projects SET updated=? WHERE id=?", (now(), pid))
    return get_project(conn, pid)


def delete_project(conn, pid: int):
    conn.execute("DELETE FROM projects WHERE id=?", (pid,))
    conn.execute("DELETE FROM counters WHERE project_id=?", (pid,))


# ---------------------------------------------------------------- capture / ingest

class _Summary:
    def __init__(self):
        self.added: list[int] = []
        self.updated: list[int] = []
        self.edges: list[int] = []
        self.suggested: list[int] = []
        self.conflicts: list[str] = []
        self.ambiguities: list[str] = []
        self.merged: list[str] = []
        self.promoted: list[int] = []
        self.issues: list[dict] = []    # the same notices, with the ids needed to act on them (all optional)

    def add(self, nid):
        if nid not in self.added:
            self.added.append(nid)

    def upd(self, nid):
        if nid not in self.added and nid not in self.updated:
            self.updated.append(nid)


def capture(db: Database, text: str, default_pid: int | None = None, dry_run: bool = False,
            create_missing: bool = False, source: str = "", target_id: int | None = None,
            target_mode: str = "answer") -> dict:
    """target_id: the note answers / gives evidence for an existing item (set from a quest, no codes typed)."""
    text = (text or "").strip()
    if not text:
        raise AtlasError("nothing to capture")
    handles = list(dict.fromkeys(h.lower() for h in tp.HANDLE_RE.findall(text)))
    with db.read() as conn:
        known = {p["handle"]: p["id"] for p in list_projects(conn)}
    unknown = [h for h in handles if h not in known]
    if unknown and not create_missing:
        return {"unknown": unknown}
    if not handles and not default_pid:
        raise AtlasError("no project: start the note with @project or pick one")
    with db.tx(rollback=dry_run) as conn:
        for h in unknown:
            known[h] = create_project(conn, h.replace("-", " ").replace("_", " ").title(), handle=h)["id"]
        targets = [known[h] for h in handles] or [default_pid]
        target = None
        if target_id:
            target = get_node(conn, int(target_id))
            targets = [target["project_id"]]          # an answer belongs to the question's project
        results = [ingest(conn, pid, text, source=source, target=target, target_mode=target_mode) for pid in targets]
    return {"results": results, "dry_run": dry_run, "created_projects": unknown}


def ingest(conn, pid: int, text: str, source: str = "", origin: str = "parser", target: dict | None = None,
           target_mode: str = "answer") -> dict:
    project = get_project(conn, pid)
    idx = alias_index(conn, pid)
    parsed = tp.parse(text, set(idx))
    source = (source or "").strip().strip('"').strip()   # Explorer's "Copy as path" adds quotes
    if target:
        # written from a quest ("Write answer" / "Add evidence"): every statement is aimed at that item
        for seg in parsed.segments:
            if seg.is_term or seg.kind_source == "definition" or seg.kind == "reference":
                continue
            if target["code"] not in seg.code_refs:
                seg.code_refs.append(target["code"])
            if seg.kind_source != "prefix":
                if target_mode == "evidence":
                    seg.kind = "evidence"
                elif seg.kind in ("claim", "idea", "explanation", "method", "") and target["kind"] in ("question", "risk"):
                    seg.kind = "solution"
    t = now()
    note_code = next_code(conn, pid, "note")
    note_id = conn.execute("INSERT INTO notes(project_id, code, text, source, created) VALUES(?,?,?,?,?)",
                           (pid, note_code, text, source, t)).lastrowid
    for tag in parsed.tags:
        conn.execute("INSERT OR IGNORE INTO note_tags(note_id, tag) VALUES(?,?)", (note_id, tag))
    S = _Summary()
    S.ambiguities.extend(parsed.ambiguities)

    def edge(src, dst, etype, conf="medium", evidence="", status="active"):
        eid, created = add_edge(conn, pid, src, dst, etype, status=status, confidence=conf, origin=origin,
                                note_id=note_id, evidence=evidence)
        if eid and created:
            (S.suggested if status == "suggested" else S.edges).append(eid)
        return eid

    _titles = {}

    def topic_titles() -> dict:
        if not _titles:
            _titles.update({tp.norm(r[1]): r[0] for r in conn.execute(
                "SELECT id, title FROM nodes WHERE project_id=? AND kind IN ('concept','method')", (pid,))})
            _titles.setdefault("", 0)        # loaded, even when the project has none
        return _titles

    def concept(display, kind="concept", excerpt="", confidence="medium"):
        display = tp.tidy(display or "")
        n = tp.norm(display)
        if not n or len(n) < 2:
            return None
        nid = idx.get(n)
        if nid is None and n in topic_titles():
            # long names (over 6 words) are not aliases; [[picked from the list]] must still find them
            nid = idx[n] = topic_titles()[n]
            add_alias(conn, nid, display)
        if nid is None:
            nid = create_node(conn, pid, kind, display, origin=origin, confidence=confidence)
            add_alias(conn, nid, display)
            idx[n] = nid
            S.add(nid)
        else:
            row = conn.execute("SELECT kind, origin, edited FROM nodes WHERE id=?", (nid,)).fetchone()
            # a bare term filed under a "Methods:"-style heading refines a generic concept
            if kind != "concept" and row["kind"] == "concept" and not row["edited"]:
                conn.execute("UPDATE nodes SET kind=? WHERE id=?", (kind, nid))
            S.upd(nid)
            touch(conn, nid)
        add_mention(conn, nid, note_id, excerpt)
        return nid

    def reference(value, excerpt=""):
        """A source: file path, URL, citation or plain label. Paths/URLs are kept whole so they can be opened."""
        value = value.strip().strip('"').strip()
        n = tp.norm(value)
        if not n:
            return None
        nid = idx.get(n)
        if nid is None:
            title = ntpath.basename(value.rstrip("\\/")) if tp.is_location(value) and not value.startswith("http") else value
            nid = create_node(conn, pid, "reference", title or value, body=value, origin=origin)
            add_alias(conn, nid, value)
            idx[n] = nid
            S.add(nid)
        else:
            S.upd(nid)
        add_mention(conn, nid, note_id, excerpt or value, "source")
        return nid

    def bump_candidate(display, excerpt):
        n = tp.norm(display)
        if not n or n in idx or len(n) < 4:
            return idx.get(n)
        r = conn.execute("SELECT count, last_note FROM candidates WHERE project_id=? AND norm=?", (pid, n)).fetchone()
        if r and r["last_note"] == note_id:
            return None
        count = (r["count"] if r else 0) + 1
        conn.execute("INSERT INTO candidates(project_id, norm, display, count, last_note) VALUES(?,?,?,?,?) "
                     "ON CONFLICT(project_id, norm) DO UPDATE SET count=excluded.count, last_note=excluded.last_note",
                     (pid, n, display, count, note_id))
        if count < 2:
            return None
        # recurring phrase -> promote to a concept and link earlier statements that used it
        conn.execute("DELETE FROM candidates WHERE project_id=? AND norm=?", (pid, n))
        nid = concept(display, excerpt=excerpt, confidence="low")
        S.promoted.append(nid)
        if nid:
            S.issues.append({"type": "promoted", "node": nid,
                             "text": f"“{display}” keeps coming up in your notes, so it is now a concept."})
        like = "%" + display.lower() + "%"
        for row in conn.execute("SELECT id, kind FROM nodes WHERE project_id=? AND id!=? AND lower(body) LIKE ?",
                                (pid, nid, like)).fetchall():
            if row["kind"] == "question":
                edge(nid, row["id"], "raises question", "low")
            else:
                edge(row["id"], nid, "relates to", "low")
        return nid

    def statement(seg) -> int:
        kind, text = seg.kind, tp.unwiki(seg.text)
        toks = tp.content_tokens(text)
        for row in conn.execute("SELECT id, title, body FROM nodes WHERE project_id=? AND kind=?", (pid, kind)):
            if tp.jaccard(toks, tp.content_tokens(row["body"] or row["title"])) >= 0.8:
                add_mention(conn, row["id"], note_id, seg.text, "source")
                S.upd(row["id"])
                S.merged.append(f"“{tp.short_title(text, 60)}” is already in your map, so this note was added "
                                f"as another source for it.")
                S.issues.append({"type": "duplicate", "node": row["id"], "note": note_id, "text": S.merged[-1]})
                return row["id"]
        nid = create_node(conn, pid, kind, tp.short_title(text), body=text, origin=origin,
                          confidence=seg.confidence)
        add_mention(conn, nid, note_id, seg.text, "source")
        S.add(nid)
        return nid

    # 1. aliases ("Graph Neural Networks (GNNs)") so later mentions resolve to one node
    for term, alias in parsed.aliases:
        nid = concept(term, excerpt=f"{term} ({alias})")
        na = tp.norm(alias)
        other = idx.get(na)
        if other and other != nid:
            ot = conn.execute("SELECT title FROM nodes WHERE id=?", (other,)).fetchone()[0]
            S.ambiguities.append(f"“{alias}” is already used for “{ot}”. Kept separate; merge them if they are the same thing.")
            S.issues.append({"type": "alias", "node": nid, "other": other, "alias": alias, "text": S.ambiguities[-1]})
        elif nid:
            add_alias(conn, nid, alias)
            idx[na] = nid

    # 2. definitions (user's own words -> concept definition)
    seg_text = {s.idx: s.text for s in parsed.segments}
    for term, definition, si in parsed.definitions:
        nid = concept(term, excerpt=seg_text.get(si, definition))
        if not nid:
            continue
        existing = conn.execute("SELECT text FROM definitions WHERE node_id=?", (nid,)).fetchall()
        dt = tp.content_tokens(definition)
        if any(tp.jaccard(dt, tp.content_tokens(e[0])) >= 0.7 for e in existing):
            continue
        conn.execute("INSERT INTO definitions(node_id, text, note_id, origin, created) VALUES(?,?,?,?,?)",
                     (nid, definition, note_id, origin, now()))
        cur = conn.execute("SELECT code, title, definition FROM nodes WHERE id=?", (nid,)).fetchone()
        if not cur["definition"]:
            conn.execute("UPDATE nodes SET definition=? WHERE id=?", (definition, nid))
        else:
            S.conflicts.append(f"“{cur['title']}” now has two different definitions. Both are kept for you to review.")
            S.issues.append({"type": "definition", "node": nid, "definition": definition, "current": cur["definition"],
                             "text": S.conflicts[-1]})

    # 3. references become F- nodes
    ref_nodes: dict[int, list[int]] = defaultdict(list)
    for rkind, value, si in parsed.references:
        nid = reference(value, excerpt=seg_text.get(si, value))
        if nid:
            ref_nodes[si].append(nid)
    source_ref = reference(source) if source and source.strip() else None

    # 4. statements, mentions, relations
    prev_stmt, last_qr = None, None
    stmt_nodes: list[int] = []
    seg_node: dict[int, int] = {}
    for seg in parsed.segments:
        mention_ids: list[int] = []

        def mention(nid):
            if nid and nid not in mention_ids:
                mention_ids.append(nid)

        for m in seg.mentions:
            if m.strength == "weak" and tp.norm(m.text) not in idx:
                mention(bump_candidate(m.text, seg.text))
                continue
            mention(concept(m.text, excerpt=seg.text, confidence="low" if seg.confidence == "low" else "medium"))
        targets = []
        for code in seg.code_refs:
            nd = node_by_code(conn, pid, code)
            if nd:
                targets.append(nd["id"])
                S.upd(nd["id"])
        rel_pairs = []
        for r in seg.relations:
            a = concept(r.left, excerpt=seg.text) if r.left else None
            b = concept(r.right, excerpt=seg.text) if r.right else None
            rel_pairs.append((a, r.type, b, r.sentence))
            mention(a)
            mention(b)

        stmt = None
        if seg.kind == "reference":
            refs = ref_nodes.get(seg.idx) or []
            stmt = refs[0] if refs else concept(seg.text, kind="reference", excerpt=seg.text)
        elif seg.is_term:
            stmt = concept(seg.text, kind=seg.kind or "concept", excerpt=seg.text)
        elif seg.kind and seg.kind != "concept":
            if not mention_ids and seg.chunks:
                # anchor an otherwise unconnected statement on its first phrase (usually the subject)
                mention(concept(seg.chunks[0], excerpt=seg.text, confidence="low"))
            stmt = statement(seg)
        if seg.alt_kinds:
            S.issues.append({"type": "kind", "node": stmt if stmt and not seg.is_term else None, "kinds": seg.alt_kinds,
                             "filed": seg.kind, "text": f"“{tp.short_title(tp.unwiki(seg.text), 60)}” reads as "
                                                        f"{seg.alt_kinds[0]} or {seg.alt_kinds[1]}; filed as {seg.kind}."})
        for ch in seg.chunks:
            mention(bump_candidate(ch, seg.text))
        mention_ids = [x for x in mention_ids if x and x != stmt]

        if stmt and seg.kind and not seg.is_term and seg.kind != "reference":
            k, conf = seg.kind, seg.confidence
            stmt_nodes.append((stmt, seg.idx))
            for pos, c in enumerate(mention_ids):
                if k == "question":
                    edge(c, stmt, "raises question", conf, seg.text)
                elif k == "explanation":
                    edge(stmt, c, "explains", conf, seg.text)
                elif k == "evidence" and pos == 0 and not targets:
                    # evidence speaks to its subject (first mention); other mentions are context
                    edge(stmt, c, "contradicts" if seg.negated else "supports", conf, seg.text)
                else:
                    edge(stmt, c, "relates to", conf, seg.text)
            for tgt in targets:
                tk = conn.execute("SELECT kind FROM nodes WHERE id=?", (tgt,)).fetchone()[0]
                if k == "solution" or (tk in ("question", "risk") and k != "question"):
                    edge(stmt, tgt, "solves", "high", seg.text)
                elif k == "evidence":
                    edge(stmt, tgt, "contradicts" if seg.negated else "supports", "high", seg.text)
                elif k == "explanation":
                    edge(stmt, tgt, "explains", "high", seg.text)
                elif k == "question":
                    edge(tgt, stmt, "raises question", "high", seg.text)
                else:
                    edge(stmt, tgt, "relates to", "high", seg.text)
            if k == "solution" and not targets:
                if last_qr:
                    edge(stmt, last_qr, "solves", conf, seg.text)
                else:
                    _suggest_solves(conn, pid, stmt, seg, edge)
            if k == "evidence" and not targets and prev_stmt:
                # evidence right after a claim about the same thing bears on that claim
                pk = conn.execute("SELECT kind FROM nodes WHERE id=?", (prev_stmt,)).fetchone()[0]
                shared = {r[0] for r in conn.execute("SELECT dst FROM edges WHERE src=?", (prev_stmt,))} & set(mention_ids)
                if pk in ("claim", "idea", "assumption", "method") and shared:
                    # adjacency is a weak signal: a negative result is only suggested as a contradiction
                    edge(stmt, prev_stmt, "contradicts" if seg.negated else "supports", "low", seg.text,
                         status="suggested" if seg.negated else "active")
            if not mention_ids and not targets and prev_stmt and prev_stmt != stmt:
                # "not clear if it scales." – no concept of its own, so it continues the previous thought
                if k == "question":
                    edge(prev_stmt, stmt, "raises question", "low", seg.text)
                elif k != "solution":
                    edge(stmt, prev_stmt, "relates to", "low", seg.text)
            if k in ("question", "risk"):
                last_qr = stmt
            prev_stmt = stmt
        elif stmt and seg.is_term:
            for c in mention_ids:
                edge(stmt, c, "relates to", seg.confidence, seg.text)

        for a, etype, b, sentence in rel_pairs:
            src = a or stmt
            if src and b and src != b:
                edge(src, b, etype, "low" if seg.confidence == "low" else "medium", sentence)

        # list items ("...integrate:" / "- databases") hang off the statement that introduced them
        item = stmt or (mention_ids[0] if mention_ids else None)
        if item:
            seg_node[seg.idx] = item
            if seg.parent is not None and seg_node.get(seg.parent) and seg_node[seg.parent] != item:
                edge(seg_node[seg.parent], item, "relates to", seg.confidence, seg.text)

    # 5. provenance: an inline citation backs its own sentence; a citation in the opening line
    #    or on a line of its own ("see https://...") is the source for the whole note
    seg_kind = {s.idx: s.kind for s in parsed.segments}
    first_idx = parsed.segments[0].idx if parsed.segments else -1
    note_refs = [n for si, ids in ref_nodes.items() if si == first_idx or seg_kind.get(si) == "reference" for n in ids]
    if source_ref:
        note_refs.append(source_ref)
    for s, si in stmt_nodes:
        for r in set(note_refs) | set(ref_nodes.get(si, [])):
            edge(s, r, "derived from", "medium")

    # 6. tags on everything this note created
    for nid in S.added:
        for tag in parsed.tags:
            conn.execute("INSERT OR IGNORE INTO tags(node_id, tag) VALUES(?,?)", (nid, tag))

    # an explicit relation later in this note may have replaced a 'relates to' it created earlier
    alive = lambda ids: [e for e in ids if conn.execute("SELECT 1 FROM edges WHERE id=?", (e,)).fetchone()]
    S.edges, S.suggested = alive(S.edges), alive(S.suggested)

    # 7. conflicts: new contradictions, or support/contradiction on the same target
    for eid in S.edges:
        e = conn.execute("SELECT * FROM edges WHERE id=?", (eid,)).fetchone()
        if e["type"] in ("contradicts", "supports"):
            opposite = "supports" if e["type"] == "contradicts" else "contradicts"
            clash = conn.execute("SELECT id FROM edges WHERE dst=? AND type=? AND status='active' AND id!=?",
                                 (e["dst"], opposite, eid)).fetchone()
            if e["type"] == "contradicts" or clash:
                d = edge_dict(conn, e)
                S.conflicts.append(f"“{tp.short_title(d['src_title'], 70)}” contradicts “{tp.short_title(d['dst_title'], 70)}”"
                                   + (", which other notes support." if clash else "."))
                if e["type"] == "contradicts":
                    S.issues.append({"type": "conflict", "edge": eid, "node": e["src"], "other": e["dst"],
                                     "text": S.conflicts[-1]})
                else:   # support that clashes with an existing contradiction: act on that one
                    S.issues.append({"type": "conflict", "edge": clash["id"], "node": e["src"], "other": e["dst"],
                                     "text": S.conflicts[-1]})

    conn.execute("UPDATE projects SET updated=? WHERE id=?", (now(), pid))
    touched = set(S.added) | set(S.updated)
    nodes_out = lambda ids: [node_dict(conn.execute("SELECT * FROM nodes WHERE id=?", (i,)).fetchone()) for i in ids]
    g = graph(conn, pid, focus_ids=touched, depth=1, limit=40, highlight=set(S.added))
    related_quests = [q for q in quests(conn, pid)["quests"] if set(q.get("nodes", [])) & touched][:6]
    for it in S.issues:                     # current names, so the page can link both sides
        for k in ("node", "other"):
            r = conn.execute("SELECT title, kind FROM nodes WHERE id=?", (it.get(k),)).fetchone() if it.get(k) else None
            if r:
                it[k + "_title"], it[k + "_kind"] = r["title"], r["kind"]
    return {
        "project": {"id": pid, "handle": project["handle"], "name": project["name"]},
        "note": {"id": note_id, "code": note_code},
        "added": nodes_out(S.added),
        "updated": nodes_out(S.updated),
        "edges": [edge_dict(conn, conn.execute("SELECT * FROM edges WHERE id=?", (e,)).fetchone()) for e in S.edges],
        "suggested": [edge_dict(conn, conn.execute("SELECT * FROM edges WHERE id=?", (e,)).fetchone()) for e in S.suggested],
        "conflicts": S.conflicts,
        "ambiguities": S.ambiguities,
        "merged": S.merged,
        "promoted": [conn.execute("SELECT title FROM nodes WHERE id=?", (i,)).fetchone()[0] for i in S.promoted],
        "issues": S.issues,
        "mermaid": g["mermaid"],
        "quests": related_quests,
    }


def _suggest_solves(conn, pid, stmt, seg, edge):
    """A solution without an explicit target: suggest the open questions/risks it most resembles."""
    toks = tp.content_tokens(seg.text)
    my = {r[0] for r in conn.execute("SELECT dst FROM edges WHERE src=?", (stmt,))}
    best = []
    for row in conn.execute("SELECT id, body, title FROM nodes WHERE project_id=? AND kind IN ('question','risk') "
                            "AND status!='resolved' AND id!=?", (pid, stmt)):
        score = tp.jaccard(toks, tp.content_tokens(row["body"] or row["title"]))
        nb = {r[0] for r in conn.execute("SELECT src FROM edges WHERE dst=? AND type='raises question'", (row["id"],))}
        nb |= {r[0] for r in conn.execute("SELECT dst FROM edges WHERE src=?", (row["id"],))}
        score += 0.15 * len(my & nb)
        if score >= 0.25:
            best.append((score, row["id"]))
    for _, qid in sorted(best, reverse=True)[:2]:
        edge(stmt, qid, "solves", "low", seg.text, status="suggested")


# ---------------------------------------------------------------- notes

def list_notes(conn, pid: int, limit: int = 200) -> list[dict]:
    rows = conn.execute("SELECT * FROM notes WHERE project_id=? ORDER BY created DESC LIMIT ?", (pid, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["nodes"] = [dict(x) for x in conn.execute(
            "SELECT DISTINCT n.id, n.code, n.kind, n.title FROM mentions m JOIN nodes n ON n.id=m.node_id "
            "WHERE m.note_id=? ORDER BY n.code", (r["id"],))]
        d["tags"] = [x[0] for x in conn.execute("SELECT tag FROM note_tags WHERE note_id=?", (r["id"],))]
        d["source_node"] = None
        if r["source"]:
            s = conn.execute("SELECT id FROM nodes WHERE project_id=? AND kind='reference' AND body=?",
                             (pid, r["source"].strip().strip('"').strip())).fetchone()
            d["source_node"] = s[0] if s else None
        out.append(d)
    return out


def concept_list(conn, pid: int) -> list[dict]:
    aliases = defaultdict(list)
    for r in conn.execute("SELECT a.node_id, a.alias FROM aliases a JOIN nodes n ON n.id=a.node_id "
                          "WHERE n.project_id=? AND n.kind IN ('concept','method')", (pid,)):
        aliases[r[0]].append(r[1])
    return [{"id": r["id"], "kind": r["kind"], "title": r["title"], "definition": r["definition"][:160],
             "notes": r["notes"], "aliases": [a for a in aliases[r["id"]] if a.lower() != r["title"].lower()]}
            for r in conn.execute(
                "SELECT n.id, n.kind, n.title, n.definition, (SELECT COUNT(DISTINCT note_id) FROM mentions "
                "WHERE node_id=n.id) AS notes FROM nodes n WHERE n.project_id=? AND n.kind IN ('concept','method') "
                "ORDER BY notes DESC, lower(n.title)", (pid,))]


def tag_list(conn, pid: int) -> list[dict]:
    """Tags used in a project, most used first, for #tag suggestions. A tag in a note also lands on every item the
    note creates, so notes are counted separately from items."""
    out = defaultdict(lambda: {"notes": 0, "items": 0})
    for r in conn.execute("SELECT t.tag, COUNT(*) FROM note_tags t JOIN notes o ON o.id=t.note_id WHERE o.project_id=? "
                          "GROUP BY t.tag", (pid,)):
        out[r[0]]["notes"] = r[1]
    for r in conn.execute("SELECT t.tag, COUNT(*) FROM tags t JOIN nodes n ON n.id=t.node_id WHERE n.project_id=? "
                          "GROUP BY t.tag", (pid,)):
        out[r[0]]["items"] = r[1]
    return [dict(tag=t, **c) for t, c in sorted(out.items(), key=lambda x: (-x[1]["notes"], -x[1]["items"], x[0]))]


def split_from(conn, node_id: int, note_id: int) -> dict:
    """Undo an automatic 'already in your map' merge: what this note said becomes its own item."""
    n = get_node(conn, node_id)
    m = conn.execute("SELECT excerpt FROM mentions WHERE node_id=? AND note_id=? AND role='source'",
                     (node_id, note_id)).fetchone()
    if not m:
        raise AtlasError("this note is not a source of that item")
    text = m["excerpt"]
    new = create_node(conn, n["project_id"], n["kind"], tp.short_title(text), body=text, origin="user")
    conn.execute("UPDATE mentions SET node_id=? WHERE node_id=? AND note_id=? AND role='source'", (new, node_id, note_id))
    # links this note made for the merged statement belong to the new item
    conn.execute("UPDATE OR IGNORE edges SET src=? WHERE src=? AND note_id=? AND dst!=?", (new, node_id, note_id, new))
    conn.execute("UPDATE OR IGNORE edges SET dst=? WHERE dst=? AND note_id=? AND src!=?", (new, node_id, note_id, new))
    return node_card(conn, new)


def delete_note(conn, note_id: int) -> dict:
    note = conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
    if not note:
        raise AtlasError("note not found")
    node_ids = [r[0] for r in conn.execute("SELECT DISTINCT node_id FROM mentions WHERE note_id=?", (note_id,))]
    conn.execute("DELETE FROM edges WHERE note_id=? AND origin='parser'", (note_id,))
    conn.execute("DELETE FROM definitions WHERE note_id=? AND origin='parser'", (note_id,))
    conn.execute("DELETE FROM notes WHERE id=?", (note_id,))
    removed = []
    for nid in node_ids:
        r = conn.execute("SELECT code, origin, edited FROM nodes WHERE id=?", (nid,)).fetchone()
        if not r or r["origin"] != "parser" or r["edited"]:
            continue
        if not conn.execute("SELECT 1 FROM mentions WHERE node_id=?", (nid,)).fetchone():
            conn.execute("DELETE FROM nodes WHERE id=?", (nid,))
            removed.append(r["code"])
        else:
            d = conn.execute("SELECT text FROM definitions WHERE node_id=? ORDER BY id LIMIT 1", (nid,)).fetchone()
            conn.execute("UPDATE nodes SET definition=? WHERE id=? AND edited=0", (d[0] if d else "", nid))
    return {"deleted": note["code"], "removed_nodes": removed}


# ---------------------------------------------------------------- cards and edits

def node_card(conn, node_id: int) -> dict:
    n = get_node(conn, node_id)
    p = get_project(conn, n["project_id"])
    out_e = [edge_dict(conn, e) for e in conn.execute("SELECT * FROM edges WHERE src=? AND status!='rejected'", (node_id,))]
    in_e = [edge_dict(conn, e) for e in conn.execute("SELECT * FROM edges WHERE dst=? AND status!='rejected'", (node_id,))]
    mentions = [dict(r) for r in conn.execute(
        "SELECT m.note_id, m.excerpt, m.role, o.code AS note_code, o.created, o.source FROM mentions m "
        "JOIN notes o ON o.id=m.note_id WHERE m.node_id=? ORDER BY o.created DESC", (node_id,))]
    defs = [dict(r) for r in conn.execute(
        "SELECT d.id, d.text, d.origin, o.code AS note_code FROM definitions d LEFT JOIN notes o ON o.id=d.note_id "
        "WHERE d.node_id=? ORDER BY d.id", (node_id,))]
    neighbours = {}
    for e in out_e:
        neighbours[e["dst"]] = (e["dst_code"], e["dst_title"], e["dst_kind"])
    for e in in_e:
        neighbours[e["src"]] = (e["src_code"], e["src_title"], e["src_kind"])
    return {
        "node": n,
        "project": {"id": p["id"], "handle": p["handle"], "name": p["name"]},
        "aliases": [r[0] for r in conn.execute("SELECT alias FROM aliases WHERE node_id=? ORDER BY alias", (node_id,))],
        "tags": [r[0] for r in conn.execute("SELECT tag FROM tags WHERE node_id=? ORDER BY tag", (node_id,))],
        "definitions": defs,
        "mentions": mentions,
        "out": out_e,
        "in": in_e,
        "questions": [{"id": i, "code": c, "title": t} for i, (c, t, k) in neighbours.items() if k == "question"],
        "evidence": [{"id": i, "code": c, "title": t} for i, (c, t, k) in neighbours.items() if k == "evidence"],
        "related": [{"id": i, "code": c, "title": t, "kind": k} for i, (c, t, k) in neighbours.items()
                    if k not in ("question", "evidence")],
    }


def update_node(conn, node_id: int, f: dict, origin: str = "user") -> dict:
    n = get_node(conn, node_id)
    sets = {}
    for k in ("title", "body", "status", "confidence"):
        if k in f and f[k] is not None:
            sets[k] = str(f[k]).strip()
    if "kind" in f and f["kind"] and f["kind"] != n["kind"]:
        if f["kind"] not in NODE_KINDS:
            raise AtlasError("unknown kind")
        sets["kind"] = f["kind"]
    if "definition" in f and f["definition"] is not None:
        d = f["definition"].strip()
        sets["definition"] = d
        if d and not conn.execute("SELECT 1 FROM definitions WHERE node_id=? AND text=?", (node_id, d)).fetchone():
            conn.execute("INSERT INTO definitions(node_id, text, origin, created) VALUES(?,?,?,?)", (node_id, d, origin, now()))
    if sets:
        sets["edited"] = 1
        sets["updated"] = now()
        conn.execute(f"UPDATE nodes SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), node_id))
        if "title" in sets:
            add_alias(conn, node_id, sets["title"])
    for a in f.get("add_aliases") or []:
        add_alias(conn, node_id, a)
    for a in f.get("remove_aliases") or []:
        conn.execute("DELETE FROM aliases WHERE node_id=? AND norm=?", (node_id, tp.norm(a)))
    for t in f.get("add_tags") or []:
        conn.execute("INSERT OR IGNORE INTO tags(node_id, tag) VALUES(?,?)", (node_id, t.lower().lstrip("#")))
    for t in f.get("remove_tags") or []:
        conn.execute("DELETE FROM tags WHERE node_id=? AND tag=?", (node_id, t.lower().lstrip("#")))
    return node_card(conn, node_id)


def merge_nodes(conn, src_id: int, into_id: int) -> dict:
    if src_id == into_id:
        raise AtlasError("cannot merge a node into itself")
    a, b = get_node(conn, src_id), get_node(conn, into_id)
    if a["project_id"] != b["project_id"]:
        raise AtlasError("nodes belong to different projects")
    conn.execute("UPDATE OR IGNORE aliases SET node_id=? WHERE node_id=?", (into_id, src_id))
    add_alias(conn, into_id, a["title"])
    conn.execute("UPDATE OR IGNORE mentions SET node_id=? WHERE node_id=?", (into_id, src_id))
    conn.execute("UPDATE definitions SET node_id=? WHERE node_id=?", (into_id, src_id))
    conn.execute("UPDATE OR IGNORE tags SET node_id=? WHERE node_id=?", (into_id, src_id))
    conn.execute("UPDATE OR IGNORE edges SET src=? WHERE src=?", (into_id, src_id))
    conn.execute("UPDATE OR IGNORE edges SET dst=? WHERE dst=?", (into_id, src_id))
    conn.execute("DELETE FROM edges WHERE src=dst")
    if not b["definition"] and a["definition"]:
        conn.execute("UPDATE nodes SET definition=? WHERE id=?", (a["definition"], into_id))
    conn.execute("DELETE FROM nodes WHERE id=?", (src_id,))
    conn.execute("UPDATE nodes SET edited=1, updated=? WHERE id=?", (now(), into_id))
    return node_card(conn, into_id)


def delete_node(conn, node_id: int):
    conn.execute("DELETE FROM nodes WHERE id=?", (node_id,))


def set_edge(conn, edge_id: int, f: dict) -> dict:
    e = conn.execute("SELECT * FROM edges WHERE id=?", (edge_id,)).fetchone()
    if not e:
        raise AtlasError("link not found")
    if "type" in f and f["type"] != e["type"]:
        if f["type"] not in EDGE_TYPES:
            raise AtlasError("unknown relationship")
        conn.execute("UPDATE OR IGNORE edges SET type=? WHERE id=?", (f["type"], edge_id))
    if "status" in f:
        conn.execute("UPDATE edges SET status=? WHERE id=?", (f["status"], edge_id))
    conn.execute("UPDATE edges SET origin='user' WHERE id=?", (edge_id,))
    return edge_dict(conn, conn.execute("SELECT * FROM edges WHERE id=?", (edge_id,)).fetchone())


# ---------------------------------------------------------------- graph + mermaid

KIND_STYLE = {
    "concept": ("(\"", "\")", "fill:#e8eeff,stroke:#5b7cfa,color:#1b2559"),
    "method": ("[/\"", "\"/]", "fill:#e6f6f4,stroke:#12a594,color:#0b3b35"),
    "idea": (">\"", "\"]", "fill:#fff4d6,stroke:#d9a300,color:#4a3800"),
    "solution": ("([\"", "\"])", "fill:#e3f7e6,stroke:#46a758,color:#173d1f"),
    "question": ("{{\"", "\"}}", "fill:#fde8f3,stroke:#d6409f,color:#4d0f33"),
    "evidence": ("[(\"", "\")]", "fill:#e6f4ff,stroke:#0090ff,color:#0a2e4d"),
    "decision": ("[[\"", "\"]]", "fill:#efe6fb,stroke:#8e4ec6,color:#2f1550"),
    "assumption": ("[\\\"", "\"\\]", "fill:#f5f0e6,stroke:#a07a3c,color:#3d2c0e"),
    "risk": ("[\"", "\"]", "fill:#ffe9e9,stroke:#e5484d,color:#4f0c0f"),
    "explanation": ("[\"", "\"]", "fill:#eef3f3,stroke:#6b8e8e,color:#1e3333"),
    "reference": ("[\"", "\"]", "fill:#f1f1f1,stroke:#8b8b8b,color:#333333"),
    "task": ("[\"", "\"]", "fill:#fff0e5,stroke:#f76b15,color:#4a1f00"),
    "claim": ("[\"", "\"]", "fill:#f4f4fb,stroke:#7b7bab,color:#24244a"),
}


def _label(code: str | None, title: str) -> str:
    t = title if len(title) <= 64 else title[:62] + "…"
    t = (t.replace("\"", "'").replace("<", "‹").replace(">", "›").replace("[", "(").replace("]", ")")
         .replace("{", "(").replace("}", ")").replace("|", "/").replace("#", "♯").replace(";", ",").replace("`", "'"))
    words, lines, cur = t.split(), [], ""
    for w in words:
        if len(cur) + len(w) > 26 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return (f"<b>{code}</b><br/>" if code else "") + "<br/>".join(lines)


# In-app (dark glass UI): tinted translucent fills, coloured hairline borders, light text.
DARK_ACCENT = {"concept": "#0a84ff", "method": "#64d2ff", "idea": "#ffd60a", "solution": "#30d158", "question": "#ff375f",
               "evidence": "#5e5ce6", "decision": "#bf5af2", "assumption": "#ac8e68", "risk": "#ff453a",
               "explanation": "#63e6e2", "reference": "#98989d", "task": "#ff9f0a", "claim": "#a2a2c8"}


def mermaid(nodes: list[dict], edges: list[dict], highlight: set | None = None, direction: str = "LR",
            clickable: bool = True, dark: bool = False) -> str:
    highlight = highlight or set()
    lines = [f"flowchart {direction}"]
    for k, (_, _, style) in KIND_STYLE.items():
        if dark:
            a = DARK_ACCENT[k]
            style = f"fill:{a}26,stroke:{a}b3,stroke-width:1.2px,color:#f2f2f7"
        lines.append(f"  classDef {k} {style}")
    lines.append("  classDef hl stroke-width:3px,stroke-dasharray:0" + (",stroke:#ffffff" if dark else ""))
    ids = {n["id"] for n in nodes}
    for n in nodes:
        o, c, _ = KIND_STYLE.get(n["kind"], KIND_STYLE["concept"])
        cls = n["kind"] + (",hl" if n["id"] in highlight else "")
        # in the app the code is noise; exports keep it so shared documents can cross-reference items
        lines.append(f"  n{n['id']}{o}{_label(None if dark else n['code'], n['title'])}{c}:::{cls}")
    red, dotted = [], []
    i = 0
    for e in edges:
        if e["src"] not in ids or e["dst"] not in ids:
            continue
        label = e["type"] if e["status"] != "suggested" else "suggested: " + e["type"]
        arrow = "-.->" if e["status"] == "suggested" or e["confidence"] == "low" else "-->"
        if e["type"] == "contradicts" and e["status"] == "active":
            arrow = "==>"
            red.append(i)
        if e["status"] == "resolved":
            label += " ✓"
        if dark and e["type"] in ("relates to", "derived from") and e["status"] == "active":
            lines.append(f"  n{e['src']} {arrow} n{e['dst']}")    # a plain line says "related"; no label clutter
        else:
            lines.append(f"  n{e['src']} {arrow}|{label}| n{e['dst']}")
        i += 1
    if red:
        lines.append(f"  linkStyle {','.join(map(str, red))} stroke:#ff453a,stroke-width:2.5px,color:#ff453a")
    if clickable:
        for n in nodes:
            lines.append(f"  click n{n['id']} call atlasPick({n['id']})")
    return "\n".join(lines)


def graph(conn, pid: int, focus_ids: set | None = None, depth: int = 2, kinds: set | None = None,
          include_suggested: bool = True, limit: int = 70, highlight: set | None = None) -> dict:
    nodes = {r["id"]: node_dict(r) for r in conn.execute("SELECT * FROM nodes WHERE project_id=?", (pid,))}
    statuses = ("active", "suggested", "resolved") if include_suggested else ("active", "resolved")
    edges = [dict(r) for r in conn.execute(
        f"SELECT * FROM edges WHERE project_id=? AND status IN ({','.join('?' * len(statuses))})", (pid, *statuses))]
    adj = defaultdict(set)
    for e in edges:
        adj[e["src"]].add(e["dst"])
        adj[e["dst"]].add(e["src"])
    keep = set(nodes)
    if focus_ids:
        keep, frontier = set(i for i in focus_ids if i in nodes), set(i for i in focus_ids if i in nodes)
        for _ in range(max(0, depth)):
            frontier = {m for f in frontier for m in adj[f]} - keep
            keep |= frontier
    if kinds:
        keep = {i for i in keep if nodes[i]["kind"] in kinds or (focus_ids and i in focus_ids)}
    truncated = False
    total = len(keep)
    if len(keep) > limit:
        truncated = True
        must = set(focus_ids or ())
        ranked = sorted(keep - must, key=lambda i: -len(adj[i]))
        keep = (must & keep) | set(ranked[:max(0, limit - len(must & keep))])
    ns = [nodes[i] for i in sorted(keep, key=lambda i: nodes[i]["code"])]
    es = [e for e in edges if e["src"] in keep and e["dst"] in keep]
    if highlight is None:
        highlight = set(focus_ids or ())
    return {"nodes": ns, "edges": es, "total": total, "truncated": truncated,
            "mermaid": mermaid(ns, es, highlight=highlight, dark=True)}


# ---------------------------------------------------------------- quests

QUEST_XP = {"answer": 15, "verify": 10, "contradiction": 25, "definition-conflict": 20, "define": 10,
            "evidence": 15, "mitigate": 15, "assumption": 15, "task": 10, "suggested": 5, "comention": 5,
            "duplicate": 10, "isolated": 5, "cluster": 20}
RANKS = ["Wanderer", "Note-taker", "Cartographer", "Pathfinder", "Navigator", "Explorer", "Atlas Keeper", "Polymath"]


def _components(node_ids, edges):
    adj = defaultdict(set)
    for e in edges:
        adj[e["src"]].add(e["dst"])
        adj[e["dst"]].add(e["src"])
    seen, comps = set(), []
    for n in node_ids:
        if n in seen or not adj[n]:
            continue
        comp, dq = [], deque([n])
        seen.add(n)
        while dq:
            x = dq.popleft()
            comp.append(x)
            for y in adj[x]:
                if y not in seen:
                    seen.add(y)
                    dq.append(y)
        comps.append(comp)
    return sorted(comps, key=len, reverse=True), adj


def quests(conn, pid: int) -> dict:
    nodes = {r["id"]: node_dict(r) for r in conn.execute("SELECT * FROM nodes WHERE project_id=?", (pid,))}
    edges = [dict(r) for r in conn.execute("SELECT * FROM edges WHERE project_id=? AND status IN ('active','suggested')", (pid,))]
    active = [e for e in edges if e["status"] == "active"]
    state = {r["key"]: r["status"] for r in conn.execute("SELECT key, status FROM quest_state WHERE project_id=?", (pid,))}
    inc = defaultdict(list)
    out = defaultdict(list)
    for e in active:
        inc[e["dst"]].append(e)
        out[e["src"]].append(e)
    mcount = defaultdict(int)
    for r in conn.execute("SELECT m.node_id, COUNT(DISTINCT m.note_id) FROM mentions m JOIN nodes n ON n.id=m.node_id "
                          "WHERE n.project_id=? GROUP BY m.node_id", (pid,)):
        mcount[r[0]] = r[1]
    q = []

    def add(key, qtype, title, detail, node_ids, actions, priority):
        if state.get(key) in ("done", "dismissed"):
            return
        q.append({"key": key, "type": qtype, "title": title, "detail": detail, "nodes": list(node_ids),
                  "codes": [nodes[i]["code"] for i in node_ids if i in nodes],
                  "titles": [nodes[i]["title"] for i in node_ids if i in nodes], "actions": actions,
                  "xp": QUEST_XP.get(qtype, 5), "priority": priority})

    lbl = lambda i: f"“{tp.short_title(nodes[i]['title'], 80)}”"
    for e in active:
        if e["type"] == "contradicts":
            add(f"contra:{e['id']}", "contradiction", "Resolve this contradiction",
                f"{lbl(e['src'])} contradicts {lbl(e['dst'])}", [e["src"], e["dst"]], ["resolve-edge", "open"], 1)
    for i, n in nodes.items():
        k, st = n["kind"], n["status"]
        if k == "question" and st != "resolved":
            solved = [e for e in inc[i] if e["type"] == "solves"]
            if solved:
                add(f"verify:{n['code']}", "verify", "Confirm the answer", f"{lbl(i)} has a proposed answer: "
                    f"{lbl(solved[0]['src'])}", [i, solved[0]["src"]], ["resolve-node", "open"], 3)
            else:
                add(f"answer:{n['code']}", "answer", "Answer the question", lbl(i), [i], ["capture-answer", "resolve-node"], 2)
        if k == "risk" and st != "resolved" and not any(e["type"] == "solves" for e in inc[i]):
            add(f"mitigate:{n['code']}", "mitigate", "Mitigate this risk", lbl(i), [i], ["capture-answer", "resolve-node"], 3)
        if k == "assumption" and st != "resolved" and not any(e["type"] in ("supports", "contradicts") for e in inc[i]):
            add(f"assume:{n['code']}", "assumption", "Validate the assumption", lbl(i), [i], ["capture-evidence", "resolve-node"], 3)
        if k in ("claim", "idea", "method") and st not in ("resolved",) and n["confidence"] == "low" \
                and not any(e["type"] in ("supports", "contradicts") for e in inc[i]):
            add(f"evidence:{n['code']}", "evidence", "Find evidence", f"{lbl(i)} is marked uncertain and has no evidence yet.",
                [i], ["capture-evidence", "dismiss"], 4)
        if k == "task" and st != "resolved":
            add(f"task:{n['code']}", "task", "Next step", lbl(i), [i], ["resolve-node", "open"], 4)
        if k in ("concept", "method") and not n["definition"] and mcount[i] >= 2:
            add(f"define:{n['code']}", "define", "Define this concept",
                f"{lbl(i)} appears in {mcount[i]} notes but has no definition.", [i], ["open", "dismiss"], 5)
    for r in conn.execute("SELECT node_id, COUNT(*) c FROM definitions d JOIN nodes n ON n.id=d.node_id "
                          "WHERE n.project_id=? GROUP BY node_id HAVING c>1", (pid,)):
        add(f"defconflict:{nodes[r[0]]['code']}", "definition-conflict", "Reconcile definitions",
            f"{lbl(r[0])} has {r[1]} different definitions.", [r[0]], ["open", "dismiss"], 2)
    for e in edges:
        if e["status"] == "suggested":
            add(f"suggest:{e['id']}", "suggested", "Suggested link",
                f"{lbl(e['src'])} — {e['type']} → {lbl(e['dst'])}?", [e["src"], e["dst"]],
                [f"accept-edge:{e['id']}", f"reject-edge:{e['id']}"], 3)
    # concepts used in the same sentence of 2+ different notes, with no link between them
    linked = {(e["src"], e["dst"]) for e in edges} | {(e["dst"], e["src"]) for e in edges}
    pairs = defaultdict(set)
    by_sentence = defaultdict(set)
    for r in conn.execute("SELECT m.note_id, m.excerpt, m.node_id FROM mentions m JOIN nodes n ON n.id=m.node_id "
                          "WHERE n.project_id=? AND n.kind IN ('concept','method')", (pid,)):
        by_sentence[(r[0], r[1])].add(r[2])
    for (note, _), ids in by_sentence.items():
        ids = sorted(ids)
        for x in range(len(ids)):
            for y in range(x + 1, len(ids)):
                pairs[(ids[x], ids[y])].add(note)
    for (a, b), notes in sorted(pairs.items(), key=lambda kv: -len(kv[1]))[:15]:
        if len(notes) >= 2 and (a, b) not in linked:
            add(f"co:{a}-{b}", "comention", "Suggested Link",
                f"{lbl(a)} and {lbl(b)} are discussed together in {len(notes)} notes but are not linked.", [a, b],
                [f"link:{a}:{b}", "dismiss"], 4)
    # likely duplicates
    concepts = [n for n in nodes.values() if n["kind"] in ("concept", "method")]
    def acr(t):
        t = t.strip()
        t = t[:-1] if t.endswith("s") and t[:-1].isupper() else t
        return t if " " not in t and t.isupper() and 2 <= len(t) <= 8 else None

    def abbreviates(short, long):
        return acr(short) is not None and len(long.split()) >= 2 and acr(short) == tp.acronym_of(long)

    toks = {n["id"]: tp.content_tokens(n["title"]) for n in concepts}
    for x in range(len(concepts)):
        for y in range(x + 1, len(concepts)):
            a, b = concepts[x], concepts[y]
            if tp.jaccard(toks[a["id"]], toks[b["id"]]) >= 0.75 or abbreviates(a["title"], b["title"]) \
                    or abbreviates(b["title"], a["title"]):
                add(f"dup:{min(a['id'], b['id'])}-{max(a['id'], b['id'])}", "duplicate", "Possible duplicate",
                    f"{lbl(a['id'])} ≈ {lbl(b['id'])}", [a["id"], b["id"]], [f"merge:{b['id']}:{a['id']}", "dismiss"], 3)
    # isolated concepts and disconnected clusters
    comps, adj = _components(list(nodes), active)
    for i, n in nodes.items():
        if not adj[i] and n["kind"] not in ("reference",):
            add(f"isolated:{n['code']}", "isolated", "Connect a stray idea", f"{lbl(i)} has no links yet.", [i], ["open", "dismiss"], 6)
    if len(comps) > 1:
        hub = lambda comp: max(comp, key=lambda i: len(adj[i]))
        main = hub(comps[0])
        for comp in comps[1:4]:
            h = hub(comp)
            add(f"cluster:{nodes[main]['code']}|{nodes[h]['code']}", "cluster", "Connect these two clusters",
                f"The cluster around {lbl(h)} ({len(comp)} nodes) is not connected to the main map around {lbl(main)}.",
                [main, h], ["open", "dismiss"], 5)
    q.sort(key=lambda x: (x["priority"], x["key"]))
    done = sum(1 for v in state.values() if v == "done")
    xp = int(conn.execute("SELECT value FROM meta WHERE key='xp'").fetchone()[0])
    return {"quests": q, "done": done, "open": len(q), "xp": xp, "level": 1 + xp // 100,
            "rank": RANKS[min(len(RANKS) - 1, xp // 100)], "next_level_xp": 100 - xp % 100}


def set_quest_state(conn, pid: int, key: str, status: str, xp: int = 0) -> int:
    conn.execute("INSERT INTO quest_state(project_id, key, status, updated) VALUES(?,?,?,?) "
                 "ON CONFLICT(project_id, key) DO UPDATE SET status=excluded.status, updated=excluded.updated",
                 (pid, key, status, now()))
    if status == "done" and xp:
        conn.execute("UPDATE meta SET value=CAST(value AS INTEGER)+? WHERE key='xp'", (xp,))
    return int(conn.execute("SELECT value FROM meta WHERE key='xp'").fetchone()[0])


# ---------------------------------------------------------------- search, paths, compare

def _adjacency(conn, pid):
    adj = defaultdict(list)
    for e in conn.execute("SELECT * FROM edges WHERE project_id=? AND status IN ('active','resolved')", (pid,)):
        adj[e["src"]].append((e["dst"], e["type"], "out"))
        adj[e["dst"]].append((e["src"], e["type"], "in"))
    return adj


def relationship_path(conn, node_id: int, max_depth: int = 6) -> list[dict]:
    """Shortest path from a node to its project's hub (most-connected node)."""
    n = get_node(conn, node_id)
    adj = _adjacency(conn, n["project_id"])
    if not adj:
        return []
    hub = max(adj, key=lambda i: len(adj[i]))
    if hub == node_id:
        return []
    prev = {node_id: None}
    dq = deque([(node_id, 0)])
    while dq:
        x, d = dq.popleft()
        if x == hub or d >= max_depth:
            continue
        for y, t, direction in adj[x]:
            if y not in prev:
                prev[y] = (x, t, direction)
                dq.append((y, d + 1))
    if hub not in prev:
        return []
    steps, cur = [], hub
    while prev[cur]:
        x, t, direction = prev[cur]
        steps.append((x, t, direction, cur))
        cur = x
    steps.reverse()
    out = []
    for x, t, direction, y in steps:
        a, b = get_node(conn, x), get_node(conn, y)
        out.append({"from": {"id": a["id"], "code": a["code"], "title": a["title"]}, "type": t, "dir": direction,
                    "to": {"id": b["id"], "code": b["code"], "title": b["title"]}})
    return out


def compare(conn, ids: list[int]) -> list[dict]:
    rows = []
    for i in ids:
        c = node_card(conn, i)
        n = c["node"]
        live = lambda e: e["status"] in ("active", "resolved")
        out_t = lambda t: [e["dst_title"] for e in c["out"] if e["type"] == t and live(e)]
        in_t = lambda t: [e["src_title"] for e in c["in"] if e["type"] == t and live(e)]
        rows.append({
            "id": i, "code": n["code"], "title": n["title"], "kind": n["kind"],
            "definition": n["definition"] or n["body"], "confidence": n["confidence"], "status": n["status"],
            "depends_on": out_t("depends on"), "derived_from": out_t("derived from"),
            "supported_by": in_t("supports"), "contradicted_by": in_t("contradicts"), "solves": out_t("solves"),
            "open_questions": [q["title"] for q in c["questions"]],
            "sources": len({m["note_code"] for m in c["mentions"]}),
        })
    return rows


# ---------------------------------------------------------------- AI / scripted enrichment

def topic_key(conn, pid: int, topic: str) -> tuple[str, str]:
    """A topic is a keyword or an item code (C-004); summaries are keyed by its normalized name."""
    topic = (topic or "").strip()
    m = re.fullmatch(r"([A-Za-z])-?(\d{1,4})", topic)
    n = node_by_code(conn, pid, f"{m[1]}-{int(m[2]):03d}") if m else None
    title = n["title"] if n else topic
    key = tp.norm(title)
    if not key:
        raise AtlasError("empty topic")
    return key, title


def set_summary(conn, pid: int, topic: str, text: str, origin: str = "ai") -> str:
    """A written summary of a topic (Claude's interpretation, made on request); shown on its topic brief."""
    key, title = topic_key(conn, pid, topic)
    if not (text or "").strip():
        raise AtlasError("empty summary")
    conn.execute("INSERT INTO summaries(project_id, topic, title, text, origin, created) VALUES(?,?,?,?,?,?) "
                 "ON CONFLICT(project_id, topic) DO UPDATE SET title=excluded.title, text=excluded.text, "
                 "origin=excluded.origin, created=excluded.created", (pid, key, title, text.strip(), origin, now()))
    return title


# ---------------------------------------------------------------- glossary (abbreviations from project documents)

GLOSSARY_KINDS = ("list", "text", "standard", "partner")    # in the document's list / spelled out in it /
                                                            # used but not spelled out / participant short name


def abbr_regex(abbr: str) -> re.Pattern:
    """Case-sensitive: the partner 'AGE' is not the word 'age'. A plural 's' is allowed (ETNs)."""
    return re.compile(r"(?<![\w-])" + re.escape(abbr) + r"s?(?![\w])")


def glossary_entry(conn, pid: int, q: str) -> dict | None:
    q = (q or "").strip()
    r = conn.execute("SELECT * FROM glossary WHERE project_id=? AND (abbr=? OR lower(abbr)=lower(?) OR lower(expansion)=lower(?)) "
                     "ORDER BY abbr=? DESC LIMIT 1", (pid, q, q, q, q)).fetchone()
    return {k: r[k] for k in ("abbr", "expansion", "context", "kind", "page", "source")} if r else None


def set_glossary(conn, pid: int, entries: list[dict], source: str = "", replace: bool = False) -> int:
    if replace:
        conn.execute("DELETE FROM glossary WHERE project_id=?", (pid,))
    for e in entries:
        abbr, exp = (e.get("abbr") or "").strip(), (e.get("expansion") or "").strip()
        if not abbr or not exp:
            raise AtlasError(f"glossary entry needs abbr and expansion: {e}")
        kind = e.get("kind", "text")
        if kind not in GLOSSARY_KINDS:
            raise AtlasError(f"unknown glossary kind {kind}")
        conn.execute("INSERT INTO glossary(project_id, abbr, expansion, context, kind, page, source, created) VALUES(?,?,?,?,?,?,?,?) "
                     "ON CONFLICT(project_id, abbr) DO UPDATE SET expansion=excluded.expansion, context=excluded.context, "
                     "kind=excluded.kind, page=excluded.page, source=excluded.source",
                     (pid, abbr, exp, (e.get("context") or "").strip(), kind, e.get("page"), e.get("source", source), now()))
    return len(entries)


def glossary(conn, pid: int) -> list[dict]:
    """All entries, with how many notes use each (abbreviation or expansion) and whether a summary exists."""
    notes = [(r[0], r[1]) for r in conn.execute("SELECT created, text FROM notes WHERE project_id=?", (pid,))]
    sums = {r[0]: r[1] for r in conn.execute("SELECT topic, created FROM summaries WHERE project_id=?", (pid,))}
    out = []
    for r in conn.execute("SELECT * FROM glossary WHERE project_id=? ORDER BY lower(abbr)", (pid,)):
        a = abbr_regex(r["abbr"])
        x = re.compile(r"(?<!\w)" + r"[\s\-]+".join(map(re.escape, re.split(r"[\s\-]+", r["expansion"]))) + r"(?!\w)", re.I)
        used = [t for t, text in notes if a.search(text) or x.search(text)]
        s = sums.get(tp.norm(r["abbr"])) or sums.get(tp.norm(r["expansion"]))
        out.append({k: r[k] for k in ("abbr", "expansion", "context", "kind", "page", "source")}
                   | {"notes": len(used), "summary": s is not None, "summary_stale": bool(s and any(t > s for t in used))})
    return out


# ---------------------------------------------------------------- requirements (traceability matrix)

REQ_STATUSES = ("not started", "in progress", "at risk", "blocked", "done", "dropped")
REQ_TRACKED = ("status", "progress", "owner", "due", "note")    # the user's fields; re-importing never overwrites them


def set_requirements(conn, pid: int, entries: list[dict], source: str = "", resolve=None) -> int:
    """Add or refresh requirements from a document. `concept` (an item code or $ref) links each to its concept."""
    t = now()
    for i, e in enumerate(entries):
        rid, title = (e.get("rid") or "").strip(), (e.get("title") or "").strip()
        if not rid or not title:
            raise AtlasError(f"requirement needs rid and title: {e}")
        node = resolve(e["concept"]) if e.get("concept") and resolve else None
        conn.execute(
            "INSERT INTO requirements(project_id, rid, grp, grp_title, title, text, source, page, wp, challenges, context, "
            "sort, node_id, due, created, updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project_id, rid) DO UPDATE SET "
            "grp=excluded.grp, grp_title=excluded.grp_title, title=excluded.title, text=excluded.text, source=excluded.source, "
            "page=excluded.page, wp=excluded.wp, challenges=excluded.challenges, context=excluded.context, sort=excluded.sort, "
            "node_id=COALESCE(excluded.node_id, requirements.node_id)",
            (pid, rid, e.get("grp", ""), e.get("grp_title", ""), title, e.get("text", ""), e.get("source", source),
             e.get("page"), e.get("wp", ""), e.get("challenges", ""), e.get("context", ""), e.get("sort", i), node,
             e.get("due", ""), t, t))
    return len(entries)


def requirement_list(conn, pid: int) -> list[dict]:
    out = []
    for r in conn.execute("SELECT q.*, n.code AS node_code, n.title AS node_title, n.kind AS node_kind, "
                          "(SELECT COUNT(*) FROM requirement_log l WHERE l.project_id=q.project_id AND l.rid=q.rid) AS changes "
                          "FROM requirements q LEFT JOIN nodes n ON n.id=q.node_id WHERE q.project_id=? ORDER BY q.sort, q.rid", (pid,)):
        d = dict(r)
        d["notes"], d["last_note"] = 0, None
        if r["node_id"]:
            # notes that mention the requirement's concept, or a statement linked to it
            ev = conn.execute(
                "SELECT COUNT(DISTINCT o.id), MAX(o.created) FROM mentions m JOIN notes o ON o.id=m.note_id WHERE m.node_id IN "
                "(SELECT ? UNION SELECT CASE WHEN e.src=? THEN e.dst ELSE e.src END FROM edges e JOIN nodes x ON "
                "x.id = CASE WHEN e.src=? THEN e.dst ELSE e.src END WHERE (e.src=? OR e.dst=?) AND e.status!='rejected' "
                f"AND x.kind IN ({','.join('?' * len(STATEMENT_KINDS))}))",
                (r["node_id"],) * 5 + tuple(STATEMENT_KINDS)).fetchone()
            d["notes"], d["last_note"] = ev[0], ev[1]
        out.append(d)
    return out


def update_requirement(conn, pid: int, rid: str, f: dict) -> dict:
    cur = conn.execute("SELECT * FROM requirements WHERE project_id=? AND rid=?", (pid, rid)).fetchone()
    if not cur:
        raise AtlasError(f"requirement {rid} not found")
    new = {}
    for k in REQ_TRACKED:
        if k not in f or f[k] is None:
            continue
        v = f[k]
        if k == "status":
            v = str(v).strip().lower()
            if v not in REQ_STATUSES:
                raise AtlasError(f"status must be one of: {', '.join(REQ_STATUSES)}")
        elif k == "progress":
            try:
                v = max(0, min(100, int(round(float(v)))))
            except (TypeError, ValueError):
                raise AtlasError("progress must be a number from 0 to 100")
        else:
            v = str(v).strip()
        new[k] = v
    if new.get("status") == "done" and "progress" not in new:
        new["progress"] = 100
    t = now()
    for k, v in new.items():
        if str(cur[k]) != str(v):
            conn.execute("INSERT INTO requirement_log(project_id, rid, field, old, new, at) VALUES(?,?,?,?,?,?)",
                         (pid, rid, k, str(cur[k]), str(v), t))
            conn.execute(f"UPDATE requirements SET {k}=?, updated=? WHERE project_id=? AND rid=?", (v, t, pid, rid))
    return next(x for x in requirement_list(conn, pid) if x["rid"] == rid)


def requirement_log(conn, pid: int, rid: str) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT field, old, new, at FROM requirement_log WHERE project_id=? AND rid=? "
                                          "ORDER BY id DESC", (pid, rid))]


def apply_ops(conn, pid: int, ops: list[dict], origin: str = "ai", refs: dict | None = None) -> list[str]:
    """Apply a list of structured edits (used by tools/atlas_cli.py). Returns a log.
    `refs` maps "$name" handles to node ids; pass the same dict to share handles across batches."""
    log = []
    refs = {} if refs is None else refs

    def nid(ref):
        if ref is None:
            return None
        if isinstance(ref, int):
            return ref
        if isinstance(ref, str) and ref.startswith("$"):
            return refs[ref[1:]]
        n = node_by_code(conn, pid, ref)
        if not n:
            raise AtlasError(f"unknown node {ref}")
        return n["id"]

    for op in ops:
        kind = op.get("op")
        if kind == "add_node":
            i = create_node(conn, pid, op.get("kind", "concept"), op["title"], body=op.get("body", ""), origin=origin,
                            confidence=op.get("confidence", "medium"), definition=op.get("definition", ""))
            if op.get("definition"):
                conn.execute("INSERT INTO definitions(node_id, text, origin, created) VALUES(?,?,?,?)",
                             (i, op["definition"], origin, now()))
            for a in op.get("aliases", []):
                add_alias(conn, i, a)
            for t in op.get("tags", []):
                conn.execute("INSERT OR IGNORE INTO tags(node_id, tag) VALUES(?,?)", (i, t))
            if op.get("ref"):
                refs[op["ref"]] = i
            log.append(f"added {get_node(conn, i)['code']} {op['title']}")
        elif kind == "update_node":
            update_node(conn, nid(op["node"]), op, origin=origin)
            log.append(f"updated {op['node']}")
        elif kind == "add_edge":
            eid, created = add_edge(conn, pid, nid(op["src"]), nid(op["dst"]), op["type"], status=op.get("status", "active"),
                                    confidence=op.get("confidence", "medium"), origin=origin, evidence=op.get("evidence", ""))
            log.append(f"{'linked' if created else 'exists'} {op['src']} {op['type']} {op['dst']}")
        elif kind == "remove_edge":
            conn.execute("DELETE FROM edges WHERE src=? AND dst=? AND type=?", (nid(op["src"]), nid(op["dst"]), op["type"]))
            log.append(f"unlinked {op['src']} {op['type']} {op['dst']}")
        elif kind == "merge":
            merge_nodes(conn, nid(op["node"]), nid(op["into"]))
            log.append(f"merged {op['node']} into {op['into']}")
        elif kind == "delete_node":
            delete_node(conn, nid(op["node"]))
            log.append(f"deleted {op['node']}")
        elif kind == "resolve":
            conn.execute("UPDATE nodes SET status='resolved', updated=? WHERE id=?", (now(), nid(op["node"])))
            log.append(f"resolved {op['node']}")
        elif kind == "add_note":
            r = ingest(conn, pid, op["text"], source=op.get("source", "ai"))
            conn.execute("UPDATE notes SET ai_reviewed=1 WHERE id=?", (r["note"]["id"],))
            log.append(f"note {r['note']['code']}: +{len(r['added'])} nodes, +{len(r['edges'])} links")
        elif kind == "set_summary":
            log.append(f"summary for '{set_summary(conn, pid, op['topic'], op['text'], origin=origin)}'")
        elif kind == "clear_summary":
            key, title = topic_key(conn, pid, op["topic"])
            conn.execute("DELETE FROM summaries WHERE project_id=? AND topic=?", (pid, key))
            log.append(f"cleared summary for '{title}'")
        elif kind == "set_glossary":
            n = set_glossary(conn, pid, op["entries"], source=op.get("source", ""), replace=bool(op.get("replace")))
            log.append(f"glossary: {n} entries{' (replaced)' if op.get('replace') else ''}")
        elif kind == "set_requirements":
            n = set_requirements(conn, pid, op["entries"], source=op.get("source", ""), resolve=nid)
            log.append(f"requirements: {n} entries")
        elif kind == "remove_requirement":
            conn.execute("DELETE FROM requirements WHERE project_id=? AND rid=?", (pid, op["rid"]))
            log.append(f"requirements: removed {op['rid']}")
        elif kind == "remove_glossary":
            conn.execute("DELETE FROM glossary WHERE project_id=? AND abbr=?", (pid, op["abbr"]))
            log.append(f"glossary: removed {op['abbr']}")
        elif kind == "mark_reviewed":
            notes = op.get("notes", "all")
            if notes == "all":
                conn.execute("UPDATE notes SET ai_reviewed=1 WHERE project_id=?", (pid,))
            else:
                for code in notes:
                    conn.execute("UPDATE notes SET ai_reviewed=1 WHERE project_id=? AND code=?", (pid, code))
            log.append(f"marked reviewed: {notes}")
        else:
            raise AtlasError(f"unknown op {kind}")
    return log


def export_project(conn, pid: int) -> dict:
    """Complete, portable JSON representation of one project."""
    p = get_project(conn, pid)
    nodes = []
    for r in conn.execute("SELECT * FROM nodes WHERE project_id=? ORDER BY kind, code", (pid,)):
        d = node_dict(r)
        d["aliases"] = [x[0] for x in conn.execute("SELECT alias FROM aliases WHERE node_id=?", (r["id"],))]
        d["tags"] = [x[0] for x in conn.execute("SELECT tag FROM tags WHERE node_id=?", (r["id"],))]
        d["definitions"] = [dict(x) for x in conn.execute(
            "SELECT d.text, d.origin, o.code AS note FROM definitions d LEFT JOIN notes o ON o.id=d.note_id WHERE node_id=?", (r["id"],))]
        d["sources"] = [dict(x) for x in conn.execute(
            "SELECT o.code AS note, m.excerpt, m.role FROM mentions m JOIN notes o ON o.id=m.note_id WHERE m.node_id=?", (r["id"],))]
        nodes.append(d)
    edges = [edge_dict(conn, e) for e in conn.execute("SELECT * FROM edges WHERE project_id=? AND status!='rejected'", (pid,))]
    notes = [dict(r) for r in conn.execute("SELECT id, code, text, source, created, ai_reviewed FROM notes WHERE project_id=? ORDER BY created", (pid,))]
    summaries = [dict(r) for r in conn.execute("SELECT title, text, origin, created FROM summaries WHERE project_id=?", (pid,))]
    gloss = [dict(r) for r in conn.execute("SELECT abbr, expansion, context, kind, page, source FROM glossary WHERE project_id=?", (pid,))]
    reqs = [{k: r[k] for k in ("rid", "grp", "grp_title", "title", "text", "source", "page", "wp", "challenges", "context",
                               "sort", "node_code", "status", "progress", "owner", "due", "note", "updated")}
            for r in requirement_list(conn, pid)]
    req_log = [dict(r) for r in conn.execute("SELECT rid, field, old, new, at FROM requirement_log WHERE project_id=? ORDER BY id", (pid,))]
    return {"format": "idea-atlas/1", "project": p, "nodes": nodes, "edges": edges, "notes": notes,
            "summaries": summaries, "glossary": gloss, "requirements": reqs, "requirement_log": req_log,
            "quests": quests(conn, pid)}
