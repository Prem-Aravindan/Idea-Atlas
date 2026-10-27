# Idea Atlas

A local semantic map for your projects. Paste notes, and they are organised into concepts, questions,
ideas, methods, evidence and typed connections, filed under the project you name with `@handle`.

- **Open:** double-click `program\IdeaAtlas.exe` (tip: right-click it → *Pin to Start* or *Pin to taskbar*).
- **Close:** close the window. The app stops completely a few seconds later; nothing keeps running.
- **Capture:** type `@project` (autocomplete) → paste → `Ctrl+Enter`. *Preview* shows the mapping
  without saving; *Undo* removes a note and what only it created.
- **Views:** Search (`Ctrl+K`), Map, Cards, Quests, Notes, Compare, Export, Projects.
- **Exports:** interactive HTML, Markdown with diagrams, JSON, CSV; full backup and import.
- **Privacy:** data lives only in `data\atlas.db`; the app listens on 127.0.0.1 only and makes no internet calls.
- **Settings:** `data\config.json` (`port`, `browser`: `"auto"` = Edge app window, `"default"` = normal browser tab).
- **Deeper pass:** open Claude Code in this folder and say "enrich my Idea Atlas" (see `CLAUDE.md`).

Write naturally; no special format is needed. Optional shortcuts to override a guess: `Q:`, `Idea:`,
`Solution:`, `Method:`, `Evidence:`, `Decision:`, `Assumption:`, `Risk:`, `Todo:`; `X is a …` definitions;
`Long Name (LN)` aliases; `[[Concept]]`; `#tags`.

Build from source: `powershell -ExecutionPolicy Bypass -File build.ps1` (with the app window closed).
