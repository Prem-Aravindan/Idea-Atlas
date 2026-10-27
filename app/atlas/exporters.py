"""Portable exports (Markdown, JSON, CSV, self-contained HTML) and JSON import."""
from __future__ import annotations

import csv
import io
import json
import time
from datetime import datetime

from . import engine as E
from .config import static_dir
from .db import now

KIND_ORDER = ["concept", "method", "explanation", "idea", "claim", "assumption", "evidence", "solution",
              "decision", "risk", "question", "task", "reference"]
KIND_TITLE = {"concept": "Concepts", "method": "Methods", "explanation": "Explanations", "idea": "Ideas",
              "claim": "Claims", "assumption": "Assumptions", "evidence": "Evidence", "solution": "Solutions",
              "decision": "Decisions", "risk": "Risks", "question": "Questions", "task": "Tasks",
              "reference": "References"}


def _date(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""


def _slug(s):
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in s).strip("-")


def filename(conn, pid, ext):
    p = E.get_project(conn, pid)
    return f"atlas-{_slug(p['handle'])}-{time.strftime('%Y%m%d-%H%M')}.{ext}"


def to_json(conn, pid) -> str:
    return json.dumps(E.export_project(conn, pid), indent=2, ensure_ascii=False)


def to_csv_nodes(conn, pid) -> str:
    data = E.export_project(conn, pid)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "kind", "title", "definition", "body", "status", "confidence", "origin", "aliases", "tags",
                "sources", "updated"])
    for n in data["nodes"]:
        w.writerow([n["code"], n["kind"], n["title"], n["definition"], n["body"], n["status"], n["confidence"],
                    n["origin"], "; ".join(n["aliases"]), "; ".join(n["tags"]),
                    "; ".join(sorted({s["note"] for s in n["sources"]})), _date(n["updated"])])
    return buf.getvalue()


def to_csv_edges(conn, pid) -> str:
    data = E.export_project(conn, pid)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["source_id", "source", "relationship", "target_id", "target", "status", "confidence", "origin", "evidence"])
    for e in data["edges"]:
        w.writerow([e["src_code"], e["src_title"], e["type"], e["dst_code"], e["dst_title"], e["status"],
                    e["confidence"], e["origin"], e["evidence"]])
    return buf.getvalue()


def to_markdown(conn, pid) -> str:
    data = E.export_project(conn, pid)
    p = data["project"]
    nodes = data["nodes"]
    by_id = {n["id"]: n for n in nodes}
    out_e, in_e = {}, {}
    for e in data["edges"]:
        out_e.setdefault(e["src"], []).append(e)
        in_e.setdefault(e["dst"], []).append(e)
    g = E.graph(conn, pid, limit=60, kinds=set(E.NODE_KINDS) - {"reference"})
    mm = E.mermaid(g["nodes"], g["edges"], clickable=False).replace("<b>", "").replace("</b>", "")
    q = data["quests"]
    L = [f"# Idea Atlas: {p['name']} (@{p['handle']})", ""]
    if p["description"]:
        L += [p["description"], ""]
    counts = {}
    for n in nodes:
        counts[n["kind"]] = counts.get(n["kind"], 0) + 1
    L += [f"*Exported {_date(now())} · {len(nodes)} nodes · {len(data['edges'])} links · {len(data['notes'])} notes · "
          f"{q['open']} open quests*", "",
          " · ".join(f"{KIND_TITLE[k]}: {counts[k]}" for k in KIND_ORDER if k in counts), "",
          "## Visual Map", ""]
    if g["truncated"]:
        L += [f"_Showing the {len(g['nodes'])} most connected of {g['total']} nodes._", ""]
    L += ["```mermaid", mm, "```", "", "## Concept Cards", ""]
    for kind in KIND_ORDER:
        group = [n for n in nodes if n["kind"] == kind]
        if not group:
            continue
        L += [f"### {KIND_TITLE[kind]}", ""]
        for n in group:
            L.append(f"#### {n['code']} · {n['title']}")
            meta = [n["kind"], f"confidence: {n['confidence']}", f"status: {n['status']}", f"origin: {n['origin']}"]
            L.append("*" + " · ".join(meta) + "*")
            L.append("")
            if n["definition"]:
                L += [f"**Definition:** {n['definition']}", ""]
            if len(n["definitions"]) > 1:
                L.append("**All definitions (review):**")
                L += [f"- {d['text']} _({d['note'] or d['origin']})_" for d in n["definitions"]]
                L.append("")
            if n["body"] and n["body"] != n["title"]:
                L += [f"> {n['body']}", ""]
            if len(n["aliases"]) > 1:
                L += ["**Aliases:** " + ", ".join(a for a in n["aliases"] if a != n["title"]), ""]
            links = []
            for e in out_e.get(n["id"], []):
                links.append(f"- → *{e['type']}* → {e['dst_code']} {e['dst_title']}" + _flag(e))
            for e in in_e.get(n["id"], []):
                links.append(f"- ← *{e['type']}* ← {e['src_code']} {e['src_title']}" + _flag(e))
            if links:
                L += ["**Connections:**"] + links + [""]
            src = sorted({s["note"] for s in n["sources"]})
            if src:
                L += ["**Sources:** " + ", ".join(src), ""]
            if n["tags"]:
                L += ["**Tags:** " + " ".join("#" + t for t in n["tags"]), ""]
    L += ["## Quest Board", "", f"Level {q['level']} {q['rank']} · {q['xp']} XP · {q['done']} quests completed", ""]
    for x in q["quests"]:
        L.append(f"- [ ] **{x['title']}** ({x['xp']} XP): {x['detail']}")
    L += ["", "## Search Index", "", "| ID | Name | Kind | Aliases | Tags |", "|---|---|---|---|---|"]
    for n in sorted(nodes, key=lambda n: n["title"].lower()):
        al = ", ".join(a for a in n["aliases"] if a != n["title"]).replace("|", "/")
        L.append(f"| {n['code']} | {n['title'].replace('|', '/')} | {n['kind']} | {al} | {' '.join(n['tags'])} |")
    L += ["", "## Original Notes", ""]
    for o in data["notes"]:
        L += [f"### {o['code']} · {_date(o['created'])}", ""]
        L += ["> " + line for line in o["text"].splitlines()] + [""]
    return "\n".join(L)


def _flag(e):
    bits = []
    if e["status"] == "suggested":
        bits.append("Suggested Link")
    if e["status"] == "resolved":
        bits.append("resolved")
    if e["confidence"] == "low":
        bits.append("hypothesis")
    if e["origin"] != "user" and e["origin"] != "parser":
        bits.append(e["origin"])
    return f" _({', '.join(bits)})_" if bits else ""


def to_html(conn, pid) -> str:
    data = E.export_project(conn, pid)
    data["exported"] = _date(now())
    mermaid_js = (static_dir() / "vendor" / "mermaid.min.js").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title = f"Idea Atlas: {data['project']['name']}"
    return (HTML_TEMPLATE.replace("__TITLE__", title.replace("<", "&lt;"))
            .replace("__DATA__", payload)
            .replace("__MERMAID__", mermaid_js.replace("</script", "<\\/script")))


EXPORTERS = {
    "md": (to_markdown, "md", "text/markdown; charset=utf-8"),
    "json": (to_json, "json", "application/json; charset=utf-8"),
    "csv-nodes": (to_csv_nodes, "nodes.csv", "text/csv; charset=utf-8"),
    "csv-edges": (to_csv_edges, "links.csv", "text/csv; charset=utf-8"),
    "html": (to_html, "html", "text/html; charset=utf-8"),
}


def backup_all(conn) -> dict:
    return {"format": "idea-atlas-backup/1", "created": now(),
            "projects": [E.export_project(conn, p["id"]) for p in E.list_projects(conn)]}


def import_project(conn, data: dict) -> dict:
    """Recreate a project from an export_project() JSON. Keeps concept IDs."""
    if data.get("format") == "idea-atlas-backup/1":
        return {"imported": [import_project(conn, p)["handle"] for p in data["projects"]]}
    if data.get("format") != "idea-atlas/1":
        raise E.AtlasError("not an Idea Atlas JSON export")
    src = data["project"]
    handle, n = src["handle"], 2
    while E.project_by_handle(conn, handle):
        handle, n = f"{src['handle']}-{n}", n + 1
    p = E.create_project(conn, src["name"] if handle == src["handle"] else f"{src['name']} ({n - 1})",
                         handle=handle, description=src.get("description", ""), color=src.get("color", ""))
    pid = p["id"]
    note_ids = {}
    for o in data["notes"]:
        note_ids[o["code"]] = conn.execute(
            "INSERT INTO notes(project_id, code, text, source, created, ai_reviewed) VALUES(?,?,?,?,?,?)",
            (pid, o["code"], o["text"], o.get("source", ""), o.get("created") or now(), o.get("ai_reviewed", 0))).lastrowid
    ids, maxn = {}, {}
    for d in data["nodes"]:
        cur = conn.execute(
            "INSERT INTO nodes(project_id, code, kind, title, body, definition, status, confidence, origin, edited, created, updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,1,?,?)",
            (pid, d["code"], d["kind"], d["title"], d.get("body", ""), d.get("definition", ""), d.get("status", "active"),
             d.get("confidence", "medium"), d.get("origin", "parser"), d.get("created") or now(), d.get("updated") or now()))
        ids[d["id"]] = cur.lastrowid
        pre, num = d["code"].split("-")
        maxn[pre] = max(maxn.get(pre, 0), int(num))
        for a in d.get("aliases", []):
            E.add_alias(conn, cur.lastrowid, a)
        for t in d.get("tags", []):
            conn.execute("INSERT OR IGNORE INTO tags(node_id, tag) VALUES(?,?)", (cur.lastrowid, t))
        for df in d.get("definitions", []):
            conn.execute("INSERT INTO definitions(node_id, text, note_id, origin, created) VALUES(?,?,?,?,?)",
                         (cur.lastrowid, df["text"], note_ids.get(df.get("note")), df.get("origin", "parser"), now()))
        for s in d.get("sources", []):
            if s["note"] in note_ids:
                E.add_mention(conn, cur.lastrowid, note_ids[s["note"]], s.get("excerpt", ""), s.get("role", "mention"))
    for o in data["notes"]:
        pre, num = o["code"].split("-")
        maxn[pre] = max(maxn.get(pre, 0), int(num))
    for pre, n in maxn.items():
        conn.execute("INSERT OR REPLACE INTO counters(project_id, prefix, n) VALUES(?,?,?)", (pid, pre, n))
    for e in data["edges"]:
        if e["src"] in ids and e["dst"] in ids:
            E.add_edge(conn, pid, ids[e["src"]], ids[e["dst"]], e["type"], status=e.get("status", "active"),
                       confidence=e.get("confidence", "medium"), origin=e.get("origin", "parser"),
                       evidence=e.get("evidence", ""))
    E.set_glossary(conn, pid, data.get("glossary", []))
    reqs = data.get("requirements", [])
    E.set_requirements(conn, pid, [dict(r, concept=r.get("node_code")) for r in reqs],
                       resolve=lambda code: (E.node_by_code(conn, pid, code) or {}).get("id"))
    for r in reqs:      # the tracking state comes back as it was, without a fresh history entry
        conn.execute("UPDATE requirements SET status=?, progress=?, owner=?, due=?, note=?, updated=? WHERE project_id=? AND rid=?",
                     (r.get("status", "not started"), r.get("progress", 0), r.get("owner", ""), r.get("due", ""),
                      r.get("note", ""), r.get("updated"), pid, r["rid"]))
    for l in data.get("requirement_log", []):
        conn.execute("INSERT INTO requirement_log(project_id, rid, field, old, new, at) VALUES(?,?,?,?,?,?)",
                     (pid, l["rid"], l["field"], l["old"], l["new"], l["at"]))
    for s in data.get("summaries", []):
        E.set_summary(conn, pid, s["title"], s["text"], origin=s.get("origin", "ai"))
        conn.execute("UPDATE summaries SET created=? WHERE project_id=? AND title=?", (s.get("created") or now(), pid, s["title"]))
    return E.get_project(conn, pid)


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#f7f7fb;--panel:#fff;--ink:#1d1d2c;--mute:#6b6b80;--line:#e3e3ee;--acc:#5b7cfa;--warn:#e5484d}
@media (prefers-color-scheme:dark){:root{--bg:#14141c;--panel:#1d1d28;--ink:#ececf4;--mute:#9a9ab0;--line:#2e2e3e;--acc:#8ea2ff;--warn:#ff6b70}}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,Segoe UI,sans-serif;background:var(--bg);color:var(--ink)}
header{padding:14px 18px;border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:center;flex-wrap:wrap}
h1{font-size:17px;margin:0}.mute{color:var(--mute)}main{display:grid;grid-template-columns:320px 1fr;min-height:calc(100vh - 60px)}
aside{border-right:1px solid var(--line);padding:12px;overflow:auto;max-height:calc(100vh - 60px)}
input,select{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--ink);font:inherit}
.item{padding:7px 9px;border-radius:8px;cursor:pointer;display:flex;gap:8px}.item:hover,.item.on{background:color-mix(in srgb,var(--acc) 12%,transparent)}
.code{font:600 11px ui-monospace,Consolas,monospace;color:var(--acc);white-space:nowrap;padding-top:2px}
section{padding:16px 22px;overflow:auto;max-height:calc(100vh - 60px)}.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:14px}
.tabs button{border:1px solid var(--line);background:var(--panel);color:var(--ink);padding:6px 12px;border-radius:8px;cursor:pointer;font:inherit}
.tabs button.on{background:var(--acc);color:#fff;border-color:var(--acc)}
blockquote{margin:6px 0;padding:6px 12px;border-left:3px solid var(--acc);background:color-mix(in srgb,var(--acc) 6%,transparent);border-radius:0 8px 8px 0}
a.n{color:var(--acc);cursor:pointer;text-decoration:none}.pill{font-size:11px;padding:1px 7px;border-radius:99px;border:1px solid var(--line);color:var(--mute)}
.mm{background:#fff;border-radius:12px;padding:10px;overflow:auto}ul{padding-left:20px}
@media (max-width:760px){main{grid-template-columns:1fr}aside{max-height:40vh;border-right:0;border-bottom:1px solid var(--line)}}
</style></head><body>
<header><h1 id="t"></h1><span class="mute" id="meta"></span><span style="flex:1"></span>
<div class="tabs"><button data-v="card" class="on">Cards</button> <button data-v="map">Map</button> <button data-v="quests">Quests</button> <button data-v="notes">Notes</button></div></header>
<main><aside><input id="q" placeholder="Search names, aliases, IDs, tags…"><div style="height:8px"></div>
<select id="k"><option value="">All kinds</option></select><div style="height:8px"></div><div id="list"></div></aside>
<section id="view"></section></main>
<script>__MERMAID__</script>
<script>
const D=__DATA__;const N=new Map(D.nodes.map(n=>[n.id,n]));let cur=D.nodes[0]&&D.nodes[0].id,view='card';
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
document.getElementById('t').textContent='Idea Atlas · '+D.project.name;
document.getElementById('meta').textContent=`@${D.project.handle} · ${D.nodes.length} nodes · ${D.edges.length} links · exported ${D.exported}`;
const kinds=[...new Set(D.nodes.map(n=>n.kind))].sort();const ks=document.getElementById('k');kinds.forEach(k=>ks.insertAdjacentHTML('beforeend',`<option>${k}</option>`));
function list(){const q=document.getElementById('q').value.toLowerCase().trim(),k=ks.value;
 const r=D.nodes.filter(n=>(!k||n.kind===k)&&(!q||n.code.toLowerCase()===q||(n.title+' '+n.aliases.join(' ')+' '+n.tags.join(' ')+' '+n.definition).toLowerCase().includes(q)));
 document.getElementById('list').innerHTML=r.map(n=>`<div class="item ${n.id===cur?'on':''}" data-id="${n.id}"><span class="code">${n.code}</span><span>${esc(n.title)}</span></div>`).join('')||'<p class="mute">No matches</p>';}
document.getElementById('list').onclick=e=>{const it=e.target.closest('.item');if(it){cur=+it.dataset.id;show('card')}};
document.getElementById('q').oninput=list;ks.onchange=list;
document.querySelector('.tabs').onclick=e=>{if(e.target.dataset.v)show(e.target.dataset.v)};
window.atlasPick=id=>{cur=id;show('card')};
document.getElementById('view').onclick=e=>{const a=e.target.closest('a.n');if(a){cur=+a.dataset.id;show('card')}};
const lnk=(id,code,title)=>`<a class="n" data-id="${id}">${code}</a> ${esc(title)}`;
function card(){const n=N.get(cur);if(!n)return'<p class="mute">Empty atlas.</p>';
 const out=D.edges.filter(e=>e.src===n.id),inn=D.edges.filter(e=>e.dst===n.id);
 const fl=e=>(e.status==='suggested'?' <span class="pill">Suggested Link</span>':'')+(e.confidence==='low'?' <span class="pill">hypothesis</span>':'');
 return `<div class="card"><div class="code">${n.code} · ${n.kind}</div><h2 style="margin:4px 0 6px">${esc(n.title)}</h2>
 <div class="mute">confidence ${n.confidence} · status ${n.status} · origin ${n.origin}</div>
 ${n.kind==='reference'&&/^([A-Za-z]:[\\/]|\\\\|https?:)/.test(n.body)?`<p><a href="${esc(/^https?:/.test(n.body)?n.body:'file:///'+n.body.replace(/\\/g,'/'))}">Open ${esc(n.body)}</a></p>`:''}
 ${n.definition?`<p><b>Definition.</b> ${esc(n.definition)}</p>`:''}
 ${n.aliases.length>1?`<p><b>Aliases.</b> ${n.aliases.map(esc).join(', ')}</p>`:''}
 ${n.tags.length?`<p>${n.tags.map(t=>'<span class="pill">#'+esc(t)+'</span>').join(' ')}</p>`:''}</div>
 <div class="card"><b>Connections</b><ul>${out.map(e=>`<li>→ <i>${e.type}</i> → ${lnk(e.dst,e.dst_code,e.dst_title)}${fl(e)}</li>`).join('')}
 ${inn.map(e=>`<li>← <i>${e.type}</i> ← ${lnk(e.src,e.src_code,e.src_title)}${fl(e)}</li>`).join('')}</ul>${!out.length&&!inn.length?'<p class="mute">No links yet.</p>':''}</div>
 <div class="card"><b>Your words</b>${n.sources.map(s=>`<blockquote>${esc(s.excerpt)}<div class="mute">${s.note}</div></blockquote>`).join('')||'<p class="mute">Added by edit/import.</p>'}</div>
 <div class="card"><b>Neighbourhood</b><div class="mm" id="mm"></div></div>`;}
function mm(nodes,edges){const S={concept:['("','")'],method:['[/"','"/]'],idea:['>"','"]'],solution:['(["','"])'],question:['{{"','"}}'],evidence:['[("','")]'],decision:['[["','"]]']};
 const C={concept:'#e8eeff,stroke:#5b7cfa',method:'#e6f6f4,stroke:#12a594',idea:'#fff4d6,stroke:#d9a300',solution:'#e3f7e6,stroke:#46a758',question:'#fde8f3,stroke:#d6409f',evidence:'#e6f4ff,stroke:#0090ff',decision:'#efe6fb,stroke:#8e4ec6',assumption:'#f5f0e6,stroke:#a07a3c',risk:'#ffe9e9,stroke:#e5484d',explanation:'#eef3f3,stroke:#6b8e8e',reference:'#f1f1f1,stroke:#8b8b8b',task:'#fff0e5,stroke:#f76b15',claim:'#f4f4fb,stroke:#7b7bab'};
 const lab=n=>(n.code+': '+n.title).slice(0,70).replace(/["<>\[\]{}|#;`]/g,' ');
 let s='flowchart LR\n'+Object.entries(C).map(([k,v])=>`classDef ${k} fill:${v},color:#222`).join('\n')+'\n';
 nodes.forEach(n=>{const [o,c]=S[n.kind]||['["','"]'];s+=`n${n.id}${o}${lab(n)}${c}:::${n.kind}\n`});
 const ids=new Set(nodes.map(n=>n.id));edges.filter(e=>ids.has(e.src)&&ids.has(e.dst)).forEach(e=>{s+=`n${e.src} ${e.status==='suggested'?'-.->':e.type==='contradicts'?'==>':'-->'}|${e.type}| n${e.dst}\n`});
 nodes.forEach(n=>{s+=`click n${n.id} call atlasPick(${n.id})\n`});return s;}
async function draw(el,src){try{const {svg,bindFunctions}=await mermaid.render('g'+Date.now(),src);el.innerHTML=svg;bindFunctions&&bindFunctions(el)}catch(e){el.textContent='Map could not be drawn: '+e.message}}
function neigh(id,limit){const keep=new Set([id]);D.edges.forEach(e=>{if(e.src===id)keep.add(e.dst);if(e.dst===id)keep.add(e.src)});return [...keep].slice(0,limit).map(i=>N.get(i)).filter(Boolean)}
function show(v){view=v;document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('on',b.dataset.v===v));const el=document.getElementById('view');list();
 if(v==='card'){el.innerHTML=card();const n=N.get(cur);if(n)draw(document.getElementById('mm'),mm(neigh(n.id,30),D.edges))}
 if(v==='map'){const deg=new Map();D.edges.forEach(e=>{deg.set(e.src,(deg.get(e.src)||0)+1);deg.set(e.dst,(deg.get(e.dst)||0)+1)});
  const top=D.nodes.filter(n=>n.kind!=='reference').sort((a,b)=>(deg.get(b.id)||0)-(deg.get(a.id)||0)).slice(0,70);
  el.innerHTML=`<p class="mute">${top.length<D.nodes.length?`Showing the ${top.length} most connected nodes. `:''}Click a node to open its card.</p><div class="mm" id="mm"></div>`;draw(document.getElementById('mm'),mm(top,D.edges))}
 if(v==='quests'){const q=D.quests;el.innerHTML=`<div class="card"><b>Level ${q.level} ${q.rank}</b> · ${q.xp} XP · ${q.done} done · ${q.open} open</div>`+q.quests.map(x=>`<div class="card"><b>${esc(x.title)}</b> <span class="pill">${x.xp} XP</span><div>${esc(x.detail)}</div><div class="mute">${x.codes.join(' · ')}</div></div>`).join('')}
 if(v==='notes'){el.innerHTML=D.notes.slice().reverse().map(o=>`<div class="card"><div class="code">${o.code} · ${new Date(o.created*1000).toLocaleString()}</div><blockquote style="white-space:pre-wrap">${esc(o.text)}</blockquote></div>`).join('')}}
mermaid.initialize({startOnLoad:false,securityLevel:'loose',theme:'default',flowchart:{htmlLabels:true}});show('card');
</script></body></html>"""
