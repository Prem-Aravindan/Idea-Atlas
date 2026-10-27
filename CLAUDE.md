# Idea Atlas — notes for Claude
Do not take screenshots.

Local Windows app that turns pasted notes into a per-project semantic map. Runs ONLY while its window
is open: `program\IdeaAtlas.exe` starts a loopback HTTP server (127.0.0.1:47321) and opens the UI in an Edge
`--app` window (user's normal profile); it exits ~6 s after the window closes (or after 3 min without pings).
Data: `data\atlas.db` (SQLite, WAL). Nothing leaves the machine; the parser is rule-based (no model).

Machine- and project-specific notes live in `CLAUDE.local.md` (not committed); read it when present.

Security constraint: the earlier always-on build (single-file PyInstaller exe, HKCU Run autostart, global hotkey,
synthetic Alt keypress, tray/hidden window, separate Edge profile, self-restart) was flagged by endpoint security. Do NOT reintroduce any of these. Keep: onedir build, --noupx,
no autostart, no hotkeys, no input injection, no background/tray mode, no self-update.

## Layout
- `app/atlas/textparse.py` – rule-based parser (segment kinds, mentions, definitions, aliases, relations)
- `app/atlas/engine.py` – integrates notes into the map; cards, graph/Mermaid, quests, search, compare, `apply_ops`
- `app/atlas/search.py` – per-project search: BM25 + typo/prefix/compound matching + co-occurrence term expansion
- `app/atlas/brief.py` – topic brief: everything said about one keyword (items, note passages, connections), extractive;
  shows a stored Claude summary (table `summaries`) flagged when newer notes arrive, and the glossary entry if any
- `app/atlas/server.py` – API (requires header `X-Atlas: 1`, Host must be loopback)
- `app/atlas/main.py` – start server, open window, exit when the window is gone
- `app/atlas/desktop.py` – open the Edge app window; error message box (nothing else)
- `app/atlas/exporters.py` – Markdown / JSON / CSV / self-contained HTML, import
- `app/static/` – UI (vanilla JS), vendored Mermaid
- `tools/atlas_cli.py` – CLI used for enrichment; `tests/` – unittest suite

## Rebuild after code changes
1. `.\.venv\Scripts\python.exe -m unittest discover -s tests`
2. Bump `__version__` in `app/atlas/__init__.py`
3. `powershell -ExecutionPolicy Bypass -File build.ps1` (runs tests, builds `program\`). It refuses to build while
   the app is open; ask the user to close the window. Never Stop-Process the user's app. Ask the user to start
   the exe themselves (double-click). Data is untouched by rebuilds.
   Source runs (not frozen) use port 47390 by default so they never collide with the installed app; set
   "dev_port" in a scratch config.json for tests, and always use a scratch IDEA_ATLAS_DATA, never data\.

## Enrichment workflow ("enrich / process / update my Idea Atlas")
The user may paste content here or ask for a pass over notes captured in the app.
1. `.\.venv\Scripts\python.exe tools\atlas_cli.py status` then `... pending [--project h]`
   (unreviewed notes + current nodes/links of that project).
2. Interpret each note as the user meant it. Rules:
   - Preserve the user's words; never rewrite note text. Separate user statements from your interpretation.
   - Reuse existing IDs (C-001, Q-002 …); merge obvious duplicates (keep aliases).
   - Relationship types only: depends on, explains, supports, contradicts, solves, is an example of,
     raises question, derived from, relates to.
   - Uncertain links: `"status": "suggested"` (shown as Suggested Link) or `"confidence": "low"` (hypothesis).
     Never present a generated connection as established evidence.
   - Conflicts: keep both sides, add a `contradicts` edge so a quest appears.
   - Ambiguity: record up to three interpretations as suggested links or ask the user.
   Before bulk edits, back up with SQLite's backup API (a plain copy of atlas.db can miss the WAL):
   `python -c "import sqlite3;sqlite3.connect('data/atlas.db').backup(sqlite3.connect('data/backups/<name>.db'))"`.
3. Write ops JSON into the scratchpad (a list of batches; `$ref` handles carry across batches), run `atlas_cli.py apply ops.json --dry-run`, then without `--dry-run`.
4. Finish with `{"op": "mark_reviewed", "notes": [...]}` and report: Added or Updated, Connections,
   Map View (Mermaid of the touched area), Open Quests (`atlas_cli.py quests --project h`).
5. Then refresh summaries, including glossary terms (see "Topic summaries" below).
   The running app shows changes immediately (no rebuild needed for data changes).

Content pasted in chat can be added with `atlas_cli.py capture --project h "text"` or an `add_note` op.

## Glossary (per project; Search tab → "Aa Glossary")
Abbreviations from a project document, built by Claude on request (PDF text via pypdf, which is in the venv).
- `kind`: `list` (the document's own list of abbreviations), `text` (spelled out in the body; give the page),
  `standard` (used but never spelled out; standard meaning, so the UI marks it as such), `partner` (consortium short name).
  Never mark a meaning as from the document unless the document says it; note ambiguities in `context`.
- Include the project's own numbering (work packages, tasks, deliverables…) as entries and as concept nodes
  (alias = code); use kind concept, not task (task nodes create quests). Skip country codes, all-caps headings and
  product names.
- `{"op": "set_glossary", "source": "<path>", "replace": true, "entries": [{"abbr", "expansion", "kind", "page", "context"}]}`;
  `{"op": "remove_glossary", "abbr": "X"}`. `atlas_cli.py glossary --project h` shows note counts and summary state.
- Abbreviations match case-sensitively in briefs (partner "AGE" is not the word "age"); the expansion matches as words.

## Traceability matrix (per project; "Traceability" tab appears only when a project has requirements)
Rows come from a project document (e.g. a proposal's objective and KPI tables; rid such as `SO1.2`).
- Each requirement links to a concept (tag `requirement`, alias = rid, definition = target text + page). Repeated targets
  share one concept. The row's Brief button opens the Topic brief of that concept.
- Claude owns the source fields (grp, grp_title, title, text verbatim, page, wp, challenges, context);
  the user owns status/progress/owner/due/note (edited in the UI, every change logged in `requirement_log`).
  `set_requirements` never overwrites the user's fields; `due` is only set on first insert.
- `{"op": "set_requirements", "source": "<path>", "entries": [{"rid", "grp", "grp_title", "title", "text", "page", "wp",
  "challenges", "context", "concept": "C-201" | "$ref", "due"}]}`; `{"op": "remove_requirement", "rid": "SO1.2"}`.
- Evidence = notes mentioning the concept or a statement linked to it. During enrichment, link statements that are
  clearly about a requirement (`relates to`; `suggested` when it is your interpretation). `atlas_cli.py requirements`.

## Topic summaries ("summarise my topics")
No model runs in the app; the user asks Claude here from time to time. Summaries appear on the Topic brief page.
Also run this at the end of every enrichment pass ("enrich / update my Idea Atlas"); glossary terms included.
1. `atlas_cli.py summaries --project h`: summaries with `newer_notes` > 0 are stale; `busy_topics_without_summary`
   lists concepts mentioned in 2+ notes; `glossary_terms_to_summarise` lists glossary terms used in notes whose
   summary is missing ("new") or stale ("refresh"). Confirm the list with the user if it is long.
2. For each topic: `atlas_cli.py brief "C-016" --project h` (a keyword or glossary abbreviation works too) and read
   all of it. For a glossary term, set `"topic"` to the abbreviation (e.g. "ETN"); start from what the proposal
   says (glossary line) and then what the user's notes add, keeping the two clearly apart.
3. Write 4–10 plain sentences (bullets with "- " allowed): what it means in the user's framing, how the idea developed
   (refer to notes by date, e.g. "on 5 Oct"; the UI shows dates, not note codes), how it connects to other topics,
   and what is still open or contradictory. Only what the brief supports; say when something is unclear. It is shown
   labelled as Claude's interpretation, never as the user's words.
4. `{"op": "set_summary", "topic": "C-016", "text": "…"}` via `atlas_cli.py apply` (dry run first); a new summary
   replaces the old one. Report which topics were summarised.

### Ops format
```json
{"project": "thesis", "ops": [
  {"op": "add_node", "kind": "concept", "title": "Graph rewiring", "definition": "…", "aliases": ["rewiring"], "ref": "gr"},
  {"op": "add_edge", "src": "$gr", "dst": "C-002", "type": "solves", "status": "suggested", "confidence": "low", "evidence": "why"},
  {"op": "update_node", "node": "C-004", "title": "…", "kind": "method", "definition": "…", "add_aliases": [], "add_tags": [], "status": "resolved"},
  {"op": "remove_edge", "src": "K-001", "dst": "C-004", "type": "relates to"},
  {"op": "merge", "node": "C-009", "into": "C-002"},
  {"op": "resolve", "node": "Q-001"},
  {"op": "delete_node", "node": "C-013"},
  {"op": "add_note", "text": "@thesis …", "source": "chat"},
  {"op": "set_summary", "topic": "C-016", "text": "…"},
  {"op": "clear_summary", "topic": "C-016"},
  {"op": "set_glossary", "source": "…pdf", "replace": true, "entries": [{"abbr": "ETN", "expansion": "Edge Twin Node", "kind": "list", "page": 1, "context": "…"}]},
  {"op": "remove_glossary", "abbr": "ETN"},
  {"op": "mark_reviewed", "notes": ["N-003"]}
]}
```
Kinds: concept, method, idea, solution, question, evidence, decision, assumption, risk, explanation,
reference, task, claim (ID prefixes C M I S Q E D A R X F T K; notes N).
