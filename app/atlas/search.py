"""Project search: BM25 ranking over the map, plus typo tolerance, prefix matching for live typing,
synonyms from aliases, and related-term expansion learned from co-occurrence in the user's own notes.
No model, no network; an index is built per project and cached until the project changes."""
from __future__ import annotations

import math
import re
import threading
from collections import Counter, defaultdict

from . import textparse as tp

WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+#]*")
HYPHEN_RE = re.compile(r"\b[A-Za-z]+(?:-[A-Za-z]+)+\b")
FIELD_WEIGHT = {"title": 3.0, "alias": 2.5, "tag": 2.0, "definition": 2.0, "body": 1.5, "excerpt": 1.0,
                "glossary": 1.0, "summary": 0.8}
STOP = tp.STOP | {"also", "like"}
_cache: dict[int, tuple] = {}
_lock = threading.Lock()


def stem(w: str) -> str:
    """Light suffix stripping; it only has to be consistent between query and documents."""
    w = w.lower()
    if len(w) <= 3 or w.isupper() or any(c.isdigit() for c in w):
        return w
    for suf, rep in (("ational", "ate"), ("ization", "ize"), ("isation", "ize"), ("fulness", "ful"),
                     ("iveness", "ive"), ("ments", ""), ("ment", ""), ("ness", ""), ("ities", "ity"),
                     ("ies", "y"), ("sses", "ss"), ("ings", ""), ("ing", ""), ("edly", ""), ("ed", ""),
                     ("ly", ""), ("es", ""), ("s", "")):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: len(w) - len(suf)] + rep
            if suf in ("ing", "ings", "ed") and len(w) > 3 and w[-1] == w[-2] and w[-1] not in "lsz":
                w = w[:-1]   # running -> run
            break
    return w


def terms(text: str) -> list[str]:
    return [stem(t) for t in WORD_RE.findall(text or "") if t.lower() not in STOP]


def _tokens(text: str):
    """(surface, stem) pairs; "over-smoothing" is also indexed as "oversmoothing"."""
    for raw in WORD_RE.findall(text or "") + HYPHEN_RE.findall(text or ""):
        if raw.lower() not in STOP:
            yield raw.lower(), stem(raw.replace("-", ""))


def _trigrams(w):
    w = f"  {w} "
    return {w[i:i + 3] for i in range(len(w) - 2)}


def _edit_distance(a, b, cap=3):
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


class Index:
    def __init__(self, conn, pid):
        self.docs = {}        # node id -> {"tf": Counter, "len": float, "texts": [(field, text)]}
        self.nodes = {}
        self.df = Counter()
        self.sent_terms = []  # term sets of sentences, for co-occurrence
        self.surface = defaultdict(Counter)   # stem -> surface forms (for "did you mean")
        rows = conn.execute("SELECT * FROM nodes WHERE project_id=?", (pid,)).fetchall()
        extra = defaultdict(list)
        for r in conn.execute("SELECT a.node_id, a.alias FROM aliases a JOIN nodes n ON n.id=a.node_id WHERE n.project_id=?", (pid,)):
            extra[r[0]].append(("alias", r[1]))
        for r in conn.execute("SELECT t.node_id, t.tag FROM tags t JOIN nodes n ON n.id=t.node_id WHERE n.project_id=?", (pid,)):
            extra[r[0]].append(("tag", r[1]))
        for r in conn.execute("SELECT DISTINCT m.node_id, m.excerpt FROM mentions m JOIN nodes n ON n.id=m.node_id "
                              "WHERE n.project_id=? AND m.excerpt!=''", (pid,)):
            extra[r[0]].append(("excerpt", r[1]))
        # Claude's topic summaries and the glossary are attached to the item they describe, so a word that only
        # appears there (e.g. "Google" in R4T's summary) still finds the item. Unattached ones become text hits.
        by_name = defaultdict(set)
        for r in rows:
            by_name[tp.norm(r["title"])].add(r["id"])
        for nid, xs in list(extra.items()):
            for f, t in xs:
                if f == "alias":
                    by_name[tp.norm(t)].add(nid)
        self.texts = []       # notes, plus summaries / glossary entries that match no item
        for topic, title, text in conn.execute("SELECT topic, title, text FROM summaries WHERE project_id=?", (pid,)):
            for nid in by_name.get(topic, ()):
                extra[nid].append(("summary", text))
            if topic not in by_name:
                self.texts.append({"type": "summary", "topic": title, "label": title, "text": text, "created": None})
        for abbr, exp, ctx in conn.execute("SELECT abbr, expansion, context FROM glossary WHERE project_id=?", (pid,)):
            text = f"{abbr}: {exp}" + (f". {ctx}" if ctx else "")
            ids = by_name.get(tp.norm(abbr), set()) | by_name.get(tp.norm(exp), set())
            for nid in ids:
                extra[nid].append(("glossary", text))
            if not ids:
                self.texts.append({"type": "glossary", "topic": abbr, "label": f"{abbr} = {exp}", "text": text, "created": None})
        for nid_, code, created, text in conn.execute("SELECT id, code, created, text FROM notes WHERE project_id=? "
                                                      "ORDER BY created DESC", (pid,)):
            self.texts.append({"type": "note", "id": nid_, "label": code, "text": text, "created": created})
        for t in self.texts:
            t["terms"] = {s for _, s in _tokens(t["text"])}
        seen_sent = set()
        for r in rows:
            texts = [("title", r["title"]), ("definition", r["definition"])]
            if r["body"] and r["body"] != r["title"]:
                texts.append(("body", r["body"]))
            texts += [x for x in extra[r["id"]] if not (x[0] == "alias" and x[1].lower() == r["title"].lower())]
            tf = Counter()
            for field, text in texts:
                for raw, s in _tokens(text):
                    tf[s] += FIELD_WEIGHT[field]
                    self.surface[s][raw] += 1
                if field in ("body", "excerpt", "definition") and text and text not in seen_sent:
                    seen_sent.add(text)
                    self.sent_terms.append(set(terms(text)))
            self.docs[r["id"]] = {"tf": tf, "len": sum(tf.values()) or 1.0, "texts": [t for t in texts if t[1]]}
            self.nodes[r["id"]] = dict(r)
            for t in tf:
                self.df[t] += 1
        self.N = len(self.docs) or 1
        self.avglen = sum(d["len"] for d in self.docs.values()) / self.N if self.docs else 1.0
        self.tdf = Counter(s for t in self.texts for s in t["terms"])
        for t in self.texts:
            for raw, s in _tokens(t["text"]):
                if s not in self.df:
                    self.surface[s][raw] += 1
        self.vocab = set(self.df) | set(self.tdf)   # words only in notes are searchable too
        self.cooc = None

    def idf(self, t):
        n = self.df.get(t, 0)
        return math.log(1 + (self.N - n + 0.5) / (n + 0.5))

    def related_terms(self, q, limit=3):
        """Terms that keep appearing in the same sentences as q in this project's notes."""
        if self.cooc is None:
            self.cooc = defaultdict(Counter)
            self.sdf = Counter()
            for s in self.sent_terms:
                for a in s:
                    self.sdf[a] += 1
                    for b in s:
                        if a != b:
                            self.cooc[a][b] += 1
        out = []
        for t, c in self.cooc.get(q, {}).items():
            if c < 2 or len(t) < 3:
                continue
            score = c / math.sqrt(self.sdf[q] * self.sdf[t])
            if score >= 0.3:
                out.append((score, t))
        return [t for _, t in sorted(out, reverse=True)[:limit]]

    def fuzzy(self, q):
        if len(q) < 4:
            return []
        tq = _trigrams(q)
        cands = []
        for v in self.vocab:
            if abs(len(v) - len(q)) > 3:
                continue
            sim = len(tq & _trigrams(v)) / len(tq | _trigrams(v))
            if sim >= 0.35:
                d = _edit_distance(q, v)
                if d <= (1 if len(q) <= 6 else 2):
                    cands.append((d, -sim, v))
        return [v for _, _, v in sorted(cands)[:2]]


def _index(conn, pid) -> Index:
    key = conn.execute("""SELECT (SELECT COUNT(*) FROM nodes WHERE project_id=?), (SELECT MAX(updated) FROM nodes WHERE project_id=?),
                          (SELECT COUNT(*) FROM mentions m JOIN nodes n ON n.id=m.node_id WHERE n.project_id=?),
                          (SELECT COUNT(*) FROM aliases a JOIN nodes n ON n.id=a.node_id WHERE n.project_id=?),
                          (SELECT COUNT(*) FROM tags t JOIN nodes n ON n.id=t.node_id WHERE n.project_id=?),
                          (SELECT COUNT(*) || ':' || COALESCE(SUM(LENGTH(text)), 0) FROM notes WHERE project_id=?),
                          (SELECT COUNT(*) || ':' || COALESCE(MAX(created), 0) FROM summaries WHERE project_id=?),
                          (SELECT COUNT(*) || ':' || COALESCE(SUM(LENGTH(expansion) + LENGTH(context)), 0)
                           FROM glossary WHERE project_id=?)""",
                       (pid,) * 8).fetchone()
    key = tuple(key)
    with _lock:
        hit = _cache.get(pid)
        if hit and hit[0] == key:
            return hit[1]
    idx = Index(conn, pid)
    with _lock:
        _cache[pid] = (key, idx)
    return idx


def _snippet(texts, want: dict, phrase: str, maxlen=220):
    """Pick the passage with the most matches; return segments [{t, m}] where m=1 exact, 2 related."""
    best, best_score = None, -1
    for field, text in texts:
        if field in ("alias", "tag"):
            continue
        sc = sum(2 if want.get(stem(w)) == 1 else 1 for w in WORD_RE.findall(text) if stem(w) in want)
        if phrase and phrase in text.lower():
            sc += 5
        sc += {"definition": .5, "body": .4, "excerpt": .3}.get(field, 0)
        if sc > best_score:
            best, best_score = text, sc
    if not best:
        return []
    text = best
    if len(text) > maxlen:   # centre the window on the first match
        first = min((m.start() for m in WORD_RE.finditer(text) if stem(m.group(0)) in want), default=0)
        start = max(0, first - 60)
        text = ("…" if start else "") + text[start:start + maxlen] + ("…" if start + maxlen < len(best) else "")
    segs, pos = [], 0
    for m in WORD_RE.finditer(text):
        k = want.get(stem(m.group(0)))
        if k and m.group(0).lower() not in STOP:
            if m.start() > pos:
                segs.append({"t": text[pos:m.start()], "m": 0})
            segs.append({"t": m.group(0), "m": k})
            pos = m.end()
    segs.append({"t": text[pos:], "m": 0})
    return segs


def search(conn, pid: int, q: str, limit: int = 40, kind: str | None = None) -> dict:
    q = (q or "").strip()
    res = {"query": q, "results": [], "connected": [], "did_you_mean": None, "expanded": [], "glossary": None, "texts": []}
    if not q:
        return res
    idx = _index(conn, pid)
    code = re.fullmatch(r"([A-Za-z])-?(\d{1,4})", q)
    qterms = list(dict.fromkeys(terms(q)))
    want: dict[str, int] = {}          # stem -> 1 (exact) / 2 (related)
    weights: dict[str, float] = {}
    notes = []
    for i, t in enumerate(qterms):
        if t in idx.vocab:
            want[t], weights[t] = 1, 1.0
        else:
            split = next(((t[:i], t[i:]) for i in range(3, len(t) - 2)
                          if t[:i] in idx.vocab and stem(t[i:]) in idx.vocab), None)
            if split:                                   # "dataaugmentation" -> data + augmentation
                for part in (split[0], stem(split[1])):
                    want[part], weights[part] = 1, 0.8
                continue
            fz = idx.fuzzy(t)
            for f in fz:
                want[f], weights[f] = 1, 0.8
            if fz:
                notes.append((t, fz[0]))
        raw_last = WORD_RE.findall(q)[-1].lower()
        if i == len(qterms) - 1 and len(raw_last) >= 3:    # live typing: last word may be incomplete
            for v in idx.vocab:
                if v.startswith(raw_last) and v not in weights:
                    want[v], weights[v] = 1, 0.6
    typed = set(want)                  # from the query itself (exact, split, fuzzy, prefix), before expansion
    g = conn.execute("SELECT abbr, expansion FROM glossary WHERE project_id=? AND (lower(abbr)=lower(?) OR "
                     "lower(expansion)=lower(?)) LIMIT 1", (pid, q, q)).fetchone()
    if g:   # a glossary term also finds its other form: ETN <-> Edge Twin Node
        res["glossary"] = {"abbr": g[0], "expansion": g[1]}
        for t in terms(g[0] + " " + g[1]):
            if t in idx.vocab and t not in weights:
                want[t], weights[t] = 1, 0.9
    for t in [t for t in qterms if t in idx.vocab]:
        for r in idx.related_terms(t):
            if r not in weights:
                want[r], weights[r] = 2, 0.35
                res["expanded"].append(max(idx.surface[r], key=idx.surface[r].get) if idx.surface[r] else r)
    if notes:
        fix, words = dict(notes), []
        for w in WORD_RE.findall(q):
            t = stem(w)
            if w.lower() not in STOP and t in fix:
                forms = idx.surface[fix[t]]
                words.append(max(forms, key=forms.get) if forms else fix[t])
            else:
                words.append(w)
        res["did_you_mean"] = " ".join(words)
    phrase = q.lower() if len(qterms) >= 2 else ""
    k1, b = 1.2, 0.75
    scored = []
    for nid, d in idx.docs.items():
        node = idx.nodes[nid]
        if kind and node["kind"] != kind:
            continue
        s, exact_hit = 0.0, False
        for t, w in weights.items():
            tf = d["tf"].get(t)
            if not tf:
                continue
            s += w * idx.idf(t) * tf * (k1 + 1) / (tf + k1 * (1 - b + b * d["len"] / idx.avglen))
            exact_hit |= want[t] == 1
        if phrase and any(phrase in (txt or "").lower() for _, txt in d["texts"]):
            s *= 1.6
            exact_hit = True
        if node["title"].lower() == q.lower() or any(f == "alias" and txt.lower() == q.lower() for f, txt in d["texts"]):
            s += 10
        if code and node["code"].replace("-", "").lower() == (code.group(1) + code.group(2).zfill(3)).lower():
            s += 100
            exact_hit = True
        if s > 0:
            scored.append((s, nid, exact_hit))
    scored.sort(key=lambda x: -x[0])
    for s, nid, exact_hit in scored[:limit]:
        node = idx.nodes[nid]
        d = idx.docs[nid]
        res["results"].append({
            "node": {k: node[k] for k in ("id", "code", "kind", "title", "definition", "status", "confidence")},
            "score": round(s, 2), "match": "exact" if exact_hit else "related",
            "snippet": _snippet(d["texts"], want, phrase),
            "fields": sorted({f for f, txt in d["texts"] if any(stem(w) in want for w in WORD_RE.findall(txt))}),
        })
    # the full text of notes (and summaries / glossary entries that belong to no item): the typed words only;
    # a glossary term counts as its abbreviation or its whole expansion, not the expansion's single words
    nt = len(idx.texts) or 1
    own = {x: w for x, w in weights.items() if x in typed}
    forms = [re.compile(r"(?<![\w-])" + re.escape(g[0]) + r"s?(?!\w)"),
             re.compile(r"(?<!\w)" + re.escape(g[1]) + r"(?!\w)", re.I)] if g else []
    hits = []
    for i, t in enumerate(idx.texts):
        s = sum(w * math.log(1 + nt / idx.tdf[x]) for x, w in own.items() if x in t["terms"])
        mark = dict.fromkeys(own, 1)
        if any(f.search(t["text"]) for f in forms):
            s += 3
            mark.update(dict.fromkeys(terms(g[0] + " " + g[1]), 1))
        if phrase and phrase in t["text"].lower():
            s *= 1.6
        if s > 0:
            hits.append((-s, i, mark))
    for _, i, mark in sorted(hits, key=lambda h: h[:2])[:10]:
        t = idx.texts[i]
        res["texts"].append({k: t.get(k) for k in ("type", "id", "topic", "label", "created")}
                            | {"snippet": _snippet([("body", t["text"])], mark, phrase)})
    # items linked to the top hits that the words alone did not find
    top = [r["node"]["id"] for r in res["results"][:5]]
    found = {r["node"]["id"] for r in res["results"]}
    if top:
        ph = ",".join("?" * len(top))
        for e in conn.execute(f"SELECT src, dst, type FROM edges WHERE status IN ('active','resolved') AND "
                              f"(src IN ({ph}) OR dst IN ({ph}))", top + top):
            other, via = (e["dst"], e["src"]) if e["src"] in top else (e["src"], e["dst"])
            if other in found or other not in idx.nodes:
                continue
            found.add(other)
            n, v = idx.nodes[other], idx.nodes[via]
            res["connected"].append({"node": {k: n[k] for k in ("id", "code", "kind", "title")},
                                     "via": {"id": v["id"], "code": v["code"], "title": v["title"]}, "type": e["type"],
                                     "dir": "out" if e["src"] == via else "in"})
        res["connected"] = res["connected"][:12]
    return res
