"""SQLite storage. One file, WAL mode, safe to read/write from the CLI while the app runs."""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY, handle TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  description TEXT DEFAULT '', color TEXT DEFAULT '#5b7cfa', archived INTEGER DEFAULT 0,
  created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS notes(
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  code TEXT, text TEXT NOT NULL, source TEXT DEFAULT '', created REAL, ai_reviewed INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS nodes(
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  code TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT DEFAULT '',
  definition TEXT DEFAULT '', status TEXT DEFAULT 'open', confidence TEXT DEFAULT 'medium',
  origin TEXT DEFAULT 'parser', edited INTEGER DEFAULT 0, created REAL, updated REAL,
  UNIQUE(project_id, code));
CREATE INDEX IF NOT EXISTS ix_nodes_project ON nodes(project_id, kind);
CREATE TABLE IF NOT EXISTS aliases(
  node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE, alias TEXT NOT NULL,
  norm TEXT NOT NULL, PRIMARY KEY(node_id, norm));
CREATE INDEX IF NOT EXISTS ix_alias_norm ON aliases(norm);
CREATE TABLE IF NOT EXISTS mentions(
  node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
  excerpt TEXT NOT NULL DEFAULT '', role TEXT DEFAULT 'mention',
  PRIMARY KEY(node_id, note_id, excerpt));
CREATE INDEX IF NOT EXISTS ix_mentions_note ON mentions(note_id);
CREATE TABLE IF NOT EXISTS definitions(
  id INTEGER PRIMARY KEY, node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  text TEXT NOT NULL, note_id INTEGER REFERENCES notes(id) ON DELETE SET NULL,
  origin TEXT DEFAULT 'parser', created REAL);
CREATE TABLE IF NOT EXISTS edges(
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  src INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  dst INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  type TEXT NOT NULL, status TEXT DEFAULT 'active', confidence TEXT DEFAULT 'medium',
  origin TEXT DEFAULT 'parser', note_id INTEGER REFERENCES notes(id) ON DELETE SET NULL,
  evidence TEXT DEFAULT '', created REAL, UNIQUE(src, dst, type));
CREATE INDEX IF NOT EXISTS ix_edges_project ON edges(project_id);
CREATE TABLE IF NOT EXISTS tags(
  node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE, tag TEXT NOT NULL,
  PRIMARY KEY(node_id, tag));
CREATE TABLE IF NOT EXISTS note_tags(
  note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE, tag TEXT NOT NULL,
  PRIMARY KEY(note_id, tag));
CREATE TABLE IF NOT EXISTS counters(project_id INTEGER, prefix TEXT, n INTEGER, PRIMARY KEY(project_id, prefix));
CREATE TABLE IF NOT EXISTS candidates(
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE, norm TEXT, display TEXT,
  count INTEGER DEFAULT 0, last_note INTEGER, PRIMARY KEY(project_id, norm));
CREATE TABLE IF NOT EXISTS quest_state(
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE, key TEXT, status TEXT,
  updated REAL, PRIMARY KEY(project_id, key));
CREATE TABLE IF NOT EXISTS summaries(
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE, topic TEXT NOT NULL, title TEXT,
  text TEXT NOT NULL, origin TEXT DEFAULT 'ai', created REAL, PRIMARY KEY(project_id, topic));
CREATE TABLE IF NOT EXISTS glossary(
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE, abbr TEXT NOT NULL, expansion TEXT NOT NULL,
  context TEXT DEFAULT '', kind TEXT DEFAULT 'text', page INTEGER, source TEXT DEFAULT '', created REAL,
  PRIMARY KEY(project_id, abbr));
CREATE TABLE IF NOT EXISTS requirements(
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE, rid TEXT NOT NULL, grp TEXT DEFAULT '',
  grp_title TEXT DEFAULT '', title TEXT NOT NULL, text TEXT DEFAULT '', source TEXT DEFAULT '', page INTEGER,
  wp TEXT DEFAULT '', challenges TEXT DEFAULT '', context TEXT DEFAULT '', sort INTEGER DEFAULT 0,
  node_id INTEGER REFERENCES nodes(id) ON DELETE SET NULL,
  status TEXT DEFAULT 'not started', progress INTEGER DEFAULT 0, owner TEXT DEFAULT '', due TEXT DEFAULT '',
  note TEXT DEFAULT '', updated REAL, created REAL, PRIMARY KEY(project_id, rid));
CREATE TABLE IF NOT EXISTS requirement_log(
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE, rid TEXT NOT NULL,
  field TEXT, old TEXT, new TEXT, at REAL);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None, timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema', ?)", (str(SCHEMA_VERSION),))
        self.conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('xp', '0')")

    @contextmanager
    def tx(self, rollback: bool = False):
        """Serialized transaction. rollback=True runs everything as a dry run."""
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("ROLLBACK" if rollback else "COMMIT")
                if not rollback:
                    # fold the WAL back into atlas.db so a plain file copy is always a complete backup
                    self.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")

    @contextmanager
    def read(self):
        with self.lock:
            yield self.conn

    def close(self):
        with self.lock:
            self.conn.close()


def now() -> float:
    return time.time()
