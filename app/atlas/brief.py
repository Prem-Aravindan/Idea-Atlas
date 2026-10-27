"""Topic brief: everything a project's notes say about one keyword, gathered on one page.

Extractive only: definitions, statements and note passages are the user's own words, grouped and dated.
A written summary appears only if one was made during a session with Claude (table `summaries`); it is
labelled as an interpretation and flagged when newer notes mention the topic."""
from __future__ import annotations

import re
import time
from collections import Counter, defaultdict

from . import engine as E
from . import search as S
from . import textparse as tp

TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+#]*(?:-[A-Za-z0-9]+)*")
SENT_RE = re.compile(r"(?<=[.!?;])\s+")
GROUPS = [
    ("Ideas and statements", ("idea", "claim", "explanation", "assumption")),
    ("Methods and solutions", ("method", "solution")),
    ("Evidence", ("evidence",)),
    ("Questions and risks", ("question", "risk")),
    ("Decisions and tasks", ("decision", "task")),
]
TOPIC_KINDS = ("concept", "method")
MAX_PASSAGES = 12
SECTION_MAX = 1500


def _token_stems(tok: str) -> set[str]:
    """'over-smoothing' -> {'oversmooth', 'over', 'smooth'}, so either spelling matches."""
    out = {S.stem(tok.replace("-", ""))}
    if "-" in tok:
        out.update(S.stem(p) for p in tok.split("-") if p and p.lower() not in S.STOP)
    return out


def _text_stems(text: str) -> set[str]:
    out = set()
    for tok in TOKEN_RE.findall(text or ""):
        out |= _token_stems(tok)
    return out


def _wanted(phrase: str) -> frozenset:
    """Stems that must all occur in a sentence for it to be about `phrase`."""
    return frozenset(S.stem(t.replace("-", "")) for t in TOKEN_RE.findall(phrase or "") if t.lower() not in S.STOP)


def _mark(text: str, want: set, exact: re.Pattern | None = None) -> list[dict]:
    """Segments [{t, m}] with m=1 on the topic's words (same shape as search snippets)."""
    spans = [m.span() for m in TOKEN_RE.finditer(text) if _token_stems(m.group(0)) & want]
    if exact:
        spans += [m.span() for m in exact.finditer(text)]
    segs, pos = [], 0
    for a, b in sorted(spans):
        if a < pos:
            continue
        if a > pos:
            segs.append({"t": text[pos:a], "m": 0})
        segs.append({"t": text[a:b], "m": 1})
        pos = b
    segs.append({"t": text[pos:], "m": 0})
    return segs


def _is_heading(lines: list[str], i: int) -> bool:
    """'2. “Constrained NL/DSL compiler”' - short, unpunctuated, starts a block."""
    s = lines[i].strip().strip("\"'“”‘’*#").strip()
    return bool(s) and len(s) <= 90 and s[-1] not in ".,;:!?" and (i == 0 or not lines[i - 1].strip())


def _passages(text: str, about) -> list[str]:
    """Sentences that are about the topic; a heading about it brings its whole section."""
    lines = (text or "").splitlines()
    heads = [_is_heading(lines, i) for i in range(len(lines))]
    out, i = [], 0
    while i < len(lines):
        line = lines[i].strip()
        if line and about(line):
            if heads[i]:
                j = i + 1
                while j < len(lines) and not heads[j]:
                    j += 1
                block = re.sub(r"\n\s*\n+", "\n\n", "\n".join(x.rstrip() for x in lines[i:j]).strip())
                if len(block) > SECTION_MAX:
                    block = block[:SECTION_MAX].rsplit("\n", 1)[0] + "\n…"
                out.append(block)
                i = j
                continue
            sents = [s.strip() for s in SENT_RE.split(line) if s.strip() and about(s)]
            out.extend(sents or [line])     # words spread over two sentences: keep the line
        i += 1
    return out


def _anchor(conn, pid: int, q: str) -> dict | None:
    """The map item the keyword names (by code, name or alias), if there is one."""
    m = re.fullmatch(r"([A-Za-z])-?(\d{1,4})", q)
    if m:
        n = E.node_by_code(conn, pid, f"{m[1]}-{int(m[2]):03d}")
        if n:
            return n
    for cond, key in (("a.norm=?", tp.norm(q)), ("replace(a.norm, ' ', '')=?", tp.norm(q).replace(" ", ""))):
        r = conn.execute(f"SELECT n.* FROM aliases a JOIN nodes n ON n.id=a.node_id WHERE n.project_id=? AND {cond} "
                         "ORDER BY n.kind NOT IN ('concept','method'), n.id LIMIT 1", (pid, key)).fetchone()
        if r:                       # 'oversmoothing' also finds 'Over-smoothing'
            return E.node_dict(r)
    return None


def brief(conn, pid: int, q: str) -> dict:
    q = (q or "").strip()
    out = {"query": q, "corrected": None, "glossary": None, "topic": None, "summary": None, "definitions": [], "groups": [],
           "topics": [], "sources": [], "conflicts": [], "timeline": [],
           "stats": {"notes": 0, "items": 0, "first": None, "last": None}}
    if not q or not tp.norm(q):
        return out
    g = out["glossary"] = E.glossary_entry(conn, pid, q)
    exact = None
    if g:
        # an abbreviation is matched exactly as written ('AGE' the partner, not the word 'age'); its expansion as words
        exact = E.abbr_regex(g["abbr"])
        anchor = _anchor(conn, pid, g["expansion"])
        if not anchor:
            r = conn.execute("SELECT n.* FROM aliases a JOIN nodes n ON n.id=a.node_id WHERE n.project_id=? AND a.alias=? "
                             "LIMIT 1", (pid, g["abbr"])).fetchone()
            anchor = E.node_dict(r) if r else None
    else:
        anchor = _anchor(conn, pid, q)
    if not anchor and not g:
        idx = S._index(conn, pid)
        if any(t not in idx.vocab for t in _wanted(q)):          # a typo: use the spelling the notes use
            fix = S.search(conn, pid, q, limit=1)["did_you_mean"]
            if fix and fix.lower() != q.lower():
                q = fix
                anchor = _anchor(conn, pid, q)
                out["corrected"] = anchor["title"] if anchor else fix

    names = [g["expansion"]] if g else [q]
    aid = anchor["id"] if anchor else None
    if anchor:
        aliases = [r[0] for r in conn.execute("SELECT alias FROM aliases WHERE node_id=? ORDER BY alias", (aid,))]
        names += [anchor["title"]] + [a for a in aliases if not g or a.lower() != g["abbr"].lower()]
        out["topic"] = dict(anchor, aliases=[a for a in aliases if a.lower() != anchor["title"].lower()],
                            tags=[r[0] for r in conn.execute("SELECT tag FROM tags WHERE node_id=?", (aid,))])
        out["definitions"] = [dict(r) for r in conn.execute(
            "SELECT d.text, d.origin, o.code AS note_code, COALESCE(o.created, d.created) AS created FROM definitions d "
            "LEFT JOIN notes o ON o.id=d.note_id WHERE d.node_id=? ORDER BY d.id", (aid,))]
    reqs = {w for w in (_wanted(n) for n in names) if w}
    want = set().union(*reqs) if reqs else set()

    def about(text: str) -> bool:
        if exact and exact.search(text):
            return True
        st = _text_stems(text)
        return any(r <= st for r in reqs)

    # map items: named in their own words, or linked to the topic item
    links = defaultdict(list)       # other id -> relations, read from the other item's side
    if anchor:
        for e in conn.execute("SELECT * FROM edges WHERE (src=? OR dst=?) AND status!='rejected'", (aid, aid)):
            other, d = (e["dst"], "in") if e["src"] == aid else (e["src"], "out")
            links[other].append({"type": e["type"], "dir": d, "status": e["status"], "confidence": e["confidence"]})
    nodes = {r["id"]: r for r in conn.execute("SELECT * FROM nodes WHERE project_id=?", (pid,))}
    seen = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT m.node_id, MIN(o.created), COUNT(DISTINCT m.note_id) FROM mentions m JOIN notes o ON o.id=m.note_id "
        "WHERE o.project_id=? GROUP BY m.node_id", (pid,))}

    def item(r, **extra):
        first, n = seen.get(r["id"], (r["created"], 0))
        return dict(E.node_dict(r), first_seen=first, notes=n, relations=links.get(r["id"], []), **extra)

    statements, topic_ids, source_ids = [], {}, set()
    for nid, r in nodes.items():
        if nid == aid:
            continue
        named = about(" ".join(filter(None, (r["title"], r["body"], r["definition"]))))
        if not named and nid not in links:
            continue
        if r["kind"] == "reference":
            source_ids.add(nid)
        elif r["kind"] == "concept":    # a method named or linked here is something said about it
            topic_ids[nid] = named
        else:
            statements.append(r)
    statements.sort(key=lambda r: seen.get(r["id"], (r["created"],))[0] or 0)
    for label, kinds in GROUPS:
        items = [item(r) for r in statements if r["kind"] in kinds]
        if items:
            out["groups"].append({"label": label, "items": items})
    out["stats"]["items"] = len(statements)

    # other topics: linked to the topic, named like it, or discussed in the same statements
    stmt_ids = {r["id"] for r in statements}
    shared = Counter()
    if stmt_ids:
        ph = ",".join("?" * len(stmt_ids))
        for e in conn.execute(f"SELECT src, dst, type, dst IN ({ph}) AS inbound FROM edges WHERE status!='rejected' AND "
                              f"(src IN ({ph}) OR dst IN ({ph}))", (*stmt_ids, *stmt_ids, *stmt_ids)):
            other = e["src"] if e["inbound"] else e["dst"]
            if other == aid or other in stmt_ids or other not in nodes:
                continue
            if nodes[other]["kind"] in TOPIC_KINDS:
                shared[other] += 1
            elif nodes[other]["kind"] == "reference" and e["type"] == "derived from":
                source_ids.add(other)
    for nid in set(topic_ids) | set(shared):
        out["topics"].append(dict(item(nodes[nid]), shared=shared[nid], named=topic_ids.get(nid, False)))
    out["topics"].sort(key=lambda t: (not t["relations"], not t["named"], -t["shared"], t["title"].lower()))
    out["topics"] = out["topics"][:30]
    out["sources"] = [{"id": i, "title": nodes[i]["title"], "body": nodes[i]["body"]}
                      for i in sorted(source_ids, key=lambda i: nodes[i]["title"].lower())]

    # unresolved contradictions inside this topic
    ids = stmt_ids | set(topic_ids) | ({aid} if aid else set())
    if ids:
        ph = ",".join("?" * len(ids))
        for e in conn.execute(f"SELECT src, dst FROM edges WHERE type='contradicts' AND status='active' AND "
                              f"src IN ({ph}) AND dst IN ({ph})", (*ids, *ids)):
            out["conflicts"].append({k: {"id": n["id"], "kind": n["kind"], "title": n["title"]}
                                     for k, n in (("a", nodes[e["src"]]), ("b", nodes[e["dst"]]))})

    # timeline: every note that talks about it, oldest first, with the sentences that do
    extra = defaultdict(list)       # sentences recorded for the topic item and items linked to it
    linked = [i for i in links if i in stmt_ids] + ([aid] if aid else [])
    if linked:
        ph = ",".join("?" * len(linked))
        for r in conn.execute(f"SELECT note_id, excerpt FROM mentions WHERE node_id IN ({ph}) AND excerpt!=''", linked):
            extra[r[0]].append(r[1])
    for o in conn.execute("SELECT id, code, text, source, created FROM notes WHERE project_id=? ORDER BY created", (pid,)):
        ps = _passages(o["text"], about)
        ps += [x for x in dict.fromkeys(extra.get(o["id"], [])) if not any(x in p for p in ps)]
        if not ps:
            continue
        out["timeline"].append({"note": {k: o[k] for k in ("id", "code", "created", "source")}, "text": o["text"],
                                "passages": [_mark(p if len(p) <= SECTION_MAX + 2 else p[:SECTION_MAX] + "…", want, exact)
                                             for p in ps[:MAX_PASSAGES]],
                                "more": max(0, len(ps) - MAX_PASSAGES)})
    tl = out["timeline"]
    out["stats"].update(notes=len(tl), first=tl[0]["note"]["created"] if tl else None,
                        last=tl[-1]["note"]["created"] if tl else None)

    keys = list({tp.norm(n) for n in names + ([g["abbr"]] if g else [])} - {""})
    s = conn.execute(f"SELECT * FROM summaries WHERE project_id=? AND topic IN ({','.join('?' * len(keys))}) "
                     "ORDER BY created DESC LIMIT 1", (pid, *keys)).fetchone()
    if s:
        out["summary"] = {"title": s["title"], "text": s["text"], "origin": s["origin"], "created": s["created"],
                          "newer_notes": sum(1 for t in tl if t["note"]["created"] > s["created"])}
    return out


def summary_status(conn, pid: int, min_notes: int = 2) -> dict:
    """For a review session: which summaries are out of date, and which busy topics have none."""
    have, keys = [], set()
    for r in conn.execute("SELECT * FROM summaries WHERE project_id=? ORDER BY title", (pid,)):
        keys.add(r["topic"])
        b = brief(conn, pid, r["title"])
        have.append({"topic": r["title"], "written": _date(r["created"]), "notes": b["stats"]["notes"],
                     "newer_notes": b["summary"]["newer_notes"] if b["summary"] else None})
    names = defaultdict(set)        # a summary filed under another name (ETN for Edge Twin Node) counts too
    for nid, alias in conn.execute("SELECT a.node_id, a.alias FROM aliases a JOIN nodes n ON n.id=a.node_id "
                                   "WHERE n.project_id=?", (pid,)):
        names[nid].add(tp.norm(alias))
    missing = [{"code": r["code"], "title": r["title"], "notes": r["c"]} for r in conn.execute(
        "SELECT n.id, n.code, n.title, COUNT(DISTINCT m.note_id) AS c FROM nodes n JOIN mentions m ON m.node_id=n.id "
        "WHERE n.project_id=? AND n.kind IN ('concept','method') GROUP BY n.id HAVING c>=? ORDER BY c DESC, n.title",
        (pid, min_notes)) if not ({tp.norm(r["title"])} | names[r["id"]]) & keys]
    gloss = [{"abbr": g["abbr"], "expansion": g["expansion"], "notes": g["notes"],
              "needs": "refresh" if g["summary_stale"] else "new"}
             for g in E.glossary(conn, pid) if g["notes"] and (g["summary_stale"] or not g["summary"])]
    return {"summaries": have, "busy_topics_without_summary": missing[:40], "glossary_terms_to_summarise": gloss}


def _date(ts) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else ""


def _plain(segs) -> str:
    return "".join(f"**{s['t']}**" if s["m"] else s["t"] for s in segs)


def to_markdown(b: dict, project: dict) -> str:
    """The brief as a readable Markdown document (also what Claude reads before writing a summary)."""
    t, g = b["topic"], b.get("glossary")
    title = f"{g['abbr']}: {g['expansion']}" if g else t["title"] if t else b["query"]
    st = b["stats"]
    L = [f"# {title}", "",
         f"_Topic brief from @{project['handle']}, {_date(time.time())}. {st['notes']} notes"
         + (f" ({_date(st['first'])} to {_date(st['last'])})" if st["first"] else "")
         + f", {st['items']} items. Quoted text is the user's own words._", ""]
    if b["corrected"]:
        L += [f"Showing results for “{b['corrected']}”.", ""]
    if g:
        where = {"list": "the proposal's list of abbreviations", "text": "the proposal text",
                 "standard": "standard meaning; not spelled out in the proposal", "partner": "consortium partner"}[g["kind"]]
        L += [f"**Glossary:** {g['abbr']} = {g['expansion']} ({where}" + (f", p.{g['page']}" if g["page"] else "") + ")"
              + (f". {g['context']}" if g["context"] else ""), ""]
    if t:
        L += [f"**{t['code']} · {t['kind']}**" + (f" · also: {', '.join(t['aliases'])}" if t["aliases"] else ""), ""]
    if b["summary"]:
        s = b["summary"]
        L += [f"## Summary (Claude's interpretation, {_date(s['created'])}"
              + (f"; {s['newer_notes']} newer notes since" if s["newer_notes"] else "") + ")", "", s["text"], ""]
    if b["definitions"]:
        L += ["## Definitions", ""] + [f"- “{d['text']}” ({d['note_code'] or d['origin']}, {_date(d['created'])})"
                                       for d in b["definitions"]] + [""]
    if b["groups"]:
        L += ["## What was said", ""]
        for g in b["groups"]:
            L += [f"### {g['label']}", ""]
            for i in g["items"]:
                rel = "; ".join(f"{r['type']} {'→ topic' if r['dir'] == 'out' else '← topic'}"
                                + (" (suggested)" if r["status"] == "suggested" else "") for r in i["relations"])
                text = i["body"] if i["body"] and i["body"] != i["title"] else i["title"]
                L.append(f"- [{i['code']} {i['kind']}{', ' + i['status'] if i['status'] == 'resolved' else ''}"
                         f"{', uncertain' if i['confidence'] == 'low' else ''}] {text} ({_date(i['first_seen'])}"
                         + (f"; {rel}" if rel else "") + ")")
            L.append("")
    if b["conflicts"]:
        L += ["## Unresolved conflicts", ""] + [f"- “{c['a']['title']}” contradicts “{c['b']['title']}”"
                                                for c in b["conflicts"]] + [""]
    if b["topics"]:
        L += ["## Connected topics", ""]
        for x in b["topics"]:
            why = [f"{r['type']} {'→' if r['dir'] == 'out' else '←'} topic" for r in x["relations"]]
            if x["named"]:
                why.append("name overlaps")
            if x["shared"]:
                why.append(f"{x['shared']} shared statements")
            L.append(f"- {x['code']} {x['title']} ({', '.join(why)})")
        L.append("")
    if b["sources"]:
        L += ["## Sources", ""] + [f"- {s['title']}" + (f" ({s['body']})" if s["body"] and s["body"] != s["title"] else "")
                                   for s in b["sources"]] + [""]
    if b["timeline"]:
        L += ["## In your notes, oldest first", ""]
        for e in b["timeline"]:
            n = e["note"]
            L.append(f"### {n['code']} · {_date(n['created'])}" + (f" · source: {n['source']}" if n["source"] else ""))
            for p in e["passages"]:
                L += ["> " + x if x else ">" for x in _plain(p).split("\n")] + [""]
            if e["more"]:
                L.append(f"> … and {e['more']} more sentences in this note")
            L.append("")
    return "\n".join(L)
